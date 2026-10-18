"""Train the EMPTY / STILL / MOVING model on labelled recordings.

    python train_model.py data/*.log                 # train + evaluate, save model.json
    python train_model.py data/*.log --out model.json
    python train_model.py --fuse data/session_*_rx*.log  # one model, all boards together

Evaluation is honest about what matters: with 2+ recordings, every recording
is held out in turn and predicted by a model trained only on the others
("leave-one-session-out"), because the real question is whether it works in a
session it has never seen. With a single recording it falls back to training
on the first 70% of each label and testing on the last 30%, and says so.

The model is a tiny neural network in plain NumPy (8 -> 16 -> 3), so it needs
no PyTorch and runs anywhere the detector runs. The rule-based detector's
score on the same windows is printed next to it for comparison.
"""

from __future__ import annotations

import argparse
import json

import numpy as np

import re
from collections import defaultdict

from features import CLASSES, FEATURE_NAMES, MOVING, fused_dataset, session_dataset

HIDDEN = 16


class TinyMLP:
    """Softmax classifier with one hidden layer, trained with full-batch Adam."""

    def __init__(self, n_in: int, n_hidden: int = HIDDEN, n_out: int = len(CLASSES), seed: int = 0,
                 feature_names: list[str] | None = None):
        rng = np.random.default_rng(seed)
        # one block of FEATURE_NAMES per board when fused
        self.feature_names = feature_names or (FEATURE_NAMES * (n_in // len(FEATURE_NAMES)))
        self.w1 = rng.normal(0, np.sqrt(2 / n_in), (n_in, n_hidden))
        self.b1 = np.zeros(n_hidden)
        self.w2 = rng.normal(0, np.sqrt(2 / n_hidden), (n_hidden, n_out))
        self.b2 = np.zeros(n_out)
        self.mean = np.zeros(n_in)
        self.std = np.ones(n_in)

    def _forward(self, x):
        h = np.maximum(0, x @ self.w1 + self.b1)
        z = h @ self.w2 + self.b2
        z = z - z.max(axis=1, keepdims=True)
        p = np.exp(z)
        return h, p / p.sum(axis=1, keepdims=True)

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        x = (np.atleast_2d(x) - self.mean) / self.std
        return self._forward(x)[1]

    def fit(self, x: np.ndarray, y: np.ndarray, epochs: int = 2000, lr: float = 0.01, l2: float = 1e-3):
        self.mean = x.mean(axis=0)
        self.std = x.std(axis=0) + 1e-9
        x = (x - self.mean) / self.std
        onehot = np.eye(self.w2.shape[1])[y]
        # Balance classes so a long EMPTY recording doesn't drown MOVING.
        n_out = self.w2.shape[1]
        weight = (len(y) / (n_out * np.maximum(np.bincount(y, minlength=n_out), 1)))[y][:, None]
        params = [self.w1, self.b1, self.w2, self.b2]
        m = [np.zeros_like(p) for p in params]
        v = [np.zeros_like(p) for p in params]
        for step in range(1, epochs + 1):
            h, p = self._forward(x)
            dz = (p - onehot) * weight / len(y)
            grads = [None, None, h.T @ dz + l2 * self.w2, dz.sum(axis=0)]
            dh = (dz @ self.w2.T) * (h > 0)
            grads[0] = x.T @ dh + l2 * self.w1
            grads[1] = dh.sum(axis=0)
            for i, (param, g) in enumerate(zip(params, grads)):
                m[i] = 0.9 * m[i] + 0.1 * g
                v[i] = 0.999 * v[i] + 0.001 * g * g
                param -= lr * (m[i] / (1 - 0.9**step)) / (np.sqrt(v[i] / (1 - 0.999**step)) + 1e-8)
        return self

    def to_json(self) -> dict:
        return {
            "features": self.feature_names,
            "classes": CLASSES,
            **{k: getattr(self, k).tolist() for k in ("mean", "std", "w1", "b1", "w2", "b2")},
        }

    @classmethod
    def from_json(cls, d: dict) -> "TinyMLP":
        names = d["features"]
        boards = len(names) // len(FEATURE_NAMES)
        if names != FEATURE_NAMES * boards or boards == 0 or d["classes"] != CLASSES:
            raise ValueError("model was trained with different features/classes; retrain it")
        model = cls(len(names), feature_names=names)
        for k in ("mean", "std", "w1", "b1", "w2", "b2"):
            setattr(model, k, np.array(d[k]))
        return model


def preprocess(x: np.ndarray) -> np.ndarray:
    """Compress ratio features (>= 0, long tail) with log1p; keep signed ones."""
    out = x.copy()
    for j in range(out.shape[1]):
        name = FEATURE_NAMES[j % len(FEATURE_NAMES)]  # fused: one block per board
        if name not in ("smooth", "turb_autocorr", "rssi_drop"):
            out[:, j] = np.log1p(np.maximum(out[:, j], 0))
    return out


def rule_baseline(x: np.ndarray) -> np.ndarray:
    """What the hand-tuned live detector would say about MOVING (size > 1x
    empty p95-ish and smooth). Only MOVING vs not; used for comparison.
    Fused rows: each board votes and at least half must agree, like multi_rx.fuse."""
    n = len(FEATURE_NAMES)
    votes = []
    for b in range(x.shape[1] // n):
        size = x[:, b * n + FEATURE_NAMES.index("size")]
        smooth = x[:, b * n + FEATURE_NAMES.index("smooth")]
        votes.append((size > 1.5) & (smooth > 0.5))
    return np.mean(votes, axis=0) >= 0.5


PRESENCE_RATIO = 2.0  # "the room looks >2x more different from the empty scan than usual"


def hybrid_predict(model: "TinyMLP", x: np.ndarray) -> np.ndarray:
    """What the live game uses: the model decides MOVING; when not moving, a
    person is PRESENT if at least half the boards see presence > PRESENCE_RATIO.

    Why not let the model do presence too: how strongly a still person changes
    the signal depends on placement (2x after the boards were turned, 19x
    before), so a model trained on one placement calls a still person "empty"
    in another. The rule compares against the room scan, so it adapts."""
    n = len(FEATURE_NAMES)
    moving = model.predict_proba(preprocess(x)).argmax(axis=1) == CLASSES.index(MOVING)
    pres = x[:, [b * n + FEATURE_NAMES.index("presence") for b in range(x.shape[1] // n)]]
    present = (pres > PRESENCE_RATIO).mean(axis=1) >= 0.5
    out = np.where(present, CLASSES.index("STILL"), CLASSES.index("EMPTY"))
    return np.where(moving, CLASSES.index(MOVING), out)


def report(name: str, y_true: np.ndarray, y_pred: np.ndarray, x_raw: np.ndarray) -> None:
    acc = np.mean(y_true == y_pred)
    mov_true = y_true == CLASSES.index(MOVING)
    mov_pred = y_pred == CLASSES.index(MOVING)
    rule = rule_baseline(x_raw)
    print(f"\n{name}: {len(y_true)} windows, 3-class accuracy {acc:.1%}")
    print("  confusion (rows = truth, cols = predicted):  " + "  ".join(f"{c:>6s}" for c in CLASSES))
    for i, c in enumerate(CLASSES):
        row = [np.sum((y_true == i) & (y_pred == j)) for j in range(len(CLASSES))]
        print(f"  {c:>44s}  " + "  ".join(f"{n:6d}" for n in row))
    for label, pred in (("model", mov_pred), ("rules", rule)):
        caught = pred[mov_true].mean() if mov_true.any() else float("nan")
        false = pred[~mov_true].mean() if (~mov_true).any() else float("nan")
        print(f"  MOVING via {label}: catches {caught:.1%} of movement, false alarm on {false:.1%} of still/empty")


def group_boards(paths: list[str]) -> list[tuple[str, list[str]]]:
    """data/s1_rx64dc.log, data/s1_rx9294.log, ... -> [("data/s1", [files sorted by board])].
    Every session must have the same boards so feature columns line up."""
    groups: dict[str, dict[str, str]] = defaultdict(dict)
    for p in paths:
        m = re.match(r"(.*)_rx(\w+)\.log$", p)
        if not m:
            raise SystemExit(f"--fuse expects <session>_rx<board>.log files, got {p}")
        groups[m.group(1)][m.group(2)] = p
    boards = sorted(next(iter(groups.values())))
    for name, files in groups.items():
        if sorted(files) != boards:
            raise SystemExit(f"{name} has boards {sorted(files)}, expected {boards}")
    print(f"boards (feature block order): {boards}")
    return [(name, [files[b] for b in boards]) for name, files in sorted(groups.items())]


def groups_files(paths: list[str]) -> list[str]:
    """The board files of the first session (all sessions have the same boards)."""
    return group_boards(paths)[0][1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("recordings", nargs="+")
    parser.add_argument("--out", default="model.json")
    parser.add_argument("--fuse", action="store_true",
                        help="files are <session>_rx<board>.log; train one model on all boards together")
    args = parser.parse_args()

    if args.fuse:
        sessions = [(name, *fused_dataset(files)) for name, files in group_boards(args.recordings)]
    else:
        sessions = [(path, *session_dataset(path)) for path in args.recordings]
    for path, x, y in sessions:
        print(f"{path}: {len(y)} windows, per class {dict(zip(CLASSES, np.bincount(y, minlength=3).tolist()))}")

    if len(sessions) >= 2:
        print("\n=== leave-one-session-out (each recording predicted by a model that never saw it) ===")
        for i, (path, x, y) in enumerate(sessions):
            train_x = np.vstack([s[1] for j, s in enumerate(sessions) if j != i])
            train_y = np.concatenate([s[2] for j, s in enumerate(sessions) if j != i])
            model = TinyMLP(x.shape[1]).fit(preprocess(train_x), train_y)
            report(path, y, model.predict_proba(preprocess(x)).argmax(axis=1), x)
            report(f"{path} [hybrid: model for MOVING + presence rule]", y, hybrid_predict(model, x), x)
    else:
        print("\n=== only one recording: train on first 70% of each label, test on last 30% ===")
        print("    (optimistic: same session. Record 2+ sessions for a real test.)")
        _, x, y = sessions[0]
        train, test = [], []
        for c in range(len(CLASSES)):
            idx = np.flatnonzero(y == c)
            cut = int(len(idx) * 0.7)
            train.extend(idx[:cut])
            test.extend(idx[cut:])
        model = TinyMLP(x.shape[1]).fit(preprocess(x[train]), y[train])
        report("held-out 30%", y[test], model.predict_proba(preprocess(x[test])).argmax(axis=1), x[test])

    all_x = np.vstack([s[1] for s in sessions])
    all_y = np.concatenate([s[2] for s in sessions])
    final = TinyMLP(all_x.shape[1]).fit(preprocess(all_x), all_y)
    saved = final.to_json()
    if args.fuse:  # what the live server needs to line boards up and run the hybrid
        saved["boards"] = sorted(re.match(r".*_rx(\w+)\.log$", p).group(1) for p in groups_files(args.recordings))
        saved["presence_ratio"] = PRESENCE_RATIO
    with open(args.out, "w") as f:
        json.dump(saved, f)
    print(f"\nsaved final model (trained on all {len(all_y)} windows) to {args.out}")


if __name__ == "__main__":
    main()
