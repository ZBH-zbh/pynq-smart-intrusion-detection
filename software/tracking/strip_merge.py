"""Fuse the leading/trailing edge strips that frame differencing produces.

The problem
-----------
``frame_diff`` reports *changed pixels*, not objects. When a solid-coloured
region moves, the area covered by both frames cancels out and only two thin
strips survive: the newly covered leading edge and the vacated trailing edge.
Measured on the project's synthetic sequence, one 56x71 rectangle comes back as
``(17, 67, 6, 69)`` and ``(73, 67, 6, 69)`` -- two 6 px slivers separated by the
object's own width.

``merge_nearby_boxes`` cannot repair this. Its ``distance_thresh`` defaults to
30 px while the strips are ~50 px apart, and simply raising it to 55 px would
also fuse two genuinely distinct objects standing side by side.

What actually separates the two cases is the **vertical extent**. The two strips
of one object share the same top and bottom because they belong to the same
rectangle. Two different objects almost never do. So the rule here is:

  merge two boxes when they are horizontally close, their vertical spans
  overlap strongly, and their heights match.

This is a heuristic, not a model. It is applied to detections before tracking,
is off by default in the demo server, and is covered by tests that measure the
merged boxes against known ground truth.
"""


def _vertical_overlap_ratio(a, b):
    """Overlap of two vertical spans, as a fraction of the shorter one."""
    _, ay, _, ah = a
    _, by, _, bh = b
    a2, b2 = ay + ah, by + bh
    overlap = min(a2, b2) - max(ay, by)
    if overlap <= 0:
        return 0.0
    shorter = min(ah, bh)
    return overlap / float(shorter) if shorter > 0 else 0.0


def _height_ratio(a, b):
    _, _, _, ah = a
    _, _, _, bh = b
    if ah == 0 or bh == 0:
        return float("inf")
    return max(ah, bh) / float(min(ah, bh))


def _horizontal_gap(a, b):
    """Signed horizontal gap; negative means the boxes already overlap in x."""
    ax, _, aw, _ = a
    bx, _, bw, _ = b
    if bx >= ax:
        return bx - (ax + aw)
    return ax - (bx + bw)


def merge_fragments_2d(boxes, gap_scale=0.6, max_gap=80.0):
    """Join boxes that are fragments of one object, in both axes.

    ``merge_edge_strips`` handles the special case that frame differencing
    creates: two full-height strips at the object's leading and trailing edge.
    A background model does not produce strips, but it does fragment an object
    whose parts are close in grey value to the background -- a white sleeve
    against a white wall leaves a hole in the mask, and the tracker then sees
    two targets whose centres jump between them.

    Morphological closing cannot repair that: bridging a 19 px hole would need a
    21x21 kernel. Grouping boxes can bridge any gap, and the gap is judged
    relative to the boxes themselves, so a large object tolerates a large gap
    while small distant blobs stay separate.

    Two boxes are joined when expanding each by
    ``gap_scale * min(w, h)`` (capped at ``max_gap``) makes them overlap.
    Merging repeats until no pair qualifies, so a chain of fragments collapses
    into one box.
    """
    if not boxes:
        return []

    merged = [tuple(int(v) for v in b) for b in boxes]

    changed = True
    while changed:
        changed = False
        result = []
        while merged:
            current = merged.pop()
            joined = False
            for index, other in enumerate(merged):
                expansion = min(
                    max_gap,
                    gap_scale * min(
                        min(current[2], current[3]),
                        min(other[2], other[3]),
                    ),
                )
                if _gap_within(current, other, expansion):
                    merged[index] = _union(current, other)
                    changed = True
                    joined = True
                    break
            if not joined:
                result.append(current)
        merged = result

    return sorted(merged, key=lambda b: b[0])


def merge_centers(boxes_in, centers_in, boxes_out):
    """Area-weighted centres for merged boxes, from the parts inside each.

    Grouping boxes changes what the box means, so the localisation points have
    to follow: the merged centre is the average of the contributing regions
    weighted by their area, which keeps the point on the mass of the object
    rather than on the centre of the union rectangle.
    """
    if not boxes_out:
        return []

    result = []
    for out in boxes_out:
        ox, oy, ow, oh = out
        total = 0.0
        sx = sy = 0.0
        for (x, y, w, h), (cx, cy) in zip(boxes_in, centers_in):
            # Attribute a part to the merged box that contains its centre.
            if ox <= cx <= ox + ow and oy <= cy <= oy + oh:
                area = float(w * h)
                sx += cx * area
                sy += cy * area
                total += area
        if total > 0:
            result.append((sx / total, sy / total))
        else:
            result.append((ox + ow / 2.0, oy + oh / 2.0))
    return result


def _gap_within(a, b, expansion):
    """True when expanding both boxes by `expansion` makes them overlap."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (
        bx > ax + aw + expansion
        or ax > bx + bw + expansion
        or by > ay + ah + expansion
        or ay > by + bh + expansion
    )


def _union(a, b):
    """Smallest box containing both."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ax2, ay2 = ax + aw, ay + ah
    bx2, by2 = bx + bw, by + bh
    nx, ny = min(ax, bx), min(ay, by)
    nx2, ny2 = max(ax2, bx2), max(ay2, by2)
    return (nx, ny, nx2 - nx, ny2 - ny)


def merge_edge_strips(
    boxes,
    max_gap=62,
    min_vertical_overlap=0.6,
    max_height_ratio=1.5,
):
    """Merge boxes that look like two edges of the same moving object.

    Parameters
    ----------
    boxes : iterable of (x, y, w, h)
    max_gap : float
        Largest horizontal gap between the facing edges that may still be one
        object. Must exceed the width of the widest object expected.
    min_vertical_overlap : float
        Required overlap of the two vertical spans, as a fraction of the
        shorter span. This is what keeps stacked objects apart.
    max_height_ratio : float
        Largest allowed height ratio between the two boxes.

    Returns
    -------
    list[(x, y, w, h)]
        Merged boxes, left to right. Chains collapse iteratively, so three
        consecutive strips fuse into one box.
    """
    if not boxes:
        return []

    ordered = sorted(
        (tuple(int(v) for v in b) for b in boxes), key=lambda b: b[0]
    )
    merged = [ordered[0]]

    for box in ordered[1:]:
        candidate = merged[-1]
        if (
            _horizontal_gap(candidate, box) <= max_gap
            and _vertical_overlap_ratio(candidate, box) >= min_vertical_overlap
            and _height_ratio(candidate, box) <= max_height_ratio
        ):
            merged[-1] = _union(candidate, box)
        else:
            merged.append(box)

    return merged
