"""Step 2b: the go/no-go test from CLAUDE.md, scored automatically.

    python go_no_go.py --trials 10

The script calibrates on the empty spot, then asks for each trial
("EMPTY" / "PERSON STILL" / "PERSON MOVING"), records a few seconds and checks
what the detector said. At the end:

  Test 1  empty vs person present   need >= 9/10
  Test 2  moving vs frozen          need >= 9/10
  Test 3  30 s frozen with people walking 2-3 m away: zero false MOVING

Every trial is also saved to go_no_go_<time>/ so we can re-tune offline.
Fail -> CLAUDE.md says switch core sensing to the HLK-LD2410 radar.
"""

import argparse
import os
import random
import time
from collections import Counter

from csi_io import add_port_args, collect, iter_frames
from signal_processing import (
    CONFIRM_READINGS,
    EMPTY,
    HOP_FRAMES,
    MOVING,
    STILL,
    WINDOW_FRAMES,
    LiveDetector,
    calibrate,
)

INSTRUCTIONS = {
    EMPTY: "Hiding spot EMPTY (person steps out of the link)",
    STILL: "Person in the hiding spot, FROZEN (normal breathing ok)",
    MOVING: "Person in the hiding spot, MOVING (wave arms, shift, sway)",
}


def run_trial(frames, cal, seconds: float, confirm: int = 1, settle: float = 1.0):
    """Return (majority state, all states, max motion, times, amps) over the trial.

    The first `settle` seconds are skipped: getting into position shouldn't
    count, and it also drains serial data that piled up while we waited for Enter.
    confirm=1 scores raw windows; the crowd test uses the game's real debouncing.
    """
    collect(frames, settle)
    times, amps = collect(frames, seconds)
    detector = LiveDetector(cal, WINDOW_FRAMES, HOP_FRAMES, confirm)
    states, max_motion = [], 0.0
    for a in amps:
        r = detector.push(a)
        if r:
            states.append(r[0])
            max_motion = max(max_motion, r[1])
    majority = Counter(states).most_common(1)[0][0] if states else EMPTY
    return majority, states, max_motion, times, amps


def save(folder, name, times, amps):
    import numpy as np

    np.savez_compressed(os.path.join(folder, name), times=times, amps=amps)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_port_args(parser)
    parser.add_argument("--trials", type=int, default=10, help="trials per test")
    parser.add_argument("--trial-seconds", type=float, default=5)
    parser.add_argument("--margin", type=float, default=1.0)
    args = parser.parse_args()

    folder = time.strftime("go_no_go_%Y%m%d_%H%M%S")
    os.makedirs(folder)
    frames = iter_frames(args.port, args.replay)

    input("Calibration: hiding spot EMPTY, press Enter...")
    collect(frames, 1)  # drain data buffered while waiting for Enter
    _, amps = collect(frames, 15)
    cal = calibrate(amps, WINDOW_FRAMES, args.margin)
    print(f"motion threshold {cal.motion_threshold:.4f}, presence threshold {cal.presence_threshold:.4f}\n")

    results = {}
    for test, labels in (("presence", [EMPTY, STILL]), ("motion", [MOVING, STILL])):
        # Random order so the person can't anticipate and we don't just measure drift.
        plan = [labels[i % 2] for i in range(args.trials)]
        random.shuffle(plan)
        correct = 0
        for i, truth in enumerate(plan, 1):
            input(f"[{test} {i}/{len(plan)}] {INSTRUCTIONS[truth]} -> press Enter when ready")
            got, _, max_motion, times, amps = run_trial(frames, cal, args.trial_seconds)
            # For the presence test, MOVING also means "someone is there".
            if test == "presence" and got == MOVING:
                got = STILL
            ok = got == truth
            correct += ok
            print(f"    expected {truth:6s} got {got:6s} {'OK' if ok else 'WRONG'}  (max motion {max_motion:.4f})")
            save(folder, f"{test}_{i:02d}_{truth}", times, amps)
        results[test] = (correct, len(plan))

    input("[crowd test] Person FROZEN in the spot; teammates walk around 2-3 m away for 30 s. Enter to start")
    _, states, max_motion, times, amps = run_trial(frames, cal, 30, confirm=CONFIRM_READINGS)
    false_moves = states.count(MOVING)
    save(folder, "crowd_30s", times, amps)

    print("\n================ RESULTS ================")
    passed = True
    for test, (c, n) in results.items():
        ok = c >= 0.9 * n
        passed &= ok
        print(f"  {test:9s} {c}/{n}  {'PASS' if ok else 'FAIL'}")
    print(f"  crowd     {false_moves} false MOVING readings  {'PASS' if false_moves == 0 else 'FAIL'}")
    passed &= false_moves == 0
    print(f"\n  {'GO' if passed else 'NO-GO'}   (recordings in {folder}/)")
    if not passed:
        print("  Try: --margin 1.5, boards lower/closer, person nearer the TX-RX line, recalibrate.")


if __name__ == "__main__":
    main()
