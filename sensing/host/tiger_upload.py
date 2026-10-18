"""Put labelled recordings and evaluation results into Tiger Data.

Called automatically at the end of every recorder (record_booth, record_breathing,
record_zones) and by the train/evaluate scripts; backfill_tiger.py does the same
for recordings made earlier. Needs TIGER_URL in server/.env; without it, or if
the database can't be reached, it says so and the local files are all you have
(nothing is lost: the .log files are the source of truth and can be re-uploaded).

Per session it writes (re-runnable: a session's old rows are replaced):
  recordings        one row: kind, person, position, boards, start, length
  training_windows  motion windows (8 features per board, every 0.1 s) and
                    breathing windows (4 per board, every 1 s): label = what the
                    recorder asked for, target = the class a model learns
  readings          the motion windows again, as detector 'recorded', so the
                    dashboards and the per-second aggregate show the session
  breathing         one row per breathing window: FFT rate, confidence, true pace
Recording times are laptop uptime; the header holds the wall-clock time the file
was saved (the end of the recording), which anchors them to real dates.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "server"))
from store import Store  # noqa: E402

KINDS = ("record_booth", "record_breathing", "record_zones", "record_labeled")


def header_of(path: str) -> str:
    with open(path) as f:
        return f.readline().lstrip("# ").strip()


def kind_of(header: str) -> str:
    first = header.split(" ")[0]
    return first.replace("record_", "") if first in KINDS else "other"


def saved_at(header: str) -> datetime:
    m = re.search(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", header)
    return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").astimezone(timezone.utc)  # laptop local time


def connect():
    """psycopg connection with the schema ensured, or None (with the reason printed)."""
    store = Store()
    if not store.url:
        print("Tiger: no TIGER_URL in server/.env, kept local files only")
        return None, None
    try:
        import psycopg

        conn = psycopg.connect(store.url, autocommit=True, connect_timeout=10)
        store.ensure_schema(conn)
        return conn, store
    except Exception as e:
        print(f"Tiger: could not connect ({type(e).__name__}: {str(e).splitlines()[0][:120]}); kept local files only")
        return None, None


def upload_session(conn, name: str, files: list[str]) -> str:
    """One session (one log per board, board order = files order) -> Tiger. Returns a summary."""
    from evaluate import session_meta
    from features import LABEL_TO_CLASS, session_windows

    session = os.path.basename(name)
    header = header_of(files[0])
    kind, end = kind_of(header), saved_at(header)
    boards = [re.search(r"_rx(\w+)\.log$", p).group(1) for p in files]
    who = session_meta(name, files)  # header, or data/sessions.json for older recordings
    person = None if who["person"] == "unknown" else who["person"]
    position = None if who["position"] == "unknown" else who["position"]

    motion, readings, t_first, t_last = [], [], None, None
    for rx, (path, board) in enumerate(zip(files, boards)):
        try:
            times, feats, labels = session_windows(path)
        except ValueError:  # no empty-room windows to measure against: no motion features
            continue
        t_last = max(t_last or times[-1], times[-1])
        t_first = min(t_first or times[0], times[0])
        for t, f, lab in zip(times, feats, labels):
            ts = end - timedelta(seconds=float(times[-1] - t))
            target = LABEL_TO_CLASS.get(lab)
            motion.append((ts, session, board, lab, person, position,
                           [float(v) for v in f], "motion", target))
            if target:
                readings.append((ts, rx, board, target, float(f[0]), 1.0, target, None, "recorded"))

    breath_windows, breath_rows = [], []
    try:
        import breathing_model as bm

        d = bm.session_dataset(files)
        last = d["t"][-1]
        for k in range(len(d["t"])):
            if not d["label"][k]:
                continue
            ts = end - timedelta(seconds=float(last - d["t"][k]))
            target = {bm.EMPTY: "EMPTY", bm.BREATHING: "BREATHING"}.get(int(d["y"][k]))
            for b, board in enumerate(boards):
                f = d["x"][k, b * len(bm.FEATURES):(b + 1) * len(bm.FEATURES)]
                if not np.isnan(f).any():
                    breath_windows.append((ts, session, board, d["label"][k], person, position,
                                           [float(v) for v in f], "breathing", target))
            truth = None if np.isnan(d["truth"][k]) else float(d["truth"][k])
            rate = None if np.isnan(d["rate"][k]) else float(d["rate"][k])
            breath_rows.append((ts, "best", rate, float(d["conf"][k]), session, truth))
    except ValueError:
        pass  # no long empty stretch (e.g. webcam sessions): no breathing windows

    with conn.cursor() as cur:
        cur.execute("DELETE FROM training_windows WHERE session = %s", (session,))
        cur.execute("DELETE FROM breathing WHERE session = %s", (session,))
        if readings:
            cur.execute("DELETE FROM readings WHERE detector IN ('recorded', 'camera_label') AND time BETWEEN %s AND %s",
                        (min(r[0] for r in readings), max(r[0] for r in readings)))
        sql = ("INSERT INTO training_windows (time, session, board, label, person, position, features, kind, target) "
               "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)")
        cur.executemany(sql, motion + breath_windows)
        cur.executemany("INSERT INTO readings (time, rx, board, state, motion, threshold, fused, p_moving, detector) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", readings)
        cur.executemany("INSERT INTO breathing (time, board, per_minute, confidence, session, truth) "
                        "VALUES (%s,%s,%s,%s,%s,%s)", breath_rows)
        seconds = float(t_last - t_first) if t_first is not None else None
        cur.execute("INSERT INTO recordings (session, kind, person, position, boards, started, seconds, header) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (session) DO UPDATE SET kind = EXCLUDED.kind, "
                    "person = EXCLUDED.person, position = EXCLUDED.position, boards = EXCLUDED.boards, "
                    "started = EXCLUDED.started, seconds = EXCLUDED.seconds, header = EXCLUDED.header",
                    (session, kind, person, position, boards,
                     end - timedelta(seconds=seconds or 0), seconds, header))
    return (f"{session} ({kind}): {len(motion)} motion windows, {len(breath_windows)} breathing windows, "
            f"{len(breath_rows)} breathing rates")


def upload(paths: list[str]) -> None:
    """Every session among `paths` (<session>_rx<board>.log files) -> Tiger. Never raises:
    a recording must not be lost because the database is down."""
    from train_model import group_boards

    conn, _ = connect()
    if conn is None:
        return
    try:
        with conn:
            for name, files in group_boards(paths):
                print("Tiger:", upload_session(conn, name, files))
    except (Exception, SystemExit) as e:
        print(f"Tiger upload failed ({type(e).__name__}: {str(e).splitlines()[0][:120] if str(e) else ''}); "
              f"the local files are fine; retry with: python backfill_tiger.py {' '.join(paths)}")


def save_model_runs(runs: list[tuple[str, str, dict]]) -> None:
    """[(model, test_set, metrics)] -> model_runs. Never raises."""
    conn, _ = connect()
    if conn is None:
        return
    try:
        with conn, conn.cursor() as cur:
            clean = [(m, t, json.dumps({k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in mt.items()}))
                     for m, t, mt in runs]
            cur.executemany("INSERT INTO model_runs (model, test_set, metrics) VALUES (%s,%s,%s)", clean)
        print(f"Tiger: saved {len(runs)} result rows to model_runs")
    except Exception as e:
        print(f"Tiger: could not save results ({type(e).__name__}); they're in the local report")
