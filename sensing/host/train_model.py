"""Train the EMPTY / STILL / MOVING model on labelled recordings.

    python train_model.py data/*.log                 # train + evaluate, save model.json
    python train_model.py data/*.log --out model.json

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

from features import CLASSES, FEATURE_NAMES, MOVING, session_dataset

HIDDEN = 16


class TinyMLP:
    """Softmax classifier with one hidden layer, trained with full-batch Adam."""

    def __init__(self, n_in: int, n_hidden: int = HIDDEN, n_out: int = len(CLASSES), seed: int = 0):
        rng = np.random.default_rng(seed)
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
        weight = (len(y) / (len(CLASSES) * np.maximum(np.bincount(y, minlength=len(CLASSES)), 1)))[y][:, None]
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
            "features": FEATURE_NAMES,
            "classes": CLASSES,
            **{k: getattr(self, k).tolist() for k in ("mean", "std", "w1", "b1", "w2", "b2")},
        }

    @classmethod
    def from_json(cls, d: dict) -> "TinyMLP":
        if d["features"] != FEATURE_NAMES or d["classes"] != CLASSES:
            raise ValueError("model was trained with different features/classes; retrain it")
        model = cls(len(d["features"]))
        for k in ("mean", "std", "w1", "b1", "w2", "b2"):
            setattr(model, k, np.array(d[k]))
        return model


def preprocess(x: np.ndarray) -> np.ndarray:
    """Compress ratio features (>= 0, long tail) with log1p; keep signed ones."""
    out = x.copy()
    for j, name in enumerate(FEATURE_NAMES):
        if name not in ("smooth", "turb_autocorr", "rssi_drop"):
            out[:, j] = np.log1p(np.maximum(out[:, j], 0))
    return out


def rule_baseline(x: np.ndarray) -> np.ndarray:
    """What the hand-tuned live detector would say about MOVING (size > 1x
    empty p95-ish and smooth). Only MOVING vs not; used for comparison."""
    size = x[:, FEATURE_NAMES.index("size")]
    smooth = x[:, FEATURE_NAMES.index("smooth")]
    return (size > 1.5) & (smooth > 0.5)


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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("recordings", nargs="+")
    parser.add_argument("--out", default="model.json")
    args = parser.parse_args()

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
    with open(args.out, "w") as f:
        json.dump(final.to_json(), f)
    print(f"\nsaved final model (trained on all {len(all_y)} windows) to {args.out}")


if __name__ == "__main__":
    main()
