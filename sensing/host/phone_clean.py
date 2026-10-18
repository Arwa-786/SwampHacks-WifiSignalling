"""Check a booth recording's labels against a phone worn by the person.

    python phone_clean.py data/booth_03_rx*.log --phone ~/Downloads/"Acceleration (without g) ....xlsx"

record_booth.py labels the Wi-Fi with what the voice ASKED ("freeze", "move").
People don't always do exactly that: a freeze with a fidget, a move that started
late. The phone (phyphox, "Acceleration (without g)", in a pocket or waistband)
feels what the body actually did. For every packet we look at the phone over the
surrounding second:
    asked to freeze, phone felt movement  -> relabelled "frozen_moved"  (not trained on)
    asked to move,   phone felt stillness -> relabelled "moving_still"  (not trained on)
Everything else keeps its label. Writes <session>c_rx<board>.log next to the
originals (the originals are never changed) and prints how much was dropped.
Train on the cleaned files like any recording: train_model.py --fuse data/booth_*c_rx*.log
"""

from __future__ import annotations

import argparse
import os
import re

import numpy as np

from phone_truth import clock_offset, load_phyphox

# Linear acceleration (gravity removed) of a body part, m/s^2, spread over 1 s.
# A phone on a still standing person: ~0.02-0.06; shifting weight or waving: 0.2+.
MOVED = 0.15
STILL = 0.05
HALF_S = 0.5


def phone_motion(times: np.ndarray, acc: np.ndarray, at: np.ndarray) -> np.ndarray:
    """Spread of the acceleration magnitude in the second around each time in `at`
    (nan where the phone has no data)."""
    mag = np.linalg.norm(acc, axis=1)
    out = np.full(len(at), np.nan)
    lo = np.searchsorted(times, at - HALF_S)
    hi = np.searchsorted(times, at + HALF_S)
    for k in range(len(at)):
        if hi[k] - lo[k] >= 20:  # at least ~20 phone samples in that second
            out[k] = mag[lo[k]:hi[k]].std()
    return out


def clean(log_path: str, phone: tuple[np.ndarray, np.ndarray]) -> tuple[str, dict]:
    offset = clock_offset(log_path)
    with open(log_path) as f:
        rows = f.read().split("\n")
    def ok(r: str) -> bool:  # a few serial lines arrive garbled; features.py skips them too
        parts = r.split("\t")
        try:
            return len(parts) == 3 and float(parts[0]) > 0
        except ValueError:
            return False
    header, body = rows[0], [r for r in rows[1:] if ok(r)]
    t_host = np.array([float(r.split("\t", 1)[0]) for r in body])
    motion = phone_motion(*phone, t_host + offset)
    counts = {"frozen_moved": 0, "moving_still": 0, "kept": 0, "no_phone": 0}
    out = []
    for r, m in zip(body, motion):
        t, line, label = r.split("\t")
        if np.isnan(m):
            counts["no_phone"] += 1          # kept as recorded: nothing to check it against
        elif label == "frozen" and m > MOVED:
            label = "frozen_moved"
            counts["frozen_moved"] += 1
        elif label in ("moving", "slow_move") and m < STILL:
            label = "moving_still"
            counts["moving_still"] += 1
        else:
            counts["kept"] += 1
        out.append(f"{t}\t{line}\t{label}")
    session = re.sub(r"_rx(\w+)\.log$", r"c_rx\1.log", log_path)
    with open(session, "w") as f:
        f.write(header + f" phone_checked=1\n" + "\n".join(out) + "\n")
    return session, counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("recordings", nargs="+")
    parser.add_argument("--phone", required=True)
    args = parser.parse_args()
    phone = load_phyphox(args.phone)
    for path in args.recordings:
        out, counts = clean(path, phone)
        total = sum(counts.values())
        print(f"{os.path.basename(out)}: dropped {counts['frozen_moved']} 'freeze' packets where the phone felt "
              f"movement, {counts['moving_still']} 'move' packets where it felt stillness "
              f"({(counts['frozen_moved'] + counts['moving_still']) / max(total, 1):.0%}); "
              f"{counts['no_phone']} with no phone data")


if __name__ == "__main__":
    main()
