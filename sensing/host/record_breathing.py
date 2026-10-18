"""Record Wi-Fi while the laptop paces your breathing, so the true rate is known.

    python record_breathing.py data/breath_01.log --person arwa --position "sitting 1m"

No breathing sensor needed: the laptop says "in" / "out" at a fixed pace and
you breathe along, so each segment's true rate is exactly the pace. Script:
    45 s  empty room (step away)      label "empty"   -> what "no breathing" looks like
    10 s  get in place, sit still     label "settle"  (not scored)
    then for each --rates (default 10, 15, 20 breaths/min):
    50 s  breathe with the voice      label "breath_<rate>"
    10 s  rest, breathe normally      label "rest"    (not scored)
    then 30 s empty room again        label "empty_end" (more "no breathing" for the model)
All RX boards are recorded at once, one log per board (<output>_rx<mac>.log),
in the same format as record_labeled.py. Then: python eval_breathing.py data/breath_*_rx*.log
"""

from __future__ import annotations

import argparse
import os
import subprocess
import threading
import time

from csi_io import iter_lines

EMPTY_S, SETTLE_S, BREATH_S, REST_S, END_EMPTY_S = 45, 10, 50, 10, 30


def say(text: str, rate: int = 200) -> None:
    subprocess.Popen(["say", "-r", str(rate), text])


def schedule(rates: list[int], natural_min: float = 0.0) -> list[tuple[float, float, str]]:
    """(start, end, label) for every segment, in seconds from the start.
    natural_min > 0: one long "breathe normally" segment instead of paced ones
    (the true rate then comes from a phone on the belly, phone_truth.py)."""
    plan, t = [], 0.0
    for dur, label in [(EMPTY_S, "empty"), (SETTLE_S, "settle")]:
        plan.append((t, t + dur, label))
        t += dur
    if natural_min > 0:
        plan.append((t, t + natural_min * 60, "breath_natural"))
        t += natural_min * 60
        rates = []
    for r in rates:
        plan.append((t, t + BREATH_S, f"breath_{r}"))
        t += BREATH_S
        plan.append((t, t + REST_S, "rest"))
        t += REST_S
    plan.append((t, t + END_EMPTY_S, "empty_end"))
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output")
    parser.add_argument("--ports", nargs="*")
    parser.add_argument("--rates", nargs="*", type=int, default=[10, 15, 20], help="breaths per minute to pace")
    parser.add_argument("--person", default="unknown")
    parser.add_argument("--position", default="unknown")
    parser.add_argument("--natural", type=float, default=0.0, metavar="MINUTES",
                        help="breathe normally for this long instead of paced (wear the phone: phone_truth.py)")
    args = parser.parse_args()

    from multi_rx import find_rx_ports

    ports = args.ports or find_rx_ports()
    if not ports:
        raise SystemExit("No RX boards streaming CSI. Is the WiFind server still running? Stop it first.")
    plan = schedule(args.rates, args.natural)
    clock = time.time() - time.monotonic()  # lines are stamped with uptime; this maps them to clock time
    total = plan[-1][1]
    print(f"Recording {len(ports)} receiver(s) for {total / 60:.1f} min: {ports}")

    label = {"now": "settle"}
    rows: dict[str, list[str]] = {p: [] for p in ports}
    macs: dict[str, str] = {}
    stop = threading.Event()

    def read(port: str):
        while not stop.is_set():  # reopen if a board drops, like the recorder/server
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
        for seg_start, seg_end, seg in plan:
            while time.monotonic() - start < seg_start:
                time.sleep(0.02)
            label["now"] = seg
            print(f"{seg_start:5.0f}s  {seg}", flush=True)
            if seg == "empty":
                say("Walk at least three meters away from the boards and stay there for forty five seconds.")
            elif seg == "empty_end":
                say("Now walk at least three meters away again, for thirty seconds.")
            elif seg == "settle":
                say("Come back to your spot behind the curtain and stay still. "
                    + ("Just breathe normally." if args.natural else "Breathe with my voice."))
            elif seg == "breath_natural":
                say("Breathe normally until I call you.")
            elif seg == "rest":
                say("Rest. Breathe normally, stay still.")
            else:
                rate = int(seg.split("_")[1])
                say(f"{rate} breaths a minute. Ready.")
                period = 60.0 / rate
                # cue "in" at the start of each breath and "out" halfway
                t = seg_start + 2.0
                while t + period <= seg_end:
                    for cue, at in (("in", t), ("out", t + period / 2)):
                        while time.monotonic() - start < at:
                            time.sleep(0.01)
                        say(cue, 180)
                    t += period
        while time.monotonic() - start < total:
            time.sleep(0.05)
    finally:
        stop.set()
    say("Done. Thank you.")

    base, ext = os.path.splitext(args.output)
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    saved = []
    for port in ports:
        mac = macs.get(port, "")
        out = args.output if len(ports) == 1 else f"{base}_rx{mac.replace(':', '')[-4:] or os.path.basename(port)}{ext}"
        with open(out, "w") as f:
            f.write(f"# record_breathing {time.strftime('%Y-%m-%d %H:%M:%S')} rates={args.rates} "
                    f"natural={args.natural} clock={clock:.3f} port={port} "
                    f"mac={mac} person={args.person.replace(' ', '_')} position={args.position.replace(' ', '_')}\n")
            f.write("\n".join(rows[port]) + "\n")
        print(f"saved {len(rows[port])} lines to {out}")
        saved.append(out)
    if len(saved) > 1:  # the files are safe on disk; now the database copy
        from tiger_upload import upload

        upload(saved)


if __name__ == "__main__":
    main()
