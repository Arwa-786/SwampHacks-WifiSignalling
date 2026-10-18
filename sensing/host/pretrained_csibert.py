"""Head-to-head: pretrained CSI-BERT2 (Hugging Face) vs our own Wi-Fi model.

    .venv-ml/bin/python pretrained_csibert.py data/session_*_rx*.log

CSI-BERT2 (RS2002/CSI-BERT2, Apache-2.0; Zhao et al., IEEE TMC 2025) is a
small transformer pretrained on ESP32 CSI (WiGesture, WiFall, WiCount): the
closest thing to "YOLO for ESP32 Wi-Fi". Used the YOLO way: the pretrained
network turns 1 s of one board's Wi-Fi into a 128-number summary (embedding),
and only a small head is trained on our webcam-labelled sessions.

Inputs are prepared exactly like the authors' code (RS2002/CSI-BERT,
WiGesture/data_process_example): 100 packets x 52 raw magnitudes, no
normalisation. Their 52 values come from the raw int8 buffer as
real = x[2i], imag = x[2i-1] for i = 0..51 (this pairs neighbouring
subcarriers; we reproduce it because that is what the model was trained on).

Each moment is the same moment our model is scored on (features.fused_dataset_with_times),
and the same leave-one-session-out protocol and scores as evaluate.py.
Embeddings are cached in data/csibert_cache_<session>.npz.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

from csi_io import parse_line
from evaluate import scores
from features import fused_dataset_with_times
from train_model import TinyMLP, group_boards, hybrid_predict, preprocess

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(HERE, "models", "csi_bert2")
SEQ = 100  # packets per window, the model's max_len


def load_raw(path: str) -> tuple[np.ndarray, np.ndarray]:
    """(host times, 52 magnitudes per packet the authors' way) for one board's log."""
    times, mags = [], []
    with open(path) as f:
        for row in f:
            if row.startswith("#"):
                continue
            parts = row.rstrip("\n").split("\t")
            if len(parts) != 3:
                continue
            frame = parse_line(parts[1], host_time=float(parts[0]))
            if frame is None:
                continue
            # our csi[k] = x[2k+1] + j*x[2k]  ->  x[2i] = csi[i].imag, x[2i-1] = csi[i-1].real
            real_part = frame.csi.imag[:52]
            imag_part = np.roll(frame.csi.real, 1)[:52]  # i = 0 wraps to x[-1] like Python indexing
            times.append(frame.host_time)
            mags.append(np.sqrt(real_part**2 + imag_part**2))
    return np.array(times), np.array(mags, dtype=np.float32)


def load_model():
    import torch

    sys.path.insert(0, MODEL_DIR)
    from model import CSI_BERT2  # the authors' model.py, downloaded from Hugging Face

    with open(os.path.join(MODEL_DIR, "config.json")) as f:
        cfg = json.load(f)
    model = CSI_BERT2(**cfg)
    from safetensors.torch import load_file

    missing, unexpected = model.load_state_dict(load_file(os.path.join(MODEL_DIR, "model.safetensors")), strict=False)
    if missing or unexpected:
        print(f"warning: {len(missing)} missing / {len(unexpected)} unexpected weights")
    model.eval()
    torch.set_num_threads(4)
    return model


def embed_session(model, files: list[str], moments: np.ndarray, batch: int = 64) -> np.ndarray:
    """(moments, boards * 256): CSI-BERT2 hidden states of the last SEQ packets
    of every board at every moment, pooled over time as [mean, std]."""
    import torch

    out = []
    for path in files:
        times, mags = load_raw(path)
        ends = np.searchsorted(times, moments, side="right")
        ends = np.clip(ends, SEQ, len(times))
        board = []
        for i in range(0, len(ends), batch):
            idx = np.stack([np.arange(e - SEQ, e) for e in ends[i : i + batch]])
            x = torch.tensor(mags[idx])
            ts = torch.tensor(times[idx] - times[idx][:, :1], dtype=torch.float32)
            ts[:, -1] = torch.clamp(ts[:, -1], min=1e-3)  # time_embedding divides by the span
            with torch.no_grad():
                h = model(x, ts, torch.ones(x.shape[:2]))
            # mean AND spread over time: movement is change within the second,
            # which a mean alone averages away
            board.append(torch.cat([h.mean(dim=1), h.std(dim=1)], dim=1).numpy())
        out.append(np.vstack(board))
    return np.hstack(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("recordings", nargs="+")
    args = parser.parse_args()

    model = None
    sessions = []
    for name, files in group_boards(args.recordings):
        t, x, y = fused_dataset_with_times(files)
        cache = os.path.join(os.path.dirname(name) or ".", f"csibert_cache_{os.path.basename(name)}.npz")
        if os.path.exists(cache) and np.load(cache)["emb"].shape == (len(t), 256 * len(files)):
            emb = np.load(cache)["emb"]
        else:
            model = model or load_model()
            print(f"embedding {name}: {len(t)} moments x {len(files)} boards ...", flush=True)
            emb = embed_session(model, files, t)
            np.savez(cache, emb=emb)
        # Same advantage our features get from the room scan: express each
        # embedding relative to this session's empty-room moments.
        empty = y == 0
        emb = emb - emb[empty].mean(axis=0) if empty.any() else emb
        sessions.append({"name": name, "x": x, "emb": emb, "y": y})

    def run(make_inputs, prep, use_hybrid=False):
        """Leave-one-session-out with a fresh head each time."""
        ys, preds = [], []
        for i, s in enumerate(sessions):
            train = [o for j, o in enumerate(sessions) if j != i]
            tx = np.vstack([make_inputs(o) for o in train])
            ty = np.concatenate([o["y"] for o in train])
            head = TinyMLP(tx.shape[1]).fit(prep(tx), ty)
            if use_hybrid:
                preds.append(hybrid_predict(head, s["x"]))
            else:
                preds.append(head.predict_proba(prep(make_inputs(s))).argmax(axis=1))
            ys.append(s["y"])
        return scores(np.concatenate(ys), np.concatenate(preds))

    # embeddings go in as they are; our ratio features get their usual log1p
    bert = run(lambda s: s["emb"], prep=lambda a: a)
    ours = run(lambda s: s["x"], prep=preprocess, use_hybrid=True)

    print("\n| Model (each session held out) | accuracy | MOVING precision | MOVING recall | STILL recall | EMPTY recall |")
    print("|---|---|---|---|---|---|")
    for label, sc in (("our features + hybrid", ours), ("pretrained CSI-BERT2 + our head", bert)):
        print(f"| {label} | {sc['accuracy']:.1%} | {sc['MOVING']['precision']:.1%} | {sc['MOVING']['recall']:.1%} | "
              f"{sc['STILL']['recall']:.1%} | {sc['EMPTY']['recall']:.1%} |")


if __name__ == "__main__":
    main()
