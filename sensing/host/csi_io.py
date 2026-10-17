"""Read CSI lines from the RX ESP32 (or from a recorded log file) and parse them.

Line formats are documented at the top of sensing/firmware/main/csi_main.c.
"""

from __future__ import annotations

import glob
import time
from dataclasses import dataclass
from typing import Iterator

import numpy as np

# Classic ESP32 boards (CH340/CP2102 USB chip) print at 460800; the ESP32-S3's
# built-in USB ignores the baud rate.
BAUD = 460800

# The ESP32 / ESP32-S3 LLTF buffer holds 64 subcarriers in FFT order:
# index 0..31 = subcarriers 0..+31, index 32..63 = subcarriers -32..-1.
# Only -26..-1 and +1..+26 carry signal; the DC carrier (0) and the guard band
# edges are always ~0, so we drop them. We also skip +1 because the first
# 4 bytes of the buffer are sometimes invalid (hardware quirk), which covers
# subcarriers 0 and +1.
VALID_SUBCARRIERS = np.r_[2:27, 38:64]  # 25 + 26 = 51 subcarriers


@dataclass
class CsiFrame:
    host_time: float  # time.monotonic() when the line was read (seconds)
    seq: int  # 802.11 sequence number, wraps at 4096
    rssi: int  # dBm
    noise_floor: int  # dBm
    esp_time_us: int  # ESP32 receive timestamp, wraps at 2^32 us (~71 min)
    csi: np.ndarray  # complex64, one value per subcarrier (all 64)

    @property
    def amplitude(self) -> np.ndarray:
        """|H| for the 51 subcarriers that carry signal."""
        return np.abs(self.csi[VALID_SUBCARRIERS]).astype(np.float32)

    @property
    def signal(self) -> np.ndarray:
        """|H| with the receiver's automatic scaling undone, using RSSI.

        The ESP32 rescales CSI so |H| stays around 13 however strong the packet
        is, so a hand dimming the whole link only shows up in RSSI. Multiplying
        by the RSSI as a linear amplitude puts that dimming back. Measured on a
        real recording: hand waving went from 1.6x to 3.6x the empty-room score.
        """
        return self.amplitude * np.float32(10 ** (self.rssi / 20))


def parse_line(line: str, host_time: float | None = None) -> CsiFrame | None:
    """Parse one `CSI,...` line. Returns None for any other or corrupted line."""
    line = line.strip()
    if not line.startswith("CSI,"):
        return None
    parts = line.split(",")
    if len(parts) != 7:
        return None
    try:
        seq, rssi, noise, ts, length = (int(p) for p in parts[1:6])
        raw = bytes.fromhex(parts[6])
    except ValueError:
        return None
    if len(raw) != length or length < 128:
        return None  # truncated line (serial hiccup)

    # Bytes are signed int8 pairs: (imaginary, real) per subcarrier.
    iq = np.frombuffer(raw[:128], dtype=np.int8).astype(np.float32).reshape(64, 2)
    csi = (iq[:, 1] + 1j * iq[:, 0]).astype(np.complex64)
    return CsiFrame(
        host_time=time.monotonic() if host_time is None else host_time,
        seq=seq,
        rssi=rssi,
        noise_floor=noise,
        esp_time_us=ts,
        csi=csi,
    )


def find_serial_ports() -> list[str]:
    """USB-serial ports that look like an ESP32 dev board on macOS/Linux."""
    patterns = [
        "/dev/cu.usbserial*",  # CP2102 / CH340 on macOS
        "/dev/cu.SLAB_USBtoUART*",  # CP2102 with the old Silicon Labs driver
        "/dev/cu.wchusbserial*",  # CH340 with the WCH driver
        "/dev/cu.usbmodem*",  # boards with native USB (ESP32-S3)
        "/dev/ttyUSB*",
        "/dev/ttyACM*",
    ]
    ports: list[str] = []
    for pattern in patterns:
        ports.extend(sorted(glob.glob(pattern)))
    return ports


def _sends_csi(port: str, seconds: float = 2.5) -> bool:
    """True if `port` is streaming CSI lines, i.e. it is the RX board."""
    import serial

    try:
        with serial.Serial(port, BAUD, timeout=0.5) as ser:
            ser.reset_input_buffer()
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                if ser.readline().startswith(b"CSI,"):
                    return True
    except (serial.SerialException, OSError):
        pass
    return False


def resolve_port(port: str | None) -> str:
    if port:
        return port
    ports = find_serial_ports()
    if not ports:
        raise SystemExit(
            "No ESP32 serial port found. Plug the RX board in with a DATA USB cable "
            "(charge-only cables are common) and run `ls /dev/cu.*`."
        )
    if len(ports) == 1:
        return ports[0]
    # TX and RX can both be on the laptop's USB; only RX prints CSI.
    for p in ports:
        if _sends_csi(p):
            print(f"Using RX board on {p}")
            return p
    raise SystemExit(f"None of {ports} is sending CSI. Is the RX board flashed and the TX board powered?")


def iter_lines(port: str | None = None, replay: str | None = None) -> Iterator[tuple[float, str]]:
    """Yield (host_time, line) from the serial port, or from a recorded file.

    Recorded files (from record.py) store `host_time<TAB>line` so replays keep
    the original timing.
    """
    if replay:
        with open(replay) as f:
            for row in f:
                if row.startswith("#") or "\t" not in row:
                    continue
                t, line = row.rstrip("\n").split("\t", 1)
                yield float(t), line
        return

    import serial  # pyserial; imported here so replay/tests don't need it

    with serial.Serial(resolve_port(port), BAUD, timeout=1) as ser:
        ser.reset_input_buffer()
        while True:
            # readline() returns b"" after the 1 s timeout; we still yield it so
            # callers can notice "no data" and stop waiting.
            raw = ser.readline()
            yield time.monotonic(), raw.decode("ascii", errors="replace")


NO_DATA_TIMEOUT_S = 5.0


def iter_frames(port: str | None = None, replay: str | None = None) -> Iterator[CsiFrame]:
    """Yield CSI frames. Stops with an error if a live port sends none for 5 s
    (wrong port, TX unpowered, or RX unplugged) instead of waiting forever."""
    last_frame = time.monotonic()
    for t, line in iter_lines(port, replay):
        frame = parse_line(line, host_time=t)
        if frame is not None:
            last_frame = time.monotonic()
            yield frame
        elif not replay and time.monotonic() - last_frame > NO_DATA_TIMEOUT_S:
            raise SystemExit(
                f"No CSI received for {NO_DATA_TIMEOUT_S:.0f} s. Check: RX (ESP32-S3, "
                "/dev/cu.usbmodem*) is plugged into the laptop, TX (WROOM) has power, "
                "and run check_link.py. Use --port to pick the RX port."
            )


def add_port_args(parser) -> None:
    parser.add_argument("--port", help="serial port of the RX board (default: auto-detect)")
    parser.add_argument("--replay", help="read a file saved by record.py instead of the board")


def collect(frames: Iterator[CsiFrame], seconds: float) -> tuple[np.ndarray, np.ndarray]:
    """Read frames for `seconds`; return (host_times, signal frames x subcarriers).
    See CsiFrame.signal: amplitudes with the receiver's auto-scaling undone."""
    times, amps = [], []
    start = None
    for frame in frames:
        if start is None:
            start = frame.host_time
        if frame.host_time - start >= seconds:
            break
        times.append(frame.host_time)
        amps.append(frame.signal)
    if not amps:
        raise SystemExit("No CSI frames received. Run check_link.py first.")
    return np.array(times), np.array(amps)
