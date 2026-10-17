"""Run the trained Wi-Fi model live (Wi-Fi only; the camera was just the teacher).

Per board, BoardFeatures keeps the last ~1 s of packets and, every 10 packets,
computes the same 8 numbers training used (features.window_raw), scaled
against the "Scanning the room" step exactly like training scaled against the
camera's empty moments (features.baseline_from / apply_baseline).

HybridModel then decides, every 0.1 s, from all boards together:
  MOVING  if the fused model says so (the game's elimination signal)
  STILL   else if at least half the boards see presence > presence_ratio x empty
  EMPTY   otherwise
followed by the usual 3-readings-in-a-row confirmation (Debouncer).
See train_model.hybrid_predict for why presence is a rule, not the model.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque

import numpy as np

from features import (
    CLASSES,
    FEATURE_NAMES,
    HOP_FRAMES,
    SPAN_FRAMES,
    WINDOW_FRAMES,
    Baseline,
    apply_baseline,
    baseline_from,
    window_profile,
    window_raw,
)
from signal_processing import CONFIRM_READINGS, EMPTY, MOVING, STILL, Debouncer
from train_model import TinyMLP, preprocess

STALE_S = 0.5  # a board whose latest features are older than this is "missing"


def board_key(mac: str | None) -> str | None:
    """'dc:b4:d9:0b:fb:28' -> 'fb28', the suffix record_labeled.py puts in file names."""
    return mac.replace(":", "")[-4:].lower() if mac else None


class BoardFeatures:
    """Feature stream of one receiver. Call push() for every CSI frame."""

    def __init__(self):
        self.sig: deque = deque(maxlen=SPAN_FRAMES)
        self.rssi: deque = deque(maxlen=SPAN_FRAMES)
        self.cal_sig: list = []
        self.cal_rssi: list = []
        self.baseline: Baseline | None = None
        self.latest: np.ndarray | None = None
        self.latest_t = 0.0
        self._n = 0

    def push(self, signal: np.ndarray, rssi: float, calibrating: bool) -> None:
        self.sig.append(signal)
        self.rssi.append(rssi)
        if calibrating:
            self.cal_sig.append(signal)
            self.cal_rssi.append(rssi)
            return
        self._n += 1
        if self.baseline is None or len(self.sig) < SPAN_FRAMES or self._n % HOP_FRAMES:
            return
        sig = np.array(self.sig)
        raw = window_raw(sig, np.array(self.rssi, dtype=np.float32))
        self.latest = apply_baseline(raw, window_profile(sig), self.baseline)[0]
        self.latest_t = time.monotonic()

    def finish_calibration(self) -> bool:
        """Learn the empty room from the frames pushed while calibrating."""
        sig = np.array(self.cal_sig)
        rssi = np.array(self.cal_rssi, dtype=np.float32)
        self.cal_sig, self.cal_rssi = [], []
        ends = range(SPAN_FRAMES, len(sig) + 1, HOP_FRAMES)
        if len(ends) < 5:  # under ~1.5 s of packets: can't learn the room
            self.baseline = None
            return False
        feats = np.array([window_raw(sig[e - SPAN_FRAMES : e], rssi[e - SPAN_FRAMES : e]) for e in ends])
        profiles = np.array([window_profile(sig[e - WINDOW_FRAMES : e]) for e in ends])
        self.baseline = baseline_from(feats, profiles)
        self.latest = None
        return True


class HybridModel:
    def __init__(self, path: str):
        with open(path) as f:
            d = json.load(f)
        if "boards" not in d:
            raise ValueError(f"{path} is a single-board model; train with --fuse")
        self.boards: list[str] = d["boards"]
        self.presence_ratio: float = d.get("presence_ratio", 2.0)
        self.model = TinyMLP.from_json(d)
        self.debouncer = Debouncer(CONFIRM_READINGS, EMPTY)
        self.p_moving = 0.0

    def decide(self, vector: np.ndarray) -> str:
        """One fused feature row (boards in self.boards order) -> raw state."""
        p = self.model.predict_proba(preprocess(vector[None, :]))[0]
        self.p_moving = float(p[CLASSES.index(MOVING)])
        if p.argmax() == CLASSES.index(MOVING):
            return MOVING
        n = len(FEATURE_NAMES)
        pres = vector[[b * n + FEATURE_NAMES.index("presence") for b in range(len(self.boards))]]
        return STILL if (pres > self.presence_ratio).mean() >= 0.5 else EMPTY


class LiveHybrid:
    """Runs HybridModel every 0.1 s over a dict of board key -> BoardFeatures."""

    def __init__(self, model: HybridModel, boards: dict[str, BoardFeatures]):
        self.model = model
        self.boards = boards
        self.state = EMPTY
        self.active = False  # False -> caller falls back to the rule detector
        self.missing: list[str] = []
        self.lock = threading.Lock()

    def step(self) -> None:
        now = time.monotonic()
        rows, missing = [], []
        for key in self.model.boards:
            bf = self.boards.get(key)
            if bf is None or bf.latest is None or now - bf.latest_t > STALE_S:
                missing.append(key)
            else:
                rows.append(bf.latest)
        with self.lock:
            self.missing = missing
            if missing:
                self.active = False
                return
            self.active = True
            self.state = self.model.debouncer.update(self.model.decide(np.concatenate(rows)))

    def run_forever(self, period: float = 0.1) -> None:
        while True:
            self.step()
            time.sleep(period)
