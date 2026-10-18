"""Tiger Data (TimescaleDB) storage for everything the sensing sees.

Tables (all time-series ones are TimescaleDB *hypertables*: Postgres split into
time chunks, so "the 8 seconds around this elimination" stays fast as data grows):
  readings      one row per receiver per tick (~10 Hz): state, motion vs its
                threshold, the fused verdict and the model's P(moving)
  events        light changes, eliminations, wins, room scans (sparse)
  breathing     breathing-rate estimates over time
  training_windows  the labelled ML training set (8 features per board per window)
  model_runs    every evaluation of every model (an in-database model history)
On top of them:
  readings_1s   continuous aggregate: per-second motion per board, kept up to
                date by TimescaleDB itself, so dashboards never scan raw rows
  compression   raw readings older than 1 h are compressed (columnar)
  retention     raw readings are deleted after 30 days (privacy by design:
                no one's movement history is kept forever); the 1 s summaries stay

Used for the "How did it catch me?" replay, the state timeline (Guardian:
"STILL for 42 s"), the scoreboard, and as the ML training/evaluation record.
The connection string comes from server/.env (TIGER_URL=postgres://...),
never from code or git.

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

# Run one at a time: continuous aggregates can't be created inside the implicit
# transaction a multi-statement string would get.
SCHEMA = [
    """CREATE TABLE IF NOT EXISTS readings (
        time       TIMESTAMPTZ NOT NULL,
        rx         SMALLINT    NOT NULL,
        board      TEXT,
        state      TEXT        NOT NULL,
        motion     REAL,
        threshold  REAL,
        fused      TEXT,
        p_moving   REAL,
        detector   TEXT
    )""",
    "SELECT create_hypertable('readings', by_range('time'), if_not_exists => TRUE)",
    "CREATE INDEX IF NOT EXISTS readings_board_time ON readings (board, time DESC)",
    """CREATE TABLE IF NOT EXISTS events (
        time    TIMESTAMPTZ NOT NULL,
        kind    TEXT        NOT NULL,
        detail  JSONB
    )""",
    "SELECT create_hypertable('events', by_range('time'), if_not_exists => TRUE)",
    "CREATE INDEX IF NOT EXISTS events_kind_time ON events (kind, time DESC)",
    """CREATE TABLE IF NOT EXISTS breathing (
        time        TIMESTAMPTZ NOT NULL,
        board       TEXT,
        per_minute  REAL,
        confidence  REAL
    )""",
    "SELECT create_hypertable('breathing', by_range('time'), if_not_exists => TRUE)",
    # the ML training set: one row per board per labelled window (webcam label)
    """CREATE TABLE IF NOT EXISTS training_windows (
        time      TIMESTAMPTZ NOT NULL,
        session   TEXT        NOT NULL,
        board     TEXT        NOT NULL,
        label     TEXT        NOT NULL,
        person    TEXT,
        position  TEXT,
        features  REAL[]      NOT NULL
    )""",
    "SELECT create_hypertable('training_windows', by_range('time'), if_not_exists => TRUE)",
    """CREATE TABLE IF NOT EXISTS model_runs (
        time      TIMESTAMPTZ NOT NULL DEFAULT now(),
        model     TEXT        NOT NULL,
        test_set  TEXT        NOT NULL,
        metrics   JSONB       NOT NULL
    )""",
    # per-second summary per board, maintained by TimescaleDB
    """CREATE MATERIALIZED VIEW IF NOT EXISTS readings_1s
       WITH (timescaledb.continuous) AS
       SELECT time_bucket('1 second', time) AS bucket, board,
              avg(motion / NULLIF(threshold, 0)) AS motion_ratio,
              max(motion / NULLIF(threshold, 0)) AS motion_ratio_max,
              avg(p_moving) AS p_moving,
              count(*) FILTER (WHERE fused = 'MOVING') AS moving_ticks,
              count(*) AS ticks
       FROM readings GROUP BY bucket, board
       WITH NO DATA""",
    """SELECT add_continuous_aggregate_policy('readings_1s',
         start_offset => INTERVAL '1 hour', end_offset => INTERVAL '2 seconds',
         schedule_interval => INTERVAL '5 seconds', if_not_exists => TRUE)""",
]
# Nice to have; skipped (and reported) if the service doesn't allow them.
OPTIONAL = [
    "CREATE EXTENSION IF NOT EXISTS timescaledb_toolkit",
    "ALTER TABLE readings SET (timescaledb.compress, timescaledb.compress_segmentby = 'board')",
    "SELECT add_compression_policy('readings', INTERVAL '1 hour', if_not_exists => TRUE)",
    "SELECT add_retention_policy('readings', INTERVAL '30 days', if_not_exists => TRUE)",
]


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


def _board(mac: str | None) -> str | None:
    return mac.replace(":", "")[-4:].lower() if mac else None


def reading_rows(t: float, snap: dict) -> list[tuple]:
    """MultiReceiver.snapshot() -> one row per receiver."""
    ts = datetime.fromtimestamp(t, timezone.utc)
    return [
        (ts, i, _board(r.get("mac")), r["state"], r.get("motion"), r.get("motion_threshold"), snap["fused"],
         snap.get("ml_p_moving"), snap.get("detector"))
        for i, r in enumerate(snap["receivers"])
    ]


class Store:
    BATCH_S = 1.0  # flush readings once a second: ~30 rows per insert

    def __init__(self, url: str | None = None, connect=None, enabled: bool = True):
        """enabled=False: never store (demo mode's fake boards must not mix with real data)."""
        load_env()
        self.url = url or os.environ.get("TIGER_URL")
        self.enabled = enabled and bool(self.url)
        self._connect = connect  # tests inject a fake; default is psycopg
        self._q: queue.Queue = queue.Queue(maxsize=20000)
        self.dropped = 0
        self.written = 0
        self.last_error: str | None = None
        self.skipped_optional: list[str] = []
        if self.enabled:
            threading.Thread(target=self._run, daemon=True).start()

    # ---- called from the app (never blocks) ----
    def add_snapshot(self, t: float, snap: dict) -> None:
        for row in reading_rows(t, snap):
            self._put(("r", row))

    def add_event(self, kind: str, detail: dict | None = None, t: float | None = None) -> None:
        ts = datetime.fromtimestamp(t or time.time(), timezone.utc)
        self._put(("e", (ts, kind, json.dumps(detail or {}))))

    def add_breathing(self, board: str | None, per_minute: float, confidence: float, t: float | None = None) -> None:
        ts = datetime.fromtimestamp(t or time.time(), timezone.utc)
        self._put(("b", (ts, board, per_minute, confidence)))

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

    def ensure_schema(self, conn) -> None:
        for sql in SCHEMA:
            conn.execute(sql)
        self.skipped_optional = []
        for sql in OPTIONAL:
            try:
                conn.execute(sql)
            except Exception as e:  # e.g. already compressed, or toolkit unavailable
                if "already" not in str(e):
                    self.skipped_optional.append(f"{sql.split('(')[0][:60]}: {str(e).splitlines()[0]}")

    def _run(self) -> None:
        conn = None
        while True:
            try:
                if conn is None:
                    conn = self._open()
                    self.ensure_schema(conn)
                items = self._drain()
                self.flush(conn, items)
                self.written += len(items)
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
        breaths = [row for kind, row in items if kind == "b"]
        with conn.cursor() as cur:
            if readings:
                cur.executemany(
                    "INSERT INTO readings (time, rx, board, state, motion, threshold, fused, p_moving, detector) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    readings,
                )
            if events:
                cur.executemany("INSERT INTO events (time, kind, detail) VALUES (%s,%s,%s)", events)
            if breaths:
                cur.executemany("INSERT INTO breathing (time, board, per_minute, confidence) VALUES (%s,%s,%s,%s)",
                                breaths)

    # ---- reads ----
    def _query(self, sql: str, params: tuple = ()) -> list[tuple]:
        with self._open() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def window(self, around: datetime, before_s: float = 6, after_s: float = 2) -> list[dict]:
        """Readings around a moment, e.g. an elimination, for the replay."""
        rows = self._query(
            "SELECT time, board, state, motion, threshold, p_moving FROM readings "
            "WHERE time BETWEEN %s - make_interval(secs => %s) AND %s + make_interval(secs => %s) "
            "ORDER BY time",
            (around, before_s, around, after_s),
        )
        return [{"t": r[0].timestamp(), "board": r[1], "state": r[2], "motion": r[3], "threshold": r[4],
                 "p_moving": r[5]} for r in rows]

    def last_event(self, kind: str) -> tuple[datetime, dict] | None:
        rows = self._query("SELECT time, detail FROM events WHERE kind = %s ORDER BY time DESC LIMIT 1", (kind,))
        return (rows[0][0], rows[0][1]) if rows else None

    def motion_history(self, minutes: float = 5) -> list[dict]:
        """Per-second motion per board from the continuous aggregate. Seconds
        with no data (board unplugged) come back empty, not missing, thanks to
        time_bucket_gapfill, so charts don't silently join across gaps."""
        rows = self._query(
            "SELECT time_bucket_gapfill('1 second', bucket) AS t, board, avg(motion_ratio), max(p_moving) "
            "FROM readings_1s WHERE bucket > now() - %s * INTERVAL '1 minute' AND bucket <= now() "
            "GROUP BY t, board ORDER BY t",
            (minutes,),
        )
        return [{"t": r[0].timestamp(), "board": r[1], "motion_ratio": r[2], "p_moving": r[3]} for r in rows]

    def state_timeline(self, minutes: float = 10) -> list[dict]:
        """The fused verdict as runs: [{state, start, end, seconds}], newest last.
        For Guardian ("STILL for 42 s") and the Fire rescue screen."""
        try:  # timescaledb_toolkit's state_agg does this in the database
            rows = self._query(
                "SELECT (state_timeline(state_agg(time, fused))).* FROM readings "
                "WHERE rx = 0 AND time > now() - %s * INTERVAL '1 minute'",
                (minutes,),
            )
        except Exception:  # no toolkit: same thing with window functions
            rows = self._query(
                "SELECT fused, min(time), max(time) FROM ("
                "  SELECT time, fused, sum(changed) OVER (ORDER BY time) AS run FROM ("
                "    SELECT time, fused, (fused IS DISTINCT FROM lag(fused) OVER (ORDER BY time))::int AS changed"
                "    FROM readings WHERE rx = 0 AND time > now() - %s * INTERVAL '1 minute') a) b "
                "GROUP BY run, fused ORDER BY min(time)",
                (minutes,),
            )
        return [{"state": r[0], "start": r[1].timestamp(), "end": r[2].timestamp(),
                 "seconds": (r[2] - r[1]).total_seconds()} for r in rows]

    def scoreboard(self, hours: float = 24) -> dict:
        rows = self._query(
            "SELECT detail->>'phase' AS phase, count(*) FROM events "
            "WHERE kind = 'phase' AND time > now() - %s * INTERVAL '1 hour' GROUP BY phase",
            (hours,),
        )
        counts = {r[0]: r[1] for r in rows}
        return {"caught": counts.get("OUT", 0), "won": counts.get("WON", 0), "red_lights": counts.get("RED", 0)}

    def storage(self) -> dict:
        """How big the readings are, raw vs compressed (the compression story)."""
        size = self._query("SELECT hypertable_size('readings')")[0][0]
        try:
            before, after = self._query(
                "SELECT sum(before_compression_total_bytes), sum(after_compression_total_bytes) "
                "FROM chunk_compression_stats('readings')"
            )[0]
        except Exception:
            before = after = None
        count = self._query("SELECT count(*) FROM readings")[0][0]
        return {"rows": count, "bytes": size, "compressed_from": before, "compressed_to": after}

    def add_model_run(self, model: str, test_set: str, metrics: dict) -> None:
        with self._open() as conn:
            conn.execute("INSERT INTO model_runs (model, test_set, metrics) VALUES (%s, %s, %s)",
                         (model, test_set, json.dumps(metrics)))
