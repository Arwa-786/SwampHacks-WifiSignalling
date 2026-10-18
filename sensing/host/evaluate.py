"""Honest evaluation of the Wi-Fi model: the judge-ready table.

    python evaluate.py data/session_*_rx*.log            # prints + saves data/eval_report.md

Every session is predicted by a model trained only on the OTHER sessions
(leave-one-session-out). With two or more people, each person is also
predicted by a model that never saw that person (leave-one-person-out), which
is the harder, more honest test. Results are broken down by person and position.

Who/where a session was recorded comes from the log header written by
record_labeled.py (--person, --position) or, for older recordings, from
data/sessions.json: {"data/session_01": {"person": "arwa", "position": "standing 1.5 m"}}.

What the numbers mean (truth = the webcam's label, never seen by the model):
  precision  of the moments the model called X, how many really were X
  recall     of the real X moments, how many the model caught
  false eliminations / min   runs of 3+ wrong MOVING readings in a row (0.3 s,
             what the game's confirmation step needs) per minute of not-moving.
             This is what would wrongly knock out a player.
The camera's label lags real movement a little, so a few errors at the start
and end of each movement are expected; the report also shows the score with
the first 1 s after every label change left out.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict

import numpy as np

from features import CLASSES, FEATURE_NAMES, MOVING, fused_dataset_with_times
from train_model import TinyMLP, group_boards, hybrid_predict, preprocess, rule_baseline

CONFIRM = 3  # readings in a row before the live game changes state
SETTLE_S = 1.0  # left out after each label change in the "settled" score
HOP_S = 0.1


def session_meta(name: str, files: list[str]) -> dict:
    meta = {"person": "unknown", "position": "unknown"}
    sidecar = os.path.join(os.path.dirname(name) or ".", "sessions.json")
    if os.path.exists(sidecar):
        with open(sidecar) as f:
            meta.update(json.load(f).get(name, {}))
    with open(files[0]) as f:
        header = f.readline()
    for key in ("person", "position"):
        m = re.search(rf"{key}=(\S+)", header)
        if m:
            meta[key] = m.group(1).replace("_", " ")
    return meta


def false_eliminations(pred_moving: np.ndarray, truth_moving: np.ndarray) -> int:
    """Runs of >= CONFIRM consecutive MOVING predictions while truly not moving."""
    runs, length = 0, 0
    for p, t in zip(pred_moving, truth_moving):
        if p and not t:
            length += 1
            if length == CONFIRM:
                runs += 1
        else:
            length = 0
    return runs


def scores(y: np.ndarray, pred: np.ndarray, keep: np.ndarray | None = None) -> dict:
    if keep is not None:
        y, pred = y[keep], pred[keep]
    out = {}
    for i, c in enumerate(CLASSES):
        tp = np.sum((pred == i) & (y == i))
        out[c] = {
            "precision": tp / max(np.sum(pred == i), 1),
            "recall": tp / max(np.sum(y == i), 1),
            "n": int(np.sum(y == i)),
        }
    mv = CLASSES.index(MOVING)
    not_moving_min = np.sum(y != mv) * HOP_S / 60
    out["accuracy"] = float(np.mean(y == pred))
    out["false_elim_per_min"] = false_eliminations(pred == mv, y == mv) / max(not_moving_min, 1e-9)
    return out


def settled_mask(times: np.ndarray, y: np.ndarray) -> np.ndarray:
    keep = np.ones(len(y), bool)
    last_change = -np.inf
    for i in range(len(y)):
        if i and y[i] != y[i - 1]:
            last_change = times[i]
        keep[i] = times[i] - last_change >= SETTLE_S
    return keep


def table(rows: list[tuple[str, dict]]) -> str:
    lines = [
        "| Test set | windows | accuracy | MOVING precision | MOVING recall | STILL recall | EMPTY recall | false eliminations / min |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, s in rows:
        n = sum(s[c]["n"] for c in CLASSES)
        lines.append(
            f"| {name} | {n} | {s['accuracy']:.1%} | {s[MOVING]['precision']:.1%} | {s[MOVING]['recall']:.1%} | "
            f"{s['STILL']['recall']:.1%} | {s['EMPTY']['recall']:.1%} | {s['false_elim_per_min']:.2f} |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("recordings", nargs="+", help="<session>_rx<board>.log files from record_labeled.py")
    parser.add_argument("--out", default="data/eval_report.md")
    args = parser.parse_args()

    sessions = []
    for name, files in group_boards(args.recordings):
        t, x, y = fused_dataset_with_times(files)
        sessions.append({"name": name, "t": t, "x": x, "y": y, **session_meta(name, files)})
        print(f"{name}: {len(y)} windows, person={sessions[-1]['person']}, position={sessions[-1]['position']}")
    if len(sessions) < 2:
        raise SystemExit("Need at least 2 sessions: each one is tested by a model trained on the others.")

    def held_out(test_idx: list[int]) -> dict:
        train = [s for i, s in enumerate(sessions) if i not in test_idx]
        tx = np.vstack([s["x"] for s in train])
        ty = np.concatenate([s["y"] for s in train])
        model = TinyMLP(tx.shape[1]).fit(preprocess(tx), ty)
        out = {"y": [], "model": [], "rules": [], "keep": []}
        for i in test_idx:
            s = sessions[i]
            out["y"].append(s["y"])
            out["model"].append(hybrid_predict(model, s["x"]))
            # rules only judge MOVING; call everything else STILL for the table
            out["rules"].append(np.where(rule_baseline(s["x"]), CLASSES.index(MOVING), CLASSES.index("STILL")))
            out["keep"].append(settled_mask(s["t"], s["y"]))
        return {k: np.concatenate(v) for k, v in out.items()}

    report = ["# WiFind evaluation", "",
              f"{len(sessions)} sessions, {len({s['person'] for s in sessions})} people. "
              "Truth = webcam labels; the model only ever sees Wi-Fi.", ""]

    # leave-one-session-out, broken down by person and position
    per = [held_out([i]) for i in range(len(sessions))]
    all_res = {k: np.concatenate([p[k] for p in per]) for k in per[0]}
    rows = [("all sessions (each held out)", scores(all_res["y"], all_res["model"])),
            ("all sessions, settled (skip 1 s after changes)", scores(all_res["y"], all_res["model"], all_res["keep"]))]
    for dim in ("person", "position"):  # a breakdown only when there's more than one value
        groups = defaultdict(list)
        for s, p in zip(sessions, per):
            groups[s[dim]].append(p)
        if len(groups) > 1:
            for value in sorted(groups):
                r = {k: np.concatenate([p[k] for p in groups[value]]) for k in per[0]}
                rows.append((f"{dim}: {value}", scores(r["y"], r["model"])))
    report += ["## Leave-one-session-out (hybrid: model for MOVING + presence rule)", "", table(rows), ""]
    runs = [("motion_hybrid", name, sc) for name, sc in rows]  # every reported number also goes to Tiger

    # leave-one-person-out
    people = sorted({s["person"] for s in sessions})
    if len(people) >= 2:
        rows = []
        for person in people:
            idx = [i for i, s in enumerate(sessions) if s["person"] == person]
            r = held_out(idx)
            rows.append((f"{person} (never in training)", scores(r["y"], r["model"])))
        report += ["## Leave-one-person-out (the model never saw this person)", "", table(rows), ""]
        runs += [("motion_hybrid", name, sc) for name, sc in rows]
    else:
        report += ["## Leave-one-person-out", "", "_Only one person recorded so far: record a second person._", ""]

    # baseline
    rule_scores = scores(all_res["y"], all_res["rules"])
    report += ["## Same held-out data, old hand-tuned rules (MOVING only)", "",
               table([("rules (only the MOVING columns mean anything)", rule_scores)]), ""]
    runs.append(("motion_rules", "all sessions (each held out)", rule_scores))

    text = "\n".join(report)
    print("\n" + text)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        f.write(text + "\n")
    print(f"\nsaved {args.out}")
    from tiger_upload import save_model_runs

    save_model_runs(runs)


if __name__ == "__main__":
    main()
