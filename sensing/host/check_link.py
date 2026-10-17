"""Step 1: is the ESP32 pair working?

Plug the RX board into the laptop, power the TX board (USB charger or a second
laptop port), then run:
    python check_link.py            # auto-detects the port
    python check_link.py --port /dev/cu.usbserial-0001

Reads 10 s of serial output and prints PASS/FAIL with what to fix.
"""

import argparse
import time

import numpy as np

from csi_io import add_port_args, iter_lines, parse_line

EXPECTED_RATE_HZ = 100  # TX sends every 10 ms


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_port_args(parser)
    parser.add_argument("--seconds", type=float, default=10)
    args = parser.parse_args()

    frames, other_lines, bad_csi_lines = [], [], 0
    start = time.monotonic()
    print(f"Listening for {args.seconds:.0f} s ...")
    for t, line in iter_lines(args.port, args.replay):
        if frames and t - frames[0].host_time >= args.seconds:
            break
        if not args.replay and time.monotonic() - start > args.seconds + 2:
            break
        frame = parse_line(line, host_time=t)
        if frame:
            frames.append(frame)
        elif line.startswith("CSI,"):
            bad_csi_lines += 1
        elif line.strip():
            other_lines.append(line.strip())

    boot = [l for l in other_lines if l.startswith("BOOT,")]
    stats = [l for l in other_lines if l.startswith("STAT,")]
    print("\n--- what the board said ---")
    for l in boot + stats[-3:]:
        print("  " + l)
    junk = [l for l in other_lines if not l.startswith(("BOOT,", "STAT,"))]
    if junk:
        print(f"  ({len(junk)} other lines, e.g. {junk[0][:80]!r})")

    print("\n--- verdict ---")
    if not other_lines and not frames:
        print("FAIL: nothing at all on the serial port.")
        print("  - Wrong port? Run `ls /dev/cu.*` with the board unplugged and plugged.")
        print("  - Charge-only USB cable? Try another cable.")
        print("  - Firmware not flashed? `pio run -e rx -t upload` in sensing/firmware.")
        print("  - Press the EN (reset) button on the board.")
        return
    if any(l.startswith("STAT,TX") for l in stats) or any(l.startswith("BOOT,TX") for l in boot):
        print("FAIL: this is the TX board. Plug in the RX board (or flash this one with -e rx).")
        return
    if not frames:
        print("FAIL: RX board is alive but receives no CSI from the TX board.")
        print("  - Is the TX board powered and flashed with `-e tx`? Its blue/red LED should be on.")
        print("  - Both boards must run the same CSI_CHANNEL (csi_main.c).")
        print("  - Move them within 1-2 m with nothing metal between them.")
        if junk:
            print("  - Garbled text usually means wrong baud rate; the firmware uses 921600.")
        return

    duration = frames[-1].host_time - frames[0].host_time
    rate = (len(frames) - 1) / duration if duration > 0 else 0.0
    seqs = np.array([f.seq for f in frames])
    gaps = np.diff(seqs) % 4096  # 802.11 sequence numbers wrap at 4096
    lost = int(np.sum(np.clip(gaps - 1, 0, None)))
    rssi = np.array([f.rssi for f in frames])
    amps = np.array([f.amplitude for f in frames])
    dead = int(np.sum(amps.mean(axis=0) < 1.0))

    print(f"  packets/s     {rate:6.1f}   (expect ~{EXPECTED_RATE_HZ})")
    print(f"  lost packets  {lost:6d}   ({100 * lost / max(1, lost + len(frames)):.1f}%)")
    print(f"  bad lines     {bad_csi_lines:6d}")
    print(f"  RSSI          {rssi.mean():6.1f} dBm (min {rssi.min()}, max {rssi.max()})")
    print(f"  subcarriers   {amps.shape[1] - dead:6d} / {amps.shape[1]} carrying signal")
    print(f"  mean |H|      {amps.mean():6.1f}")

    problems = []
    if rate < 0.7 * EXPECTED_RATE_HZ:
        problems.append("packet rate is low: check STAT drops, USB cable/hub, or Wi-Fi congestion on this channel")
    if rssi.mean() > -25:
        problems.append("RSSI is very high: boards are too close (<30 cm) and the receiver saturates; move them apart")
    if rssi.mean() < -75:
        problems.append("RSSI is weak: move boards closer or remove obstacles/metal near the antennas")
    if dead > 5:
        problems.append(f"{dead} subcarriers are flat zero: TX may be sending 802.11b (check rate config)")
    if bad_csi_lines > 0.05 * len(frames):
        problems.append("many corrupted lines: serial can't keep up; try a different USB port/cable")

    if problems:
        print("WARN: link works but:")
        for p in problems:
            print("  - " + p)
    else:
        print("PASS: ESP32 pair is streaming CSI. Next: python live_motion.py")


if __name__ == "__main__":
    main()
