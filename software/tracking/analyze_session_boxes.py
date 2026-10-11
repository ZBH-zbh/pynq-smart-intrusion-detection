"""Look at what the false crossings on the empty-room control were made of.

The control session had nobody in front of the camera, and the replay still
reported 3 crossings. Before designing anything to suppress them, it matters
whether they came from small noise blobs (a size or speed filter is the answer)
or from large slow shapes (a persistence rule is the answer).

Prints the boxes on the frames around each reported crossing.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from session import Session  # noqa: E402

WINDOWS = [34.42, 45.25, 45.31, 50.31]


def describe(box):
    x, y, w, h = box
    return (f"({x:5.1f},{y:5.1f} {w:5.1f}x{h:5.1f} "
            f"area={w * h:6.0f} cx={x + w / 2:5.1f})")


def main():
    session = Session(sys.argv[1])
    print(f"{len(session)} frames, {session.summary()}")
    print()

    interesting = set()
    for centre in WINDOWS:
        for index in range(len(session)):
            if abs(session.timestamp(index) - centre) < 0.30:
                interesting.add(index)

    for index in sorted(interesting):
        boxes = session.boxes(index)
        t = session.timestamp(index)
        if not boxes:
            print(f"t={t:7.2f} n={index:5d}   (no boxes)")
            continue
        print(f"t={t:7.2f} n={index:5d}   {len(boxes)} box(es)")
        for box in boxes:
            print(f"                    {describe(box)}")

    # How does box size distribute across the whole recording? Small blobs
    # separated from large ones would make a size floor an obvious lever.
    sizes = []
    for index in range(len(session)):
        for box in session.boxes(index):
            sizes.append(box[2] * box[3])
    sizes.sort()
    if sizes:
        print()
        print(f"box areas over the session: n={len(sizes)}  "
              f"min={sizes[0]:.0f}  median={sizes[len(sizes) // 2]:.0f}  "
              f"max={sizes[-1]:.0f}")
        for threshold in (100, 300, 600, 1000, 2000):
            kept = sum(1 for s in sizes if s >= threshold)
            print(f"    area >= {threshold:5d} keeps {kept:4d}/{len(sizes)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
