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


def raw_features(session: Session) -> tuple[np.ndarray, list[str], list[int]]:
    """Unscaled features for every hop, the label of each window, and the frame
    index where each window ends.

    Windows that straddle two labels are dropped: the moment someone starts
    moving is ambiguous and would teach the model noise.
    """
    sig, rssi, labels = session.signal, session.rssi, session.labels
    agg = aggregate_subcarriers(sig)
    # Per-packet spatial CV ("turbulence"), ESPectre's gain-invariant feature.
    turb = agg.std(axis=1) / np.maximum(agg.mean(axis=1), 1e-12)
    level = sig.mean(axis=1)

    span = max(WINDOW_FRAMES, SMOOTH_WINDOW_FRAMES)
    rows, ys, ends = [], [], []
    for end in range(span, len(sig) + 1, HOP_FRAMES):
        if len(set(labels[end - span : end])) != 1:
            continue
        short = slice(end - WINDOW_FRAMES, end)
        long = slice(end - SMOOTH_WINDOW_FRAMES, end)
        t = turb[long]
        q75, q25 = np.percentile(t, [75, 25])
        rows.append(
            [
                motion_score(agg[short]),
                smoothness_score(agg[long]),
                (q75 - q25) / max(abs(t.mean()), 1e-12),
                _lag1(t),
                level[short].std() / max(level[short].mean(), 1e-12),
                rssi[short].std(),
                float(rssi[short].mean()),  # turned into a drop in scale_features
                0.0,  # presence: needs the empty profile, filled in scale_features
            ]
        )
        ys.append(labels[end - 1])
        ends.append(end)
    feats = np.array(rows, dtype=np.float64).reshape(-1, len(FEATURE_NAMES))
    return feats, ys, ends


def scale_features(session: Session, feats: np.ndarray, labels: list[str], ends: list[int]) -> np.ndarray:
    """Express features relative to this session's EMPTY windows."""
    is_empty = np.array([LABEL_TO_CLASS.get(lab) == EMPTY for lab in labels])
    if not is_empty.any():
        raise ValueError(f"{session.name}: needs some EMPTY-labelled data as the baseline")
    out = feats.copy()
    names = FEATURE_NAMES

    norm = normalize_gain(session.signal)
    profiles = np.array([norm[e - WINDOW_FRAMES : e].mean(axis=0) for e in ends])
    empty_profile = profiles[is_empty].mean(axis=0)
    out[:, names.index("presence")] = np.abs(profiles - empty_profile).mean(axis=1)

    r = names.index("rssi_drop")
    out[:, r] = np.median(feats[is_empty, r]) - feats[:, r]

    for j, name in enumerate(names):
        if name in UNSCALED:
            continue
        base = np.median(out[is_empty, j])
        out[:, j] = out[:, j] / max(base, 1e-9)
    return out


def session_dataset(path: str) -> tuple[np.ndarray, np.ndarray]:
    """(features, class index) for one labelled recording; unknown labels dropped."""
    session = load_session(path)
    feats, labels, ends = raw_features(session)
    scaled = scale_features(session, feats, labels, ends)
    keep = [i for i, lab in enumerate(labels) if lab in LABEL_TO_CLASS]
    y = np.array([CLASSES.index(LABEL_TO_CLASS[labels[i]]) for i in keep])
    return scaled[keep], y
