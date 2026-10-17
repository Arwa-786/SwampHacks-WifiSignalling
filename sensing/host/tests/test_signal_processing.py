"""Tests with synthetic CSI so the logic can be checked without hardware.

The fake channel: a fixed amplitude profile across 51 subcarriers, small
random noise, and a small random per-frame gain error (what is left of the
ESP32's automatic scaling after CsiFrame.signal undoes it with the RSSI).
A "person" changes the profile; "motion" makes each subcarrier wobble
differently; "breathing" is a slow sinusoid on some subcarriers.
"""

import numpy as np
import pytest

from breathing import MIN_CONFIDENCE
from csi_io import VALID_SUBCARRIERS, iter_frames, parse_line
from signal_processing import (
    EMPTY,
    MOVING,
    STILL,
    Debouncer,
    LiveDetector,
    calibrate,
    estimate_rate,
    motion_score,
    normalize_gain,
)

FS = 100.0
N_SUB = len(VALID_SUBCARRIERS)
rng = np.random.default_rng(0)
BASE = rng.uniform(10, 30, N_SUB)
PERSON = BASE * rng.uniform(0.8, 1.2, N_SUB)  # profile with a body in the link


def fake(seconds, profile=BASE, motion=0.0, breath_hz=None, breath_depth=0.0, gain_jitter=0.02, seed=None):
    rng = np.random.default_rng(seed) if seed is not None else globals()["rng"]
    n = int(seconds * FS)
    t = np.arange(n) / FS
    amps = np.tile(profile, (n, 1)) * (1 + rng.normal(0, 0.01, (n, N_SUB)))
    if motion:
        phase = rng.uniform(0, 2 * np.pi, N_SUB)
        freq = rng.uniform(1, 3, N_SUB)
        amps *= 1 + motion * np.sin(2 * np.pi * freq * t[:, None] + phase)
    if breath_hz:
        affected = rng.random(N_SUB) < 0.4
        amps[:, affected] *= 1 + breath_depth * np.sin(2 * np.pi * breath_hz * t)[:, None]
    gain = 1 + rng.uniform(-gain_jitter, gain_jitter, (n, 1))
    return t, amps * gain


# ------------------------------------------------------------------ parsing


def make_line(seq=7, rssi=-40, values=None):
    iq = np.zeros(128, dtype=np.int8) if values is None else values
    return f"CSI,{seq},{rssi},-95,123456,128,{iq.tobytes().hex()}"


def test_parse_line_decodes_imag_real_pairs():
    iq = np.zeros(128, dtype=np.int8)
    iq[2 * 5] = 4  # subcarrier index 5, imaginary
    iq[2 * 5 + 1] = -3  # subcarrier index 5, real
    frame = parse_line(make_line(values=iq), host_time=1.0)
    assert frame.seq == 7 and frame.rssi == -40 and frame.esp_time_us == 123456
    assert frame.csi[5] == pytest.approx(-3 + 4j)
    assert frame.amplitude[list(VALID_SUBCARRIERS).index(5)] == pytest.approx(5.0)
    assert len(frame.amplitude) == 51


@pytest.mark.parametrize(
    "line",
    ["", "STAT,RX,100,0", "BOOT,RX,aa:bb:cc:dd:ee:ff,11", "CSI,1,2,3", "CSI,1,-40,-95,5,128,zz", "CSI,1,-40,-95,5,128,00ff"],
)
def test_parse_line_rejects_other_or_truncated_lines(line):
    assert parse_line(line) is None


def test_replay_file_round_trip(tmp_path):
    path = tmp_path / "rec.log"
    path.write_text("# header\n1.00\tBOOT,RX,x,11\n1.01\t" + make_line(seq=1) + "\n1.02\t" + make_line(seq=2) + "\n")
    frames = list(iter_frames(replay=str(path)))
    assert [f.seq for f in frames] == [1, 2]
    assert frames[1].host_time == pytest.approx(1.02)


# ------------------------------------------------------------------- motion


def test_gain_normalization_cancels_agc_jumps():
    _, amps = fake(2, gain_jitter=0.3)
    raw = motion_score(amps / amps.mean())
    assert motion_score(normalize_gain(amps)) < raw / 5


def test_motion_scores_much_higher_than_still():
    _, still = fake(1)
    _, moving = fake(1, motion=0.2)
    assert motion_score(normalize_gain(moving)) > 5 * motion_score(normalize_gain(still))


def test_whole_link_dimming_counts_as_motion():
    # On real boards a hand in the direct path mostly dims every subcarrier
    # together. The first detector divided that away and missed the hand.
    t, still = fake(1, seed=1)
    wave = still * (1 + 0.1 * np.sin(2 * np.pi * 1.5 * t))[:, None]
    assert motion_score(wave) > 3 * motion_score(still)


def test_random_noise_burst_is_not_motion():
    # A burst of big but random (packet-to-packet independent) noise is not a
    # body moving: the smoothness test must reject it even though it is large.
    _, empty = fake(15, seed=2)
    cal = calibrate(empty, window_frames=50)
    _, burst = fake(3, gain_jitter=0.02, seed=3)
    burst = burst * (1 + np.random.default_rng(4).normal(0, 0.05, burst.shape))
    assert MOVING not in run_detector(cal, burst)


def run_detector(cal, amps, confirm=3):
    det = LiveDetector(cal, window_frames=50, hop_frames=20, confirm=confirm)
    return [r[0] for r in (det.push(a) for a in amps) if r]


def test_live_detector_states():
    _, empty = fake(15)
    cal = calibrate(empty, window_frames=50)
    assert set(run_detector(cal, fake(3)[1])) == {EMPTY}
    assert run_detector(cal, fake(3, profile=PERSON)[1])[-1] == STILL
    assert run_detector(cal, fake(3, profile=PERSON, motion=0.2)[1])[-1] == MOVING


def test_calibrate_needs_enough_data():
    with pytest.raises(ValueError):
        calibrate(fake(0.5)[1], window_frames=50)


def test_debouncer_needs_three_agreeing_readings():
    d = Debouncer(needed=3)
    assert [d.update(s) for s in [MOVING, MOVING, EMPTY, MOVING, MOVING, MOVING]] == [EMPTY] * 5 + [MOVING]


def test_debouncer_needed_one_switches_immediately():
    d = Debouncer(needed=1)
    assert d.update(MOVING) == MOVING
    assert d.update(STILL) == STILL


# ---------------------------------------------------------------- breathing


@pytest.mark.parametrize("bpm", [10, 15, 24])
def test_breathing_rate_found(bpm):
    t, amps = fake(30, profile=PERSON, breath_hz=bpm / 60, breath_depth=0.03)
    # Real packets arrive with jitter; drop 10% at random to exercise resampling.
    keep = rng.random(len(t)) > 0.1
    est = estimate_rate(t[keep], amps[keep])
    assert est.per_minute == pytest.approx(bpm, abs=1.5)
    assert est.confidence > 0.6


@pytest.mark.parametrize("seed", range(5))
def test_no_breathing_gives_low_confidence(seed):
    t, amps = fake(30, profile=PERSON, seed=100 + seed)
    assert estimate_rate(t, amps).confidence < MIN_CONFIDENCE


def test_fuse_needs_half_the_receivers_for_moving():
    from multi_rx import fuse

    assert fuse([MOVING, EMPTY, EMPTY]) == STILL  # one glitchy link is outvoted
    assert fuse([MOVING, MOVING, EMPTY]) == MOVING
    assert fuse([MOVING, EMPTY]) == MOVING
    assert fuse([EMPTY, EMPTY, EMPTY]) == EMPTY
    assert fuse([STILL, EMPTY, EMPTY]) == STILL
    assert fuse([]) == EMPTY


def test_room_scan_survives_a_restart(tmp_path):
    from features import Baseline
    from multi_rx import MultiReceiver
    from signal_processing import Calibration, LiveDetector

    path = str(tmp_path / "scan.json")
    a = MultiReceiver(["/dev/fake0"], scan_path=path)
    rx = a.receivers[0]
    rx.mac = "aa:bb:cc:dd:ee:ff"
    rx.detector = LiveDetector(Calibration(np.ones(4), 0.5, 0.2, 0.1), 50, 10, 3)
    rx.ml.baseline = Baseline(np.ones(4), -60.0, np.arange(8.0))
    a.scan_time = 123.0
    a._save()

    b = MultiReceiver(["/dev/fake0"], scan_path=path)  # "restart"
    rx2 = b.receivers[0]
    MultiReceiver._restore(rx2, b._saved["aa:bb:cc:dd:ee:ff"])
    assert rx2.detector.cal.motion_threshold == 0.5
    assert rx2.ml.baseline.rssi == -60.0 and b.scan_time == 123.0
