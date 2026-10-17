"""Step 2: live motion meter (the core of Red Light, Green Light).

    python live_motion.py
    python live_motion.py --margin 1.5     # fewer false "MOVING", less sensitive

1. "Scanning the room": keep the hiding spot EMPTY and the area still for
   the calibration time.
2. Then a live bar shows the motion score and the debounced state:
   EMPTY / STILL (someone there, frozen) / MOVING.
"""

import argparse

from csi_io import add_port_args, collect, iter_frames
from signal_processing import (
    CONFIRM_READINGS,
    HOP_FRAMES,
    MOVING,
    STILL,
    WINDOW_FRAMES,
    LiveDetector,
    calibrate,
)

COLORS = {MOVING: "\033[91m", STILL: "\033[93m"}  # red, yellow; EMPTY uses default
RESET = "\033[0m"


def bar(value: float, threshold: float, width: int = 40) -> str:
    """Bar scaled so the threshold sits at the middle ('|')."""
    filled = min(width, int(width / 2 * value / max(threshold, 1e-9)))
    cells = ["#" if i < filled else " " for i in range(width)]
    cells[width // 2] = "|"
    return "".join(cells)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_port_args(parser)
    parser.add_argument("--calibrate-seconds", type=float, default=15)
    parser.add_argument("--margin", type=float, default=1.0, help="motion size threshold = 95th pct of empty-room score x margin")
    args = parser.parse_args()

    frames = iter_frames(args.port, args.replay)
    input("Scanning the room: make sure the hiding spot is EMPTY, then press Enter...")
    print(f"Calibrating for {args.calibrate_seconds:.0f} s, stay out of the link...")
    collect(frames, 1)  # drain data buffered while waiting for Enter
    times, amps = collect(frames, args.calibrate_seconds)
    rate = (len(times) - 1) / (times[-1] - times[0])
    cal = calibrate(amps, WINDOW_FRAMES, args.margin)
    print(
        f"Done: {len(times)} frames ({rate:.0f}/s). "
        f"motion threshold {cal.motion_threshold:.4f} (empty median {cal.motion_baseline:.4f}), "
        f"presence threshold {cal.presence_threshold:.4f}"
    )
    print("Bar: '|' is the motion threshold. Ctrl+C to stop.\n")

    detector = LiveDetector(cal, WINDOW_FRAMES, HOP_FRAMES, CONFIRM_READINGS)
    try:
        for frame in frames:
            reading = detector.push(frame.signal)
            if reading is None:
                continue
            state, motion, presence = reading
            color = COLORS.get(state, "")
            print(
                f"\r{color}{state:7s}{RESET} motion [{bar(motion, cal.motion_threshold)}] {motion:.4f}"
                f"  smooth {detector.last_smoothness:4.2f}"
                f"  presence {presence / cal.presence_threshold:4.1f}x  rssi {frame.rssi}  ",
                end="",
                flush=True,
            )
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
