"""Can the three receivers tell WHERE movement happened? (zone experiment)

    python eval_zones.py data/zones_*_rx*.log    # prints + saves data/zones_report.md and .png

Input: recordings from record_zones.py (1 TX, 3 RX, two taped zones A and B).

Each receiver keeps its own numbers instead of being merged into one verdict.
For every 1 s window and every board we use the same 8 features as the motion
model, each relative to the empty room ("3.0" = 3x the empty room). The
question is whether the PATTERN across the boards differs by zone:

    zone A moves:  RX1 ██████████  RX2 ███  RX3 ██
    zone B moves:  RX1 ██          RX2 ███  RX3 ██████████

Three ways of answering, from plain to ML:
  1. Signatures: average motion per board for each condition (the bar chart).
  2. Who moved: none / A / B / both, from the 3 boards' features, by a
     nearest-centroid rule (3 numbers: motion per board) and by the tiny
     neural network (24 numbers). Always tested on a ROUND it never trained
     on (leave-one-round-out), so it can't just memorise the recording.
  3. Location: a score from 0 (zone A) to 1 (zone B) for windows where exactly
     one zone moved; reported as how well it separates A from B (AUC).

The game numbers that matter (the model + the elimination rule):
  * right player caught: in an "A moves" segment, A is caught and B is not
  * wrong player blamed: anyone caught who was told to freeze. Must be 0.
A player counts as caught when the model says they moved for CATCH_WINDOWS
windows in a row (~0.8 s), like the elimination rule planned for the game.

Trap: in pair mode zone A is always the same person, so a model could learn
that person's way of moving instead of their place. With 2+ sessions recorded
with the players swapped (record_zones.py --swapped), every session is also
held out whole, which only works if the model learned places.
"""

from __future__ import annotations

import argparse
import os
from collections import defaultdict

import numpy as np

from features import FEATURE_NAMES, FUSE_TOLERANCE_S, session_windows
from train_model import TinyMLP, group_boards, preprocess

WHO = ["none", "A", "B", "both"]
COND_TO_WHO = {"a_moves": "A", "b_moves": "B", "both_move": "both", "both_freeze": "none",
               "a_freeze": "none", "b_freeze": "none"}
SKIP_S = 1.5  # after each instruction: reaction time, the person is still starting/stopping
CATCH_WINDOWS = 8  # consecutive 0.1 s hops = ~0.8 s of "moved" before someone is out
SIZE = FEATURE_NAMES.index("size")


def who_moved(cond: str) -> int:
    return WHO.index(COND_TO_WHO[cond])


def caught(who: int) -> set[str]:
    return {"none": set(), "A": {"A"}, "B": {"B"}, "both": {"A", "B"}}[WHO[who]]


# ---------------- loading ----------------

def load(paths: list[str]) -> dict:
    """All condition windows from every session, every board's features side by side.
    Returns arrays: x (n, boards, 8), t, cond, round, session, segment."""
    out = defaultdict(list)
    for name, files in group_boards(paths):
        per = [session_windows(p) for p in files]
        with open(files[0]) as f:
            swapped = "swapped=1" in f.readline()
        ref_t, _, ref_lab = per[0]
        first_seen: dict[str, float] = {}
        for k, t in enumerate(ref_t):
            lab = ref_lab[k]
            if "@" not in lab:
                continue
            cond, rnd = lab.split("@")
            first_seen.setdefault(lab, t)
            if t < first_seen[lab] + SKIP_S:
                continue
            boards = []
            for times, x, labs in per:
                j = int(np.argmin(np.abs(times - t)))
                if abs(times[j] - t) > FUSE_TOLERANCE_S or labs[j] != lab:
                    break
                boards.append(x[j])
            else:
                out["x"].append(np.array(boards))
                out["t"].append(t)
                out["cond"].append(cond)
                out["round"].append(int(rnd))
                out["session"].append(os.path.basename(name))
                out["segment"].append(f"{os.path.basename(name)}:{lab}")
                out["swapped"].append(swapped)
    if not out["x"]:
        raise SystemExit("No zone windows found. Were these recorded with record_zones.py?")
    d = {k: np.array(v) for k, v in out.items()}
    d["y"] = np.array([who_moved(c) for c in d["cond"]])
    d["boards"] = [os.path.basename(f).split("_rx")[-1].split(".")[0] for f in group_boards(paths)[0][1]]
    return d


# ---------------- models ----------------

def signature(x: np.ndarray) -> np.ndarray:
    """(n, boards) log motion per board: the 'which receivers changed' pattern."""
    return np.log1p(np.maximum(x[:, :, SIZE], 0))


class NearestCentroid:
    """Plain rule: each class is its average signature; pick the closest."""

    def fit(self, x, y):
        self.classes = np.unique(y)
        self.centroids = np.array([x[y == c].mean(axis=0) for c in self.classes])
        return self

    def predict(self, x):
        d = ((x[:, None, :] - self.centroids[None]) ** 2).sum(axis=2)
        return self.classes[d.argmin(axis=1)]


def mlp_inputs(x: np.ndarray) -> np.ndarray:
    return preprocess(x.reshape(len(x), -1))


def held_out_rounds(d: dict):
    """Leave-one-round-out: (train mask, test mask) per (session, round)."""
    keys = sorted(set(zip(d["session"], d["round"])))
    for s, r in keys:
        test = (d["session"] == s) & (d["round"] == r)
        yield ~test, test


def held_out_sessions(d: dict):
    """Leave-one-session-out: with the players swapped between sessions, a
    model that learned a person's style instead of a place fails here."""
    for s in sorted(set(d["session"])):
        test = d["session"] == s
        yield ~test, test


def cross_validate(d: dict, folds=held_out_rounds) -> dict:
    """Held-out predictions from both models + the A-vs-B location score."""
    n = len(d["y"])
    pred_rule, pred_mlp = np.zeros(n, int), np.zeros(n, int)
    loc = np.full(n, np.nan)
    single = np.isin(d["y"], [WHO.index("A"), WHO.index("B")])
    sig, feats = signature(d["x"]), mlp_inputs(d["x"])
    for train, test in folds(d):
        pred_rule[test] = NearestCentroid().fit(sig[train], d["y"][train]).predict(sig[test])
        mlp = TinyMLP(feats.shape[1], n_out=len(WHO)).fit(feats[train], d["y"][train])
        pred_mlp[test] = mlp.predict_proba(feats[test]).argmax(axis=1)
        tr = train & single
        if len(np.unique(d["y"][tr])) == 2:
            # location: P(zone B) from a 2-class network trained only on one-zone moments
            lm = TinyMLP(feats.shape[1], n_out=2).fit(feats[tr], (d["y"][tr] == WHO.index("B")).astype(int))
            loc[test] = lm.predict_proba(feats[test])[:, 1]
    return {"rule": pred_rule, "mlp": pred_mlp, "location": loc}


# ---------------- scores ----------------

def auc(neg: np.ndarray, pos: np.ndarray) -> float:
    """P(a random B moment scores higher than a random A moment)."""
    if not len(neg) or not len(pos):
        return float("nan")
    return float((pos[:, None] > neg[None]).mean() + 0.5 * (pos[:, None] == neg[None]).mean())


def segment_outcomes(pred: np.ndarray, y: np.ndarray, segment: np.ndarray, k: int = CATCH_WINDOWS) -> list[dict]:
    """Play each instruction segment as a red light: who would be out?
    A player is out after k consecutive windows that include them."""
    out = []
    for seg in dict.fromkeys(segment):  # keeps order
        m = segment == seg
        truth = caught(int(y[m][0]))
        run = {"A": 0, "B": 0}
        out_players = set()
        for p in pred[m]:
            for player in ("A", "B"):
                run[player] = run[player] + 1 if player in caught(int(p)) else 0
                if run[player] >= k:
                    out_players.add(player)
        out.append({"segment": seg, "truth": truth, "out": out_players,
                    "right": out_players == truth, "wrong_blamed": sorted(out_players - truth),
                    "missed": sorted(truth - out_players)})
    return out


def confusion(y: np.ndarray, pred: np.ndarray, classes: list[int]) -> str:
    lines = ["| told \\ model says | " + " | ".join(WHO[c] for c in classes) + " |",
             "|---|" + "---|" * len(classes)]
    for c in classes:
        row = [int(((y == c) & (pred == p)).sum()) for p in classes]
        lines.append(f"| {WHO[c]} | " + " | ".join(map(str, row)) + " |")
    return "\n".join(lines)


def bars(values: np.ndarray, width: int = 24) -> list[str]:
    top = max(values.max(), 1e-9)
    return ["█" * max(1, round(width * v / top)) for v in values]


def report(d: dict, cv: dict, cv_sessions: dict | None = None) -> tuple[str, bool]:
    y, boards = d["y"], d["boards"]
    classes = sorted(np.unique(y).tolist())
    lines = [f"# Zone experiment: can 3 receivers tell where movement happened?\n",
             f"{len(set(d['session']))} session(s), {len(set(zip(d['session'], d['round'])))} rounds, "
             f"{len(y)} windows. Each round held out in turn.\n",
             "## 1. Signatures: motion per board (x empty room, median)\n", "```"]
    med = {c: np.median(d["x"][d["cond"] == c][:, :, SIZE], axis=0) for c in dict.fromkeys(d["cond"])}
    top = max(v.max() for v in med.values())
    for c, v in med.items():
        lines.append(f"{c}:")
        for b, val in zip(boards, v):
            lines.append(f"  RX {b:>5} {'█' * max(1, round(24 * val / top)):<24} {val:5.1f}x")
    lines.append("```\n")

    rule_acc = float((cv["rule"] == y).mean())
    mlp_acc = float((cv["mlp"] == y).mean())
    chance = float(np.bincount(y).max() / len(y))
    lines += ["## 2. Who moved (every 0.1 s, held-out rounds)\n",
              "| model | accuracy | always guessing the commonest |", "|---|---|---|",
              f"| nearest centroid (motion per board, 3 numbers) | {rule_acc:.1%} | {chance:.1%} |",
              f"| tiny neural network (8 features x 3 boards) | {mlp_acc:.1%} | {chance:.1%} |\n",
              "Neural network:\n", confusion(y, cv["mlp"], classes), ""]

    a, b = cv["location"][y == WHO.index("A")], cv["location"][y == WHO.index("B")]
    a, b = a[~np.isnan(a)], b[~np.isnan(b)]
    sep = auc(a, b)
    lines += ["## 3. Location score (0 = zone A, 1 = zone B)\n",
              f"Zone A moving: median {np.median(a) if len(a) else float('nan'):.2f}. "
              f"Zone B moving: median {np.median(b) if len(b) else float('nan'):.2f}. "
              f"Separation (AUC): {sep:.3f} (0.5 = can't tell, 1.0 = perfect).\n"]

    segs = segment_outcomes(cv["mlp"], y, d["segment"])
    right = np.mean([s["right"] for s in segs])
    wrong = [s for s in segs if s["wrong_blamed"]]
    false_catch = [s for s in segs if not s["truth"] and s["out"]]
    lines += ["## 4. As a game (each instruction played as a red light, ~0.8 s rule)\n",
              f"* right outcome (exactly the players who moved are out): {right:.0%} of {len(segs)} segments",
              f"* wrong player blamed: {len(wrong)} segments"
              + (": " + ", ".join(f"{s['segment']} -> {'+'.join(s['wrong_blamed'])}" for s in wrong[:6]) if wrong else ""),
              f"* someone out while everyone froze: {len(false_catch)} segments\n"]

    passed = bool(not wrong and right >= 0.9 and (np.isnan(sep) or sep >= 0.95))
    swapped = sorted(set(d["swapped"]))
    if cv_sessions is not None and swapped == [False, True]:
        acc = float((cv_sessions["mlp"] == y).mean())
        wrong_s = [s for s in segment_outcomes(cv_sessions["mlp"], y, d["segment"]) if s["wrong_blamed"]]
        lines += ["## 5. Players swapped zones between sessions (each session held out whole)\n",
                  f"Who-moved accuracy {acc:.1%}; wrong player blamed in {len(wrong_s)} segments. "
                  "If this is much worse than section 2, the model learned who moves, not where.\n"]
        passed = passed and not wrong_s
    else:
        lines += ["_Caution: no session with the players swapped (record_zones.py --swapped), so this "
                  "can't rule out the model recognising each person's way of moving instead of the zone._\n"]
    verdict = ("**PASS**: the boards see zone A and zone B differently enough to judge players separately. "
               "Next: repeat at the venue spot and with other people, then build per-player judging."
               if passed else
               "**NOT YET**: the signatures overlap too much for fair separate judging. Look at the bar chart: "
               "if no board favours a zone, move the zones closer to different receivers (or the receivers "
               "apart) and record again; if the boards differ but the model mixes them up, record more rounds.")
    lines += ["## Verdict\n", verdict, ""]
    return "\n".join(lines), passed


def plot(d: dict, cv: dict, path: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    conds = list(dict.fromkeys(d["cond"]))
    colors = {"a_moves": "#d95f02", "b_moves": "#1b9e77", "both_move": "#7570b3",
              "both_freeze": "#999999", "a_freeze": "#bbbbbb", "b_freeze": "#777777"}
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.2))

    ax = axes[0]  # signatures
    width = 0.8 / len(conds)
    for i, c in enumerate(conds):
        med = np.median(d["x"][d["cond"] == c][:, :, SIZE], axis=0)
        ax.bar(np.arange(len(d["boards"])) + i * width, med, width, label=c, color=colors.get(c))
    ax.set_xticks(np.arange(len(d["boards"])) + width * (len(conds) - 1) / 2)
    ax.set_xticklabels([f"RX {b}" for b in d["boards"]])
    ax.set_ylabel("motion (x empty room)")
    ax.set_title("Which receivers react to which zone")
    ax.legend(fontsize=8)

    ax = axes[1]  # timeline of the first session
    s0 = d["session"] == d["session"][0]
    t0 = d["t"][s0].min()
    for bi, b in enumerate(d["boards"]):
        ax.plot(d["t"][s0] - t0, d["x"][s0][:, bi, SIZE], ".", ms=2, label=f"RX {b}",
                color=["#222222", "#e7298a", "#1f78b4", "#a6761d"][bi % 4])  # unlike the band colours
    for seg in dict.fromkeys(d["segment"][s0]):
        m = d["segment"] == seg
        ax.axvspan(d["t"][m].min() - t0, d["t"][m].max() - t0, color=colors.get(d["cond"][m][0]), alpha=0.15)
    ax.set_yscale("log")
    ax.set_xlabel("seconds")
    ax.set_title(f"{d['session'][0]}: each board over time (bands = instruction)")
    ax.legend(fontsize=8, markerscale=4)

    ax = axes[2]  # location score
    for who, c in (("A", "a_moves"), ("B", "b_moves")):
        v = cv["location"][d["y"] == WHO.index(who)]
        ax.hist(v[~np.isnan(v)], bins=20, range=(0, 1), alpha=0.6, label=f"zone {who} moved", color=colors[c])
    ax.set_xlabel("location score (0 = A, 1 = B)")
    ax.set_title("Held-out location score")
    ax.legend(fontsize=8)

    fig.tight_layout()
    fig.savefig(path, dpi=130)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("recordings", nargs="+")
    parser.add_argument("--out", default="data/zones_report.md")
    args = parser.parse_args()

    d = load(args.recordings)
    if len(set(zip(d["session"], d["round"]))) < 2:
        raise SystemExit("Need at least 2 rounds to hold one out.")
    cv = cross_validate(d)
    many = len(set(d["session"])) > 1
    text, _ = report(d, cv, cross_validate(d, held_out_sessions) if many else None)
    print(text)
    with open(args.out, "w") as f:
        f.write(text)
    png = os.path.splitext(args.out)[0] + ".png"
    plot(d, cv, png)
    print(f"saved {args.out} and {png}")


if __name__ == "__main__":
    main()
