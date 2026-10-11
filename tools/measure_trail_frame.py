"""What does the trail add to each frame, on the board?

``TrailStore.add`` is called once per frame per track from inside the pipeline's
frame loop, and it rebuilds the spline whenever the target has moved far enough
to record a point. So the per-frame cost is the cost of one ``add`` call.

The synthetic source is not a good vehicle for this: its track keeps being
re-created, so the trail never grows long. This drives the store directly with
targets at plausible speeds instead, which is the input the cost depends on.

Targets: a hand crossing the 320 px frame in about a second is ~320 px/s; a
quick wave is around double that.
"""
import statistics
import sys
import time

sys.path.insert(0, "/home/xilinx/intrusion_demo")

import trail  # noqa: E402

FRAME_MS = 1000.0 / 15.0        # the loop paces itself to 15 FPS


def run(speed, seconds=8.0, settings=None):
    store = trail.TrailStore(**(settings or {}))
    dt = 1 / 15.0
    costs = []
    points = 0
    t = 0.0
    x = 20.0
    while t < seconds:
        # Cross the frame, then come back, so a long-lived track is exercised.
        x += speed * dt
        if x > 300.0:
            x = 20.0 + (x - 300.0)
        start = time.perf_counter()
        store.add(1, (x, 120.0), timestamp=t)
        costs.append((time.perf_counter() - start) * 1000.0)
        points = len(store.recorded(1))
        t += dt
    return costs, points


def report(label, costs, points):
    mean = statistics.fmean(costs)
    worst = max(costs)
    print(f"{label:<34} points={points:>3}  "
          f"mean={mean:6.2f} ms ({100 * mean / FRAME_MS:5.1f}% of frame)  "
          f"max={worst:6.2f} ms ({100 * worst / FRAME_MS:5.1f}%)")


def main():
    print(f"frame period at 15 FPS: {FRAME_MS:.1f} ms\n")
    for speed in (160.0, 320.0, 640.0):
        costs, points = run(speed)
        report(f"target at {speed:.0f} px/s", costs, points)

    print()
    costs, points = run(320.0, settings={"seconds": 20.0})
    report("320 px/s, trail-seconds 20", costs, points)
    costs, points = run(320.0, settings={"min_move": 12.0})
    report("320 px/s, trail-min-move 12", costs, points)


if __name__ == "__main__":
    main()
