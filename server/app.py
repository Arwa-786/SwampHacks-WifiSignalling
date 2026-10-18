"""WiFind local server: live Wi-Fi vision UI + Red Light, Green Light.

    cd server && python3 app.py            # then open http://localhost:8000
    python3 app.py --demo                  # no boards: fake receivers for UI work
    python3 app.py --hub https://64-177-42-5.sslip.io   # also serve Roblox/judges via the Vultr hub (hub.py)

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
  GET  /api/ai/last      the AI gator's last line and how it was made (--gator)
  GET  /api/ai/rescue    one-sentence room status for the Fire rescue screen

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
from collections import deque

import numpy as np
import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "sensing", "host"))

from game import Game  # noqa: E402
from gator import Gator, Voice, booth_today, rescue_template  # noqa: E402
from store import Store  # noqa: E402
from multi_rx import WATERFALL_ROWS, MultiReceiver  # noqa: E402

LAYOUT_PATH = os.path.join(HERE, "layout.json")
# Trained Wi-Fi model (train_model.py --fuse). Used when present; otherwise the
# per-board rule detectors decide.
MODEL_PATH = os.path.join(HERE, "..", "sensing", "host", "models", "wifind_fused.json")
# Breathing model (train_breathing.py --out ...); used when present.
BREATHING_MODEL_PATH = os.path.join(HERE, "..", "sensing", "host", "models", "breathing.json")
SCAN_PATH = os.path.join(HERE, "room_scan.json")  # last room scan, restored on restart
# Auto-scan between rounds (--auto-scan): Roblox gives the hider time to step
# out (Config.STEP_OUT_S), then says SCAN and we scan the empty room.
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
        still = self.state() == "STILL"
        return {
            "fused": self.state(),
            # demo only: a made-up breathing reading so the screens can be built without boards
            "breathing": {"status": "ok", "p": 0.9 if still else 0.2, "present": still, "per_minute": 16.0 if still else None}
            if self.state() != "MOVING" else {"status": "moving"},
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


# Roblox runs the booth step by step and tells us each one:
#   LOBBY (step out) -> SCAN (we scan the empty room now) -> HIDE -> COUNTDOWN
#   -> GREEN/RED ... -> OUT or WON;  PAUSED when the host pauses.
ROBLOX_PHASES = {"LOBBY", "SCAN", "HIDE", "COUNTDOWN", "GREEN", "RED", "OUT", "WON", "PAUSED"}
# The hidden player can't see any screen, so every step must be heard
# (laptop speaker; --speak). Short, clear, a little swampy.
SPOKEN = {
    "LOBBY": "Round over! Hider, come on out.",
    "HIDE": "Next hider, climb in and get comfy.",
    "COUNTDOWN": "Ready? Three. Two. One.",
    "GREEN": "Green light!",
    "RED": "Red light!",
    "OUT": "Chomp! The Wi-Fi felt that.",
    "WON": "You made it! The gator never saw you.",
    "PAUSED": "Paused. Stay right where you are.",
}
SCAN_LINES = ["Shh. Listening to the empty swamp.",
              "Something splashed during my scan, so I'm keeping the old one.",
              "All quiet. Got it."]
RECENT_S = 10.0  # motion history kept in memory for the gator's "what did the Wi-Fi see" facts


class StartBody(BaseModel):
    player: str = "Player"


def default_layout(macs: list[str]) -> dict:
    """TX in the middle, receivers spread around it (metres, top-down)."""
    layout = {"room": {"w": 4.0, "h": 3.0}, "tx": {"x": 2.0, "y": 1.5}, "rx": {}}
    for i, mac in enumerate(macs):
        angle = -np.pi / 2 + i * 2 * np.pi / max(len(macs), 1)
        layout["rx"][mac] = {"x": round(2.0 + 1.2 * np.cos(angle), 2), "y": round(1.5 + 1.2 * np.sin(angle), 2)}
    return layout


def motion_ratios(snap: dict) -> list[float]:
    """Each receiver's motion as a multiple of its own still threshold."""
    return [r["motion"] / r["motion_threshold"] if r.get("motion_threshold") else 0.0 for r in snap["receivers"]]


def round_facts(rnd: dict, phase: str, now: float, recent) -> dict:
    """What the gator may know about a finished round: numbers only, no names."""
    reds = rnd.get("reds", 0)
    facts = {"result": phase, "seconds_played": round(now - rnd.get("start", now), 1),
             "red_lights_survived": max(reds - 1, 0) if phase == "OUT" else reds, "catch": None}
    if phase == "OUT" and rnd.get("red_t"):
        since_red = [ratios for t, ratios in recent if t >= rnd["red_t"]]
        if since_red:
            peak = np.max(since_red, axis=0)
            facts["catch"] = {"peak_motion_x_threshold": round(float(peak.max()), 1),
                              "receivers_that_saw_it": int((peak > 1.0).sum()),
                              "caught_seconds_after_red_light_called": round(now - rnd["red_t"], 1)}
    return facts


def create_app(multi, store: Store | None = None, speak: bool = False, auto_scan: bool = False,
               gator: Gator | None = None, voice: Voice | None = None) -> FastAPI:
    """`gator`: AI lines after each round (None = fixed lines only).
    `voice`: plays cached Vultr TTS recordings when present, else macOS `say`."""
    app = FastAPI(title="WiFind")
    store = store or Store()  # Tiger Data; a silent no-op without TIGER_URL
    game = Game()
    status = {"calibrating": False, "roblox_phase": "LOBBY", "roblox_phase_t": 0.0, "round": {},
              "state": None, "state_since": time.time(), "last_facts": None}
    recent: deque = deque(maxlen=int(RECENT_S / TICK_S))  # (wall time, motion ratios per board)
    lock = threading.Lock()

    def loop():
        while True:
            if multi.calibrated and not status["calibrating"]:
                with lock:
                    game.tick(time.monotonic(), multi.state())
                snap = multi.snapshot()
                now = time.time()
                recent.append((now, motion_ratios(snap)))
                if snap["fused"] != status["state"]:
                    status["state"], status["state_since"] = snap["fused"], now
                store.add_snapshot(now, snap)
            time.sleep(TICK_S)

    threading.Thread(target=loop, daemon=True).start()

    app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")  # pixel.js etc.

    @app.get("/")
    def index():
        return FileResponse(os.path.join(HERE, "static", "index.html"))

    @app.get("/tools")
    def tools():
        """Booth operator page: room scan, board map, raw waterfalls (not for judges)."""
        return FileResponse(os.path.join(HERE, "static", "tools.html"))

    @app.get("/lab")
    @app.get("/rescue")  # the old name still works
    def lab_page():
        """The Sensing Lab: fire rescue, smart lights, hot car, home care."""
        return FileResponse(os.path.join(HERE, "static", "lab.html"))

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
            subprocess.Popen(voice.command(text) if voice else ["say", "-r", "220", text])

    def gator_after_round(phase: str) -> None:
        """Off the game's path: build the facts, ask the gator (it has its own
        deadline and fallbacks), speak the line after the fixed cue."""
        facts = round_facts(status["round"], phase, time.time(), list(recent))
        if store.enabled:
            try:
                facts["booth_today"] = booth_today(store.scoreboard())
            except Exception:
                pass
        status["last_facts"] = facts
        say(gator.taunt(facts))

    def start_scan(seconds: float, lead_s: float = 0.0, announce: bool = False) -> bool:
        """Scan the empty room in the background. `lead_s` gives the player time
        to step out first; `announce` tells them out loud (auto-scan)."""
        if status["calibrating"]:
            return False
        status["calibrating"] = True  # set now so a second request can't start one

        def run():
            try:
                if announce:
                    say(SCAN_LINES[0])
                time.sleep(lead_s)
                before = multi.scan_noise() if announce else {}
                old_saved, old_time = dict(getattr(multi, "_saved", {})), getattr(multi, "scan_time", None)
                multi.calibrate(seconds)
                after = multi.scan_noise() if announce else {}
                worse = [m for m in before if m in after and after[m] > before[m] * SPOILED_SCAN_RATIO]
                if announce and len(worse) >= 2:
                    multi.restore_scan(old_saved, old_time)
                    store.add_event("scan_rejected", {"before": before, "after": after})
                    say(SCAN_LINES[1])
                    return
                store.add_event("calibrated", {"seconds": seconds, "auto": announce})
                if announce:
                    say(SCAN_LINES[2])
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
            "receiver_alive": [r.get("alive", False) for r in snap["receivers"]],
            "simulated": isinstance(multi, FakeMulti),
            "ratios": [round(r, 2) for r in ratios],  # per board, for the swamp page's bobbers
            "detector": snap.get("detector", "rules"),
            "breathing": snap.get("breathing", {"status": "no model"}),  # see breathing_model.BreathingLive
        }

    @app.get("/api/store")
    def store_status():
        return {"enabled": store.enabled, "written": store.written, "queued": store._q.qsize(),
                "dropped": store.dropped, "error": store.last_error, "skipped_optional": store.skipped_optional}

    # ---- Tiger Data reads (empty answers when no database is configured) ----
    @app.get("/api/replay")
    def replay(before: float = 6, after: float = 2):
        """'How did it catch me?': every board's signal around the last catch."""
        if not store.enabled:
            return {"ok": False, "reason": "no database"}
        moment = store.last_event("caught")
        if not moment:
            return {"ok": False, "reason": "nobody caught yet"}
        return {"ok": True, "caught_at": moment[0].timestamp(), "readings": store.window(moment[0], before, after)}

    @app.get("/api/history")
    def history(minutes: float = 5):
        return {"ok": store.enabled, "points": store.motion_history(minutes) if store.enabled else []}

    @app.get("/api/timeline")
    def timeline(minutes: float = 10):
        return {"ok": store.enabled, "runs": store.state_timeline(minutes) if store.enabled else []}

    @app.get("/api/scoreboard")
    def scoreboard(hours: float = 24):
        return store.scoreboard(hours) if store.enabled else {"caught": 0, "won": 0, "red_lights": 0}

    @app.get("/api/storage")
    def storage():
        return store.storage() if store.enabled else {"rows": 0}

    @app.post("/api/phase")
    def set_phase(body: PhaseBody):
        phase = body.phase.upper()
        if phase not in ROBLOX_PHASES:
            return {"ok": False, "reason": "unknown phase"}
        now = time.time()
        if phase == "GREEN" and status["roblox_phase"] in ("LOBBY", "SCAN", "HIDE", "COUNTDOWN", "OUT", "WON"):
            status["round"] = {"start": now, "reds": 0}  # a new round begins
        elif phase == "RED":
            status["round"]["reds"] = status["round"].get("reds", 0) + 1
            status["round"]["red_t"] = now
        status["roblox_phase"], status["roblox_phase_t"] = phase, now
        store.add_event("phase", {"phase": phase, "sensed": multi.state()})
        if phase == "OUT":  # the moment the replay looks back from
            store.add_event("caught", {"sensed": multi.state()})
        if phase in SPOKEN:
            say(SPOKEN[phase])
        if gator and phase in ("OUT", "WON"):
            threading.Thread(target=gator_after_round, args=(phase,), daemon=True).start()
        if auto_scan and phase == "SCAN":  # Roblox already gave the hider time to step out
            start_scan(AUTO_SCAN_S, 0.0, announce=True)
        return {"ok": True}

    @app.get("/api/ai/last")
    def ai_last():
        return {"enabled": gator is not None, "facts": status["last_facts"], **(gator.last if gator else {})}

    @app.get("/api/ai/rescue")
    def ai_rescue(room: str = "Room 1"):
        facts = {"room": room, "state": status["state"] or multi.state(),
                 "seconds_in_state": round(time.time() - status["state_since"], 1)}
        return {"facts": facts, "line": gator.rescue(facts) if gator else rescue_template(facts)}

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
    parser.add_argument("--breathing-model", default=BREATHING_MODEL_PATH, help="train_breathing.py --out ... (used if it exists)")
    parser.add_argument("--speak", action="store_true", help="say red/green light out loud (booth cue)")
    parser.add_argument("--gator", action="store_true", help="AI gator lines after each round (Vultr; needs --speak to hear them)")
    parser.add_argument("--gator-model", default=None, help="Vultr model (default VULTR_CHAT_MODEL in .env; pick with gator_eval.py)")
    parser.add_argument("--gator-mode", choices=["tools", "prompt"], default="tools")
    parser.add_argument("--hub", help="Vultr hub URL to connect out to (hub.py); needs WIFIND_EDGE_TOKEN in .env")
    parser.add_argument("--auto-scan", action="store_true", help="scan the empty room when Roblox says SCAN (between rounds)")
    args = parser.parse_args()

    model = None if args.no_model or not os.path.exists(args.model) else args.model
    print(f"Detector: {'trained Wi-Fi model ' + model if model else 'rules (no model)'}")
    breathing = args.breathing_model if os.path.exists(args.breathing_model) else None
    print(f"Breathing: {'model ' + breathing if breathing else 'no model (train_breathing.py --out ...)'}")
    multi = FakeMulti() if args.demo else MultiReceiver(args.ports, model_path=model, scan_path=SCAN_PATH,
                                                        breathing_model_path=breathing)
    multi.start()
    print("Open http://localhost:%d  (Scan the room first, with the area empty)" % args.port)
    voice = Voice()
    # render the fixed cues once in the background (no-op without VULTR_TTS_MODEL/VOICE)
    threading.Thread(target=lambda: voice.prerender(list(SPOKEN.values()) + SCAN_LINES), daemon=True).start()
    gator = None
    if args.gator:
        gator = Gator(mode=args.gator_mode, **({"model": args.gator_model} if args.gator_model else {}))
        print("AI gator:", f"{gator.model} ({gator.mode})" if gator.client.enabled else "no VULTR_INFERENCE_API_KEY, fixed lines")
    # demo receivers are fake: keep them out of the Tiger database
    app = create_app(multi, store=Store(enabled=not args.demo), speak=args.speak, auto_scan=args.auto_scan,
                     gator=gator, voice=voice)
    if args.hub:
        import edge_bridge
        from store import load_env

        load_env()
        token = os.environ.get("WIFIND_EDGE_TOKEN")
        if not token:
            raise SystemExit("--hub needs WIFIND_EDGE_TOKEN in server/.env (the same value as on the hub)")
        hub_status = edge_bridge.start(args.hub, token, f"http://127.0.0.1:{args.port}")
        app.get("/api/hub")(lambda: hub_status)
        print(f"Hub: connecting out to {args.hub} (status: /api/hub)")
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
