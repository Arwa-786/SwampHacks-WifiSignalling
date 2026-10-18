import numpy as np

from phone_truth import clock_offset, rate


def test_rate_finds_a_belly_rhythm_and_ignores_slow_drift():
    t = np.arange(0, 60, 0.01)
    belly = 0.02 * np.sin(2 * np.pi * 16 / 60 * t)          # 16 breaths a minute
    drift = 0.05 * np.sin(2 * np.pi * 0.05 * t)             # 3 / min wander: not breathing
    rng = np.random.default_rng(0)
    acc = np.stack([drift + rng.normal(0, 0.003, len(t)), belly + rng.normal(0, 0.003, len(t)),
                    rng.normal(0, 0.003, len(t))], axis=1)
    per_min, clarity = rate(t, acc, 0, 60)
    assert abs(per_min - 16) < 1 and clarity > 0.5


def test_clock_offset_is_read_from_the_recording_header(tmp_path):
    p = tmp_path / "breath_rx1.log"
    p.write_text("# record_breathing 2026-10-18 00:20:00 natural=3.0 clock=1792296000.125 port=x\n")
    assert clock_offset(str(p)) == 1792296000.125
