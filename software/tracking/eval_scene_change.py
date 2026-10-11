"""How fast does the background converge when the scene changes permanently?

Diagnostic for a live observation: after the camera was moved, the board kept
reporting ~15 targets even though consecutive frames differed in only 0.2% of
pixels. That means the background had not caught up with the new view.

The conditional update is supposed to absorb a permanent change at
``alpha_foreground``. This measures how long that actually takes, and prints the
mean absolute difference between the current frame and the background so the
convergence itself is visible rather than inferred.
"""
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DEPLOY = os.path.abspath(os.path.join(HERE, "..", "pynq_deploy"))
sys.path.insert(0, DEPLOY)
sys.path.insert(0, HERE)

from bg_detector import BackgroundDetector  # noqa: E402
from motion_common import HEIGHT, WIDTH  # noqa: E402


def view_a(seed=1):
    rng = np.random.default_rng(seed)
    frame = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    frame[:, :, 0] = np.linspace(20, 60, WIDTH, dtype=np.uint8)[None, :]
    frame[:, :, 1] = np.linspace(30, 90, WIDTH, dtype=np.uint8)[None, :]
    frame[:, :, 2] = np.linspace(40, 120, WIDTH, dtype=np.uint8)[None, :]
    cv2.rectangle(frame, (200, 20), (300, 120), (200, 200, 200), -1)
    return np.clip(
        frame.astype(np.int16) + rng.integers(-1, 2, frame.shape), 0, 255
    ).astype(np.uint8)


def view_b():
    """A different scene: the camera has been moved to look elsewhere."""
    frame = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    frame[:, :, 0] = np.linspace(90, 30, WIDTH, dtype=np.uint8)[None, :]
    frame[:, :, 1] = np.linspace(110, 40, WIDTH, dtype=np.uint8)[None, :]
    frame[:, :, 2] = np.linspace(130, 50, WIDTH, dtype=np.uint8)[None, :]
    cv2.rectangle(frame, (40, 90), (150, 210), (60, 60, 60), -1)
    return frame


def main():
    a, b = view_a(), view_b()

    for label, options in (
        ("alpha_fg=0.002 (slow)", dict(alpha_foreground=0.002)),
        ("alpha_fg=0.01 (shipped)", dict(alpha_foreground=0.01)),
        ("alpha_fg=0.03", dict(alpha_foreground=0.03)),
    ):
        detector = BackgroundDetector(**options)
        for _ in range(40):                     # learn view A
            detector.process(a)

        counts = []
        diffs = []
        for i in range(1500):
            mask, boxes = detector.process(b)
            counts.append(len(boxes))
            if i % 100 == 0:
                gray = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
                diffs.append((i, float(np.abs(
                    gray.astype(np.int16) - detector.background.astype(np.int16)
                ).mean())))

        print(f"\n=== {label} ===")
        print("  frame : " + " ".join(f"{i:5d}" for i, _ in diffs))
        print("  mean|cur-bg| : "
              + " ".join(f"{d:5.1f}" for _, d in diffs))
        for probe in (50, 200, 500, 1000, 1499):
            print(f"  boxes at frame {probe:4d}: {counts[probe]}")


if __name__ == "__main__":
    main()
