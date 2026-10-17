"""Step 3: breathing rate (and an EXPERIMENTAL heart-rate attempt).

    python breathing.py                  # breaths per minute
    python breathing.py --band heart     # experimental, see below

The person sits still in the link (see README for placement). Every 2 s we
look at the last --window seconds, band-pass filter each subcarrier, and find
the strongest periodic component with an FFT.

Check it honestly: count your own breaths for 30 s and compare.

Heart rate: the chest moves ~0.1-0.5 mm per heartbeat vs ~5 mm per breath.
Single-antenna ESP32 CSI is generally too noisy for that (CLAUDE.md lists Wi-Fi
heart rate as unreliable/cut). The heart mode is here so we can *measure* that
claim against a MAX30102 fingertip sensor or a phone pulse app, not to demo it.
Low confidence numbers mean "no real peak, ignore the reading".
Big body movements also create strong slow rhythms, so only trust readings
while live_motion.py would say STILL.
"""

import argparse
from collections import deque

import numpy as np

from csi_io import add_port_args, collect, iter_frames
from signal_processing import BREATHING_BAND, HEART_BAND, estimate_rate

MIN_CONFIDENCE = 0.75  # share of in-band power at the peak (see estimate_rate).
# Synthetic tests: a real rhythm scores ~0.95, pure noise up to ~0.65.


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_port_args(parser)
    parser.add_argument("--band", choices=["breathing", "heart"], default="breathing")
    parser.add_argument("--window", type=float, default=None, help="seconds of history (default 30 / 15)")
    args = parser.parse_args()

    band = BREATHING_BAND if args.band == "breathing" else HEART_BAND
    window_s = args.window or (30.0 if args.band == "breathing" else 15.0)
    unit = "breaths/min" if args.band == "breathing" else "beats/min (EXPERIMENTAL)"

    frames = iter_frames(args.port, args.replay)
    print(f"Sit still in the link. Filling a {window_s:.0f} s window...")
    times, amps = collect(frames, window_s)
    buf_t, buf_a = deque(times), deque(amps)

    next_report = buf_t[-1] + 2.0
    try:
        for frame in frames:
            buf_t.append(frame.host_time)
            buf_a.append(frame.signal)
            while buf_t[-1] - buf_t[0] > window_s:
                buf_t.popleft()
                buf_a.popleft()
            if frame.host_time < next_report:
                continue
            next_report = frame.host_time + 2.0
            est = estimate_rate(np.array(buf_t), np.array(buf_a), band)
            if est.confidence >= MIN_CONFIDENCE:
                print(f"{est.per_minute:5.1f} {unit}   confidence {est.confidence:.2f}")
            else:
                print(f"  --  no clear rhythm (confidence {est.confidence:.2f}); moving, or not in the link?")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
