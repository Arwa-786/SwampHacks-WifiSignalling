"""Experiment 2: is there a breathing RHYTHM in the CSI that goes away during a
breath-hold? (Exploratory, offline; follows the review of breath_ratio_test.py.)

Fixes over breath_ratio_test.py:
  - Each window is filtered on its own (detrend + Hann + FFT inside the window),
    so breathing just before or after a hold can't leak into a hold window
    (filtfilt over the whole recording runs backwards and forwards in time).
  - Scores RHYTHM, not wobble size:
      peak  = how much of a signal's 0.05-2 Hz power sits in its strongest
              0.2-0.6 Hz peak (+-1 bin); sway is broad and slow, breathing is a peak
      agree = how many of the 10 most rhythmic signals put their peak at the same
              rate (within 0.05 Hz = 3 breaths/min of their median)
      score = median peak of those 10 signals x agree
  - Every board is reported on its own (no "loudest board wins").
  - Reports AUC instead of a threshold: the chance a random window of A scores
    above a random window of B (0.5 = can't tell apart, 1.0 = perfect), so
    session-to-session scale differences don't matter.

Signals: amplitude |H| per subcarrier, and complex ratios H_k / H_ref inside each
packet (phase + amplitude), H_ref = strongest subcarrier in the empty room.
This is still NOT a reproduction of SA-WiSense (arXiv 2507.17623), which also
selects subcarrier pairs and reconstructs the waveform.

    python breath_rhythm_test.py

Result (2026-10-18, first run; AUC per board, breath_03 = the hold session):
              tune 01/02 breath>empty   03 breath>empty   03 BREATH>HOLD
  amp   64dc        0.41 / 0.56               0.51              0.67
        9294        0.36 / 0.65               0.47              0.27
        fb28        0.65 / 0.70               0.54              0.24
  ratio 64dc        0.78 / 0.73               0.60              0.66
        9294        0.45 / 0.74               0.77              0.87
        fb28        0.70 / 0.74               0.45              0.54
  - Rhythm from amplitude: about chance. Rhythm from ratios: above chance on
    5 of 6 tuning results, so the ratio signal carries some breathing rhythm.
  - Breath vs hold: the board chosen IN ADVANCE from the tuning sessions (ratio,
    64dc, best mean on 01/02) gives 0.66 (weak). 9294 gives 0.87, but picking it
    after seeing breath_03 is post hoc, so it is a lead, not a result.
  - The ~20 hold windows come from only 3 holds (overlapping windows), so the
    real sample is 3 holds. Needs more hold sessions before any claim.

Pre-registered check on a NEW session (breath_04: no curtain, sitting, 4 holds,
TX 1 m behind), test = ratio, rx 9294, breath vs hold:  0.35  -> FAILED.
  All boards on breath_04, BREATH>HOLD: amp 0.53 / 0.18 / 0.37, ratio 0.49 / 0.35 / 0.77
  (64dc / 9294 / fb28). The best board moved from 9294 to fb28, which is what
  chance looks like. The old breathing model again said "breathing" in 100% of holds.
  Conclusion: with this setup we have no evidence the CSI separates breathing from
  breath-holding. Next ideas: boards much closer to the chest, lying down, metal
  reflector under the TX (Gu et al.), longer holds, phone-checked holds.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import detrend

from breath_ratio_test import BOARDS, EMPTY, FS, load, to_grid

WIN_S, STEP_S = 12.0, 1.0   # 12 s = 2.4 breaths at 12/min; an 18 s hold fits ~6 windows
TOP = 10


def window_scores(g: np.ndarray) -> float:
    n = len(g)
    x = detrend(g, axis=0) * np.hanning(n)[:, None]
    nfft = 8 * n
    spec = np.abs(np.fft.rfft(x, nfft, axis=0)) ** 2
    f = np.fft.rfftfreq(nfft, 1 / FS)
    total = spec[(f >= 0.05) & (f <= 2.0)].sum(0) + 1e-12
    band = (f >= 0.2) & (f <= 0.6)
    fb = f[band]; sb = spec[band]
    k = sb.argmax(0)
    lo, hi = np.clip(k - 8, 0, len(fb) - 1), np.clip(k + 8, 0, len(fb) - 1)  # +-1 natural bin (nfft = 8n)
    peak = np.array([sb[lo[j]:hi[j] + 1, j].sum() for j in range(sb.shape[1])]) / total
    top = np.argsort(peak)[-TOP:]
    fpk = fb[k[top]]
    agree = np.mean(np.abs(fpk - np.median(fpk)) <= 0.05)
    return float(np.median(peak[top]) * agree)


def board_windows(name: str, board: str, kind: str):
    t, h, lab = load(f"data/{name}_rx{board}.log")
    em = np.isin(lab, EMPTY)
    if kind == "amp":
        x, tt = np.abs(h), t
    else:
        ref = int(np.argmax(np.abs(h[em]).mean(0)))
        keep = np.abs(h[:, ref]) > 0.2 * np.abs(h[em, ref]).mean()
        r = np.delete(h[keep] / h[keep, ref][:, None], ref, axis=1)
        x, tt = np.hstack([np.unwrap(np.angle(r), axis=0), np.abs(r)]), t[keep]
    grid = np.arange(t[0], t[-1], 1 / FS)
    g = to_grid(tt, x, grid)
    glab = lab[np.clip(np.searchsorted(t, grid), 0, len(t) - 1)]
    w = int(WIN_S * FS); s = int(STEP_S * FS)
    scores, labels = [], []
    for i in range(0, len(grid) - w, s):
        u = set(glab[i:i + w])
        if len(u) != 1:
            continue  # only windows entirely inside one part of the script
        scores.append(window_scores(g[i:i + w])); labels.append(u.pop())
    return np.array(scores), np.array(labels)


def auc(a: np.ndarray, b: np.ndarray) -> float:
    """P(score of a random A window > score of a random B window), ties count half."""
    if not len(a) or not len(b):
        return float("nan")
    return float(((a[:, None] > b[None, :]).mean() + 0.5 * (a[:, None] == b[None, :]).mean()))


def test_session(name: str) -> None:
    """Breath vs hold on one hold session, every board and signal."""
    print(f"\n== {name}: breathing vs hold (pre-registered: ratio, rx 9294)")
    for kind in ("amp", "ratio"):
        for b in BOARDS:
            sc, lab = board_windows(name, b, kind)
            nat, hold, emp = sc[lab == "breath_natural"], sc[lab == "hold"], sc[np.isin(lab, EMPTY)]
            print(f"  {kind:5s} rx {b}: BREATH>HOLD {auc(nat, hold):.2f}, breath>empty {auc(nat, emp):.2f} "
                  f"(windows: breathing {len(nat)}, hold {len(hold)}, empty {len(emp)})")


def main():
    import sys
    if len(sys.argv) > 1:  # python breath_rhythm_test.py breath_04
        for name in sys.argv[1:]:
            test_session(name)
        return
    print(f"windows {WIN_S:.0f} s; AUC 0.5 = can't tell apart, 1.0 = breathing always scores higher")
    for kind in ("amp", "ratio"):
        print(f"\n== {kind}")
        for b in BOARDS:
            line = []
            for name in ("breath_01", "breath_02"):
                sc, lab = board_windows(name, b, kind)
                br = sc[np.char.startswith(lab.astype(str), "breath_")]
                line.append(f"{name[-2:]} breath>empty {auc(br, sc[np.isin(lab, EMPTY)]):.2f}")
            sc, lab = board_windows("breath_03", b, kind)
            nat, hold, emp = sc[lab == "breath_natural"], sc[lab == "hold"], sc[np.isin(lab, EMPTY)]
            print(f"  rx {b}: " + ", ".join(line) +
                  f" | 03 breath>empty {auc(nat, emp):.2f}, BREATH>HOLD {auc(nat, hold):.2f} "
                  f"(windows: breathing {len(nat)}, hold {len(hold)}, empty {len(emp)}; "
                  f"median score {np.median(nat):.3f} vs hold {np.median(hold):.3f})")


if __name__ == "__main__":
    main()
