"""Breathing as ML: "someone is here, still and breathing" vs "empty room".

The model decides WHETHER there is breathing; the rate (breaths per minute)
stays a frequency measurement (signal_processing.estimate_rate), shown only
when the model says yes. A rate needs many paced sessions to learn, and the
FFT rate can already be checked against the paced truth (eval_breathing.py).

Every STEP_S we look at the last WINDOW_S seconds of each board and compute
FEATURES. Like the motion model, everything except `confidence` is a ratio to
the EMPTY room (in training: the session's empty windows; live: the room scan),
so the numbers mean the same in any room or placement.

Training data (labels -> class):
  record_breathing.py: "empty"           -> EMPTY       "breath_<n>", "rest" -> BREATHING
  record_booth.py:     "empty", "empty_again" -> EMPTY  "frozen"            -> BREATHING
  record_labeled.py:   "empty"           -> EMPTY       "still" (webcam)    -> BREATHING
  (a person freezing is still breathing; moving segments are left out: the
   game's motion model handles those, and gross motion swamps breathing)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import numpy as np
from scipy.signal import butter, sosfiltfilt

from features import load_session
from signal_processing import BREATHING_BAND, estimate_rate, normalize_gain, resample_uniform
from train_model import TinyMLP

WINDOW_S = 15.0  # >= 2 breaths even at 10 per minute
STEP_S = 1.0
FS = 20.0
MOTION_BAND = (0.8, 3.0)  # Hz: faster than breathing = the body moving
MIN_FILL = 0.6  # a window needs >= 60% of the packets it should have (~100/s)
FEATURES = ["confidence", "band_power", "motion_power", "presence"]
SCAN_S = 10.0  # the live room scan is 6-10 s of empty room; training measures its baseline the same way
CHUNK_S = 3.0  # ...cut into 3 s pieces, so there's a typical value and a spread
EMPTY, BREATHING = 0, 1
CLASSES = ["EMPTY", "BREATHING"]


def label_class(label: str) -> int | None:
    if label in ("empty", "empty_again", "empty_end"):
        return EMPTY
    if label.startswith("breath_") or label in ("rest", "frozen", "still"):
        return BREATHING
    return None  # settle, moving, walking...: not used


def _band_power(x: np.ndarray, band: tuple[float, float]) -> float:
    sos = butter(2, band, btype="bandpass", fs=FS, output="sos")
    return float((sosfiltfilt(sos, x, axis=0) ** 2).mean())


def raw_window(times: np.ndarray, sig: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """One board, one window -> (unscaled features, average pattern, FFT rate per minute).
    `presence` is filled in by Baseline.apply (it needs the empty room's pattern)."""
    norm = sig / np.maximum(sig.mean(axis=0), 1e-12)  # per subcarrier, like estimate_rate
    x = resample_uniform(times, norm, FS)
    x = x - x.mean(axis=0)
    est = estimate_rate(times, sig, BREATHING_BAND, FS)
    feats = np.array([est.confidence, _band_power(x, BREATHING_BAND), _band_power(x, MOTION_BAND), 0.0])
    return feats, normalize_gain(sig).mean(axis=0), est.per_minute


@dataclass
class Baseline:
    """The empty room for one board: its average pattern and median powers."""
    profile: np.ndarray
    band_power: float
    motion_power: float
    presence: float

    @classmethod
    def from_empty(cls, feats: np.ndarray, profiles: np.ndarray) -> "Baseline":
        profile = profiles.mean(axis=0)
        presence = np.abs(profiles - profile).mean(axis=1)
        return cls(profile, float(np.median(feats[:, 1])), float(np.median(feats[:, 2])), float(np.median(presence)))

    @classmethod
    def from_scan(cls, times: np.ndarray, sig: np.ndarray) -> "Baseline":
        """The empty room from a short recording (the live room scan, or the last
        SCAN_S seconds of a recording's first empty stretch), in CHUNK_S pieces."""
        feats, profiles = [], []
        t = times[0]
        while t + CHUNK_S <= times[-1] + 1e-9:
            m = (times >= t) & (times < t + CHUNK_S)
            if m.sum() >= MIN_FILL * CHUNK_S * 100:
                f, p, _ = raw_window(times[m], sig[m])
                feats.append(f); profiles.append(p)
            t += CHUNK_S
        if len(feats) < 2:
            raise ValueError("the empty-room scan is too short for a breathing baseline")
        return cls.from_empty(np.array(feats), np.array(profiles))

    def apply(self, feats: np.ndarray, profiles: np.ndarray) -> np.ndarray:
        """Log-ratios to the empty room: 0 = like the empty room, +1 = e (2.7x) more."""
        out = np.atleast_2d(feats).astype(float).copy()
        presence = np.abs(np.atleast_2d(profiles) - self.profile).mean(axis=1)
        out[:, 1] = np.log(out[:, 1] / max(self.band_power, 1e-12) + 1e-6)
        out[:, 2] = np.log(out[:, 2] / max(self.motion_power, 1e-12) + 1e-6)
        out[:, 3] = np.log(presence / max(self.presence, 1e-12) + 1e-6)
        return out

    def to_json(self) -> dict:
        return {"profile": self.profile.tolist(), "band_power": self.band_power,
                "motion_power": self.motion_power, "presence": self.presence}


def scan_frames(session) -> tuple[np.ndarray, np.ndarray]:
    """What a live room scan would have seen in this recording: the last SCAN_S
    seconds of the first empty stretch (by then the person has walked away)."""
    labels = np.array(session.labels)
    empty = np.isin(labels, ["empty", "empty_again", "empty_end"])
    if not empty.any():
        raise ValueError(f"{session.name}: no empty-room stretch to use as the scan")
    first = int(np.argmax(empty))
    end = first + int(np.argmax(~empty[first:])) if (~empty[first:]).any() else len(empty)
    t, sig = session.times[first:end], session.signal[first:end]
    keep = t >= t[-1] - SCAN_S
    return t[keep], sig[keep]


def session_dataset(paths: list[str], baseline: str = "scan") -> dict:
    """One session recorded on several boards (board order = paths order) ->
    windows on a shared time grid with every board's scaled features side by side.
    Returns t, x (n, boards*4), y (class, or -1 = not used), truth (paced rate or nan),
    rate + conf (FFT rate and confidence of the board with the clearest rhythm), label."""
    boards = [load_session(p) for p in paths]
    start = max(b.times[0] for b in boards) + WINDOW_S
    end = min(b.times[-1] for b in boards)
    grid = np.arange(start, end, STEP_S)
    per_board, labels = [], []
    for b in boards:
        feats, profiles, rates, labs = [], [], [], []
        for t in grid:
            m = (b.times > t - WINDOW_S) & (b.times <= t)
            idx = np.flatnonzero(m)
            ok = len(idx) >= MIN_FILL * WINDOW_S * 100
            window_labels = {b.labels[i] for i in idx} if ok else set()
            labs.append(window_labels.pop() if len(window_labels) == 1 else None)
            if ok:
                f, p, r = raw_window(b.times[idx], b.signal[idx])
            else:
                f, p, r = np.full(4, np.nan), np.full(b.signal.shape[1], np.nan), np.nan
            feats.append(f); profiles.append(p); rates.append(r)
        per_board.append((np.array(feats), np.array(profiles), np.array(rates)))
        labels.append(labs)

    # a moment counts only if every board agrees on its label and has data
    lab = [labels[0][k] if all(l[k] == labels[0][k] for l in labels) else None for k in range(len(grid))]
    y = np.array([label_class(l) if l is not None and label_class(l) is not None else -1 for l in lab])
    valid = np.all([~np.isnan(f[:, 0]) for f, _, _ in per_board], axis=0)
    y[~valid] = -1
    if not np.any(y == EMPTY):
        raise ValueError(f"{paths[0]}: needs some empty-room windows as the baseline")

    xs, conf, rates = [], [], []
    for b, (feats, profiles, r) in zip(boards, per_board):
        # "scan": measured like the live room scan (what the server can do);
        # "windows": every empty 15 s window (more data, but not what live has)
        base = (Baseline.from_scan(*scan_frames(b)) if baseline == "scan"
                else Baseline.from_empty(feats[y == EMPTY], profiles[y == EMPTY]))
        scaled = np.full_like(feats, np.nan)
        scaled[valid] = base.apply(feats[valid], profiles[valid])
        xs.append(scaled); conf.append(feats[:, 0]); rates.append(r)
    conf, rates = np.array(conf), np.array(rates)
    best = np.nanargmax(np.where(np.isnan(conf), -1, conf), axis=0)
    truth = np.array([float(l.split("_")[1]) if l and re.fullmatch(r"breath_\d+", l) else np.nan for l in lab])
    return {"t": grid, "x": np.hstack(xs), "y": y, "truth": truth, "rate": rates[best, np.arange(len(grid))],
            "conf": np.nanmax(np.where(np.isnan(conf), -1, conf), axis=0),
            "label": np.array([l or "" for l in lab], dtype=object)}


# ---------------------------------------------------------------- the model

def preprocess(x: np.ndarray) -> np.ndarray:
    return np.nan_to_num(x, nan=0.0)


def train(x: np.ndarray, y: np.ndarray) -> TinyMLP:
    return TinyMLP(x.shape[1], n_out=2, feature_names=FEATURES * (x.shape[1] // len(FEATURES))).fit(preprocess(x), y)


def p_breathing(model: TinyMLP, x: np.ndarray) -> np.ndarray:
    return model.predict_proba(preprocess(np.atleast_2d(x)))[:, BREATHING]


def save(model: TinyMLP, boards: list[str], path: str) -> None:
    with open(path, "w") as f:
        json.dump({"kind": "breathing", "features": model.feature_names, "classes": CLASSES, "boards": boards,
                   "window_s": WINDOW_S, **{k: getattr(model, k).tolist() for k in ("mean", "std", "w1", "b1", "w2", "b2")}}, f)


def load(path: str) -> tuple[TinyMLP, list[str]]:
    with open(path) as f:
        d = json.load(f)
    if d.get("kind") != "breathing" or d["classes"] != CLASSES:
        raise ValueError(f"{path} is not a breathing model; retrain it with train_breathing.py")
    model = TinyMLP(len(d["features"]), n_out=2, feature_names=d["features"])
    for k in ("mean", "std", "w1", "b1", "w2", "b2"):
        setattr(model, k, np.array(d[k]))
    return model, d["boards"]


# ---------------------------------------------------------------- live (the server)

class BreathingLive:
    """The breathing model on live packets. MultiReceiver pushes every board's
    packets, calls scan() around each room scan (the baseline, measured exactly
    like in training: Baseline.from_scan), and update() every couple of seconds.

    update() answers honestly when it can't measure:
      "no model"    no trained breathing model loaded
      "needs scan"  no room scan since the server started (a restored scan has
                    no breathing baseline)
      "warming up"  less than WINDOW_S of packets since the boards started
      "weak signal" a board delivered too few packets in the last WINDOW_S
      "moving"      gross movement swamps breathing; ask again when still
      "ok"          p = P(breathing), present = p >= 0.5, per_minute = median of
                    the boards' FFT rates over RATE_WINDOW_S (only when present)
    """

    RATE_WINDOW_S = 40.0  # Gu et al. 2019, and our sessions: 40 s windows beat 30 s

    def __init__(self, model_path: str | None):
        import threading
        from collections import deque

        self.model, self.boards = load(model_path) if model_path else (None, [])
        self.buffers: dict[str, deque] = {}
        self.baselines: dict[str, Baseline] = {}
        self.lock = threading.Lock()
        self._deque = deque

    def push(self, board: str | None, t: float, signal: np.ndarray) -> None:
        if board is None:
            return
        with self.lock:
            buf = self.buffers.setdefault(board, self._deque(maxlen=int((self.RATE_WINDOW_S + 5) * 110)))
            buf.append((t, signal))

    def _frames(self, board: str, t0: float, t1: float) -> tuple[np.ndarray, np.ndarray]:
        with self.lock:
            rows = [(t, s) for t, s in self.buffers.get(board, ()) if t0 <= t <= t1]
        if not rows:
            return np.empty(0), np.empty((0, 0))
        return np.array([r[0] for r in rows]), np.array([r[1] for r in rows])

    def scan(self, t0: float, t1: float) -> None:
        """The room was empty from t0 to t1 (uptime clock): learn each board's baseline."""
        for board in list(self.buffers):
            t, sig = self._frames(board, max(t0, t1 - SCAN_S), t1)
            try:
                self.baselines[board] = Baseline.from_scan(t, sig)
            except ValueError:
                self.baselines.pop(board, None)

    def update(self, now: float, moving: bool = False) -> dict:
        if self.model is None:
            return {"status": "no model"}
        if any(b not in self.baselines for b in self.boards):
            return {"status": "needs scan"}
        if moving:
            return {"status": "moving"}
        blocks, rates = [], []
        for board in self.boards:
            t, sig = self._frames(board, now - self.RATE_WINDOW_S, now)
            recent = t >= now - WINDOW_S
            if len(t) == 0 or now - t[0] < WINDOW_S:
                return {"status": "warming up"}
            if recent.sum() < MIN_FILL * WINDOW_S * 100:
                return {"status": "weak signal"}
            f, p, _ = raw_window(t[recent], sig[recent])
            blocks.append(self.baselines[board].apply(f, p)[0])
            if t[-1] - t[0] >= 0.75 * self.RATE_WINDOW_S:
                rates.append(estimate_rate(t, sig, BREATHING_BAND, FS).per_minute)
        prob = float(p_breathing(self.model, np.concatenate(blocks))[0])
        present = prob >= 0.5
        rate = float(np.median(rates)) if present and rates else None
        return {"status": "ok", "p": round(prob, 3), "present": present,
                "per_minute": None if rate is None else round(rate, 1)}
