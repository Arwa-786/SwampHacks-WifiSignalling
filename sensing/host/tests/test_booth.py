"""Booth recorder: its script, and that its recordings train the usual model (no hardware)."""

import numpy as np

from csi_io import VALID_SUBCARRIERS
from features import CLASSES, LABEL_TO_CLASS, fused_dataset
from record_booth import schedule
from train_model import TinyMLP, preprocess


def test_script_has_no_gaps_and_only_known_or_ignored_labels():
    plan = schedule(rounds=4, seed=3)
    assert all(a[1] == b[0] for a, b in zip(plan, plan[1:]))
    labels = {lab for _, _, lab, _ in plan}
    assert labels - set(LABEL_TO_CLASS) == {"step_away", "step_in", "settle"}  # transitions: never scored
    for kind in ("frozen", "moving", "slow_move"):
        assert sum(lab == kind for _, _, lab, _ in plan) == 4  # once per round
    assert 3.5 < plan[-1][1] / 60 < 4.5  # minutes


def test_every_cue_is_spoken_before_its_labelled_part():
    plan = schedule(rounds=2, seed=1)
    for prev, seg in zip(plan, plan[1:]):
        if seg[2] in ("frozen", "moving", "slow_move"):
            assert prev[2] == "settle" and prev[3]  # 1.5 s reaction window carries the prompt


def write_session(tmp_path, name, seed):
    """3 boards; 'moving' wobbles the link strongly, 'slow_move' gently, a standing
    person dims it a little (presence), empty is plain noise."""
    plan = schedule(rounds=2, seed=seed)
    rng = np.random.default_rng(seed)
    paths = []
    for b in range(3):
        base = rng.uniform(30, 60, 64)
        phase = np.linspace(0, 2 * np.pi, 64) + rng.uniform(0, 2 * np.pi)
        rows = []
        for start, end, label, _ in plan:
            wobble = {"moving": 0.15, "slow_move": 0.06}.get(label, 0.0)
            present = 0.0 if label in ("empty", "empty_again", "step_away") else 1.0
            for t in np.arange(start, end, 0.01):
                amp = base * (1 + rng.normal(0, 0.02, 64)) * (1 - 0.08 * present)
                amp *= 1 + wobble * np.sin(2 * np.pi * 1.4 * t + phase)
                iq = np.zeros((64, 2), dtype=np.int8)
                iq[:, 1] = np.clip(np.round(amp), -127, 127)
                iq[np.setdiff1d(np.arange(64), VALID_SUBCARRIERS)] = 0
                rssi = -50 - int(3 * present)
                rows.append(f"{t:.4f}\tCSI,0,{rssi},-95,{int(t * 1e6)},128,{iq.tobytes().hex()}\t{label}")
        p = tmp_path / f"{name}_rx{b:04d}.log"
        p.write_text("# record_booth synthetic person=test position=test\n" + "\n".join(rows) + "\n")
        paths.append(str(p))
    return paths


def test_booth_recordings_train_a_model_that_works_on_an_unseen_session(tmp_path):
    x1, y1 = fused_dataset(write_session(tmp_path, "booth_01", seed=1))
    x2, y2 = fused_dataset(write_session(tmp_path, "booth_02", seed=2))
    assert set(np.unique(y1)) == {0, 1, 2}  # EMPTY, STILL, MOVING all present
    model = TinyMLP(x1.shape[1]).fit(preprocess(x1), y1)
    pred = model.predict_proba(preprocess(x2)).argmax(axis=1)
    moving = CLASSES.index("MOVING")
    assert (pred[y2 == moving] == moving).mean() > 0.9   # moves caught
    assert (pred[y2 != moving] == moving).mean() < 0.05  # stillness / empty not called moving
