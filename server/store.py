"""Tiger Data (TimescaleDB) storage for everything the sensing sees.

Two tables:
  readings  one row per receiver per tick (~10 Hz): state + motion vs threshold.
            A TimescaleDB *hypertable*: Postgres split into time chunks, so
            "the 10 seconds around this elimination" stays fast as data grows.
  events    light changes, eliminations, wins, calibrations (sparse).

Used for the "How did it catch me?" replay, accuracy numbers, and as the
ML training set. The connection string comes from server/.env
(TIGER_URL=postgres://...), never from code or git.

Writes go through a queue and a background thread, so a slow or offline
database never stalls sensing or the game. With no TIGER_URL, Store does nothing.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    time       TIMESTAMPTZ NOT NULL,
    rx         SMALLINT    NOT NULL,
    state      TEXT        NOT NULL,
    motion     REAL,
    threshold  REAL,
    fused      TEXT
);
SELECT create_hypertable('readings', by_range('time'), if_not_exists => TRUE);
CREATE TABLE IF NOT EXISTS events (
    time    TIMESTAMPTZ NOT NULL,
    kind    TEXT        NOT NULL,
    detail  JSONB
);
CREATE INDEX IF NOT EXISTS events_kind_time ON events (kind, time DESC);
"""


def load_env(path: str = os.path.join(HERE, ".env")) -> None:
    """Minimal .env reader (KEY=VALUE lines) so we need no extra library."""
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"'))


def reading_rows(t: float, snap: dict) -> list[tuple]:
    """MultiReceiver.snapshot() -> one row per receiver."""
    ts = datetime.fromtimestamp(t, timezone.utc)
    return [
        (ts, i, r["state"], r.get("motion"), r.get("motion_threshold"), snap["fused"])
        for i, r in enumerate(snap["receivers"])
    ]


class Store:
    BATCH_S = 1.0  # flush readings once a second: ~30 rows per insert

    def __init__(self, url: str | None = None, connect=None):
        load_env()
        self.url = url or os.environ.get("TIGER_URL")
        self.enabled = bool(self.url)
        self._connect = connect  # tests inject a fake; default is psycopg
        self._q: queue.Queue = queue.Queue(maxsize=20000)
        self.dropped = 0
        self.last_error: str | None = None
        if self.enabled:
            threading.Thread(target=self._run, daemon=True).start()

    # ---- called from the app (never blocks) ----
    def add_snapshot(self, t: float, snap: dict) -> None:
        for row in reading_rows(t, snap):
            self._put(("r", row))

    def add_event(self, kind: str, detail: dict | None = None, t: float | None = None) -> None:
        ts = datetime.fromtimestamp(t or time.time(), timezone.utc)
        self._put(("e", (ts, kind, json.dumps(detail or {}))))

    def _put(self, item) -> None:
        if not self.enabled:
            return
        try:
            self._q.put_nowait(item)
        except queue.Full:  # database down for a long time: keep sensing alive
            self.dropped += 1

    # ---- background writer ----
    def _open(self):
        if self._connect:
            return self._connect(self.url)
        import psycopg

        return psycopg.connect(self.url, autocommit=True)

    def _run(self) -> None:
        conn = None
        while True:
            try:
                if conn is None:
                    conn = self._open()
                    conn.execute(SCHEMA)
                self.flush(conn, self._drain())
                self.last_error = None
            except Exception as e:  # reconnect on any database error
                self.last_error = str(e)
                conn = None
                time.sleep(3)
            time.sleep(self.BATCH_S)

    def _drain(self) -> list:
        items = []
        while True:
            try:
                items.append(self._q.get_nowait())
            except queue.Empty:
                return items

    @staticmethod
    def flush(conn, items: list) -> None:
        readings = [row for kind, row in items if kind == "r"]
        events = [row for kind, row in items if kind == "e"]
        with conn.cursor() as cur:
            if readings:
                cur.executemany(
                    "INSERT INTO readings (time, rx, state, motion, threshold, fused) VALUES (%s,%s,%s,%s,%s,%s)",
                    readings,
                )
            if events:
                cur.executemany("INSERT INTO events (time, kind, detail) VALUES (%s,%s,%s)", events)

    # ---- reads (replay) ----
    def window(self, around: datetime, before_s: float = 6, after_s: float = 2) -> list[dict]:
        """Readings around a moment, e.g. an elimination, for the replay."""
        with self._open() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT time, rx, state, motion, threshold FROM readings "
                "WHERE time BETWEEN %s - make_interval(secs => %s) AND %s + make_interval(secs => %s) "
                "ORDER BY time",
                (around, before_s, around, after_s),
            )
            return [
                {"t": r[0].timestamp(), "rx": r[1], "state": r[2], "motion": r[3], "threshold": r[4]}
                for r in cur.fetchall()
            ]
