"""Pick BackgroundDetector defaults by measuring configs on the size/speed grid.

The board measurement shows close(7x7) costs about 8.5 ms and the erosion about
2.8 ms, which is the difference between ~11.6 and ~13.3 FPS. Both exist to
repair frame-difference artefacts (gaps between fragments, thin-strip noise),
so with a background model they may be unnecessary. This checks whether dropping
them costs any detection quality.
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DEPLOY = os.path.abspath(os.path.join(HERE, "..", "pynq_deploy"))
sys.path.insert(0, DEPLOY)
sys.path.insert(0, HERE)

from bg_detector import BackgroundDetector  # noqa: E402
from test_bg_detector import iou, scene  # noqa: E402

CONFIGS = [
    ("close7+erode a120", dict(close_ksize=7, erosion=True, min_area=120)),
    ("open3+erode  a120", dict(close_ksize=1, erosion=True, min_area=120)),
    ("open3+noerode a120", dict(close_ksize=1, erosion=False, min_area=120)),
    ("open3+noerode a300", dict(close_ksize=1, erosion=False, min_area=300)),
    ("close7+noerode a300", dict(close_ksize=7, erosion=False, min_area=300)),
]

GRID = [
    (56, 71, 2), (56, 71, 4), (56, 71, 8), (56, 71, 16),
    (30, 38, 2), (30, 38, 4), (30, 38, 8), (30, 38, 16),
    (16, 20, 2), (16, 20, 4), (16, 20, 8), (16, 20, 16),
    (9, 11, 2), (9, 11, 8),
]


def evaluate(options):
    recalls, precisions, fragments = [], [], []
    for obj_w, obj_h, speed in GRID:
        detector = BackgroundDetector(**options)
        frames, truth = scene(60, 12, obj_w, obj_h, speed)
        detections = [detector.process(f)[1] for f in frames]
        tp = fp = fn = 0
        frag = []
        for boxes, gt in zip(detections, truth):
            if gt is None:
                continue
            hits = [b for b in boxes if iou(b, gt) >= 0.5]
            if hits:
                tp += 1
                frag.append(len(boxes))
            else:
                fn += 1
            fp += max(0, len(boxes) - len(hits))
        recalls.append(tp / (tp + fn) if tp + fn else 0.0)
        precisions.append(tp / (tp + fp) if tp + fp else 0.0)
        fragments.append(sum(frag) / len(frag) if frag else 0.0)
    return (sum(recalls) / len(recalls), sum(precisions) / len(precisions),
            sum(fragments) / len(fragments))


def main():
    print(f"{'config':<22} {'recall':>7} {'prec':>7} {'boxes/obj':>10}")
    for label, options in CONFIGS:
        r, p, f = evaluate(options)
        print(f"{label:<22} {r:>7.3f} {p:>7.3f} {f:>10.2f}")


if __name__ == "__main__":
    main()
