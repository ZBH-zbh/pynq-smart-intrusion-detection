"""Where does the remaining trail cost go on the board?

After vectorising the arithmetic a 90-point rebuild still costs ~8.6 ms. The
polynomial work is now a handful of small numpy calls, so the suspicion is the
final conversion of the dense array back into a list of Python tuples -- 540
tuples for a 90-point trail at 6 samples per segment.
"""
import sys
import time

sys.path.insert(0, "/home/xilinx/intrusion_demo")

import numpy as np  # noqa: E402
import trail  # noqa: E402


def timeit(fn, repeats=200):
    start = time.perf_counter()
    for _ in range(repeats):
        fn()
    return (time.perf_counter() - start) / repeats * 1000.0


def main():
    for count in (40, 90, 200):
        pts = np.random.RandomState(3).rand(count, 2) * 300.0
        points = [tuple(p) for p in pts]

        dense = None

        def build():
            nonlocal dense
            dense = trail.catmull_rom(points, samples=6)

        t_all = timeit(build)

        # The same array, but stopping before the Python conversion.
        control = np.empty((count + 2, 2))
        control[0] = pts[0]
        control[1:count + 1] = pts
        control[count + 1] = pts[-1]
        p0, p1 = control[0:count - 1], control[1:count]
        p2, p3 = control[2:count + 1], control[3:count + 2]
        t = np.arange(6) / 6.0
        t2, t3 = t * t, t * t * t

        def maths_only():
            for axis in (0, 1):
                a, b = p0[:, axis:axis + 1], p1[:, axis:axis + 1]
                c, d = p2[:, axis:axis + 1], p3[:, axis:axis + 1]
                0.5 * ((2.0 * b) + (-a + c) * t
                       + (2.0 * a - 5.0 * b + 4.0 * c - d) * t2
                       + (-a + 3.0 * b - 3.0 * c + d) * t3)

        t_maths = timeit(maths_only)

        arr = np.random.RandomState(4).rand((count - 1) * 6, 2)

        t_comp = timeit(lambda: [(float(p[0]), float(p[1])) for p in arr])
        t_tolist = timeit(lambda: arr.tolist())
        t_map = timeit(lambda: list(map(tuple, arr.tolist())))

        print(f"--- {count} control points, {(count - 1) * 6} output points ---")
        print(f"  catmull_rom total      {t_all:7.3f} ms")
        print(f"  numpy maths only       {t_maths:7.3f} ms")
        print(f"  list comp w/ float()   {t_comp:7.3f} ms")
        print(f"  arr.tolist()           {t_tolist:7.3f} ms")
        print(f"  map(tuple, tolist())   {t_map:7.3f} ms")


if __name__ == "__main__":
    main()
