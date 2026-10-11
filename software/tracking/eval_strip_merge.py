"""Measure merge_edge_strips against the synthetic sequence's known objects.

Everything is collected in ONE pass with a single detector, because
SoftwareMotionDetector is stateful (it keeps the previous frame) and reusing a
detector that has already consumed the whole sequence silently diffs frame 139
against frame 0.

Metrics are standard detection metrics rather than box counts: a merged box
counts as a true positive when its IoU against a real object is >= 0.5.

    python eval_strip_merge.py
"""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEPLOY = os.path.abspath(os.path.join(HERE, "..", "pynq_deploy"))
sys.path.insert(0, DEPLOY)
sys.path.insert(0, HERE)

from benchmark_motion import generate_synthetic_frames
from opencv_software_motion import SoftwareMotionDetector
from strip_merge import merge_edge_strips

IOU_HIT = 0.5

# The synthetic generator also draws a static block at (270, 10) that flips
# colour every frame. It is a genuine changed region, but its grey difference
# sits exactly on the threshold, so it is tracked separately instead of being
# counted as a missed object.
FLICKER = ("flicker", (270, 10, 36, 36))


def ground_truth(index):
    """Objects actually drawn in frame `index`, as (name, (x, y, w, h))."""
    boxes = []
    x = 15 + (index * 8) % 230
    y = 65 + int(12 * math.sin(index / 13.0))
    boxes.append(("big", (x, y, 57, 72)))
    if 55 <= index % 120 < 100:
        x2 = 250 - ((index - 55) * 5) % 190
        boxes.append(("small", (x2, 150, 37, 57)))
    return boxes


def iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = min(ax + aw, bx + bw) - max(ax, bx)
    iy = min(ay + ah, by + bh) - max(ay, by)
    if ix <= 0 or iy <= 0:
        return 0.0
    inter = float(ix * iy)
    return inter / (aw * ah + bw * bh - inter)


def best_iou(box, boxes):
    return max((iou(box, b) for b in boxes), default=0.0)


def score(predicted, truths):
    """Greedy one-to-one matching: recall, precision, false positives."""
    matched_truth = set()
    true_pos = 0
    false_pos = 0
    for box in predicted:
        best, best_j = 0.0, None
        for j, (_, gt) in enumerate(truths):
            if j in matched_truth:
                continue
            value = iou(box, gt)
            if value > best:
                best, best_j = value, j
        if best >= IOU_HIT and best_j is not None:
            matched_truth.add(best_j)
            true_pos += 1
        else:
            false_pos += 1
    return true_pos, false_pos, len(truths) - len(matched_truth)


def main():
    frames = generate_synthetic_frames(140)

    detector = SoftwareMotionDetector()          # single, sequential pass
    per_frame = []
    for i, frame in enumerate(frames):
        _, targets, _ = detector.process(frame)
        per_frame.append((i, targets, merge_edge_strips(targets)))

    def mean(xs):
        return sum(xs) / len(xs) if xs else 0.0

    raw_counts = [len(t) for _, t, _ in per_frame]
    mgd_counts = [len(m) for _, _, m in per_frame]
    print(f"frames                   : {len(frames)}")
    print(f"raw boxes / frame        : mean {mean(raw_counts):.2f} "
          f"(min {min(raw_counts)}, max {max(raw_counts)})")
    print(f"merged boxes / frame     : mean {mean(mgd_counts):.2f} "
          f"(min {min(mgd_counts)}, max {max(mgd_counts)})")

    # --- recall / precision against the drawn objects -------------------
    totals = {"raw": [0, 0, 0], "mgd": [0, 0, 0]}
    raw_ious, mgd_ious = [], []
    for i, targets, merged in per_frame:
        truths = ground_truth(i)
        for key, boxes in (("raw", targets), ("mgd", merged)):
            tp, fp, fn = score(boxes, truths)
            totals[key][0] += tp
            totals[key][1] += fp
            totals[key][2] += fn
        for _, gt in truths:
            raw_ious.append(best_iou(gt, targets))
            mgd_ious.append(best_iou(gt, merged))

    for key, label in (("raw", "raw detections"), ("mgd", "merged       ")):
        tp, fp, fn = totals[key]
        recall = tp / (tp + fn) if tp + fn else 0.0
        precision = tp / (tp + fp) if tp + fp else 0.0
        print(f"{label}           : TP {tp}  FP {fp}  FN {fn}  "
              f"recall {recall:.3f}  precision {precision:.3f}")
    print(f"best IoU vs truth, raw   : mean {mean(raw_ious):.3f}")
    print(f"best IoU vs truth, merged: mean {mean(mgd_ious):.3f}")

    # --- flicker block, reported separately ------------------------------
    flicker_hits = sum(
        1 for _, _, m in per_frame if best_iou(FLICKER[1], m) >= IOU_HIT
    )
    print(f"flicker block found      : {flicker_hits}/{len(per_frame)} frames")

    print("\nper-frame samples:")
    for target in (1, 2, 20, 40, 41, 55, 60, 100, 139):
        i, targets, merged = per_frame[target]
        truths = ground_truth(i)
        print(f"  f{i:3d} raw={targets}")
        print(f"       mgd={merged}")
        print(f"       gtd={truths}")


if __name__ == "__main__":
    main()
