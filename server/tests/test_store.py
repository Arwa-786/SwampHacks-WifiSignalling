from store import Store, reading_rows

SNAP = {
    "fused": "MOVING",
    "receivers": [
        {"state": "MOVING", "motion": 2.0, "motion_threshold": 1.0},
        {"state": "STILL", "motion": 0.4, "motion_threshold": 1.1},
    ],
}


class FakeCursor:
    def __init__(self, log):
        self.log = log

    def executemany(self, sql, rows):
        self.log.append((sql.split()[2], list(rows)))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class FakeConn:
    def __init__(self):
        self.log = []

    def cursor(self):
        return FakeCursor(self.log)


def test_one_row_per_receiver_with_fused_state():
    rows = reading_rows(0.0, SNAP)
    assert [r[1:] for r in rows] == [(0, "MOVING", 2.0, 1.0, "MOVING"), (1, "STILL", 0.4, 1.1, "MOVING")]


def test_without_url_store_is_a_silent_no_op(monkeypatch):
    import store

    monkeypatch.setattr(store, "load_env", lambda: None)  # ignore a real server/.env
    monkeypatch.delenv("TIGER_URL", raising=False)
    s = Store(url=None)
    s.add_snapshot(0.0, SNAP)
    s.add_event("red")
    assert not s.enabled and s._q.qsize() == 0


def test_flush_batches_readings_and_events_separately():
    items = [("r", r) for r in reading_rows(0.0, SNAP)] + [("e", ("t", "red", "{}"))]
    conn = FakeConn()
    Store.flush(conn, items)
    assert [(table, len(rows)) for table, rows in conn.log] == [("readings", 2), ("events", 1)]
