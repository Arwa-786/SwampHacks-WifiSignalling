"""Record the zone experiment: does movement in zone A look different from
movement in zone B on the three receivers?

    python record_zones.py data/zones_01.log --mode pair --zones "A=left of RX1, B=right of RX3"
    python record_zones.py data/zones_01.log --mode solo     # one person walks between zones

Hardware stays as it is (1 TX, 3 RX). Mark two spots on the floor with tape,
zone A and zone B, about 1.5-2 m apart, both inside the sensing area. The laptop
speaks every instruction and labels the recording with what it asked for, so
no camera is needed (it couldn't see behind the curtain anyway).

pair mode (two people, the real game):
    20 s  empty room                              label "empty"   (the baseline)
     8 s  both go to your zones and freeze        label "settle"  (not scored)
    then --rounds rounds, each with the 4 conditions in a shuffled order:
     8 s  "Zone A move, zone B freeze"            label "a_moves@<round>"
     8 s  "Zone B move, zone A freeze"            label "b_moves@<round>"
     8 s  "Both move"                             label "both_move@<round>"
     8 s  "Both freeze"                           label "both_freeze@<round>"
    each followed by 4 s of "freeze"              label "rest"    (not scored)
solo mode (one person): conditions a_moves, b_moves, a_freeze, b_freeze; a
"walk to zone X" break before each one (label "walk"). It answers the same
question without the second, still person in the room, so treat a solo pass
as promising, not proof.

Record pair mode twice: the second time with the players swapped between zones
(--swapped). Otherwise a model could learn each person's way of moving instead
of where they are, and eval_zones.py says so.

The round number in the label lets eval_zones.py hold out a whole round at a
time. All RX boards are recorded at once, one log per board (<output>_rx<mac>.log),
in the same format as record_labeled.py. Then: python eval_zones.py data/zones_*_rx*.log
"""

from __future__ import annotations

import argparse
import os
import random
import threading
import time

from csi_io import iter_lines
from record_breathing import say

EMPTY_S, SETTLE_S, COND_S, REST_S, WALK_S = 20, 8, 8, 4, 5

PROMPTS = {
    "a_moves": "Zone A, move. Zone B, freeze.",
    "b_moves": "Zone B, move. Zone A, freeze.",
    "both_move": "Both of you, move.",
    "both_freeze": "Both of you, freeze.",
    "a_freeze": "Freeze in zone A.",
    "b_freeze": "Freeze in zone B.",
}
SOLO_MOVE = {"a_moves": "Move.", "b_moves": "Move.", "a_freeze": "Freeze.", "b_freeze": "Freeze."}
CONDITIONS = {"pair": ["a_moves", "b_moves", "both_move", "both_freeze"],
              "solo": ["a_moves", "b_moves", "a_freeze", "b_freeze"]}


def schedule(mode: str, rounds: int, seed: int = 0) -> list[tuple[float, float, str, str]]:
    """(start, end, label, spoken prompt) for every segment, seconds from the start.
    Conditions are shuffled within each round so order (getting tired, drifting
    position) can't masquerade as a zone difference."""
    rng = random.Random(seed)
    plan, t = [], 0.0

    def add(dur: float, label: str, prompt: str):
        nonlocal t
        plan.append((t, t + dur, label, prompt))
        t += dur

    add(EMPTY_S, "empty", "Everyone step away from the boards for twenty seconds.")
    add(SETTLE_S, "settle", "Go to your zones and freeze." if mode == "pair" else "Go to zone A and freeze.")
    for r in range(1, rounds + 1):
        conds = CONDITIONS[mode][:]
        rng.shuffle(conds)
        for c in conds:
            if mode == "solo":
                add(WALK_S, "walk", f"Walk to zone {c[0].upper()}.")
                add(COND_S, f"{c}@{r}", SOLO_MOVE[c])
            else:
                add(COND_S, f"{c}@{r}", PROMPTS[c])
                add(REST_S, "rest", "Freeze.")
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output")
    parser.add_argument("--mode", choices=["pair", "solo"], default="pair")
    parser.add_argument("--rounds", type=int, default=6)
    parser.add_argument("--zones", default="unknown", help='where the zones are, e.g. "A=left of RX1, B=right of RX3"')
    parser.add_argument("--motion", default="normal", help='what "move" meant, e.g. "small shift", "arm wave"')
    parser.add_argument("--swapped", action="store_true", help="the players swapped zones vs the first session")
    parser.add_argument("--ports", nargs="*")
    parser.add_argument("--seed", type=int, default=None, help="condition order (default: random)")
    args = parser.parse_args()

    from multi_rx import find_rx_ports

    ports = args.ports or find_rx_ports()
    if not ports:
        raise SystemExit("No RX boards streaming CSI. Is the WiFind server still running? Stop it first.")
    seed = args.seed if args.seed is not None else random.randrange(10**6)
    plan = schedule(args.mode, args.rounds, seed)
    total = plan[-1][1]
    print(f"Recording {len(ports)} receiver(s) for {total / 60:.1f} min ({args.mode}, {args.rounds} rounds): {ports}")

    label = {"now": "settle"}
    rows: dict[str, list[str]] = {p: [] for p in ports}
    macs: dict[str, str] = {}
    stop = threading.Event()

    def read(port: str):
        while not stop.is_set():  # reopen if a board drops, like the other recorders
            try:
                for t, line in iter_lines(port):
                    if stop.is_set():
                        return
                    if line.startswith("STAT,") and port not in macs and len(line.split(",")) > 4:
                        macs[port] = line.strip().split(",")[4]
                    if line.startswith("CSI,"):
                        rows[port].append(f"{t:.4f}\t{line.rstrip()}\t{label['now']}")
            except Exception as e:
                print(f"[{port}] lost connection ({e.__class__.__name__}); retrying", flush=True)
                time.sleep(1)

    for p in ports:
        threading.Thread(target=read, args=(p,), daemon=True).start()

    start = time.monotonic()
    try:
        for seg_start, _, seg, prompt in plan:
            while time.monotonic() - start < seg_start:
                time.sleep(0.02)
            label["now"] = seg
            print(f"{seg_start:5.0f}s  {seg:16s} {prompt}", flush=True)
            say(prompt)
        while time.monotonic() - start < total:
            time.sleep(0.05)
    finally:
        stop.set()
    say("Done. Thank you.")

    base, ext = os.path.splitext(args.output)
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    for port in ports:
        mac = macs.get(port, "")
        out = args.output if len(ports) == 1 else f"{base}_rx{mac.replace(':', '')[-4:] or os.path.basename(port)}{ext}"
        with open(out, "w") as f:
            f.write(f"# record_zones {time.strftime('%Y-%m-%d %H:%M:%S')} mode={args.mode} rounds={args.rounds} "
                    f"seed={seed} swapped={int(args.swapped)} port={port} mac={mac} zones={args.zones.replace(' ', '_')} "
                    f"motion={args.motion.replace(' ', '_')}\n")
            f.write("\n".join(rows[port]) + "\n")
        print(f"saved {len(rows[port])} lines to {out}")


if __name__ == "__main__":
    main()
