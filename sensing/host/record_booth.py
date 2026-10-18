"""Record ML training data in the real booth: the laptop speaks, you follow.

    python record_booth.py data/booth_01.log --position "standing, TX 1m behind" --person arwa
    python record_booth.py data/booth_02.log --position "crouching, TX 1m behind" --person arwa

No camera (it can't see behind the curtain): the labels come from the script
the laptop reads out. Set up the booth first (receivers on the laptop side,
you on your X behind the curtain, transmitter ~1 m behind you), then run this
with the WiFind server STOPPED (the recorder needs the boards).

The script (~4 min), labels in quotes:
     12 s  "Walk at least three meters away"      "step_away" (not scored)
     20 s  empty room                             "empty"     (also the baseline every
                                                               feature is measured against)
     12 s  "Come back and stand on your X"        "step_in"   (not scored)
    then --rounds rounds, each a shuffled mix of:
     15 s  "Freeze."                              "frozen"    -> STILL
      8 s  "Move! Step in place, wave both arms"  "moving"    -> MOVING
      8 s  "Small moves. Shift your weight..."    "slow_move" -> MOVING (red light catches these too)
    each starts with 1.5 s "settle" (reaction time, not scored) and ends with 3 s "settle"
      8 s  "Step out of the space"                "step_away"
     15 s  empty room again                       "empty_again"

Labels are the ones features.LABEL_TO_CLASS already knows, so the usual tools work:
    python train_model.py --fuse data/booth_*_rx*.log --out models/wifind_booth.json
    python evaluate.py data/booth_*_rx*.log --out data/booth_report.md
Record at least 2 sessions (3+ is better: vary standing / crouching / another person),
because every number is measured on a session the model never trained on.
"""

from __future__ import annotations

import argparse
import random

from record_zones import record_script

REACT_S = 1.5  # after each spoken cue: the person is still starting/stopping
SEGMENTS = {
    "frozen": (15, "Freeze."),
    "moving": (8, "Move! Step in place and wave both arms."),
    "slow_move": (8, "Small moves. Slowly shift your weight or turn your head."),
}


def schedule(rounds: int, seed: int = 0) -> list[tuple[float, float, str, str]]:
    """(start, end, label, spoken prompt) for every segment, seconds from the start.
    Freeze/move order is shuffled each round, so 'what comes next' can't be learned."""
    rng = random.Random(seed)
    plan, t = [], 0.0

    def add(dur: float, label: str, prompt: str = ""):
        nonlocal t
        plan.append((t, t + dur, label, prompt))
        t += dur

    # transitions get labels the trainer ignores ("walk" would mean MOVING to features.py)
    add(12, "step_away", "Please walk at least three meters away from the boards, and stay there until I call you back.")
    add(20, "empty", "Listening to the empty room.")
    add(12, "step_in", "Come back and stand on your X behind the curtain.")
    for _ in range(rounds):
        kinds = list(SEGMENTS)
        rng.shuffle(kinds)
        for kind in kinds:
            dur, prompt = SEGMENTS[kind]
            add(REACT_S, "settle", prompt)  # the cue is spoken here; the labelled part starts after
            add(dur, kind)
            add(3, "settle")
    add(8, "step_away", "Now step out of the space.")
    add(15, "empty_again", "Stay out for fifteen more seconds.")
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output")
    parser.add_argument("--rounds", type=int, default=4)
    parser.add_argument("--person", default="unknown")
    parser.add_argument("--position", default="unknown", help='e.g. "standing, TX 1m behind"')
    parser.add_argument("--ports", nargs="*")
    parser.add_argument("--seed", type=int, default=None, help="segment order (default: random)")
    args = parser.parse_args()

    seed = args.seed if args.seed is not None else random.randrange(10**6)
    plan = schedule(args.rounds, seed)
    record_script(plan, args.output, f"record_booth rounds={args.rounds} seed={seed} "
                  f"person={args.person.replace(' ', '_')} position={args.position.replace(' ', '_')}", args.ports)


if __name__ == "__main__":
    main()
