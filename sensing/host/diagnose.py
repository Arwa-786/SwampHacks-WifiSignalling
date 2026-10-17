"""Guided recording: how strongly does each kind of movement show up?

    python diagnose.py                  # saves diagnose_<time>.log
    python diagnose.py --wall           # person on the other side of a wall
    python diagnose.py --replay diagnose_20261017_041500.log   # re-analyse

Walks you through a few 10 s phases (empty, hand still, hand waving, ...),
records the raw CSI, and prints the motion/presence score of each phase
relative to the empty room. If "wave hand between boards" isn't clearly above
"empty", the placement (or the radio setup) is the problem, not the threshold.
"""

import argparse
import time

import numpy as np

from csi_io import iter_lines, parse_line
from signal_processing import (
    CONFIRM_READINGS,
    HOP_FRAMES,
    MOVING,
    WINDOW_FRAMES,
    LiveDetector,
    calibrate,
    motion_score,
    normalize_gain,
    presence_score,
)

PHASES = [
    ("empty", "Stay AWAY from the boards, don't move"),
    ("hand_still", "Hold your hand STILL right between the boards"),
    ("hand_wave", "WAVE your hand between the boards"),
    ("move_beside", "Move/wave BESIDE the boards (~50 cm off the line), not between"),
    ("body_still", "Lean your head/body INTO the line between the boards, stay still"),
    ("empty_again", "Step away again, don't move"),
]
# Person on the far side of a wall, up to ~2 m from it. Covers what the game
# needs: frozen must stay not-MOVING, slow and normal movement must be MOVING.
WALL_PHASES = [
    ("empty", "Nobody behind the wall. Everyone stay still"),
    ("frozen", "Person behind the wall: SIT FROZEN, hands in lap, breathe normally"),
    ("slow_move", "Person behind the wall: move SLOWLY (slowly raise and lower one arm)"),
    ("frozen_again", "Person behind the wall: FREEZE again"),
    ("normal_move", "Person behind the wall: move NORMALLY (shift, wave, lean)"),
    ("walk", "Person behind the wall: WALK around slowly"),
    ("empty_again", "Person LEAVES the area behind the wall. Everyone stay still"),
]
PHASE_SECONDS = 10
COUNTDOWN_SECONDS = 5
SETTLE_SECONDS = 2  # skipped at the start of each phase while you get into position


def record(port, seconds_per_phase, phases):
    """Guide through phases; return {phase: (times, amplitudes, rssi)} and the raw rows."""
    lines = iter_lines(port)
    rows, data = [], {}
    for name, instruction in phases:
        print(f"\n>>> NEXT: {instruction}\a")
        for n in range(COUNTDOWN_SECONDS, 0, -1):
            print(f"    get ready, starting in {n}...", end="\r", flush=True)
            end = time.monotonic() + 1
            for t, line in lines:  # keep draining the port while counting down
                if t >= end:
                    break
        print(f"    RECORDING {seconds_per_phase:.0f} s: {name}  ({instruction})\a")
        start = time.monotonic()
        times, amps, rssi = [], [], []
        for t, line in lines:
            if t - start >= seconds_per_phase:
                break
            rows.append(f"{t:.4f}\t{line.rstrip()}\t{name}")
            frame = parse_line(line, host_time=t)
            if frame and t - start >= SETTLE_SECONDS:
                times.append(t)
                amps.append(frame.amplitude)
                rssi.append(frame.rssi)
        data[name] = (np.array(times), np.array(amps), np.array(rssi, dtype=float))
        print(f"    got {len(amps)} frames")
    return data, rows


def load(path):
    data = {}
    for row in open(path):
        if row.startswith("#"):
            continue
        t, line, name = row.rstrip("\n").split("\t")
        frame = parse_line(line, host_time=float(t))
        if frame:
            data.setdefault(name, ([], [], []))
            data[name][0].append(frame.host_time)
            data[name][1].append(frame.amplitude)
            data[name][2].append(frame.rssi)
    return {k: (np.array(t), np.array(a), np.array(r, dtype=float)) for k, (t, a, r) in data.items()}


def windows(x, normalize=True):
    x = normalize_gain(x) if normalize else np.asarray(x, dtype=np.float32)
    return [x[i : i + WINDOW_FRAMES] for i in range(0, len(x) - WINDOW_FRAMES + 1, WINDOW_FRAMES // 2)]


# Three ways to measure "the signal is wobbling", compared on the same data:
#   shape : current detector. Each frame divided by its mean, so only changes in
#           the *pattern* across subcarriers count (immune to receiver gain jumps).
#   level : raw amplitudes. Also sees the whole signal dimming when a hand
#           blocks the direct path, but gain jumps look like motion too.
#   rssi  : plain received signal strength in dBm.
def scores(amps, rssi):
    return {
        "shape": np.array([motion_score(w) for w in windows(amps)]),
        "level": np.array([motion_score(w) for w in windows(amps, normalize=False)]),
        "rssi": np.array([w.std() for w in windows(rssi[:, None], normalize=False)]),
    }


def report(data):
    empty = data.get("empty")
    if empty is None or len(empty[1]) < 2 * WINDOW_FRAMES:
        raise SystemExit("not enough 'empty' frames to compare against")
    empty_profile = normalize_gain(empty[1]).mean(axis=0)
    base = {k: max(np.median(v), 1e-6) for k, v in scores(empty[1], empty[2]).items()}
    base_presence = np.median([presence_score(w, empty_profile) for w in windows(empty[1])])

    print("\nmotion = median (max) over 0.5 s windows, as a multiple of the empty room")
    cal = calibrate(empty[1] * 10 ** (empty[2][:, None] / 20), WINDOW_FRAMES)
    print(f"{'phase':13s} {'frames':>6s} {'rssi':>5s}   {'shape':>11s}  {'level':>11s}  {'rssi':>11s}   presence   DETECTOR SAYS MOVING")
    for name in data:
        if len(data[name][1]) < WINDOW_FRAMES:
            continue
        _, amps, rssi = data[name]
        cols = []
        for k, v in scores(amps, rssi).items():
            r = v / base[k]
            cols.append(f"{np.median(r):5.1f} ({r.max():4.1f})")
        p = np.median([presence_score(w, empty_profile) for w in windows(amps)]) / base_presence
        det = LiveDetector(cal, WINDOW_FRAMES, HOP_FRAMES, CONFIRM_READINGS)
        states = [r[0] for r in (det.push(a) for a in amps * 10 ** (rssi[:, None] / 20)) if r]
        moving = np.mean([st == MOVING for st in states]) if states else float("nan")
        print(f"{name:13s} {len(amps):6d} {rssi.mean():5.0f}   {'  '.join(cols)}   {p:6.1f}     {moving:5.0%} of the time")
    print(
        "\nGoal: ~0% MOVING for empty/frozen/still phases, ~100% for wave/move/walk phases.\n"
        "Send this table to Claude."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port")
    parser.add_argument("--replay")
    parser.add_argument("--seconds", type=float, default=PHASE_SECONDS)
    parser.add_argument("--wall", action="store_true", help="through-wall test phases (person behind a wall)")
    args = parser.parse_args()

    if args.replay:
        report(load(args.replay))
        return
    data, rows = record(args.port, args.seconds, WALL_PHASES if args.wall else PHASES)
    path = time.strftime("diagnose_%Y%m%d_%H%M%S.log")
    with open(path, "w") as f:
        f.write(f"# diagnose {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("\n".join(rows) + "\n")
    print(f"\nsaved {path}")
    report(data)


if __name__ == "__main__":
    main()
