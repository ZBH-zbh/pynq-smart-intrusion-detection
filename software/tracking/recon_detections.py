"""Reconnaissance: what boxes does the detector actually produce on the
synthetic sequence? Needed before writing assertions for the tracker test.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEPLOY = os.path.join(HERE, "..", "pynq_deploy")
sys.path.insert(0, os.path.abspath(DEPLOY))
sys.path.insert(0, HERE)

import cv2
import numpy as np

from benchmark_motion import generate_synthetic_frames
from opencv_software_motion import SoftwareMotionDetector
from motion_common import HEIGHT, WIDTH

FPS = 15.0


def ground_truth(index):
    """Ground-truth boxes for the synthetic generator, straight from the source."""
    out = {}
    x = 15 + (index * 8) % 230
    y = 65 + int(12 * np.sin(index / 13.0))
    out["big"] = (x, y, 55, 70)

    if 55 <= index % 120 < 100:
        x2 = 250 - ((index - 55) * 5) % 190
        out["small"] = (x2, 150, 35, 55)
    return out


def main():
    frames = generate_synthetic_frames(140)
    detector = SoftwareMotionDetector()

    print("frame | ground truth                    | detected")
    print("-" * 78)
    for i, frame in enumerate(frames):
        mask, targets, alarm = detector.process(frame)
        gt = ground_truth(i)
        gt_s = " ".join(f"{k}={v}" for k, v in gt.items())
        print(f"{i:5d} | {gt_s:<31} | {targets}")

    print()
    print("箱数分布：")
    counts = {}
    for frame in frames:
        _, targets, _ = detector.process(frame)
        counts[len(targets)] = counts.get(len(targets), 0) + 1
    print(" ", counts)


if __name__ == "__main__":
    main()
