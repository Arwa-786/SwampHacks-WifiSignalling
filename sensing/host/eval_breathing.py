"""Score Wi-Fi breathing-rate estimates against paced breathing.

    python eval_breathing.py data/breath_*_rx*.log        # prints + saves data/breathing_report.md

For every paced segment (record_breathing.py labels them breath_<rate>), we
slide a 30 s window every 5 s, estimate the rate on each board, and keep the
board with the clearest rhythm (highest confidence, see estimate_rate). The
error is measured against the pace the voice set.

The "empty" segment is the control: with nobody there, a good detector should
NOT report a confident breathing rhythm. "Breathing detected" means
confidence >= MIN_CONFIDENCE (breathing.py's threshold).
"""

from __future__ import annotations

import argparse
import os

import numpy as np

from breathing import MIN_CONFIDENCE
from features import load_session
from signal_processing import BREATHING_BAND, RateEstimate, estimate_rate
from phone_truth import clock_offset, load_phyphox, rate
from train_model import group_boards

WINDOW_S = 40.0  # Gu et al. 2019 (arXiv 1908.05108): "40 s of data is enough"; on our first
# session 40 s also beat 30 s for the board that tracks breathing (RX2: 0.9 vs 2.5 /min off at 10/min)
STEP_S = 5.0
SKIP_S = 5.0
PHONE_MIN_CLARITY = 0.4  # phone windows with a muddier rhythm than this aren't trusted as truth  # the start of a segment: the person is still syncing with the voice


def segments(labels: list[str], times: np.ndarray) -> list[tuple[str, float, float]]:
    """Contiguous runs of the same label -> (label, start time, end time)."""
    out, start = [], 0
    for i in range(1, len(labels) + 1):
        if i == len(labels) or labels[i] != labels[start]:
            out.append((labels[start], float(times[start]), float(times[i - 1])))
            start = i
    return out


def best_estimate(boards: list, t0: float, t1: float) -> RateEstimate | None:
    """Rate over [t0, t1] from the board with the clearest rhythm."""
    best = None
    for s in boards:
        m = (s.times >= t0) & (s.times < t1)
        if m.sum() < 20 * WINDOW_S:  # under ~20 packets/s: too many drops on this board
            continue
        e = estimate_rate(s.times[m], s.signal[m], BREATHING_BAND)
        if best is None or e.confidence > best.confidence:
            best = e
    return best


def board_rate(board, t0: float, t1: float) -> float:
    m = (board.times >= t0) & (board.times < t1)
    return estimate_rate(board.times[m], board.signal[m], BREATHING_BAND).per_minute if m.sum() >= 20 * (t1 - t0) else np.nan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("recordings", nargs="+")
    parser.add_argument("--out", default="data/breathing_report.md")
    parser.add_argument("--phone", help="phyphox export (.xlsx/.zip) from a phone on the belly: the true rate "
                                        "per window (needed for --natural sessions; overrides the paced rate)")
    args = parser.parse_args()
    phone = load_phyphox(args.phone) if args.phone else None

    rows, empty_conf = [], []
    for name, files in group_boards(args.recordings):
        boards = [load_session(f) for f in files]
        clock = clock_offset(files[0]) if phone else 0.0
        for label, start, end in segments(boards[0].labels, boards[0].times):
            if not (label.startswith("breath_") or label in ("empty", "empty_end")):
                continue
            ests, truths, per_board = [], [], []
            t = start + SKIP_S
            while t + WINDOW_S <= end:
                e = best_estimate(boards, t, t + WINDOW_S)
                true = (rate(*phone, t + clock, t + clock + WINDOW_S) if phone else (np.nan, 1.0))
                if e and (not phone or true[1] >= PHONE_MIN_CLARITY):  # skip windows the phone itself is unsure of
                    ests.append(e)
                    truths.append(true[0])
                    per_board.append([board_rate(b, t, t + WINDOW_S) for b in boards])
                t += STEP_S
            if not ests:
                continue
            if label in ("empty", "empty_end"):
                empty_conf += [e.confidence for e in ests]
                continue
            true = np.array(truths) if phone else np.full(len(ests), float(label.split("_")[1]))
            rates = np.array([e.per_minute for e in ests])
            conf = np.array([e.confidence for e in ests])
            board_err = np.nanmedian(np.abs(np.array(per_board) - true[:, None]), axis=0)
            rows.append((f"{os.path.basename(name)} {label}", float(np.median(true)), float(np.median(rates)),
                         float(np.median(np.abs(rates - true))), float(np.mean(np.abs(rates - true) <= 2)),
                         float(np.mean(conf >= MIN_CONFIDENCE)),
                         " / ".join(f"{os.path.basename(f).split('_rx')[-1][:-4]} {err:.1f}" for f, err in zip(files, board_err))))

    lines = ["# WiFind breathing evaluation", "",
             ("True rate = a phone on the belly (" + os.path.basename(args.phone) + ")" if phone
              else "True rate = the pace the laptop voice set.") + " Wi-Fi only; 40 s windows every 5 s.", "",
             "| Session | true / min | Wi-Fi median / min | median error | within ±2 / min | breathing detected "
             "| median error per board |",
             "|---|---|---|---|---|---|---|"]
    for sess, true, med, err, within, det, boards_txt in rows:
        lines.append(f"| {sess} | {true:.1f} | {med:.1f} | {err:.1f} | {within:.0%} | {det:.0%} | {boards_txt} |")
    if empty_conf:
        false_det = np.mean(np.array(empty_conf) >= MIN_CONFIDENCE)
        lines += ["", f"**Empty room (control):** breathing wrongly 'detected' in {false_det:.0%} of "
                  f"{len(empty_conf)} windows (median confidence {np.median(empty_conf):.2f}, "
                  f"threshold {MIN_CONFIDENCE})."]
    text = "\n".join(lines)
    print(text)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        f.write(text + "\n")
    print(f"\nsaved {args.out}")


if __name__ == "__main__":
    main()
