"""Read several RX boards at once and combine their verdicts.

One TX, several RX boards around the hiding spot: each TX->RX link sees the
person from a different direction, so a movement one link misses another
catches, and a glitch on one link is outvoted by the others.

    python multi_rx.py                 # auto-finds every RX, calibrates, live view

Each receiver gets its own calibration and its own LiveDetector. The fused
state is MOVING when at least half the receivers (rounded up: 2 of 3) say
MOVING, STILL when any says STILL, else EMPTY.

Boards are identified by the MAC in their STAT lines, because serial port
names change whenever a board is re-plugged.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np

from csi_io import _sends_csi, find_serial_ports, iter_lines, parse_line
from signal_processing import (
    CONFIRM_READINGS,
    EMPTY,
    HOP_FRAMES,
    MOVING,
    STILL,
    WINDOW_FRAMES,
    Calibration,
    LiveDetector,
    calibrate,
)
from features import Baseline

from ml_live import BoardFeatures, HybridModel, LiveHybrid, board_key  # noqa: E402

SILENT_RECONNECT_S = 3.0
WATERFALL_ROWS = 200  # ~2 s of history per receiver for the UI


def fuse(states: list[str]) -> str:
    """MOVING needs ceil(n/2) votes (2 of 3, 1 of 2, 1 of 1)."""
    if not states:
        return EMPTY
    if sum(s == MOVING for s in states) >= math.ceil(len(states) / 2):
        return MOVING
    if any(s in (STILL, MOVING) for s in states):
        return STILL
    return EMPTY


@dataclass
class Receiver:
    port: str
    mac: str | None = None
    rssi: float = 0.0
    packets_per_s: float = 0.0
    state: str = EMPTY
    motion: float = 0.0
    smoothness: float = 0.0
    presence: float = 0.0
    detector: LiveDetector | None = None
    calibrating: list = field(default_factory=list)
    waterfall: deque = field(default_factory=lambda: deque(maxlen=WATERFALL_ROWS))
    last_frame_time: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)
    ml: BoardFeatures = field(default_factory=BoardFeatures)

    def summary(self) -> dict:
        with self.lock:
            return {
                "port": self.port,
                "mac": self.mac,
                "rssi": self.rssi,
                "packets_per_s": self.packets_per_s,
                "state": self.state,
                "motion": self.motion,
                "motion_threshold": self.detector.cal.motion_threshold if self.detector else None,
                "smoothness": self.smoothness,
                "presence": self.presence,
                "presence_threshold": self.detector.cal.presence_threshold if self.detector else None,
                "alive": time.monotonic() - self.last_frame_time < 2.0,
            }


class MultiReceiver:
    def __init__(self, ports: list[str] | None = None, model_path: str | None = None, scan_path: str | None = None):
        if ports is None:
            ports = find_rx_ports()
        if not ports:
            raise SystemExit("No RX boards found. Plug them in and check the TX has power (check_link.py).")
        self.receivers = [Receiver(p) for p in ports]
        self._calibrating = threading.Event()
        # Trained Wi-Fi model (optional). Boards are matched to the model by MAC
        # once their STAT lines arrive; until then, or if a board is missing,
        # state() falls back to the per-board rule detectors + vote.
        self.hybrid = LiveHybrid(HybridModel(model_path), {}) if model_path else None
        # The last room scan is saved here and restored per board (by MAC) on
        # start, so restarting the server doesn't mean asking everyone to leave
        # again. If the boards moved since, scan again.
        self.scan_path = scan_path
        self.scan_time: float | None = None
        self._saved: dict = {}
        if scan_path and os.path.exists(scan_path):
            with open(scan_path) as f:
                saved = json.load(f)
            self._saved, self.scan_time = saved.get("boards", {}), saved.get("time")

    # ------------------------------------------------------------ reading
    def start(self) -> None:
        for rx in self.receivers:
            threading.Thread(target=self._read, args=(rx,), daemon=True).start()
        if self.hybrid:
            threading.Thread(target=self.hybrid.run_forever, daemon=True).start()

    def _read(self, rx: Receiver) -> None:
        """Read one board forever. A board that is bumped or re-plugged drops
        its USB connection ("Device not configured"); without this loop its
        thread would die and the receiver would silently go dead."""
        lost = False
        while True:
            try:
                self._read_once(rx)
            except Exception as e:  # serial errors: port vanished, read failed
                if not lost:  # say it once, not every retry
                    print(f"[{rx.port}] lost connection ({e.__class__.__name__}); retrying", flush=True)
                lost = True
            time.sleep(1.0)

    def _read_once(self, rx: Receiver) -> None:
        last_data = time.monotonic()
        for t, line in iter_lines(rx.port):
            # A board behind the USB hub can go silent without any error (the
            # old connection is dead but still "open"); reopen it after 3 s.
            if line.strip():
                last_data = t
            elif t - last_data > SILENT_RECONNECT_S:
                raise TimeoutError(f"no data for {SILENT_RECONNECT_S:.0f} s")
            if line.startswith("STAT,"):
                parts = line.strip().split(",")
                with rx.lock:
                    rx.packets_per_s = float(parts[2]) if len(parts) > 2 else 0.0
                    if len(parts) > 4 and rx.mac != parts[4]:
                        rx.mac = parts[4]
                        if self.hybrid:
                            self.hybrid.boards[board_key(rx.mac)] = rx.ml
                        if rx.detector is None and rx.mac in self._saved:
                            self._restore(rx, self._saved[rx.mac])
                continue
            frame = parse_line(line, host_time=t)
            if frame is None:
                continue
            signal = frame.signal
            with rx.lock:
                rx.rssi = frame.rssi
                rx.last_frame_time = t
                # Gain-normalized amplitudes are what the waterfall shows.
                rx.waterfall.append(frame.amplitude / max(frame.amplitude.mean(), 1e-6))
                rx.ml.push(signal, frame.rssi, self._calibrating.is_set())
                if self._calibrating.is_set():
                    rx.calibrating.append(signal)
                    continue
                if rx.detector is None:
                    continue
                reading = rx.detector.push(signal)
                if reading:
                    rx.state, rx.motion, rx.presence = reading
                    rx.smoothness = rx.detector.last_smoothness

    # -------------------------------------------------------- calibration
    def calibrate(self, seconds: float = 10.0, margin: float = 1.0) -> None:
        """Learn the empty room for every receiver at once."""
        for rx in self.receivers:
            with rx.lock:
                rx.calibrating = []
        self._calibrating.set()
        time.sleep(seconds)
        self._calibrating.clear()
        for rx in self.receivers:
            with rx.lock:
                frames = np.array(rx.calibrating)
                rx.calibrating = []
                if len(frames) < 2 * WINDOW_FRAMES:
                    print(f"{rx.port}: only {len(frames)} frames during calibration, receiver ignored")
                    rx.detector = None
                    continue
                rx.detector = LiveDetector(calibrate(frames, WINDOW_FRAMES, margin), WINDOW_FRAMES, HOP_FRAMES, CONFIRM_READINGS)
                rx.state = EMPTY
                rx.ml.finish_calibration()
        self.scan_time = time.time()
        self._save()

    def _save(self) -> None:
        if not self.scan_path:
            return
        boards = {}
        for rx in self.receivers:
            with rx.lock:
                if rx.mac is None or rx.detector is None:
                    continue
                c, b = rx.detector.cal, rx.ml.baseline
                boards[rx.mac] = {
                    "rules": {"empty_profile": c.empty_profile.tolist(), "motion_threshold": c.motion_threshold,
                              "presence_threshold": c.presence_threshold, "motion_baseline": c.motion_baseline},
                    "ml": None if b is None else {"empty_profile": b.empty_profile.tolist(), "rssi": b.rssi,
                                                  "medians": b.medians.tolist()},
                }
        self._saved = boards
        with open(self.scan_path, "w") as f:
            json.dump({"time": self.scan_time, "boards": boards}, f)

    def scan_noise(self) -> dict[str, float]:
        """Per board: the empty room's typical motion score in the current scan.
        A person moving during the scan pushes this up (see scan_is_spoiled)."""
        with_ml = {}
        for rx in self.receivers:
            with rx.lock:
                if rx.mac and rx.ml.baseline is not None:
                    with_ml[rx.mac] = float(rx.ml.baseline.medians[0])  # "size" feature
        return with_ml

    def restore_scan(self, saved: dict, scan_time: float | None) -> None:
        """Put back an earlier scan (from _saved) and rewrite the scan file."""
        for rx in self.receivers:
            with rx.lock:
                if rx.mac in saved:
                    self._restore(rx, saved[rx.mac])
        self.scan_time = scan_time
        self._save()

    @staticmethod
    def _restore(rx: Receiver, saved: dict) -> None:
        """Called with rx.lock held, when a board's MAC first arrives."""
        r = saved["rules"]
        cal = Calibration(np.array(r["empty_profile"]), r["motion_threshold"], r["presence_threshold"], r["motion_baseline"])
        rx.detector = LiveDetector(cal, WINDOW_FRAMES, HOP_FRAMES, CONFIRM_READINGS)
        if saved.get("ml"):
            m = saved["ml"]
            rx.ml.baseline = Baseline(np.array(m["empty_profile"]), m["rssi"], np.array(m["medians"]))
        print(f"[{rx.port}] restored the saved room scan", flush=True)

    @property
    def calibrated(self) -> bool:
        return any(rx.detector for rx in self.receivers)

    # ------------------------------------------------------------- output
    def state(self) -> str:
        if self.hybrid and self.hybrid.active:
            return self.hybrid.state
        return fuse([rx.state for rx in self.receivers if rx.detector is not None])

    def detector_info(self) -> dict:
        """Which detector is deciding right now, for the UI and logs."""
        scan = {"scan_age_s": round(time.time() - self.scan_time) if self.scan_time else None}
        if not self.hybrid:
            return {"detector": "rules", **scan}
        return {
            **scan,
            "detector": "ml" if self.hybrid.active else "rules",
            "ml_p_moving": round(self.hybrid.model.p_moving, 3),
            "ml_missing_boards": self.hybrid.missing,
        }

    def snapshot(self) -> dict:
        return {"fused": self.state(), "receivers": [rx.summary() for rx in self.receivers], **self.detector_info()}

    def waterfall(self, index: int) -> np.ndarray:
        rx = self.receivers[index]
        with rx.lock:
            return np.array(rx.waterfall)


def find_rx_ports() -> list[str]:
    """Every serial port that is streaming CSI (checked in parallel, ~3 s)."""
    ports = find_serial_ports()
    with ThreadPoolExecutor(max_workers=max(1, len(ports))) as pool:
        ok = list(pool.map(_sends_csi, ports))
    return [p for p, good in zip(ports, ok) if good]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ports", nargs="*", help="RX ports (default: auto-detect)")
    parser.add_argument("--calibrate-seconds", type=float, default=10)
    parser.add_argument("--margin", type=float, default=1.0)
    args = parser.parse_args()

    multi = MultiReceiver(args.ports)
    print(f"Receivers: {[rx.port for rx in multi.receivers]}")
    multi.start()
    input("Scanning the room: keep everyone out of the area, then press Enter...")
    print(f"Calibrating {args.calibrate_seconds:.0f} s, stay still and away...")
    multi.calibrate(args.calibrate_seconds, args.margin)
    colors = {MOVING: "\033[91m", STILL: "\033[93m"}
    try:
        while True:
            snap = multi.snapshot()
            parts = [
                f"{(r['mac'] or r['port'])[-5:]}:{colors.get(r['state'], '')}{r['state'][:4]}\033[0m"
                f" m{r['motion'] / (r['motion_threshold'] or 1):3.1f}x s{r['smoothness']:4.2f}"
                for r in snap["receivers"]
            ]
            fused = snap["fused"]
            print(f"\r{colors.get(fused, '')}{fused:6s}\033[0m | " + " | ".join(parts) + "   ", end="", flush=True)
            time.sleep(0.1)
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
