"""Train and test the breathing model (breathing_model.py).

    python train_breathing.py data/breath_*_rx*.log data/booth_*_rx*.log
    python train_breathing.py ... --out models/breathing.json      # also saves the model

Inputs: record_breathing.py and record_booth.py sessions (one log per board).
Every session is predicted by a model trained only on the OTHER sessions
(leave-one-session-out), and the old rule (FFT confidence >= 0.75 on the
clearest board) is scored on the same windows, so the comparison is fair.
With a single session it falls back to a time split and says so.

Printed (and saved to data/breathing_model_report.md):
  breathing found      share of "someone still and breathing" windows called BREATHING
  empty called breathing  share of empty-room windows wrongly called BREATHING (must be low)
  rate error           paced sessions only: |FFT rate - the pace the voice set|, on
                       windows the model called BREATHING
"""

from __future__ import annotations

import argparse
import os

import numpy as np

import breathing_model as bm
from breathing import MIN_CONFIDENCE
from train_model import group_boards


def scores(y: np.ndarray, pred: np.ndarray, truth: np.ndarray, rate: np.ndarray) -> dict:
    breathing, empty = y == bm.BREATHING, y == bm.EMPTY
    out = {
        "windows": int(len(y)),
        "breathing found": float(np.mean(pred[breathing] == bm.BREATHING)) if breathing.any() else float("nan"),
        "empty called breathing": float(np.mean(pred[empty] == bm.BREATHING)) if empty.any() else float("nan"),
    }
    paced = ~np.isnan(truth) & (pred == bm.BREATHING)
    err = np.abs(rate[paced] - truth[paced])
    out["rate error /min"] = float(np.median(err)) if len(err) else float("nan")
    out["rate within ±2"] = float(np.mean(err <= 2)) if len(err) else float("nan")
    return out


def table(rows: list[tuple[str, dict]]) -> str:
    keys = ["windows", "breathing found", "empty called breathing", "rate error /min", "rate within ±2"]
    lines = ["| test set | " + " | ".join(keys) + " |", "|---|" + "---|" * len(keys)]
    for name, sc in rows:
        cells = []
        for k in keys:
            v = sc[k]
            cells.append(str(v) if k == "windows" else ("-" if np.isnan(v) else (f"{v:.1f}" if "error" in k else f"{v:.0%}")))
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("recordings", nargs="+")
    parser.add_argument("--out", help="save the final model here, e.g. models/breathing.json")
    parser.add_argument("--report", default="data/breathing_model_report.md")
    args = parser.parse_args()

    sessions = []
    for name, files in group_boards(args.recordings):
        try:
            d = bm.session_dataset(files)
        except ValueError as e:  # e.g. webcam sessions: labels flip too often for 15 s windows
            print(f"skipping {name}: {e}")
            continue
        keep = d["y"] >= 0
        if not np.any(d["y"][keep] == bm.BREATHING):
            print(f"skipping {name}: no 'still and breathing' windows")
            continue
        sessions.append({"name": os.path.basename(name), **{k: v[keep] for k, v in d.items() if k != "label"}})
        counts = np.bincount(sessions[-1]["y"], minlength=2)
        print(f"{name}: {keep.sum()} windows (empty {counts[0]}, breathing {counts[1]})")

    if not sessions:
        raise SystemExit("No usable sessions: record with record_breathing.py or record_booth.py.")
    rows_model, rows_rule = [], []
    if len(sessions) >= 2:
        title = "each session predicted by a model trained on the others"
        for i, s in enumerate(sessions):
            others = [o for j, o in enumerate(sessions) if j != i]
            model = bm.train(np.vstack([o["x"] for o in others]), np.concatenate([o["y"] for o in others]))
            pred = (bm.p_breathing(model, s["x"]) >= 0.5).astype(int)
            rows_model.append((s["name"], scores(s["y"], pred, s["truth"], s["rate"])))
    else:
        title = "ONE session only: first 70% of each class trains, last 30% tests (optimistic; record more)"
        s = sessions[0]
        train_idx, test_idx = [], []
        for c in (bm.EMPTY, bm.BREATHING):
            idx = np.flatnonzero(s["y"] == c)
            cut = int(len(idx) * 0.7)
            train_idx += list(idx[:cut]); test_idx += list(idx[cut:])
        model = bm.train(s["x"][train_idx], s["y"][train_idx])
        t = np.array(test_idx)
        pred = (bm.p_breathing(model, s["x"][t]) >= 0.5).astype(int)
        rows_model.append(("held-out 30%", scores(s["y"][t], pred, s["truth"][t], s["rate"][t])))
        sessions = [{**s, **{k: s[k][t] for k in ("x", "y", "truth", "rate")}}]

    for s in sessions:  # the old rule on the same windows: clearest board's FFT confidence
        n_boards = s["x"].shape[1] // len(bm.FEATURES)
        conf = s["x"][:, [b * len(bm.FEATURES) for b in range(n_boards)]]
        pred = (np.nanmax(conf, axis=1) >= MIN_CONFIDENCE).astype(int)
        rows_rule.append((s["name"], scores(s["y"], pred, s["truth"], s["rate"])))

    text = "\n".join(["# Breathing model", "", f"Model ({title}):", "", table(rows_model), "",
                      f"Old rule (FFT confidence >= {MIN_CONFIDENCE}), same windows:", "", table(rows_rule), ""])
    print("\n" + text)
    os.makedirs(os.path.dirname(args.report) or ".", exist_ok=True)
    with open(args.report, "w") as f:
        f.write(text)
    print(f"saved {args.report}")
    from tiger_upload import save_model_runs

    save_model_runs([("breathing_mlp", name, sc) for name, sc in rows_model]
                    + [(f"breathing_rule_conf{MIN_CONFIDENCE}", name, sc) for name, sc in rows_rule])

    if args.out:
        groups = [g for g in group_boards(args.recordings) if os.path.basename(g[0]) in {s["name"] for s in sessions}]
        full = [bm.session_dataset(files) for _, files in groups]
        x = np.vstack([d["x"][d["y"] >= 0] for d in full])
        y = np.concatenate([d["y"][d["y"] >= 0] for d in full])
        boards = [os.path.basename(p).split("_rx")[-1].split(".")[0] for p in groups[0][1]]
        bm.save(bm.train(x, y), boards, args.out)
        print(f"saved the final model (all {len(y)} windows) to {args.out}")


if __name__ == "__main__":
    main()
