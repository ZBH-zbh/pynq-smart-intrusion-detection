"""Measure the trail fit cost on the board itself.

The unit tests assert an absolute millisecond budget, and those numbers were
calibrated on the PC. The board's ARM core is far slower, so "2.3 ms for 200
points" says nothing about the board. This prints the real numbers so the
budget can be stated against the machine that actually has to meet it.
"""
import os
import sys
import time

sys.path.insert(0, "/home/xilinx/intrusion_demo")

import trail  # noqa: E402

FRAME_MS = 1000.0 / 16.7      # the live link runs at 16.7 FPS


def main():
    print(f"frame period at 16.7 FPS: {FRAME_MS:.1f} ms")
    print(f"{'points':>7} {'ms':>8} {'% of frame':>11}")
    for points in (20, 40, 80, 120, 200, 300, 400):
        # Enough repeats to be meaningful, few enough to stay quick.
        repeats = max(20, int(4000 / points))
        cost = trail.measure_cost(points=points, repeats=repeats)
        print(f"{points:>7} {cost:>8.2f} {100.0 * cost / FRAME_MS:>10.1f}%")

    # How many points a trail actually reaches under the shipped settings, at
    # 15 FPS with a fast target -- the cost only matters if it is reachable.
    print()
    for seconds, move in ((6.0, 3.0), (20.0, 3.0)):
        store = trail.TrailStore(min_move=move, seconds=seconds)
        t = 0.0
        dt = 1 / 15.0
        x = 20.0
        while t < seconds:
            x = 20.0 + 400.0 * t
            store.add(1, (x, 120.0), timestamp=t)
            t += dt
        n = len(store.recorded(1))
        cost = trail.measure_cost(points=max(n, 2), repeats=100)
        print(f"seconds={seconds:>4} min_move={move} -> {n:>3} recorded points, "
              f"fit {cost:.2f} ms ({100.0 * cost / FRAME_MS:.1f}% of frame)")


if __name__ == "__main__":
    main()
