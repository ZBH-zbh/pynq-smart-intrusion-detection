"""Settle a contradiction: the camera measures 66 ms, the tracker reports 60.

Read out of context these cannot both be right. The tracker reports 16.67 FPS
(60 ms) while a fresh open-and-measure says 15.01 FPS (66.6 ms).

They are not the same experiment. The tracker opens the camera once and streams
for minutes; the probe sets the mode, which restarts the stream, and then reads
a couple of dozen frames. This opens once and reads a long run, reporting block
by block, so it is visible whether the rate drifts after the stream settles --
and which of the two numbers is the honest one to put in the documentation.
"""
import statistics
import sys
import time

import cv2

BLOCK = 30
BLOCKS = 10


def main():
    cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
    if not cap.isOpened():
        print("could not open /dev/video0")
        return 1
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
    cap.set(cv2.CAP_PROP_FPS, 30)

    # No further mode changes from here: this is what the tracker does.
    for _ in range(10):
        cap.read()

    print(f"{'block':>6} {'ms/frame':>9} {'fps':>7}   (blocks of {BLOCK})")
    all_gaps = []
    for block in range(BLOCKS):
        stamps = []
        for _ in range(BLOCK):
            ok, _ = cap.read()
            if not ok:
                break
            stamps.append(time.perf_counter())
        gaps = [(b - a) * 1000.0 for a, b in zip(stamps, stamps[1:])]
        if not gaps:
            break
        all_gaps += gaps
        print(f"{block:>6} {statistics.fmean(gaps):>9.2f} "
              f"{1000.0 / statistics.fmean(gaps):>7.2f}")

    cap.release()
    if all_gaps:
        print(f"\noverall: {statistics.fmean(all_gaps):.2f} ms  "
              f"{1000.0 / statistics.fmean(all_gaps):.2f} fps  "
              f"({len(all_gaps)} gaps)")
        print(f"last {BLOCK}: "
              f"{statistics.fmean(all_gaps[-BLOCK:]):.2f} ms  "
              f"{1000.0 / statistics.fmean(all_gaps[-BLOCK:]):.2f} fps")
    return 0


if __name__ == "__main__":
    sys.exit(main())
