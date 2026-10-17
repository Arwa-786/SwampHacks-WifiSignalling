"""ML pipeline on synthetic labelled recordings (no hardware needed)."""

import json

import numpy as np

from csi_io import VALID_SUBCARRIERS
from features import CLASSES, FEATURE_NAMES, MOVING, session_dataset
from train_model import TinyMLP, preprocess


def write_recording(path, seed, phases=(("empty", 0.0), ("frozen", 0.0), ("walk", 0.15), ("empty_again", 0.0))):
    """Labelled log in the format diagnose.py writes. 'frozen' dims the link
    (a body in the way); 'walk' adds a smooth wobble whose phase drifts slowly
    across subcarriers, like a real reflection path (neighbours move together)."""
    rng = np.random.default_rng(seed)
    base = rng.uniform(30, 60, 64)
    rows, t = [], 0.0
    for label, motion in phases:
        freq = rng.uniform(1, 2)
        phase = np.linspace(0, 2 * np.pi, 64) + rng.uniform(0, 2 * np.pi)
        for i in range(800):
            amp = base * (1 + rng.normal(0, 0.02, 64))
            if motion:
                amp *= 1 + motion * np.sin(2 * np.pi * freq * t + phase)
            # RSSI is steady with the odd 1 dB step, as on the real boards.
            rssi = -50 - (3 if label == "frozen" else 0) - int(rng.random() < 0.02)
            iq = np.zeros((64, 2), dtype=np.int8)
            iq[:, 1] = np.clip(np.round(amp), -127, 127)  # real part only
            iq[np.setdiff1d(np.arange(64), VALID_SUBCARRIERS)] = 0
            line = f"CSI,{i % 4096},{rssi},-95,{int(t * 1e6)},128,{iq.tobytes().hex()}"
            rows.append(f"{t:.4f}\t{line}\t{label}")
            t += 0.01
    path.write_text("# synthetic\n" + "\n".join(rows) + "\n")
    return path


def test_features_are_relative_to_the_empty_room(tmp_path):
    x, y = session_dataset(str(write_recording(tmp_path / "a.log", seed=1)))
    assert x.shape[1] == len(FEATURE_NAMES)
    assert set(y) == {0, 1, 2}
    size = FEATURE_NAMES.index("size")
    empty = y == CLASSES.index("EMPTY")
    moving = y == CLASSES.index(MOVING)
    assert abs(np.median(x[empty, size]) - 1.0) < 1e-6  # empty is the 1x baseline
    assert np.median(x[moving, size]) > 3


def test_model_generalises_to_an_unseen_session_and_round_trips(tmp_path):
    xa, ya = session_dataset(str(write_recording(tmp_path / "a.log", seed=1)))
    xb, yb = session_dataset(str(write_recording(tmp_path / "b.log", seed=2)))
    model = TinyMLP(xa.shape[1]).fit(preprocess(xa), ya, epochs=800)
    pred = model.predict_proba(preprocess(xb)).argmax(axis=1)
    moving = yb == CLASSES.index(MOVING)
    assert (pred[moving] == CLASSES.index(MOVING)).mean() > 0.9
    assert (pred[~moving] == CLASSES.index(MOVING)).mean() < 0.05

    loaded = TinyMLP.from_json(json.loads(json.dumps(model.to_json())))
    np.testing.assert_allclose(loaded.predict_proba(preprocess(xb)), model.predict_proba(preprocess(xb)))


class FakeLandmark:
    def __init__(self, x, y, visibility=0.9):
        self.x, self.y, self.visibility = x, y, visibility


def test_camera_labeler_empty_still_moving():
    from record_labeled import MotionLabeler

    rng = np.random.default_rng(0)
    body = rng.uniform(0.3, 0.7, (33, 2))
    body[:, 1] = np.linspace(0.2, 0.8, 33)  # ~0.6 image-heights tall

    def pose(offset):
        return [FakeLandmark(x + offset, y) for x, y in body]

    lab = MotionLabeler(moving=0.15, still=0.04)
    assert lab.update(0.0, None)[0] == "empty"
    still = [lab.update(i / 30, pose(0.0005 * np.sin(i)))[0] for i in range(30)]  # breathing-size jitter
    assert still[-1] == "still"
    moving = [lab.update(1 + i / 30, pose(0.01 * i))[0] for i in range(30)]  # 0.3 image-widths/s
    assert moving[-1] == "moving"
    assert lab.update(2.0, [FakeLandmark(0.5, 0.5, visibility=0.1)] * 33)[0] == "unsure"
