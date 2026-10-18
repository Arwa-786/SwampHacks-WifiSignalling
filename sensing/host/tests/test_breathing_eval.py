from types import SimpleNamespace

import numpy as np

from eval_breathing import best_estimate, segments
from record_breathing import schedule


def fake_board(rate_per_min, seconds=40, fs=100, amplitude=0.02, seed=0):
    """CSI-like signal: 52 subcarriers, chest movement modulates the link."""
    rng = np.random.default_rng(seed)
    t = np.arange(0, seconds, 1 / fs)
    breath = amplitude * np.sin(2 * np.pi * rate_per_min / 60 * t) if rate_per_min else 0
    gains = rng.uniform(0.5, 1.5, 52)
    signal = 20 * (1 + np.outer(breath if rate_per_min else np.zeros_like(t), gains)) + rng.normal(0, 0.3, (len(t), 52))
    return SimpleNamespace(times=t, signal=signal)


def test_best_board_finds_the_paced_rate():
    boards = [fake_board(0, seed=1), fake_board(15, seed=2)]  # one board sees nothing
    e = best_estimate(boards, 0, 30)
    assert abs(e.per_minute - 15) < 1.5 and e.confidence > 0.75


def test_segments_split_on_label_changes():
    labels = ["empty"] * 3 + ["breath_10"] * 2
    assert segments(labels, np.arange(5.0)) == [("empty", 0.0, 2.0), ("breath_10", 3.0, 4.0)]


def test_breathing_script_covers_every_rate_in_order():
    plan = schedule([10, 20])
    assert [lab for _, _, lab in plan] == ["empty", "settle", "breath_10", "rest", "breath_20", "rest", "empty_end"]
    assert all(a[1] == b[0] for a, b in zip(plan, plan[1:]))  # no gaps
