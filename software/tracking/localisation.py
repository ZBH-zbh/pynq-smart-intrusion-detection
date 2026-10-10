"""Localisation points for boxes, without touching how the boxes are decided.

The shipped detection chain (``motion_common`` and the PL pipeline) decides the
green boxes, and that logic is deliberately left exactly as it is -- it was
tuned as a compromise and re-deriving it here would only make it worse.

Tracking, however, needs a *point* per target, and the centre of a bounding box
is the least steady choice available: a thin protrusion on the mask edge moves
the box centre by half its length while it moves the region's pixel centroid by
that length divided by the number of foreground pixels. Measured, a 30 px spur
moved the box centre by more than 8 px and the centroid by under 1.6 px.

So this module reads a point out of the mask that the chain already produced.
It observes; it does not change any box.
"""

import cv2
import numpy as np


def region_centers(mask, boxes):
    """Pixel centroid of the mask inside each box, as a list of (x, y).

    Falls back to the box centre when a box contains no foreground pixels, which
    happens when the detector merged boxes and left gaps between them. The
    result is always the same length as ``boxes``.
    """
    centers = []
    for x, y, w, h in boxes:
        x0, y0 = max(0, int(x)), max(0, int(y))
        x1, y1 = min(mask.shape[1], int(x) + int(w)), \
            min(mask.shape[0], int(y) + int(h))
        if x1 <= x0 or y1 <= y0:
            centers.append((x + w / 2.0, y + h / 2.0))
            continue

        window = mask[y0:y1, x0:x1]
        total = int(cv2.countNonZero(window))
        if total == 0:
            centers.append((x + w / 2.0, y + h / 2.0))
            continue

        moments = cv2.moments(window, binaryImage=True)
        if moments["m00"] <= 0:
            centers.append((x + w / 2.0, y + h / 2.0))
            continue
        centers.append((
            x0 + moments["m10"] / moments["m00"],
            y0 + moments["m01"] / moments["m00"],
        ))
    return centers
