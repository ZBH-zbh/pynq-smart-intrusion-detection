"""Is a detection burst a moving object, or the whole picture changing?

The empty-room control still reports one crossing after a persistence rule
would have removed the other two: track 4 travels monotonically from +23 px to
-99 px across the line in four frames, which is dynamically indistinguishable
from a real fast crossing.

But its boxes are 96x124 and 205x80, and 205 px is most of the frame width. A
hand is not 205 px wide. That points somewhere else: the whole image changing,
which the frame-difference chain reports as one enormous "moving" region.

The masks were recorded losslessly, so this can be settled by looking at how
much of the frame the mask actually covers -- an object covers a small band, a
global change covers a large fraction.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from session import Session  # noqa: E402

WATCH = [34.42, 45.25, 45.31, 50.31]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("session")
    parser.add_argument("--around", type=float, default=0.8)
    args = parser.parse_args()

    session = Session(args.session)
    total_pixels = session.width * session.height

    fractions = []
    for index, mask, boxes, _centers, timestamp, _fps in session.frames():
        fraction = float((mask > 0).sum()) / total_pixels
        fractions.append((index, timestamp, fraction, boxes))

    print(f"{len(fractions)} frames, mask covers "
          f"{100 * min(f[2] for f in fractions):.2f}% to "
          f"{100 * max(f[2] for f in fractions):.2f}%")
    print()

    # Every frame the detector produced anything for, with how much of the
    # picture the mask says changed.
    active = [f for f in fractions if f[3]]
    print(f"{len(active)} frames had at least one box:")
    print(f"{'frame':>6} {'t':>8} {'mask%':>7}  boxes")
    for index, timestamp, fraction, boxes in active:
        shape = "  ".join(f"{int(b[2])}x{int(b[3])}" for b in boxes)
        print(f"{index:>6} {timestamp:>8.2f} {100 * fraction:>7.2f}  {shape}")

    print()
    print("Around each reported crossing:")
    for centre in WATCH:
        print(f"  t={centre}:")
        for index, timestamp, fraction, boxes in fractions:
            if abs(timestamp - centre) <= args.around:
                print(f"    f{index:>5} t={timestamp:7.2f} "
                      f"mask={100 * fraction:6.2f}%  boxes={len(boxes)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
