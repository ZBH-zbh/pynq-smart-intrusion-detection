"""Measure contour fill ratios, to set min_fill_ratio from data.

min_fill_ratio was first guessed at 0.35 and it rejected legitimate solid
objects (recall dropped to 0.68 on a 40x60 object). This prints the actual
distribution for solid objects of several sizes and speeds, and for the
sprawling stripe pattern a moving curtain produces.
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
from test_bg_detector import fragmented_scene, scene  # noqa: E402


def fill_ratios(detector_factory, frames, warmup=12):
    detector = detector_factory()
    ratios = []
    for i, frame in enumerate(frames):
        if i < warmup:
            detector.process(frame)
            continue
        detector.process(frame)
        mask = detector._last_mask
        contours, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < 100:
                continue
            x, y, w, h = cv2.boundingRect(contour)
            if w * h:
                ratios.append((area / float(w * h), area, (x, y, w, h)))
    return ratios


def report(label, ratios):
    if not ratios:
        print(f"{label:<34} no contours")
        return
    values = np.array([r[0] for r in ratios])
    print(f"{label:<34} n={len(values):4d}  "
          f"min {values.min():5.2f}  p05 {np.percentile(values, 5):5.2f}  "
          f"p50 {np.percentile(values, 50):5.2f}  max {values.max():5.2f}")
    # The lowest-fill contours are what a threshold has to reject.
    for ratio, area, box in sorted(ratios)[:3]:
        print(f"      lowest: fill {ratio:.2f} area {area:.0f} box {box}")


def with_mask_capture():
    """BackgroundDetector does not keep the raw mask; patch it in for measuring."""
    original = BackgroundDetector._post_process

    def wrapped(self, binary):
        mask, boxes = original(self, binary)
        self._last_mask = mask
        return mask, boxes

    BackgroundDetector._post_process = wrapped


def main():
    with_mask_capture()

    for obj_w, obj_h, speed in ((56, 71, 8), (30, 38, 6), (16, 20, 4),
                                (40, 60, 6)):
        frames, _ = scene(60, 12, obj_w, obj_h, speed)
        report(f"solid {obj_w}x{obj_h} @ {speed}px/f",
               fill_ratios(BackgroundDetector, frames))

    frames, _ = fragmented_scene(60, 12, 40, 120, 4, (0.42, 0.58))
    report("fragmented 40x120 (gap 19px)",
           fill_ratios(BackgroundDetector, frames))

    # Sprawling stripes: what a moving curtain looks like.
    background = np.zeros((240, 320, 3), np.uint8)
    background[:, :, :] = 80
    stripe_frames = [background.copy()]
    for i in range(40):
        frame = background.copy()
        offset = i % 8
        for x in range(-8 + offset, 320, 12):
            cv2.line(frame, (x, 0), (x, 239), (130, 130, 130), 3)
        stripe_frames.append(frame)
    report("sprawling stripes (curtain)",
           fill_ratios(BackgroundDetector, stripe_frames, warmup=1))


if __name__ == "__main__":
    main()
