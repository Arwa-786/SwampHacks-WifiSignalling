"""Load recorded, webcam-labelled sessions into Tiger Data.

    python backfill_tiger.py data/session_*_rx*.log

For every board's windows: one row in `training_windows` (label + the 8
features the model learns from) and one in `readings` (so the dashboards and
the per-second continuous aggregate show the session), with detector =
'camera_label' to keep it apart from live data.

Recording times are laptop uptime (time.monotonic); the header line holds the
wall-clock time the file was saved, i.e. the end of the recording, which
anchors them to real dates.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timedelta, timezone

from evaluate import session_meta
from features import LABEL_TO_CLASS, session_windows
from train_model import group_boards

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "server"))
from store import Store  # noqa: E402


def saved_at(path: str) -> datetime:
    with open(path) as f:
        m = re.search(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", f.readline())
    local = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
    return local.astimezone(timezone.utc)  # header is laptop local time


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("recordings", nargs="+")
    args = parser.parse_args()
    store = Store()
    if not store.url:
        raise SystemExit("No TIGER_URL in server/.env")
    import psycopg

    with psycopg.connect(store.url, autocommit=True) as conn:
        store.ensure_schema(conn)
        for name, files in group_boards(args.recordings):
            meta = session_meta(name, files)
            session = os.path.basename(name)
            conn.execute("DELETE FROM training_windows WHERE session = %s", (session,))  # re-runnable
            windows, readings = [], []
            for rx, path in enumerate(files):
                board = re.search(r"_rx(\w+)\.log$", path).group(1)
                times, feats, labels = session_windows(path)
                end = saved_at(path)
                for t, f, lab in zip(times, feats, labels):
                    cls = LABEL_TO_CLASS.get(lab)
                    if cls is None:  # "unsure": not training data
                        continue
                    ts = end - timedelta(seconds=float(times[-1] - t))
                    windows.append((ts, session, board, cls, meta["person"], meta["position"], [float(v) for v in f]))
                    readings.append((ts, rx, board, cls, float(f[0]), 1.0, cls, None, "camera_label"))
            conn.execute("DELETE FROM readings WHERE detector = 'camera_label' AND time BETWEEN %s AND %s",
                         (min(w[0] for w in windows), max(w[0] for w in windows)))
            with conn.cursor() as cur:
                cur.executemany("INSERT INTO training_windows VALUES (%s,%s,%s,%s,%s,%s,%s)", windows)
                cur.executemany("INSERT INTO readings (time, rx, board, state, motion, threshold, fused, p_moving, "
                                "detector) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", readings)
            print(f"{session}: {len(windows)} labelled windows from {len(files)} boards "
                  f"({windows[0][0]:%H:%M}-{windows[-1][0]:%H:%M} UTC)")
        conn.execute("CALL refresh_continuous_aggregate('readings_1s', NULL, NULL)")


if __name__ == "__main__":
    main()
