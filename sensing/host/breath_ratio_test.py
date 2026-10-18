"""Experiment: does a breathing-speed wobble in the CSI disappear when the person
holds their breath? (Exploratory, offline; the live server is not changed.)

Our breathing model failed the breath-hold test (breath_03): it said "breathing"
during holds as often as during breathing, because "breathing person vs empty
room" can be told apart by presence alone. Here we look only at the WOBBLE at
breathing speed, never at presence, and compare three signals:

  amp    |H| per subcarrier (roughly what the current model sees)
  ratio  H_k / H_ref within the same packet, phase and amplitude
         (SA-WiSense, arXiv 2507.17623: on a single-antenna ESP32 every
          subcarrier of a packet shares the same phase errors from the radio's
          clock and timing, so dividing by a reference subcarrier cancels most
          of them; what's left includes the body's tiny path changes)

Per board and signal: resample to 20 Hz, band-pass 0.2-0.6 Hz (12-36 breaths
a minute), then in 8 s windows take each signal's spread divided by its spread
in the empty room. Score = median of the 10 most-wobbling signals (the subcarriers
that happen to see the chest best change with the setup, so we pick per window).

Tuning: the threshold is set on breath_01 + breath_02 (no holds) so that at most
5% of empty-room windows are called breathing. breath_03 (with holds) is used
only for the test.

    python breath_ratio_test.py

Result (2026-10-18, first run): NO clear breathing signal. In breath_03 the
breathing-speed wobble during holds was almost as large as during breathing
(median score: amp 4.4 hold vs 5.2 breathing; ratio 5.7 vs 6.2), and lower right
after the holds than during them, so this wobble is mostly sway/noise, not the
chest. Thresholds tuned on breath_01/02 did not carry over to breath_03 either
(scores differ a lot between sessions). One hold session only: a first look.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import butter, filtfilt

from csi_io import VALID_SUBCARRIERS, parse_line

FS = 20.0
WIN_S, STEP_S = 8.0, 1.0
BAND = (0.2, 0.6)
TOP = 10
BOARDS = ("64dc", "9294", "fb28")
EMPTY = ("empty", "empty_again", "empty_end")


def load(path: str):
    t, h, lab = [], [], []
    for row in open(path):
        if row.startswith("#"):
            continue
        p = row.rstrip("\n").split("\t")
        if len(p) != 3:
            continue
        f = parse_line(p[1], host_time=float(p[0]))
        if f is None:
            continue
        t.append(f.host_time); h.append(f.csi[VALID_SUBCARRIERS]); lab.append(p[2])
    return np.array(t), np.array(h), np.array(lab)


def to_grid(t, x, grid):
    """Average packets into 50 ms bins on `grid`, fill empty bins by interpolation."""
    idx = np.clip(np.searchsorted(grid, t) - 1, 0, len(grid) - 1)
    out = np.zeros((len(grid), x.shape[1])); n = np.bincount(idx, minlength=len(grid))
    np.add.at(out, idx, x)
    ok = n > 0
    out[ok] /= n[ok, None]
    for k in range(x.shape[1]):
        out[~ok, k] = np.interp(grid[~ok], grid[ok], out[ok, k])
    return out


def signals(t, h, empty_mask, kind):
    if kind == "amp":
        return np.abs(h)
    ref = int(np.argmax(np.abs(h[empty_mask]).mean(0)))  # strongest subcarrier in the empty room
    keep = np.abs(h[:, ref]) > 0.2 * np.abs(h[empty_mask, ref]).mean()  # weak denominator -> skip packet
    r = h[keep] / h[keep, ref][:, None]
    r = np.delete(r, ref, axis=1)
    return np.hstack([np.unwrap(np.angle(r), axis=0), np.abs(r)]), keep


def session_scores(name: str, kind: str):
    """Window score per board, on one shared time grid -> (window end times, scores (boards, n), labels)."""
    per_board, labels_out = [], None
    data = [load(f"data/{name}_rx{b}.log") for b in BOARDS]
    start = max(d[0][0] for d in data); end = min(d[0][-1] for d in data)
    grid = np.arange(start, end, 1 / FS)
    ends = np.arange(start + WIN_S, end, STEP_S)
    bpb, bpa = butter(2, [BAND[0] / (FS / 2), BAND[1] / (FS / 2)], "band")
    for t, h, lab in data:
        em = np.isin(lab, EMPTY)
        if kind == "amp":
            x, tt = signals(t, h, em, kind), t
        else:
            x, keep = signals(t, h, em, kind); tt = t[keep]
        g = filtfilt(bpb, bpa, to_grid(tt, x, grid), axis=0)
        # label of each grid sample (nearest packet), and window label only if uniform
        glab = lab[np.clip(np.searchsorted(t, grid), 0, len(t) - 1)]
        spreads, wl = [], []
        for e in ends:
            m = (grid > e - WIN_S) & (grid <= e)
            spreads.append(g[m].std(0))
            u = set(glab[m]); wl.append(u.pop() if len(u) == 1 else "")
        spreads = np.array(spreads); wl = np.array(wl)
        # each signal's typical wobble in the FIRST empty part (like the live room scan)
        base = np.median(spreads[wl == "empty"], axis=0) + 1e-9
        rel = np.sort(spreads / base, axis=1)[:, -TOP:]
        per_board.append(np.median(rel, axis=1))
        labels_out = wl if labels_out is None else np.where(labels_out == wl, wl, "")
    return ends, np.array(per_board), labels_out


def main():
    for kind in ("amp", "ratio"):
        tune = [session_scores(s, kind) for s in ("breath_01", "breath_02")]
        test = session_scores("breath_03", kind)
        # combine boards: the strongest board's score (whichever sees the chest best)
        comb = lambda sc: sc.max(0)
        emp = np.concatenate([comb(sc)[np.isin(lab, EMPTY)] for _, sc, lab in tune])
        brt = np.concatenate([comb(sc)[np.char.startswith(lab.astype(str), "breath_")] for _, sc, lab in tune])
        thr = np.quantile(emp, 0.95)
        print(f"\n== {kind}: threshold {thr:.2f} (95th pct of empty, breath_01+02)")
        print(f"   tune  breathing called breathing {np.mean(brt > thr):.0%}, empty {np.mean(emp > thr):.0%}")
        _, sc, lab = test
        s = comb(sc)
        for name, m in [("empty", np.isin(lab, ("empty",))), ("breathing", lab == "breath_natural"),
                        ("HOLD", lab == "hold"), ("after hold", lab == "breath_after_hold"),
                        ("empty_end", lab == "empty_end")]:
            if m.any():
                print(f"   test  {name:10s} windows {m.sum():3d}  median score {np.median(s[m]):5.2f}  "
                      f"called breathing {np.mean(s[m] > thr):4.0%}")


if __name__ == "__main__":
    main()
