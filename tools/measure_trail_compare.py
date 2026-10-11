"""Put the two trail cost measurements side by side, in one process.

``measure_cost(N)`` times ``build`` on N points; the pipeline instead pays for
one ``add`` per frame, and ``add`` rebuilds only when the target has moved far
enough to record a point. Measuring them in the same process under the same
conditions is the only way to compare them -- run separately they disagreed by
a factor of two, which is not a difference the code can explain.
"""
import statistics
import sys
import time

sys.path.insert(0, "/home/xilinx/intrusion_demo")

import trail  # noqa: E402

FRAME_MS = 1000.0 / 15.0


def time_build(points, repeats=30):
    costs = []
    for _ in range(repeats):
        start = time.perf_counter()
        trail.build([(20 + i * 7, 120 + 20 * (i % 3)) for i in range(points)])
        costs.append((time.perf_counter() - start) * 1000.0)
    return statistics.fmean(costs)


def time_add_at(points, samples=30):
    """Cost of one `add` that has to rebuild a trail of `points` points."""
    store = trail.TrailStore()
    dt = 1 / 15.0
    t = 0.0
    x = 20.0
    while len(store.recorded(1)) < points:
        x += 320.0 * dt
        if x > 300.0:
            x = 20.0
        store.add(1, (x, 120.0), timestamp=t)
        t += dt
        if t > 60.0:
            break
    reached = len(store.recorded(1))

    costs = []
    for _ in range(samples):
        x += 320.0 * dt
        if x > 300.0:
            x = 20.0
        t += dt
        start = time.perf_counter()
        store.add(1, (x, 120.0), timestamp=t)
        costs.append((time.perf_counter() - start) * 1000.0)
    return statistics.fmean(costs), max(costs), reached


def main():
    print(f"frame period at 15 FPS: {FRAME_MS:.1f} ms\n")
    print(f"{'points':>7} {'build(N)':>10} {'add mean':>10} {'add max':>9} "
          f"{'build %frame':>13} {'add %frame':>11}")
    for points in (40, 91, 150, 200):
        b = time_build(points)
        a, worst, reached = time_add_at(points)
        print(f"{reached:>7} {b:>9.2f}ms {a:>9.2f}ms {worst:>8.2f}ms "
              f"{100 * b / FRAME_MS:>12.1f}% {100 * a / FRAME_MS:>10.1f}%")


if __name__ == "__main__":
    main()
