"""Laptop side of the hub link: dials OUT to the Vultr hub and keeps it fed.

Started by `app.py --hub https://...`. It talks to the laptop's own app.py over
plain local HTTP, exactly like a browser would, so the game/sensing code needs
no changes and the local screen keeps working if the internet drops.

  every SENSE_S: GET local /api/sense  -> send {"type": "sense"} to the hub
  on {"type": "req"} from the hub      -> run it against local app.py, send the
                                          answer back as {"type": "resp"}
Reconnects forever with a short backoff (venue Wi-Fi comes and goes).
"""

from __future__ import annotations

import json
import threading
import time

import httpx

SENSE_S = 0.1  # Roblox polls the hub every 0.25 s; keep the hub's copy fresher than that
RETRY_S = (1, 2, 5, 5, 10)


def ws_url(hub: str) -> str:
    return hub.rstrip("/").replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/edge"


def handle(local: httpx.Client, req: dict) -> dict:
    """Run one forwarded request against the local app; never raises."""
    try:
        r = local.request(req["method"], req["path"] + (f"?{req['query']}" if req.get("query") else ""),
                          content=req.get("body"), headers={"content-type": "application/json"}, timeout=3.5)
        return {"type": "resp", "id": req["id"], "status": r.status_code, "body": r.text,
                "content_type": r.headers.get("content-type")}
    except Exception as e:
        return {"type": "resp", "id": req["id"], "status": 502,
                "body": json.dumps({"ok": False, "reason": f"laptop app error: {type(e).__name__}"})}


def run(hub: str, token: str, local_url: str, status: dict, stop: threading.Event) -> None:
    from websockets.sync.client import connect

    local = httpx.Client(base_url=local_url)
    attempt = 0
    while not stop.is_set():
        try:
            with connect(ws_url(hub), additional_headers={"Authorization": f"Bearer {token}"},
                         open_timeout=10, ping_interval=10, ping_timeout=10) as ws:
                status.update(connected=True, error=None)
                attempt = 0
                lock = threading.Lock()

                def send(msg: dict):
                    with lock:
                        ws.send(json.dumps(msg))

                def push_sense():
                    while not stop.is_set():
                        started = time.monotonic()
                        try:
                            data = local.get("/api/sense", timeout=1).json()
                        except httpx.HTTPError:
                            data = None  # local app busy for a moment; the hub calls it OFFLINE if it lasts
                        try:
                            if data is not None:
                                send({"type": "sense", "data": data})
                        except Exception:
                            return  # connection gone; the outer loop reconnects and starts a new pusher
                        time.sleep(max(0.0, SENSE_S - (time.monotonic() - started)))

                pusher = threading.Thread(target=push_sense, daemon=True)
                pusher.start()
                for raw in ws:  # forwarded requests; each in its own thread so a slow one can't block the rest
                    msg = json.loads(raw)
                    if msg.get("type") == "req":
                        threading.Thread(target=lambda m=msg: send(handle(local, m)), daemon=True).start()
        except Exception as e:
            status.update(connected=False, error=f"{type(e).__name__}: {e}"[:200])
        delay = RETRY_S[min(attempt, len(RETRY_S) - 1)]
        attempt += 1
        stop.wait(delay)


def start(hub: str, token: str, local_url: str) -> dict:
    """Run the link in the background; returns a live status dict."""
    status = {"hub": hub, "connected": False, "error": None}
    threading.Thread(target=run, args=(hub, token, local_url, status, threading.Event()), daemon=True).start()
    return status
