"""Record Wi-Fi CSI with labels from the webcam ("the camera teaches the Wi-Fi").

    conda activate wifind            # Python 3.12 env with mediapipe + opencv
    python record_labeled.py data/session_01.log --minutes 3

While the RX board streams CSI, the laptop webcam watches the hiding spot and
Google's MediaPipe Pose model tracks 33 body points. Every camera frame becomes
a label:
    empty    no person in view
    moving   body points move faster than --moving body-heights per second
    still    slower than --still (breathing is fine)
    unsure   in between, or tracking lost -> left out of training
Each CSI line is saved with the label of the latest camera frame, in the same
format diagnose.py writes, so train_model.py can use both. No video or images
are saved, only the labels.

Point the camera at the space between the boards. Mix it up during a session:
leave, come back, sit frozen, move slowly, move a lot, walk past.
Press q in the preview window to stop early.
"""

from __future__ import annotations

import argparse
import os
import threading
import time
import urllib.request
from collections import deque

import numpy as np

from csi_io import iter_lines

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
    "pose_landmarker_lite/float16/latest/pose_landmarker_lite.task"
)
MODEL_PATH = os.path.join(os.path.dirname(__file__), "models", "pose_landmarker_lite.task")

# Body points used for motion: shoulders, elbows, wrists, hips, knees, nose.
TRACKED = [0, 11, 12, 13, 14, 15, 16, 23, 24, 25, 26]
MIN_VISIBILITY = 0.5
SPEED_WINDOW_S = 0.3  # smooth keypoint speed over this long
STALE_LABEL_S = 0.5  # CSI lines with no camera label this recent are "unsure"


def ensure_model() -> str:
    if not os.path.exists(MODEL_PATH):
        os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
        print(f"downloading pose model (~6 MB) to {MODEL_PATH}")
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
    return MODEL_PATH


class MotionLabeler:
    """Turns pose landmarks over time into empty / still / moving / unsure."""

    def __init__(self, moving: float, still: float):
        self.moving, self.still = moving, still
        self.history: deque[tuple[float, np.ndarray]] = deque()

    def update(self, t: float, landmarks) -> tuple[str, float]:
        if not landmarks:
            self.history.clear()
            return "empty", 0.0
        pts = np.array([[lm.x, lm.y] for lm in landmarks])
        vis = np.array([getattr(lm, "visibility", 1.0) or 0.0 for lm in landmarks])
        use = [i for i in TRACKED if vis[i] >= MIN_VISIBILITY]
        if len(use) < 4:
            self.history.clear()
            return "unsure", 0.0
        # Body height in image units: nose to the lower of the hips/knees seen.
        ys = pts[use, 1]
        body = max(ys.max() - ys.min(), 0.05)
        self.history.append((t, pts))
        while self.history and t - self.history[0][0] > SPEED_WINDOW_S:
            self.history.popleft()
        if len(self.history) < 2:
            return "unsure", 0.0
        (t0, p0), (t1, p1) = self.history[0], self.history[-1]
        speed = float(np.median(np.linalg.norm(p1[use] - p0[use], axis=1)) / body / max(t1 - t0, 1e-3))
        if speed >= self.moving:
            return "moving", speed
        if speed <= self.still:
            return "still", speed
        return "unsure", speed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output")
    parser.add_argument("--port", help="RX board serial port (default: auto-detect)")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--minutes", type=float, default=3)
    parser.add_argument("--moving", type=float, default=0.15, help="body-heights/s counted as moving")
    parser.add_argument("--still", type=float, default=0.04, help="body-heights/s counted as still")
    parser.add_argument("--no-preview", action="store_true")
    args = parser.parse_args()

    import cv2
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions
    from mediapipe.tasks.python.vision import PoseLandmarker, PoseLandmarkerOptions, RunningMode

    landmarker = PoseLandmarker.create_from_options(
        PoseLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=ensure_model()),
            running_mode=RunningMode.VIDEO,
            num_poses=1,
        )
    )
    cam = cv2.VideoCapture(args.camera)
    if not cam.isOpened():
        raise SystemExit(
            "Can't open the webcam. On macOS: System Settings > Privacy & Security > Camera, "
            "allow your terminal app, then run again."
        )

    # Camera labels as (time, label); the serial thread looks up the latest.
    labels: deque[tuple[float, str]] = deque(maxlen=1000)
    rows: list[str] = []
    counts: dict[str, int] = {}
    stop = threading.Event()
    lock = threading.Lock()

    def read_serial():
        for t, line in iter_lines(args.port):
            if stop.is_set():
                return
            if not line.startswith("CSI,"):
                continue
            with lock:
                label = labels[-1][1] if labels and t - labels[-1][0] <= STALE_LABEL_S else "unsure"
            rows.append(f"{t:.4f}\t{line.rstrip()}\t{label}")
            counts[label] = counts.get(label, 0) + 1

    reader = threading.Thread(target=read_serial, daemon=True)
    reader.start()

    labeler = MotionLabeler(args.moving, args.still)
    colors = {"moving": (0, 0, 255), "still": (0, 200, 255), "empty": (200, 200, 200), "unsure": (128, 128, 128)}
    start = time.monotonic()
    print(f"Recording for {args.minutes:g} min. Press q in the preview window to stop.")
    try:
        while time.monotonic() - start < args.minutes * 60 and reader.is_alive():
            ok, frame = cam.read()
            if not ok:
                continue
            t = time.monotonic()
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            result = landmarker.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), int(t * 1000))
            landmarks = result.pose_landmarks[0] if result.pose_landmarks else None
            label, speed = labeler.update(t, landmarks)
            with lock:
                labels.append((t, label))
            if not args.no_preview:
                h, w = frame.shape[:2]
                for lm in landmarks or []:
                    cv2.circle(frame, (int(lm.x * w), int(lm.y * h)), 3, colors[label], -1)
                cv2.putText(frame, f"{label.upper()}  speed {speed:.2f}", (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.9, colors[label], 2)
                cv2.putText(frame, f"CSI lines {len(rows)}  left {args.minutes * 60 - (t - start):.0f}s", (10, 60),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
                cv2.imshow("WiFind teacher (q to stop)", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
    finally:
        stop.set()
        cam.release()
        cv2.destroyAllWindows()

    if not rows:
        raise SystemExit("No CSI received. Is the RX board plugged in? Run check_link.py.")
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w") as f:
        f.write(f"# record_labeled {time.strftime('%Y-%m-%d %H:%M:%S')} moving>={args.moving} still<={args.still}\n")
        f.write("\n".join(rows) + "\n")
    print(f"saved {len(rows)} labelled CSI lines to {args.output}: {counts}")
    if counts.get("empty", 0) < 500:
        print("Warning: little EMPTY data; training needs some (leave the camera view for 10+ s).")


if __name__ == "__main__":
    main()
