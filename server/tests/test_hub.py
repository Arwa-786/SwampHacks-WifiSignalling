import json
import threading
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import hub
from hub import create_hub

AUTH = {"Authorization": "Bearer secret"}


def client():
    return TestClient(create_hub(token="secret"))


def test_sense_is_offline_without_the_laptop():
    assert client().get("/api/sense").json()["state"] == "OFFLINE"
    assert client().get("/api/live").status_code == 503


def test_wrong_token_is_refused():
    with pytest.raises(WebSocketDisconnect):
        with client().websocket_connect("/edge", headers={"Authorization": "Bearer nope"}) as ws:
            ws.receive_text()


def test_roblox_gets_the_latest_verdict_then_offline_when_it_goes_stale(monkeypatch):
    c = client()
    with c.websocket_connect("/edge", headers=AUTH) as ws:
        ws.send_text(json.dumps({"type": "sense", "data": {"state": "MOVING", "motion": 2.5}}))
        time.sleep(0.1)
        assert c.get("/api/sense").json() == {"state": "MOVING", "motion": 2.5}
        monkeypatch.setattr(hub, "STALE_S", 0.0)
        assert c.get("/api/sense").json()["state"] == "OFFLINE"


def serve(app, port):
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            return server
        time.sleep(0.05)
    raise RuntimeError("server did not start")


def free_port():
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_end_to_end_laptop_dials_out_and_roblox_reaches_it_through_the_hub():
    """Real servers: hub <- WebSocket <- edge_bridge -> laptop app (fake boards)."""
    import httpx

    import edge_bridge
    from app import FakeMulti, create_app

    hub_port, app_port = free_port(), free_port()
    servers = [serve(create_hub(token="secret"), hub_port), serve(create_app(FakeMulti()), app_port)]
    status = edge_bridge.start(f"http://127.0.0.1:{hub_port}", "secret", f"http://127.0.0.1:{app_port}")
    try:
        roblox = httpx.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=5)
        for _ in range(100):
            if roblox.get("/api/sense").json()["state"] != "OFFLINE":
                break
            time.sleep(0.05)
        assert status["connected"]
        assert roblox.get("/api/sense").json()["state"] in {"MOVING", "STILL"}
        assert roblox.post("/api/phase", json={"phase": "RED"}).json() == {"ok": True}
        assert roblox.get("/api/phase").json()["phase"] == "RED"  # it reached the laptop's app
    finally:
        for server in servers:
            server.should_exit = True
