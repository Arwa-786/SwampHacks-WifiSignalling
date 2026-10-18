"""Zone experiment on synthetic 3-board recordings (no hardware needed)."""

import numpy as np

from csi_io import VALID_SUBCARRIERS
from eval_zones import WHO, cross_validate, load, report, segment_outcomes
from record_zones import schedule


def write_boards(tmp_path, reach, rounds=3, seed=0, same_style=False):
    """One log per board for a pair-mode session. reach[board] = (how much
    zone A's movement shows on this board, same for zone B). Unless
    same_style, A and B move at different rhythms, as two people would."""
    plan = [(s, e, lab) for s, e, lab, _ in schedule("pair", rounds, seed)]
    rng = np.random.default_rng(seed)
    paths = []
    for b, (from_a, from_b) in enumerate(reach):
        base = rng.uniform(30, 60, 64)
        phase = np.linspace(0, 2 * np.pi, 64) + rng.uniform(0, 2 * np.pi)
        rows = []
        for start, end, label in plan:
            cond = label.split("@")[0]
            a = cond in ("a_moves", "both_move")
            bb = cond in ("b_moves", "both_move")
            for t in np.arange(start, end, 0.01):
                amp = base * (1 + rng.normal(0, 0.02, 64))
                amp *= 1 + (0.15 * from_a * a) * np.sin(2 * np.pi * 1.3 * t + phase)
                b_freq, b_phase = (1.3, phase) if same_style else (1.7, phase[::-1])
                amp *= 1 + (0.15 * from_b * bb) * np.sin(2 * np.pi * b_freq * t + b_phase)
                iq = np.zeros((64, 2), dtype=np.int8)
                iq[:, 1] = np.clip(np.round(amp), -127, 127)
                iq[np.setdiff1d(np.arange(64), VALID_SUBCARRIERS)] = 0
                line = f"CSI,0,-50,-95,{int(t * 1e6)},128,{iq.tobytes().hex()}"
                rows.append(f"{t:.4f}\t{line}\t{label}")
        p = tmp_path / f"zones_01_rx{b:04d}.log"
        p.write_text("# synthetic\n" + "\n".join(rows) + "\n")
        paths.append(str(p))
    return paths


def test_distinct_signatures_pass(tmp_path):
    # board 0 near zone A, board 2 near zone B, board 1 in the middle
    d = load(write_boards(tmp_path, reach=[(1.0, 0.1), (0.5, 0.5), (0.1, 1.0)]))
    text, passed = report(d, cross_validate(d))
    assert passed, text
    assert "wrong player blamed: 0" in text
    assert "swapped" in text  # one session can't rule out learning who, not where


def test_identical_signatures_do_not_pass(tmp_path):
    d = load(write_boards(tmp_path, reach=[(0.6, 0.6)] * 3, same_style=True))
    text, passed = report(d, cross_validate(d))
    assert not passed, text


def test_only_consecutive_moves_get_someone_out():
    a, none = WHO.index("A"), WHO.index("none")
    y = np.array([a] * 10 + [none] * 10)
    seg = np.array(["s1"] * 10 + ["s2"] * 10)
    pred = np.array([a] * 10 + [a, none] * 5)  # flickers in the frozen segment: never 8 in a row
    out = segment_outcomes(pred, y, seg)
    assert out[0]["right"] and out[1]["right"] and not out[1]["wrong_blamed"]


def test_conditions_are_shuffled_within_rounds_with_no_gaps():
    plan = schedule("pair", 2, seed=1)
    conds = [lab for _, _, lab, _ in plan if "@" in lab]
    assert sorted(c.split("@")[0] for c in conds if c.endswith("@1")) == ["a_moves", "b_moves", "both_freeze", "both_move"]
    assert all(a[1] == b[0] for a, b in zip(plan, plan[1:]))
    solo = [lab for _, _, lab, _ in schedule("solo", 1)]
    assert solo.count("walk") == 4 and "both_move@1" not in solo
