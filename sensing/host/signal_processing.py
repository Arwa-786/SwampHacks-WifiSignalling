"""Turn CSI amplitudes into motion / presence / breathing-rate readings.

Big picture: the TX->RX signal arrives over many paths (direct, bouncing off
walls, the floor, and the person). When the person moves, the path lengths
change and the amplitude of each subcarrier wobbles. So:

  * motion   = the amplitudes wobble by a clear amount over the last ~0.5 s
               AND the wobble is smooth from packet to packet over the last
               ~1 s (bodies move smoothly; radio noise jumps randomly). The
               smoothness test is what lets small/slow movements through
               without letting noise through. Idea from ESPectre
               (github.com/francescopace/espectre).
  * presence = how different the average amplitude pattern is from the empty room
  * breathing = a slow (0.1-0.6 Hz) periodic wobble, found with an FFT

Every threshold is relative to a calibration recorded with the hiding spot
empty, because the absolute numbers change with every room and board placement.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np
from scipy.signal import butter, sosfiltfilt


def normalize_gain(amplitudes: np.ndarray) -> np.ndarray:
    """Divide each frame by its own mean amplitude.

    The ESP32 receiver has automatic gain control: from packet to packet it may
    scale ALL subcarriers up or down together. Without this step a gain jump
    looks exactly like motion. A person changes subcarriers by different
    amounts, so dividing by the frame mean removes the gain but keeps the
    person's effect. Works on one frame (1-D) or many (2-D, frames x subcarriers).
    """
    amplitudes = np.asarray(amplitudes, dtype=np.float32)
    mean = amplitudes.mean(axis=-1, keepdims=True)
    return amplitudes / np.maximum(mean, 1e-6)


def motion_score(window: np.ndarray) -> float:
    """Average over subcarriers of how much each one wobbles, relative to its
    own level (std / mean), so the score doesn't depend on signal strength.

    window: frames x subcarriers of CsiFrame.signal (auto-scaling undone, so a
    hand dimming the whole link counts as motion). Low when nothing moves (just
    radio noise); several times higher when someone moves.
    """
    return float((window.std(axis=0) / np.maximum(window.mean(axis=0), 1e-12)).mean())


def aggregate_subcarriers(window: np.ndarray, k: int = 5) -> np.ndarray:
    """Average each run of k neighbouring subcarriers. A moving body changes
    ~10 neighbouring subcarriers together while noise is independent on each,
    so this cuts noise without hiding motion. frames x N -> frames x (N-k+1)."""
    window = np.asarray(window, dtype=np.float32)
    c = np.cumsum(np.pad(window, ((0, 0), (1, 0))), axis=1)
    return (c[:, k:] - c[:, :-k]) / k


def smoothness_score(window: np.ndarray) -> float:
    """Lag-1 autocorrelation over time, averaged over subcarriers.

    ~0 for pure noise (each packet's error is independent), ~0.8 for a person
    moving (the signal drifts smoothly between packets 10 ms apart), whatever
    the size of the movement. Scale-free, so no calibration needed.
    """
    x = window - window.mean(axis=0)
    num = (x[1:] * x[:-1]).sum(axis=0)
    den = (x * x).sum(axis=0) + 1e-12
    return float((num / den).mean())


def presence_score(window: np.ndarray, empty_profile: np.ndarray) -> float:
    """Mean absolute difference between this window's average amplitude
    pattern and the empty-room pattern. A body in the link changes the static
    multipath even when it is perfectly still."""
    return float(np.abs(window.mean(axis=0) - empty_profile).mean())


@dataclass
class Calibration:
    empty_profile: np.ndarray  # mean gain-normalized amplitude per subcarrier
    motion_threshold: float  # size test; see motion_score
    presence_threshold: float
    motion_baseline: float  # median motion score while empty (for display)


def calibrate(
    amplitudes: np.ndarray,
    window_frames: int,
    margin: float = 1.0,
    presence_margin: float = 1.5,
) -> Calibration:
    """Learn thresholds from a recording of the EMPTY hiding spot.

    amplitudes: frames x subcarriers of CsiFrame.signal.

    We slide the same window used live across the recording, collect the scores
    the empty room produces (noise, people walking far away), and put the
    threshold at the 95th percentile of those times a margin. Raise `margin`
    if you get false "moving" readings, lower it if it misses motion.

    `margin` defaults to 1.0 (not higher) because MOVING also needs the
    smoothness test, which already rejects noise; on a real recording this
    caught 98% of movement beside the link vs 69% for size alone at 1.5,
    with fewer false MOVING on a still hand.
    """
    norm = normalize_gain(amplitudes)
    if len(norm) < 2 * window_frames:
        raise ValueError(f"need at least {2 * window_frames} frames to calibrate, got {len(norm)}")
    empty_profile = norm.mean(axis=0)

    hop = max(1, window_frames // 4)
    starts = range(0, len(norm) - window_frames + 1, hop)
    agg = aggregate_subcarriers(amplitudes)
    motion = np.array([motion_score(agg[i : i + window_frames]) for i in starts])
    presence = np.array([presence_score(norm[i : i + window_frames], empty_profile) for i in starts])
    return Calibration(
        empty_profile=empty_profile,
        motion_threshold=float(np.percentile(motion, 95) * margin),
        presence_threshold=float(np.percentile(presence, 95) * presence_margin),
        motion_baseline=float(np.median(motion)),
    )


# At ~100 packets/s: a 0.5 s window, a new reading every 0.1 s, and 3 agreeing
# readings before the state changes. Measured on a real recording: ~0.3 s from
# a hand starting to wave to "MOVING" (it was ~0.5 s with a 0.2 s hop).
WINDOW_FRAMES = 50
HOP_FRAMES = 10
CONFIRM_READINGS = 3
# Smoothness needs more samples than the size test to be reliable: 1 s.
SMOOTH_WINDOW_FRAMES = 100
# Noise scores ~0, real movement ~0.8 (measured); halfway-plus is "smooth".
SMOOTH_THRESHOLD = 0.5

EMPTY = "EMPTY"
MOVING = "MOVING"
STILL = "STILL"


def classify(motion: float, smoothness: float, presence: float, cal: Calibration) -> str:
    """Raw (un-debounced) state for one window."""
    if motion > cal.motion_threshold and smoothness > SMOOTH_THRESHOLD:
        return MOVING
    if presence > cal.presence_threshold:
        return STILL
    return EMPTY


class Debouncer:
    """Only change the reported state after `needed` identical raw readings in a
    row (CLAUDE.md: "change state only after 3 consistent readings"). Stops a
    single noisy window from eliminating a player."""

    def __init__(self, needed: int = 3, initial: str = EMPTY):
        self.needed = needed
        self.state = initial
        self._candidate = initial
        self._count = 0

    def update(self, raw: str) -> str:
        if raw == self.state:
            self._candidate, self._count = raw, 0
        elif raw == self._candidate:
            self._count += 1
            if self._count >= self.needed:
                self.state, self._count = raw, 0
        else:
            self._candidate, self._count = raw, 1
            if self.needed <= 1:
                self.state, self._count = raw, 0
        return self.state


class LiveDetector:
    """Feed frames one at a time; get a (state, motion, presence) reading every
    `hop_frames` frames. `last_smoothness` holds the latest smoothness score."""

    def __init__(self, cal: Calibration, window_frames: int, hop_frames: int, confirm: int = 3):
        self.cal = cal
        self.window_frames = window_frames
        self.window = deque(maxlen=max(window_frames, SMOOTH_WINDOW_FRAMES))
        self.last_smoothness = 0.0
        self.hop_frames = hop_frames
        self.debouncer = Debouncer(confirm)
        self._since_last = 0

    def push(self, amplitude: np.ndarray) -> tuple[str, float, float] | None:
        """amplitude: one frame of CsiFrame.signal."""
        self.window.append(np.asarray(amplitude, dtype=np.float32))
        self._since_last += 1
        if len(self.window) < self.window.maxlen or self._since_last < self.hop_frames:
            return None
        self._since_last = 0
        w = np.array(self.window)
        agg = aggregate_subcarriers(w)
        recent = w[-self.window_frames :]
        m = motion_score(agg[-self.window_frames :])
        sm = smoothness_score(agg[-SMOOTH_WINDOW_FRAMES:])
        self.last_smoothness = sm
        p = presence_score(normalize_gain(recent), self.cal.empty_profile)
        return self.debouncer.update(classify(m, sm, p, self.cal)), m, p


# ---------------------------------------------------------------- breathing

BREATHING_BAND = (0.1, 0.6)  # Hz = 6-36 breaths per minute
HEART_BAND = (0.8, 2.0)  # Hz = 48-120 bpm. Experimental: ESP32 is usually too noisy.


def resample_uniform(times: np.ndarray, amplitudes: np.ndarray, fs: float) -> np.ndarray:
    """Packets don't arrive exactly every 10 ms (and some are lost). An FFT
    needs evenly spaced samples, so linearly interpolate onto a fixed grid."""
    t_uniform = np.arange(times[0], times[-1], 1.0 / fs)
    return np.stack([np.interp(t_uniform, times, amplitudes[:, k]) for k in range(amplitudes.shape[1])], axis=1)


@dataclass
class RateEstimate:
    per_minute: float
    confidence: float  # 0-1: share of in-band power at the peak; > 0.6 is a clear rhythm


def estimate_rate(
    times: np.ndarray,
    amplitudes: np.ndarray,
    band: tuple[float, float] = BREATHING_BAND,
    fs: float = 20.0,
    top_k: int = 10,
) -> RateEstimate:
    """Dominant periodic rate in `band` across the best subcarriers.

    times: seconds (frames,); amplitudes: frames x subcarriers of CsiFrame.signal.
    Use >= 20 s of data for breathing so a few full breaths fit in the window.
    """
    # Divide each subcarrier by its own average (not each frame by its mean):
    # breathing dims/brightens the whole link together, and per-frame
    # normalization would cancel exactly that.
    amplitudes = np.asarray(amplitudes, dtype=np.float32)
    x = resample_uniform(np.asarray(times), amplitudes / np.maximum(amplitudes.mean(axis=0), 1e-12), fs)
    x = x - x.mean(axis=0)
    # Band-pass: removes slow drift (below band) and fast motion/noise (above).
    # sosfiltfilt runs forward and backward so the signal isn't time-shifted.
    sos = butter(2, band, btype="bandpass", fs=fs, output="sos")
    x = sosfiltfilt(sos, x, axis=0)

    # Zero-pad to 8x for a finer frequency grid (pure interpolation of the
    # spectrum; doesn't add real resolution, but makes the peak easier to locate).
    n_fft = 8 * len(x)
    spectrum = np.abs(np.fft.rfft(x * np.hanning(len(x))[:, None], n=n_fft, axis=0)) ** 2
    freqs = np.fft.rfftfreq(n_fft, 1.0 / fs)
    in_band = (freqs >= band[0]) & (freqs <= band[1])
    band_spec = spectrum[in_band]

    # Some subcarriers barely see the chest; keep the ones with the sharpest peak.
    peakiness = band_spec.max(axis=0) / (np.median(band_spec, axis=0) + 1e-12)
    best = np.argsort(peakiness)[-top_k:]
    combined = band_spec[:, best].mean(axis=1)

    # Confidence = how much of the in-band power sits within +-0.05 Hz
    # (+-3 per minute) of the peak. A real rhythm concentrates its power
    # there (~0.8-1.0); noise spreads out (~0.3 in our synthetic tests).
    band_freqs = freqs[in_band]
    peak = int(np.argmax(combined))
    near_peak = np.abs(band_freqs - band_freqs[peak]) <= 0.05
    return RateEstimate(
        per_minute=float(band_freqs[peak] * 60.0),
        confidence=float(combined[near_peak].sum() / (combined.sum() + 1e-12)),
    )
