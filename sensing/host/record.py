"""Save raw serial output to a file so we can replay/tune offline.

    python record.py empty_corner.log --seconds 60 --note "venue table, nobody"
    python live_motion.py --replay empty_corner.log

Format: `host_time<TAB>raw line`, header lines start with '#'.
"""

import argparse
import time

from csi_io import iter_lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output")
    parser.add_argument("--port")
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--note", default="", help="free text saved in the header (label, setup)")
    args = parser.parse_args()

    n = 0
    with open(args.output, "w") as f:
        f.write(f"# recorded {time.strftime('%Y-%m-%d %H:%M:%S')} note={args.note}\n")
        start = None
        for t, line in iter_lines(args.port):
            start = start or t
            if t - start >= args.seconds:
                break
            f.write(f"{t:.4f}\t{line.rstrip()}\n")
            n += line.startswith("CSI,")
    print(f"saved {n} CSI frames to {args.output}")


if __name__ == "__main__":
    main()
