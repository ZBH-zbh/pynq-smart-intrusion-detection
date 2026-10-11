"""Compare detection modes as object size and speed vary.

Why
---
Two reported problems look separate but probably share one cause:

  1. a hand about half a metre from the camera is not detected at all
  2. a hand close to the camera is detected only in part, and the tracked part
     jumps between the top and the bottom of the hand, so the path looks erratic

Frame differencing reports *changed pixels*. A large fast object changes a lot
of pixels; a small or slow one changes few. Both problems are consistent with
"not enough changed pixels", but three different mechanisms could be responsible
and they need different fixes:

  * the grey difference never reaches THRESH=30
  * it does, but the 3x3 erosion in the PL chain eats a thin strip away
  * it survives erosion but the contour lands under MIN_CONTOUR_AREA=300

This script separates them by running the same sequences through several modes
and reporting recall/precision against the known object, plus how many boxes
each moving object is broken into.

Modes
-----
  current    previous-frame diff + erode + close/open + area floor  (the PL chain)
  no_erode   same, with the erosion removed
  thr15      same, with the difference threshold halved
  area100    same, with the contour area floor lowered
  bg_avg     running-average background + close/open + area floor
  bg_mog2    MOG2 background model + close/open + area floor

    python eval_detection_modes.py
"""
import math
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DEPLOY = os.path.abspath(os.path.join(HERE, "..", "pynq_deploy"))
sys.path.insert(0, DEPLOY)
sys.path.insert(0, HERE)

from motion_common import (  # noqa: E402
    HEIGHT,
    WIDTH,
    emulate_current_pl_erosion,
    process_mask,
)

THRESH = 30
MIN_AREA = 300
FRAMES = 70
IOU_HIT = 0.5


# --------------------------------------------------------------------------
# a parameterised version of the project's synthetic scene
# --------------------------------------------------------------------------

def make_sequence(count, obj_w, obj_h, speed, seed=11):
    """Static textured background plus one solid rectangle moving right.

    Returns (frames, truth_boxes) where truth is the object's box per frame.
    """
    rng = np.random.default_rng(seed)
    # A little structure so a background model has something to model, and so
    # the object's grey value differs from the background by about 120.
    background = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    background[:, :, 0] = np.linspace(20, 60, WIDTH, dtype=np.uint8)[None, :]
    background[:, :, 1] = np.linspace(30, 90, WIDTH, dtype=np.uint8)[None, :]
    background[:, :, 2] = np.linspace(40, 120, WIDTH, dtype=np.uint8)[None, :]
    cv2.rectangle(background, (240, 20), (300, 90), (150, 150, 150), -1)

    frames, truth = [], []
    x = 10.0
    y = (HEIGHT - obj_h) / 2.0
    for i in range(count):
        frame = background.copy()
        xi = int(round(x))
        yi = int(round(y))
        cv2.rectangle(
            frame, (xi, yi), (xi + obj_w, yi + obj_h), (40, 180, 240), -1
        )
        truth.append((xi, yi, obj_w + 1, obj_h + 1))
        noise = rng.integers(-1, 2, size=frame.shape, dtype=np.int16)
        frames.append(
            np.clip(frame.astype(np.int16) + noise, 0, 255).astype(np.uint8)
        )
        x += speed
        if x + obj_w >= WIDTH - 5:
            x = 10.0
    return frames, truth


# --------------------------------------------------------------------------
# detection modes
# --------------------------------------------------------------------------

def _boxes_from_binary(binary, erode, area_floor):
    mask = emulate_current_pl_erosion(binary) if erode else binary
    final = process_mask(mask)
    contours, _ = cv2.findContours(
        final, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    boxes = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if area_floor <= area <= 50000:
            boxes.append(cv2.boundingRect(contour))
    return boxes


def run_current(frames, thresh=THRESH, erode=True, area_floor=MIN_AREA):
    previous = None
    out = []
    for frame in frames:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if previous is None:
            previous = gray
            out.append([])
            continue
        difference = cv2.absdiff(gray, previous)
        _, binary = cv2.threshold(difference, thresh, 255, cv2.THRESH_BINARY)
        out.append(_boxes_from_binary(binary, erode, area_floor))
        previous = gray
    return out


def run_bg_average(frames, thresh=THRESH, alpha=0.05, area_floor=MIN_AREA):
    background = None
    out = []
    for frame in frames:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if background is None:
            background = gray.astype(np.float32)
            out.append([])
            continue
        reference = background.astype(np.uint8)
        difference = cv2.absdiff(gray, reference)
        _, binary = cv2.threshold(difference, thresh, 255, cv2.THRESH_BINARY)
        out.append(_boxes_from_binary(binary, False, area_floor))
        cv2.accumulateWeighted(gray, background, alpha)
    return out


def run_bg_mog2(frames, area_floor=MIN_AREA, var_threshold=16):
    model = cv2.createBackgroundSubtractorMOG2(
        history=200, varThreshold=var_threshold, detectShadows=False
    )
    out = []
    for frame in frames:
        mask = model.apply(frame, learningRate=0.01)
        _, binary = cv2.threshold(mask, 127, 255, cv2.THRESH_BINARY)
        out.append(_boxes_from_binary(binary, False, area_floor))
    return out


def run_pl_background(frames, thresh=THRESH, shift=4, erode=True,
                      area_floor=MIN_AREA):
    """Bit-exact emulation of the proposed in-PL background model.

    This is the arithmetic that would go into frame_diff.cpp:

        delta = (int)current - (int)background
        background = background + (delta >> SHIFT)      // arithmetic shift
        difference = |current - background|

    The shift gives an integer alpha of 1/2^SHIFT. The existing
    ``previous_frame`` BRAM buffer becomes ``background`` in place, so this
    needs no extra memory in the PL.
    """
    background = None
    out = []
    for frame in frames:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if background is None:
            background = gray.copy()
            out.append([])
            continue

        difference = cv2.absdiff(gray, background)
        _, binary = cv2.threshold(difference, thresh, 255, cv2.THRESH_BINARY)
        out.append(_boxes_from_binary(binary, erode, area_floor))

        # Arithmetic shift on a signed value, matching ap_int>> in HLS.
        delta = gray.astype(np.int16) - background.astype(np.int16)
        background = np.clip(
            background.astype(np.int16) + (delta >> shift), 0, 255
        ).astype(np.uint8)
    return out


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------

def iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = min(ax + aw, bx + bw) - max(ax, bx)
    iy = min(ay + ah, by + bh) - max(ay, by)
    if ix <= 0 or iy <= 0:
        return 0.0
    inter = float(ix * iy)
    return inter / (aw * ah + bw * bh - inter)


def score(detections_per_frame, truth_per_frame):
    tp = fp = fn = 0
    ious = []
    fragments = []
    for boxes, truth in zip(detections_per_frame, truth_per_frame):
        if not truth:
            continue
        best = max((iou(b, truth) for b in boxes), default=0.0)
        ious.append(best)
        hits = [b for b in boxes if iou(b, truth) >= IOU_HIT]
        if hits:
            tp += 1
            fragments.append(len(boxes))
        else:
            fn += 1
        fp += max(0, len(boxes) - len(hits))
    recall = tp / (tp + fn) if tp + fn else 0.0
    precision = tp / (tp + fp) if tp + fp else 0.0
    mean_iou = sum(ious) / len(ious) if ious else 0.0
    mean_frag = sum(fragments) / len(fragments) if fragments else 0.0
    return recall, precision, mean_iou, mean_frag


MODES = [
    ("current", lambda f: run_current(f)),
    ("pl_bg4_300", lambda f: run_pl_background(f, shift=4)),
    ("pl_bg4_a120", lambda f: run_pl_background(f, shift=4, area_floor=120)),
    ("pl_bg4_ne120", lambda f: run_pl_background(
        f, shift=4, erode=False, area_floor=120)),
    ("pl_bg5_a120", lambda f: run_pl_background(f, shift=5, area_floor=120)),
    ("bg_mog2", lambda f: run_bg_mog2(f, area_floor=120)),
]


def main():
    sizes = [
        ("near 56x71", 56, 71),
        ("mid  30x38", 30, 38),
        ("far  16x20", 16, 20),
        ("tiny  9x11", 9, 11),
    ]
    speeds = [2, 4, 8, 16]

    for label, obj_w, obj_h in sizes:
        for speed in speeds:
            frames, truth = make_sequence(FRAMES, obj_w, obj_h, speed)
            print(f"\n=== object {label}, speed {speed} px/frame "
                  f"({speed * 15} px/s at 15 FPS) ===")
            print(f"{'mode':<10} {'recall':>7} {'prec':>7} {'meanIoU':>8} "
                  f"{'boxes/obj':>10}")
            for name, runner in MODES:
                detections = runner(frames)
                r, p, mi, frag = score(detections, truth)
                print(f"{name:<10} {r:>7.3f} {p:>7.3f} {mi:>8.3f} "
                      f"{frag:>10.2f}")


if __name__ == "__main__":
    main()
