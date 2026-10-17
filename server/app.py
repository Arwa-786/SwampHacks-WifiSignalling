"""WiFind local server: live Wi-Fi vision UI + Red Light, Green Light.

    cd server && python3 app.py            # then open http://localhost:8000
    python3 app.py --demo                  # no boards: fake receivers for UI work

Runs on the laptop with the RX boards plugged in. The page polls /api/live
~10x per second (plain HTTP, no extra libraries).

Endpoints:
  GET  /                 the UI
  GET  /api/live         receivers, fused state, new waterfall rows, game
  POST /api/calibrate    "scanning the room" (keep the area empty)
  POST /api/game/start   {"player": "nickname"}
  POST /api/game/red     call red light (later: Roblox players do this too)
  GET/POST /api/layout   board positions on the room map (dragged in the UI)
  GET  /api/sense        small Wi-Fi verdict for Roblox (polled ~4x per second)
  POST /api/phase        Roblox tells the booth the light: {"phase": "GREEN"|"RED"|...}

Roblox runs the game itself (Luau). It only needs the verdict, and it reports the
light back so the booth speaker/LEDs (which the hidden player relies on) follow it.
No names or biometric data go to Roblox.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time

import numpy as np
import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "sensing", "host"))

from game import Game  # noqa: E402
from store import Store  # noqa: E402
from multi_rx import WATERFALL_ROWS, MultiReceiver  # noqa: E402

LAYOUT_PATH = os.path.join(HERE, "layout.json")
# Trained Wi-Fi model (train_model.py --fuse). Used when present; otherwise the
# per-board rule detectors decide.
MODEL_PATH = os.path.join(HERE, "..", "sensing", "host", "models", "wifind_fused.json")
SCAN_PATH = os.path.join(HERE, "room_scan.json")  # last room scan, restored on restart
# Auto-scan between rounds (--auto-scan): when Roblox returns to the lobby, the
# player gets AUTO_SCAN_LEAD_S to step out, then the empty room is scanned.
AUTO_SCAN_LEAD_S = 8.0  # 4 s wasn't enough: the player was still walking out
# Reject an auto-scan whose empty-room noise is this much above the previous
# scan's on at least 2 boards: someone moved during it (it happened: +35-60%).
SPOILED_SCAN_RATIO = 1.25
AUTO_SCAN_S = 6.0
TICK_S = 0.1


class FakeMulti:
    """Stand-in for MultiReceiver so the UI can be built without boards.
    Receiver 0 'sees' a slow wave every few seconds."""

    def __init__(self, n: int = 3):
        self.macs = [f"de:mo:00:00:00:0{i}" for i in range(n)]
        self.start_t = time.monotonic()
        self.calibrated = True
        rng = np.random.default_rng(0)
        self.base = rng.uniform(0.6, 1.4, (n, 51))

    def start(self):
        pass

    def calibrate(self, seconds=10.0, margin=1.0):
        time.sleep(seconds)

    def scan_noise(self):
        return {}

    def restore_scan(self, saved, scan_time):
        pass

    def _moving(self, i, t):
        return i < 2 and (t % 8) < 3

    def state(self):
        t = time.monotonic() - self.start_t
        return "MOVING" if sum(self._moving(i, t) for i in range(len(self.macs))) >= 2 else "STILL"

    def snapshot(self):
        t = time.monotonic() - self.start_t
        return {
            "fused": self.state(),
            "receivers": [
                {
                    "port": f"/dev/demo{i}",
                    "mac": mac,
                    "rssi": -50 - 3 * i,
                    "packets_per_s": 98,
                    "state": "MOVING" if self._moving(i, t) else "STILL",
                    "motion": 3.0 if self._moving(i, t) else 0.5,
                    "motion_threshold": 1.0,
                    "smoothness": 0.85 if self._moving(i, t) else 0.05,
                    "presence": 2.0,
                    "presence_threshold": 1.0,
                    "alive": True,
                }
                for i, mac in enumerate(self.macs)
            ],
        }

    def waterfall(self, i):
        t = time.monotonic() - self.start_t
        ts = t - np.arange(WATERFALL_ROWS)[::-1] * 0.01
        wobble = 0.25 * np.sin(2 * np.pi * 1.2 * ts[:, None] + np.linspace(0, 3, 51)) if self._moving(i, t) else 0
        rng = np.random.default_rng(int(t * 100))
        return self.base[i] * (1 + wobble + rng.normal(0, 0.03, (WATERFALL_ROWS, 51)))


class PhaseBody(BaseModel):
    phase: str


ROBLOX_PHASES = {"LOBBY", "GREEN", "RED", "OUT", "WON"}
# The hidden player can't see any screen, so the light must be heard. Until the
# booth speaker/LEDs exist, the laptop says it (macOS `say`; --speak).
SPOKEN = {"GREEN": "Green light!", "RED": "Red light!", "OUT": "You're out! Wi-Fi caught you moving.", "WON": "You made it!"}


class StartBody(BaseModel):
    player: str = "Player"


def default_layout(macs: list[str]) -> dict:
    """TX in the middle, receivers spread around it (metres, top-down)."""
    layout = {"room": {"w": 4.0, "h": 3.0}, "tx": {"x": 2.0, "y": 1.5}, "rx": {}}
    for i, mac in enumerate(macs):
        angle = -np.pi / 2 + i * 2 * np.pi / max(len(macs), 1)
        layout["rx"][mac] = {"x": round(2.0 + 1.2 * np.cos(angle), 2), "y": round(1.5 + 1.2 * np.sin(angle), 2)}
    return layout


def create_app(multi, store: Store | None = None, speak: bool = False, auto_scan: bool = False) -> FastAPI:
    app = FastAPI(title="WiFind")
    store = store or Store()  # Tiger Data; a silent no-op without TIGER_URL
    game = Game()
    status = {"calibrating": False, "roblox_phase": "LOBBY", "roblox_phase_t": 0.0}
    lock = threading.Lock()

    def loop():
        while True:
            if multi.calibrated and not status["calibrating"]:
                with lock:
                    game.tick(time.monotonic(), multi.state())
                store.add_snapshot(time.time(), multi.snapshot())
            time.sleep(TICK_S)

    threading.Thread(target=loop, daemon=True).start()

    @app.get("/")
    def index():
        return FileResponse(os.path.join(HERE, "static", "index.html"))

    @app.get("/api/live")
    def live(rows: int = 10):
        snap = multi.snapshot()
        rows = max(1, min(rows, WATERFALL_ROWS))
        for i, r in enumerate(snap["receivers"]):
            w = multi.waterfall(i)
            r["waterfall"] = np.round(w[-rows:], 3).tolist() if len(w) else []
        with lock:
            snap["game"] = game.snapshot(time.monotonic())
            snap["events"] = [e.__dict__ for e in game.events[-5:]]
        snap["calibrating"] = status["calibrating"]
        snap["calibrated"] = multi.calibrated
        return snap

    def say(text: str) -> None:
        if speak:
            subprocess.Popen(["say", "-r", "220", text])

    def start_scan(seconds: float, lead_s: float = 0.0, announce: bool = False) -> bool:
        """Scan the empty room in the background. `lead_s` gives the player time
        to step out first; `announce` tells them out loud (auto-scan)."""
        if status["calibrating"]:
            return False
        status["calibrating"] = True  # set now so a second request can't start one

        def run():
            try:
                if announce:
                    say("Please step out of the hiding spot. Scanning the room.")
                time.sleep(lead_s)
                before = multi.scan_noise() if announce else {}
                old_saved, old_time = dict(getattr(multi, "_saved", {})), getattr(multi, "scan_time", None)
                multi.calibrate(seconds)
                after = multi.scan_noise() if announce else {}
                worse = [m for m in before if m in after and after[m] > before[m] * SPOILED_SCAN_RATIO]
                if announce and len(worse) >= 2:
                    multi.restore_scan(old_saved, old_time)
                    store.add_event("scan_rejected", {"before": before, "after": after})
                    say("Someone moved during the scan. Keeping the previous scan. Next player, go in.")
                    return
                store.add_event("calibrated", {"seconds": seconds, "auto": announce})
                if announce:
                    say("Room scanned. Next player, go in.")
            finally:
                status["calibrating"] = False

        threading.Thread(target=run, daemon=True).start()
        return True

    @app.post("/api/calibrate")
    def calibrate_room(seconds: float = 10.0):
        if not start_scan(seconds):
            return {"ok": False, "reason": "already calibrating"}
        return {"ok": True, "seconds": seconds}

    @app.post("/api/game/start")
    def start_game(body: StartBody):
        if not multi.calibrated:
            return {"ok": False, "reason": "scan the room first"}
        with lock:
            game.start(time.monotonic(), body.player.strip()[:20] or "Player")
        return {"ok": True}

    @app.post("/api/game/red")
    def red_light(by: str = "host"):
        with lock:
            return {"ok": game.call_red(time.monotonic(), by=by[:20])}

    @app.get("/api/sense")
    def sense():
        snap = multi.snapshot()
        # motion as a multiple of each receiver's own calibrated threshold, so
        # Roblox can show a meter without knowing anything about CSI units
        ratios = [r["motion"] / r["motion_threshold"] for r in snap["receivers"] if r.get("motion_threshold")]
        return {
            "state": "SCANNING" if status["calibrating"] else (snap["fused"] if multi.calibrated else "UNCALIBRATED"),
            "calibrated": multi.calibrated,
            "calibrating": status["calibrating"],
            "motion": round(max(ratios), 2) if ratios else 0.0,
            "receivers": [r["state"] for r in snap["receivers"]],
            "detector": snap.get("detector", "rules"),
        }

    @app.get("/api/store")
    def store_status():
        return {"enabled": store.enabled, "queued": store._q.qsize(), "dropped": store.dropped,
                "error": store.last_error}

    @app.post("/api/phase")
    def set_phase(body: PhaseBody):
        phase = body.phase.upper()
        if phase not in ROBLOX_PHASES:
            return {"ok": False, "reason": "unknown phase"}
        status["roblox_phase"], status["roblox_phase_t"] = phase, time.time()
        store.add_event("phase", {"phase": phase, "sensed": multi.state()})
        if phase in SPOKEN:
            say(SPOKEN[phase])
        if auto_scan and phase == "LOBBY":
            start_scan(AUTO_SCAN_S, AUTO_SCAN_LEAD_S, announce=True)
        return {"ok": True}

    @app.get("/api/phase")
    def get_phase():
        return {"phase": status["roblox_phase"], "since": status["roblox_phase_t"]}

    @app.get("/api/layout")
    def get_layout():
        macs = [r["mac"] or r["port"] for r in multi.snapshot()["receivers"]]
        layout = default_layout(macs)
        if os.path.exists(LAYOUT_PATH):
            with open(LAYOUT_PATH) as f:
                saved = json.load(f)
            layout["room"] = saved.get("room", layout["room"])
            layout["tx"] = saved.get("tx", layout["tx"])
            layout["rx"].update({k: v for k, v in saved.get("rx", {}).items() if k in layout["rx"]})
        return layout

    @app.post("/api/layout")
    def save_layout(layout: dict):
        with open(LAYOUT_PATH, "w") as f:
            json.dump(layout, f, indent=2)
        return {"ok": True}

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--demo", action="store_true", help="fake receivers (no boards needed)")
    parser.add_argument("--ports", nargs="*")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--model", default=MODEL_PATH, help="fused Wi-Fi model (train_model.py --fuse)")
    parser.add_argument("--no-model", action="store_true", help="use the rule detector only")
    parser.add_argument("--speak", action="store_true", help="say red/green light out loud (booth cue)")
    parser.add_argument("--auto-scan", action="store_true", help="scan the empty room whenever Roblox returns to the lobby")
    args = parser.parse_args()

    model = None if args.no_model or not os.path.exists(args.model) else args.model
    print(f"Detector: {'trained Wi-Fi model ' + model if model else 'rules (no model)'}")
    multi = FakeMulti() if args.demo else MultiReceiver(args.ports, model_path=model, scan_path=SCAN_PATH)
    multi.start()
    print("Open http://localhost:%d  (Scan the room first, with the area empty)" % args.port)
    uvicorn.run(create_app(multi, speak=args.speak, auto_scan=args.auto_scan), host="0.0.0.0", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
