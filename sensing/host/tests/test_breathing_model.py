"""Breathing model on synthetic recordings (no hardware): a still, breathing person
slowly modulates the link at the breathing rate; an empty room is just noise."""

import numpy as np

import breathing_model as bm
from csi_io import VALID_SUBCARRIERS
from record_breathing import schedule


def write_session(tmp_path, name, seed, rates=(10, 20)):
    plan = schedule(list(rates))
    rng = np.random.default_rng(seed)
    paths = []
    for b in range(3):
        base = rng.uniform(30, 60, 64)
        gains = rng.uniform(0.5, 1.5, 64)
        rows = []
        for start, end, label in plan:
            present = 0.0 if label.startswith("empty") else 1.0
            rate = int(label.split("_")[1]) if label.startswith("breath_") else 14
            for t in np.arange(start, end, 0.01):
                chest = 0.03 * present * np.sin(2 * np.pi * rate / 60 * t)
                amp = base * (1 - 0.06 * present) * (1 + chest * gains) * (1 + rng.normal(0, 0.01, 64))
                iq = np.zeros((64, 2), dtype=np.int8)
                iq[:, 1] = np.clip(np.round(amp), -127, 127)
                iq[np.setdiff1d(np.arange(64), VALID_SUBCARRIERS)] = 0
                rows.append(f"{t:.4f}\tCSI,0,-50,-95,{int(t * 1e6)},128,{iq.tobytes().hex()}\t{label}")
        p = tmp_path / f"{name}_rx{b:04d}.log"
        p.write_text("# synthetic\n" + "\n".join(rows) + "\n")
        paths.append(str(p))
    return paths


def test_labels_map_to_classes():
    assert bm.label_class("empty_end") == bm.EMPTY
    assert bm.label_class("breath_15") == bm.BREATHING
    assert bm.label_class("frozen") == bm.BREATHING
    assert bm.label_class("settle") is None and bm.label_class("moving") is None


def test_model_trained_on_one_session_finds_breathing_in_another(tmp_path):
    a = bm.session_dataset(write_session(tmp_path, "breath_01", seed=1))
    b = bm.session_dataset(write_session(tmp_path, "breath_02", seed=2))
    ka, kb = a["y"] >= 0, b["y"] >= 0
    model = bm.train(a["x"][ka], a["y"][ka])
    pred = (bm.p_breathing(model, b["x"][kb]) >= 0.5).astype(int)
    y = b["y"][kb]
    assert (pred[y == bm.BREATHING] == bm.BREATHING).mean() > 0.9
    assert (pred[y == bm.EMPTY] == bm.BREATHING).mean() < 0.1
    # the FFT rate on paced windows is close to the pace
    paced = ~np.isnan(b["truth"][kb])
    assert np.median(np.abs(b["rate"][kb][paced] - b["truth"][kb][paced])) < 1.5


def test_model_round_trips_through_json(tmp_path):
    x = np.random.default_rng(0).normal(size=(40, 12))
    y = (x[:, 1] > 0).astype(int)
    model = bm.train(x, y)
    bm.save(model, ["a", "b", "c"], str(tmp_path / "m.json"))
    loaded, boards = bm.load(str(tmp_path / "m.json"))
    assert boards == ["a", "b", "c"]
    assert np.allclose(bm.p_breathing(model, x), bm.p_breathing(loaded, x))


def test_live_path_matches_training_on_an_unseen_session(tmp_path):
    """Replay a recording through BreathingLive exactly as the server receives it."""
    from features import load_session

    train = bm.session_dataset(write_session(tmp_path, "breath_01", seed=1))
    k = train["y"] >= 0
    bm.save(bm.train(train["x"][k], train["y"][k]), ["0000", "0001", "0002"], str(tmp_path / "m.json"))
    live = bm.BreathingLive(str(tmp_path / "m.json"))
    assert live.update(0.0) == {"status": "needs scan"}

    files = write_session(tmp_path, "breath_02", seed=2)
    sess = {b: load_session(f) for b, f in zip(["0000", "0001", "0002"], files)}
    scan_end = max(bm.scan_frames(s)[0][-1] for s in sess.values())
    events = sorted((t, b, i) for b, s in sess.items() for i, t in enumerate(s.times))
    labels, said, scanned, nxt = sess["0000"].labels, {}, False, 0.0
    for t, b, i in events:
        live.push(b, t, sess[b].signal[i])
        if not scanned and t >= scan_end:
            live.scan(scan_end - bm.SCAN_S, scan_end)
            scanned, nxt = True, t
        if scanned and t >= nxt:
            nxt = t + 2
            lab = labels[min(int(np.searchsorted(sess["0000"].times, t)), len(labels) - 1)]
            if lab.startswith("breath_") and t - bm.WINDOW_S > sess["0000"].times[labels.index(lab)]:
                r = live.update(t)
                said.setdefault("breathing", []).append(r.get("present"))
    assert np.mean(said["breathing"]) > 0.9
    assert live.update(t, moving=True) == {"status": "moving"}
