"""Measure how much detection quality swings across different scenes.

The complaint is that moving the camera to a different place breaks tracking.
The detector's parameters are absolute -- threshold=30 grey levels, min_area=300
-- so they are only correct for the scene they were tuned on. This builds a
battery of scenes that vary in exactly the ways a different room would:

  * background structure: flat, a gradient, or textured clutter
  * object contrast: how much the object differs from the background
  * sensor noise: a quiet scene versus a dim, noisy one

and reports recall (does it find the object) and the false-positive rate on the
same scene with nothing moving. A detector that is "tuned" shows a huge spread
across this battery; one that adapts shows a flat row.
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

HEIGHT, WIDTH = 240, 320
WARMUP = 20
MOVING = 40
OBJECT_W, OBJECT_H = 40, 60
SPEED = 6


def make_background(kind, seed=7):
    rng = np.random.default_rng(seed)
    base = np.zeros((HEIGHT, WIDTH), dtype=np.uint8)
    if kind == "flat":
        base[:, :] = 110
    elif kind == "gradient":
        base[:, :] = np.linspace(60, 170, WIDTH, dtype=np.uint8)[None, :]
    elif kind == "textured":
        base[:, :] = np.linspace(60, 170, WIDTH, dtype=np.uint8)[None, :]
        for _ in range(40):
            x = int(rng.integers(0, WIDTH - 20))
            y = int(rng.integers(0, HEIGHT - 20))
            w = int(rng.integers(8, 40))
            h = int(rng.integers(8, 40))
            value = int(rng.integers(40, 200))
            base[y:y + h, x:x + w] = value
    else:
        raise ValueError(kind)
    return base


def make_scene(kind, contrast, noise, seed=7):
    """Returns (background_frames, moving_frames, truth).

    The object's contrast is applied RELATIVE to the local background, so the
    battery measures the threshold rather than camouflage. An earlier version
    gave the object a fixed grey value, which made it invisible wherever the
    background happened to be the same brightness -- a real limitation, but a
    different one, and it hid what the threshold was doing.
    """
    rng = np.random.default_rng(seed + hash((kind, contrast, noise)) % 1000)
    base = make_background(kind, seed)

    def noisy(gray):
        if noise <= 0:
            return gray
        values = rng.normal(0, noise, size=gray.shape)
        return np.clip(gray.astype(np.float32) + values, 0, 255).astype(np.uint8)

    still, moving, truth = [], [], []
    for _ in range(WARMUP):
        still.append(cv2.cvtColor(noisy(base), cv2.COLOR_GRAY2BGR))

    x = 10
    y = (HEIGHT - OBJECT_H) // 2
    for _ in range(MOVING):
        frame = base.copy()
        patch = frame[y:y + OBJECT_H, x:x + OBJECT_W].astype(np.int16)
        frame[y:y + OBJECT_H, x:x + OBJECT_W] = np.clip(
            patch + contrast, 0, 255
        ).astype(np.uint8)
        moving.append(cv2.cvtColor(noisy(frame), cv2.COLOR_GRAY2BGR))
        truth.append((x, y, OBJECT_W + 1, OBJECT_H + 1))
        x += SPEED
    return still, moving, truth


def iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = min(ax + aw, bx + bw) - max(ax, bx)
    iy = min(ay + ah, by + bh) - max(ay, by)
    if ix <= 0 or iy <= 0:
        return 0.0
    inter = float(ix * iy)
    return inter / (aw * ah + bw * bh - inter)


def evaluate(make_detector, kind, contrast, noise):
    still, moving, truth = make_scene(kind, contrast, noise)
    detector = make_detector()

    false_positives = 0
    for frame in still:
        _, boxes = detector.process(frame)
        false_positives += len(boxes)

    hits = 0
    for frame, gt in zip(moving, truth):
        _, boxes = detector.process(frame)
        if max((iou(b, gt) for b in boxes), default=0.0) >= 0.5:
            hits += 1
    return hits / len(truth), false_positives


def min_detectable_contrast(make_detector, kinds, contrasts, noise):
    """Smallest contrast whose recall is at least 0.8 in every background.

    This is the number that decides whether a room works: the same object has
    to differ from its background by at least this much before it can be seen
    at all. A detector with a fixed threshold reports one number for every
    room; a detector that adapts reports a number that tracks the room's noise.
    """
    for contrast in contrasts:
        recalls = [evaluate(make_detector, kind, contrast, noise)[0]
                   for kind in kinds]
        if all(r >= 0.8 for r in recalls):
            return contrast
    return None


if __name__ == "__main__":
    KINDS = ("flat", "gradient", "textured")
    CONTRASTS = (4, 6, 8, 12, 16, 24, 30, 40, 60, 90)
    NOISES = (0.0, 1.0, 2.0, 5.0, 10.0)

    fixed = lambda: BackgroundDetector(adaptive_threshold=False, threshold=30)
    adaptive = BackgroundDetector

    print("Minimum object contrast (grey levels) needed to detect it,")
    print("per scene noise level, identical across flat/gradient/textured:\n")
    print(f"  {'scene noise':>12} | {'fixed thr=30':>13} | {'adaptive':>9}")
    print("  " + "-" * 42)
    for noise in NOISES:
        f = min_detectable_contrast(fixed, KINDS, CONTRASTS, noise)
        a = min_detectable_contrast(adaptive, KINDS, CONTRASTS, noise)
        print(f"  {noise:>12.1f} | {str(f):>13} | {str(a):>9}")

    print("\nfalse positives on the still frames of every scene:")
    for name, factory in (("fixed thr=30", fixed), ("adaptive", adaptive)):
        total = 0
        for kind in KINDS:
            for contrast in (8, 30, 60):
                for noise in NOISES:
                    total += evaluate(factory, kind, contrast, noise)[1]
        print(f"  {name:<14} {total} boxes")
