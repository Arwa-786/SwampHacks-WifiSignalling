"""WiFind hub: the public face of the booth, running on the Vultr server.

    uvicorn hub:app --host 127.0.0.1 --port 9000      # on the server, behind Caddy (HTTPS)
    python app.py --hub https://64-177-42-5.sslip.io  # on the laptop: connects out to it

The ESP32 receivers are plugged into the laptop by USB, so sensing and the
red-light decision stay there (a venue Wi-Fi hiccup must never cause a false
catch). The laptop can't be reached from the internet, and we don't want a
tunnel, so the laptop dials OUT to this hub over one WebSocket and keeps it open:

  laptop --WebSocket /edge--> hub
     every 0.1 s: the Wi-Fi verdict ("sense")      -> kept in memory here
     answers to forwarded requests ("resp")
  hub --> laptop
     forwarded requests ("req"): Roblox's light changes, the dashboard's calls

Who calls the hub:
  Roblox     GET /api/sense (answered from memory: fast, never waits on the
             laptop), POST /api/phase (forwarded, so the laptop speaks the cue)
  dashboard  GET / and every other /api/... (forwarded to the laptop's app.py)
If the laptop is gone, /api/sense says OFFLINE, which Roblox already handles.

The laptop proves who it is with WIFIND_EDGE_TOKEN (same value in server/.env
on the laptop and in /etc/wifind.env on the server).
"""

from __future__ import annotations

import asyncio
import itertools
import json
import os
import secrets
import time

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response

HERE = os.path.dirname(os.path.abspath(__file__))
STALE_S = 1.5  # no verdict from the laptop for this long -> OFFLINE
FORWARD_TIMEOUT_S = 4.0

OFFLINE = {"state": "OFFLINE", "calibrated": False, "calibrating": False, "motion": 0.0,
           "receivers": [], "detector": "none"}


def create_hub(token: str | None = None) -> FastAPI:
    token = token if token is not None else os.environ.get("WIFIND_EDGE_TOKEN", "")
    if not token:
        raise SystemExit("Set WIFIND_EDGE_TOKEN: without it anyone could pose as the booth laptop.")
    app = FastAPI(title="WiFind hub")
    edge = {"ws": None, "sense": None, "sense_t": 0.0, "connected_t": 0.0}
    pending: dict[str, asyncio.Future] = {}
    ids = itertools.count()
    send_lock = asyncio.Lock()

    @app.websocket("/edge")
    async def edge_socket(ws: WebSocket):
        given = ws.headers.get("authorization", "").removeprefix("Bearer ")
        if not secrets.compare_digest(given, token):
            await ws.close(code=4401)
            return
        await ws.accept()
        if edge["ws"] is not None:  # a reconnecting laptop replaces its old, dead socket
            try:
                await edge["ws"].close()
            except Exception:
                pass
        edge.update(ws=ws, connected_t=time.time())
        try:
            while True:
                msg = json.loads(await ws.receive_text())
                if msg.get("type") == "sense":
                    edge["sense"], edge["sense_t"] = msg["data"], time.time()
                elif msg.get("type") == "resp" and msg.get("id") in pending:
                    pending.pop(msg["id"]).set_result(msg)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            if edge["ws"] is ws:
                edge["ws"] = None

    def fresh() -> bool:
        return edge["ws"] is not None and time.time() - edge["sense_t"] < STALE_S

    @app.get("/api/sense")
    def sense():
        return edge["sense"] if fresh() else OFFLINE

    @app.get("/healthz")
    def health():
        return {"edge": edge["ws"] is not None, "verdict_age_s": round(time.time() - edge["sense_t"], 2)
                if edge["sense_t"] else None, "connected_since": edge["connected_t"] or None}

    @app.get("/")
    def index():
        return FileResponse(os.path.join(HERE, "static", "index.html"))

    @app.api_route("/api/{path:path}", methods=["GET", "POST"])
    async def forward(path: str, request: Request):
        ws = edge["ws"]
        if ws is None:
            return JSONResponse({"ok": False, "reason": "booth laptop not connected"}, status_code=503)
        rid = str(next(ids))
        fut = asyncio.get_running_loop().create_future()
        pending[rid] = fut
        req = {"type": "req", "id": rid, "method": request.method, "path": f"/api/{path}",
               "query": request.url.query, "body": (await request.body()).decode() or None}
        try:
            async with send_lock:
                await ws.send_text(json.dumps(req))
            resp = await asyncio.wait_for(fut, FORWARD_TIMEOUT_S)
        except Exception:
            pending.pop(rid, None)
            return JSONResponse({"ok": False, "reason": "booth laptop did not answer"}, status_code=504)
        return Response(resp.get("body") or "", status_code=resp.get("status", 200),
                        media_type=resp.get("content_type") or "application/json")

    return app


if os.environ.get("WIFIND_EDGE_TOKEN"):  # `uvicorn hub:app` on the server
    app = create_hub()
