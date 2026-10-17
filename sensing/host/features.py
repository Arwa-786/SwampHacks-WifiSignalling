"""Turn labelled CSI recordings into feature vectors for the ML model.

A labelled recording is a text file with one row per serial line:
    host_time<TAB>raw serial line<TAB>label
diagnose.py writes this (label = phase name) and record_labeled.py writes it
with labels from the webcam teacher. Lines starting with '#' are comments.

Every 0.1 s we look at the last ~1 s of packets and compute a handful of
numbers (FEATURE_NAMES). Each one is divided by its median over the session's
EMPTY data, so "3.0" means "3x the empty room" in any room or placement; the
live detector does the same with its calibration.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from csi_io import parse_line
from signal_processing import (
    SMOOTH_WINDOW_FRAMES,
    WINDOW_FRAMES,
    aggregate_subcarriers,
    motion_score,
    normalize_gain,
    smoothness_score,
)

HOP_FRAMES = 10

EMPTY, STILL, MOVING = "EMPTY", "STILL", "MOVING"
CLASSES = [EMPTY, STILL, MOVING]

# Recording labels -> the three classes the game needs. Anything with a
# person moving near the link counts as MOVING (that is what eliminates).
LABEL_TO_CLASS = {
    "empty": EMPTY,
    "empty_again": EMPTY,
    "hand_still": STILL,
    "body_still": STILL,
    "frozen": STILL,
    "frozen_again": STILL,
    "still": STILL,
    "hand_wave": MOVING,
    "move_beside": MOVING,
    "slow_move": MOVING,
    "normal_move": MOVING,
    "walk": MOVING,
    "moving": MOVING,
}

FEATURE_NAMES = [
    "size",  # motion_score of the last 0.5 s (subcarrier-averaged)
    "smooth",  # lag-1 autocorrelation over the last 1 s (not baseline-scaled)
    "turb_iqr",  # ESPectre: spread of per-packet spatial CV over 1 s
    "turb_autocorr",  # ESPectre: smoothness of that per-packet CV (not scaled)
    "level_cv",  # std/mean of the overall signal level over 0.5 s
    "rssi_std",  # RSSI wobble over 0.5 s (dB)
    "rssi_drop",  # empty-room RSSI minus current RSSI (dB, not scaled)
    "presence",  # distance of the 0.5 s average pattern from the empty pattern
]
# Already scale-free; dividing by the empty median would be meaningless.
UNSCALED = {"smooth", "turb_autocorr", "rssi_drop"}


@dataclass
class Session:
    name: str
    times: np.ndarray  # (frames,)
    signal: np.ndarray  # (frames, subcarriers), CsiFrame.signal
    rssi: np.ndarray  # (frames,)
    labels: list[str]  # raw label per frame


def load_session(path: str) -> Session:
    times, signal, rssi, labels = [], [], [], []
    with open(path) as f:
        for row in f:
            if row.startswith("#"):
                continue
            parts = row.rstrip("\n").split("\t")
            if len(parts) != 3:
                continue
            frame = parse_line(parts[1], host_time=float(parts[0]))
            if frame is None:
                continue
            times.append(frame.host_time)
            signal.append(frame.signal)
            rssi.append(frame.rssi)
            labels.append(parts[2])
    if not times:
        raise ValueError(f"{path}: no labelled CSI frames")
    return Session(path, np.array(times), np.array(signal), np.array(rssi, dtype=np.float32), labels)


def _lag1(v: np.ndarray) -> float:
    v = v - v.mean()
    return float((v[1:] * v[:-1]).sum() / ((v * v).sum() + 1e-12))


SPAN_FRAMES = max(WINDOW_FRAMES, SMOOTH_WINDOW_FRAMES)


def window_raw(sig: np.ndarray, rssi: np.ndarray) -> np.ndarray:
    """The 8 unscaled features of one window: the last SPAN_FRAMES packets of
    CsiFrame.signal (frames x subcarriers) and their RSSI. Training and the
    live server both call this, so the model sees identical numbers."""
    agg = aggregate_subcarriers(sig)
    # Per-packet spatial CV ("turbulence"), ESPectre's gain-invariant feature.
    turb = agg.std(axis=1) / np.maximum(agg.mean(axis=1), 1e-12)
    level = sig.mean(axis=1)
    short = slice(-WINDOW_FRAMES, None)
    t = turb[-SMOOTH_WINDOW_FRAMES:]
    q75, q25 = np.percentile(t, [75, 25])
    return np.array(
        [
            motion_score(agg[short]),
            smoothness_score(agg[-SMOOTH_WINDOW_FRAMES:]),
            (q75 - q25) / max(abs(t.mean()), 1e-12),
            _lag1(t),
            level[short].std() / max(level[short].mean(), 1e-12),
            rssi[short].std(),
            float(rssi[short].mean()),  # turned into a drop by apply_baseline
            0.0,  # presence: needs the empty profile, filled by apply_baseline
        ]
    )


def window_profile(sig: np.ndarray) -> np.ndarray:
    """Average gain-normalized pattern of the last 0.5 s (for presence)."""
    return normalize_gain(sig[-WINDOW_FRAMES:]).mean(axis=0)


def raw_features(session: Session) -> tuple[np.ndarray, list[str], list[int]]:
    """Unscaled features for every hop, the label of each window, and the frame
    index where each window ends.

    Windows that straddle two labels are dropped: the moment someone starts
    moving is ambiguous and would teach the model noise.
    """
    sig, rssi, labels = session.signal, session.rssi, session.labels
    rows, ys, ends = [], [], []
    for end in range(SPAN_FRAMES, len(sig) + 1, HOP_FRAMES):
        if len(set(labels[end - SPAN_FRAMES : end])) != 1:
            continue
        rows.append(window_raw(sig[end - SPAN_FRAMES : end], rssi[end - SPAN_FRAMES : end]))
        ys.append(labels[end - 1])
        ends.append(end)
    feats = np.array(rows, dtype=np.float64).reshape(-1, len(FEATURE_NAMES))
    return feats, ys, ends


@dataclass
class Baseline:
    """The empty room, learned from EMPTY windows (the camera's empty moments
    in training, the "Scanning the room" step live)."""

    empty_profile: np.ndarray
    rssi: float  # median RSSI while empty
    medians: np.ndarray  # median of each feature while empty (after presence/rssi)


def baseline_from(feats: np.ndarray, profiles: np.ndarray) -> Baseline:
    """feats/profiles of empty-room windows only."""
    empty_profile = profiles.mean(axis=0)
    f = feats.copy()
    f[:, FEATURE_NAMES.index("presence")] = np.abs(profiles - empty_profile).mean(axis=1)
    r = FEATURE_NAMES.index("rssi_drop")
    rssi = float(np.median(f[:, r]))
    f[:, r] = rssi - f[:, r]
    return Baseline(empty_profile, rssi, np.median(f, axis=0))


def apply_baseline(feats: np.ndarray, profiles: np.ndarray, b: Baseline) -> np.ndarray:
    """Express features relative to the empty room: "3.0" = 3x the empty room."""
    out = np.atleast_2d(feats).astype(np.float64).copy()
    out[:, FEATURE_NAMES.index("presence")] = np.abs(np.atleast_2d(profiles) - b.empty_profile).mean(axis=1)
    r = FEATURE_NAMES.index("rssi_drop")
    out[:, r] = b.rssi - out[:, r]
    for j, name in enumerate(FEATURE_NAMES):
        if name not in UNSCALED:
            out[:, j] = out[:, j] / max(b.medians[j], 1e-9)
    return out


def scale_features(session: Session, feats: np.ndarray, labels: list[str], ends: list[int]) -> np.ndarray:
    """Express features relative to this session's EMPTY windows."""
    is_empty = np.array([LABEL_TO_CLASS.get(lab) == EMPTY for lab in labels])
    if not is_empty.any():
        raise ValueError(f"{session.name}: needs some EMPTY-labelled data as the baseline")
    profiles = np.array([window_profile(session.signal[e - WINDOW_FRAMES : e]) for e in ends])
    return apply_baseline(feats, profiles, baseline_from(feats[is_empty], profiles[is_empty]))


def session_windows(path: str) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """(end time of each window, scaled features, raw label) for one recording."""
    session = load_session(path)
    feats, labels, ends = raw_features(session)
    scaled = scale_features(session, feats, labels, ends)
    return session.times[np.array(ends) - 1], scaled, labels


def session_dataset(path: str) -> tuple[np.ndarray, np.ndarray]:
    """(features, class index) for one labelled recording; unknown labels dropped."""
    _, scaled, labels = session_windows(path)
    keep = [i for i, lab in enumerate(labels) if lab in LABEL_TO_CLASS]
    y = np.array([CLASSES.index(LABEL_TO_CLASS[labels[i]]) for i in keep])
    return scaled[keep], y


# Windows from different boards count as "the same moment" if they end within
# this long of each other (each board hops every 10 packets = ~0.1 s).
FUSE_TOLERANCE_S = 0.15


def fused_dataset(paths: list[str]) -> tuple[np.ndarray, np.ndarray]:
    """One session recorded on several boards at once (record_labeled.py writes
    one file per board, all timed by the same laptop clock) -> one row per
    moment with every board's features side by side, board order = paths order.

    Moments where a board has no window nearby (dropped packets) or where the
    boards disagree on the label (a label edge) are left out.
    """
    per = [session_windows(p) for p in paths]
    ref_t, ref_x, ref_lab = per[0]
    rows, ys = [], []
    for k, t in enumerate(ref_t):
        lab = ref_lab[k]
        if lab not in LABEL_TO_CLASS:
            continue
        parts = [ref_x[k]]
        for times, x, labs in per[1:]:
            j = int(np.searchsorted(times, t))
            near = [i for i in (j - 1, j) if 0 <= i < len(times)]
            i = min(near, key=lambda i: abs(times[i] - t)) if near else None
            if i is None or abs(times[i] - t) > FUSE_TOLERANCE_S or labs[i] != lab:
                break
            parts.append(x[i])
        else:
            rows.append(np.concatenate(parts))
            ys.append(CLASSES.index(LABEL_TO_CLASS[lab]))
    return np.array(rows).reshape(-1, len(FEATURE_NAMES) * len(paths)), np.array(ys, dtype=int)
