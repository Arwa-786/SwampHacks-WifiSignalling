from fastapi.testclient import TestClient

from app import FakeMulti, create_app


def client():
    return TestClient(create_app(FakeMulti()))


def test_sense_gives_roblox_a_small_verdict():
    d = client().get("/api/sense").json()
    assert d["state"] in {"MOVING", "STILL"}
    assert d["calibrated"] is True
    assert len(d["receivers"]) == 3
    assert d["motion"] > 0


def test_phase_round_trip_and_rejects_unknown():
    c = client()
    assert c.post("/api/phase", json={"phase": "red"}).json()["ok"] is True
    assert c.get("/api/phase").json()["phase"] == "RED"
    assert c.post("/api/phase", json={"phase": "PARTY"}).json()["ok"] is False
    assert c.get("/api/phase").json()["phase"] == "RED"


def test_scan_phase_triggers_an_announced_room_scan():
    import app as app_module

    c = TestClient(create_app(FakeMulti(), auto_scan=True))
    assert c.post("/api/phase", json={"phase": "LOBBY"}).json()["ok"]
    assert c.get("/api/sense").json()["state"] != "SCANNING"  # lobby = step out, no scan yet
    assert c.post("/api/phase", json={"phase": "SCAN"}).json()["ok"]
    assert c.get("/api/sense").json()["state"] == "SCANNING"
    assert c.post("/api/calibrate").json()["ok"] is False  # one scan at a time


def test_auto_scan_spoiled_by_movement_keeps_the_previous_scan():
    import time

    import app as app_module

    class NoisyScan(FakeMulti):
        """Second scan comes out 50% noisier on every board: someone moved."""

        def __init__(self):
            super().__init__()
            self.noise = {"a": 1.0, "b": 1.0, "c": 1.0}
            self.restored = False

        def calibrate(self, seconds=10.0, margin=1.0):
            self.noise = {k: 1.5 for k in self.noise}

        def scan_noise(self):
            return dict(self.noise)

        def restore_scan(self, saved, scan_time):
            self.restored = True

    multi = NoisyScan()
    c = TestClient(create_app(multi, auto_scan=True))
    c.post("/api/phase", json={"phase": "SCAN"})
    for _ in range(50):
        if multi.restored:
            break
        time.sleep(0.02)
    assert multi.restored


def test_tiger_reads_answer_safely_without_a_database():
    c = client()
    assert c.get("/api/replay").json() == {"ok": False, "reason": "no database"}
    assert c.get("/api/history").json() == {"ok": False, "points": []}
    assert c.get("/api/timeline").json() == {"ok": False, "runs": []}
    assert c.get("/api/scoreboard").json() == {"caught": 0, "won": 0, "red_lights": 0}
    assert c.get("/api/storage").json() == {"rows": 0}


def test_out_phase_records_the_catch_moment_for_the_replay():
    class Recorder:
        enabled = False

        def __init__(self):
            self.events = []

        def add_event(self, kind, detail=None):
            self.events.append(kind)

        def add_snapshot(self, t, snap):
            pass

    rec = Recorder()
    c = TestClient(create_app(FakeMulti(), store=rec))
    c.post("/api/phase", json={"phase": "OUT"})
    assert rec.events == ["phase", "caught"]
