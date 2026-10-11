"""Tests for the trail smoothing.

The claim being tested is visual: a trail through per-frame detector positions
is a staircase, and the point of this module is to make it look like a path
without moving it off the route the target took. So the tests measure the mean
turn between segments before and after, and bound how far the smoothed curve is
allowed to stray from the recorded points.
"""
import math
import os
import random
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
DEPLOY = os.path.abspath(os.path.join(HERE, "..", "pynq_deploy"))
sys.path.insert(0, DEPLOY)
sys.path.insert(0, HERE)

import trail  # noqa: E402
from trail import TrailStore, build, catmull_rom, moving_average, simplify  # noqa: E402


#: Cost budgets are stated against a frame, not in absolute milliseconds.
#:
#: These tests run on the board as well as on the PC, and the board's 650 MHz
#: ARM core is an order of magnitude slower, so a budget of "6 ms" is generous
#: on one and unreachable on the other -- which is exactly how two of these
#: tests failed the first time they were run on real hardware. The board paces
#: the loop to 15 FPS, so one frame is 66 ms, and "less than a third of a
#: frame" is a claim that means the same thing on both machines.
#:
#: The fit cannot lean on being cached to meet this: ``TrailStore.add`` calls it
#: inline every time the target has moved far enough to record a point, which
#: for a walking target is every frame. Measured on the board, one rebuild of
#: the shipped 6-second trail (~90 points) costs 5.6 ms after the rewrite, and
#: cost 18 ms before it -- 27% of a frame.
BOARD_FRAME_MS = 1000.0 / 15.0


def staircase(count=60, drift=1.2, jitter=8.0, seed=3):
    """A target creeping right while the detector's centre jitters up and down.

    This is the shape the trail work exists for: the drift is small compared to
    the jitter, so the raw polyline reads as noise rather than as travel.
    """
    rng = random.Random(seed)
    return [(20 + i * drift, 120 + rng.uniform(-jitter, jitter))
            for i in range(count)]


class SimplifyTests(unittest.TestCase):
    def test_empty_input(self):
        self.assertEqual(simplify([], 5), [])

    def test_a_stationary_target_records_almost_nothing(self):
        """The dead band is what stops a still target scribbling a blob."""
        still = staircase(count=60, drift=0.2, jitter=2.0)
        kept = simplify(still, min_move=6.0)
        self.assertLess(len(kept), 6,
                        f"kept {len(kept)} of {len(still)} points")

    def test_a_moving_target_keeps_its_points(self):
        moving = staircase(count=60, drift=8.0, jitter=1.0)
        kept = simplify(moving, min_move=6.0)
        self.assertEqual(len(kept), len(moving))

    def test_the_first_point_is_always_kept(self):
        self.assertEqual(simplify([(5, 5)], 6)[0], (5, 5))
        self.assertEqual(simplify([(5, 5), (6, 6)], 6)[0], (5, 5))

    def test_a_zero_deadband_keeps_everything(self):
        points = staircase(20)
        self.assertEqual(simplify(points, 0), points)

    def test_kept_points_are_never_closer_than_the_deadband(self):
        points = staircase(80, drift=2.0, jitter=5.0)
        kept = simplify(points, min_move=6.0)
        for a, b in zip(kept, kept[1:]):
            self.assertGreaterEqual(math.dist(a, b), 6.0 - 1e-9)


class SplineTests(unittest.TestCase):
    def test_a_single_point(self):
        self.assertEqual(catmull_rom([(1, 2)]), [(1, 2)])

    def test_two_points_stay_a_straight_segment(self):
        dense = catmull_rom([(0, 0), (10, 0)], samples=4)
        self.assertTrue(all(abs(y) < 1e-6 for _, y in dense))

    def test_the_curve_passes_through_every_control_point(self):
        points = [(0, 0), (10, 5), (20, 0), (30, 8), (40, 2)]
        dense = catmull_rom(points, samples=8)
        for control in points:
            nearest = min(math.dist(control, p) for p in dense)
            self.assertLess(nearest, 0.4,
                            f"curve misses control point {control}")

    def test_the_curve_starts_and_ends_on_the_data(self):
        points = [(0, 0), (10, 5), (20, 0)]
        dense = catmull_rom(points, samples=8)
        self.assertAlmostEqual(math.dist(dense[0], points[0]), 0.0, places=6)
        self.assertAlmostEqual(math.dist(dense[-1], points[-1]), 0.0, places=6)

    def test_a_straight_line_stays_straight(self):
        points = [(i * 10, 0) for i in range(5)]
        dense = catmull_rom(points, samples=6)
        self.assertLess(max(abs(y) for _, y in dense), 1e-6)

    def test_sharper_sampling_gives_more_points(self):
        points = [(0, 0), (10, 5), (20, 0)]
        self.assertGreater(len(catmull_rom(points, samples=12)),
                           len(catmull_rom(points, samples=3)))


class VectorisedEquivalenceTests(unittest.TestCase):
    """The fit was rewritten from Python loops to whole-array maths.

    It had to be: on the board one rebuild of a 90-point trail cost 18 ms of a
    66 ms frame. Faster is only acceptable if it draws the same curve, so the
    original loop is kept here as the reference and the two are compared
    directly. A tolerance of 1e-9 is far tighter than a pixel.
    """

    @staticmethod
    def _reference_catmull_rom(points, samples=8):
        """The original implementation, kept only to check the fast one."""
        if len(points) < 2:
            return list(points)
        control = [points[0]] + list(points) + [points[-1]]
        dense = []
        for i in range(1, len(control) - 2):
            p0, p1, p2, p3 = (control[i - 1], control[i], control[i + 1],
                              control[i + 2])
            for step in range(samples):
                t = step / float(samples)
                t2 = t * t
                t3 = t2 * t
                x = 0.5 * (
                    (2 * p1[0])
                    + (-p0[0] + p2[0]) * t
                    + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                    + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3
                )
                y = 0.5 * (
                    (2 * p1[1])
                    + (-p0[1] + p2[1]) * t
                    + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                    + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3
                )
                dense.append((x, y))
        dense.append(tuple(points[-1]))
        return dense

    @staticmethod
    def _reference_moving_average(points, window):
        if window <= 1 or len(points) < 3:
            return list(points)
        half = window // 2
        result = [points[0]]
        for i in range(1, len(points) - 1):
            lo = max(0, i - half)
            hi = min(len(points), i + half + 1)
            span = points[lo:hi]
            result.append((
                sum(p[0] for p in span) / len(span),
                sum(p[1] for p in span) / len(span),
            ))
        result.append(points[-1])
        return result

    def _wiggly(self, count, seed=11):
        rng = random.Random(seed)
        return [(20.0 + i * 6.0 + rng.uniform(-3, 3),
                 120.0 + rng.uniform(-40, 40)) for i in range(count)]

    def test_the_spline_matches_the_loop_it_replaced(self):
        for count in (2, 3, 4, 9, 40, 120):
            for samples in (1, 3, 6, 12):
                points = self._wiggly(count)
                self.assertEqual(
                    catmull_rom(points, samples=samples),
                    self._reference_catmull_rom(points, samples=samples),
                    f"catmull_rom differs at {count} points, samples={samples}",
                )

    def test_the_average_matches_the_loop_it_replaced(self):
        for count in (3, 4, 8, 50, 130):
            for window in (2, 3, 7, 15):
                points = self._wiggly(count, seed=count)
                fast = moving_average(points, window)
                slow = self._reference_moving_average(points, window)
                self.assertEqual(len(fast), len(slow))
                for a, b in zip(fast, slow):
                    self.assertAlmostEqual(a[0], b[0], places=9)
                    self.assertAlmostEqual(a[1], b[1], places=9)

    def test_the_short_input_paths_are_untouched(self):
        """These return the caller's own points, so identity matters."""
        one = [(1, 2)]
        self.assertEqual(moving_average(one, 7), one)
        two = [(1, 1), (2, 2)]
        self.assertEqual(moving_average(two, 7), two)
        self.assertEqual(catmull_rom(one), one)

    def test_the_output_is_plain_python_tuples(self):
        """The renderer unpacks these; numpy scalars would leak if unguarded."""
        for point in catmull_rom(self._wiggly(6), samples=3):
            self.assertIsInstance(point, tuple)
            self.assertIsInstance(point[0], float)
            self.assertIsInstance(point[1], float)


class AverageTests(unittest.TestCase):
    def test_endpoints_are_preserved(self):
        points = [(0, 0), (10, 10), (20, 0), (30, 10)]
        averaged = moving_average(points, 3)
        self.assertEqual(averaged[0], points[0])
        self.assertEqual(averaged[-1], points[-1])

    def test_a_window_of_one_changes_nothing(self):
        points = staircase(10)
        self.assertEqual(moving_average(points, 1), points)

    def test_short_input_is_returned_unchanged(self):
        self.assertEqual(moving_average([(1, 1), (2, 2)], 5), [(1, 1), (2, 2)])


class SmoothingEffectTests(unittest.TestCase):
    """Does it actually make the path look like a path?"""

    def test_the_raw_trail_is_genuinely_jagged(self):
        """Baseline: without this work the problem is real, not imagined."""
        raw = staircase()
        self.assertGreater(trail.turning(raw), 100.0)

    def test_smoothing_reduces_the_turning_a_lot(self):
        raw = staircase()
        dense = build(raw)
        raw_turn = trail.turning(raw)
        smooth_turn = trail.turning(dense)
        self.assertLess(
            smooth_turn, raw_turn * 0.2,
            f"turning only fell from {raw_turn:.0f} to {smooth_turn:.0f} deg",
        )

    def test_the_smoothed_path_stays_near_the_recorded_points(self):
        raw = staircase()
        kept = simplify(raw, 6.0)
        dense = build(raw)
        deviation = trail.max_deviation(dense, kept)
        self.assertLess(
            deviation, 6.0,
            f"the curve strays {deviation:.1f} px from the recorded route",
        )

    def test_a_straight_run_stays_straight(self):
        raw = [(20 + i * 9, 100 + (2 if i % 2 else -2)) for i in range(40)]
        dense = build(raw)
        self.assertLess(trail.turning(dense), 12.0)

    def test_a_corner_is_rounded_but_not_cut(self):
        """The apex of a sharp turn must survive, only the corner is eased."""
        raw = ([(20 + i * 8, 60) for i in range(12)]
               + [(116, 60 + i * 8) for i in range(1, 12)])
        dense = build(raw, min_move=0.0, smooth_window=3)
        # The curve should still reach close to the corner.
        corner = (116, 60)
        nearest = min(math.dist(corner, p) for p in dense)
        self.assertLess(nearest, 8.0)
        self.assertLess(trail.turning(dense), trail.turning(raw))

    def test_an_empty_or_tiny_trail_is_returned_safely(self):
        self.assertEqual(build([]), [])
        self.assertEqual(len(build([(1, 1)])), 1)
        self.assertEqual(len(build([(1, 1), (30, 30)])), 2)


class TrailStoreTests(unittest.TestCase):
    def test_the_first_point_is_recorded_immediately(self):
        store = TrailStore()
        self.assertTrue(store.add(1, (10, 10)))
        self.assertEqual(store.path(1), [(10.0, 10.0)])

    def test_a_point_inside_the_deadband_is_not_recorded(self):
        store = TrailStore(min_move=6.0)
        store.add(1, (10, 10))
        self.assertFalse(store.add(1, (13, 12)), "moved under 6 px")
        self.assertEqual(len(store.recorded(1)), 1)

    def test_a_point_outside_the_deadband_is_recorded(self):
        store = TrailStore(min_move=6.0)
        store.add(1, (10, 10))
        self.assertTrue(store.add(1, (30, 10)))
        self.assertEqual(len(store.recorded(1)), 2)

    def test_the_path_is_rebuilt_only_on_a_recorded_point(self):
        """This is what keeps the fit off the per-frame path."""
        store = TrailStore(min_move=6.0)
        store.add(1, (10, 10))
        before = store.rebuilds
        for dx in (1, 2, 3, 4, 5):
            store.add(1, (10 + dx, 10))
        self.assertEqual(store.rebuilds, before,
                         "a rebuild happened without a new point")
        store.add(1, (40, 10))
        self.assertEqual(store.rebuilds, before + 1)

    def test_the_path_is_available_without_rebuilding(self):
        store = TrailStore(min_move=6.0)
        for i in range(12):
            store.add(1, (20 + i * 10, 100 + (i % 2) * 8))
        cached = store.path(1)
        self.assertGreater(len(cached), 2)
        # Reading it repeatedly must not change or recompute anything.
        self.assertIs(store.path(1), cached)

    def test_trails_are_dropped_immediately_when_asked(self):
        store = TrailStore()
        store.add(1, (10, 10), timestamp=0.0)
        store.add(2, (50, 50), timestamp=0.0)
        store.drop_except([2], keep_lingering=False)
        self.assertEqual(store.path(1), ())
        self.assertEqual(len(store.path(2)), 1)

    def test_a_trail_lingers_after_its_track_goes(self):
        """Stopping mid-gesture must not wipe the trail off the screen."""
        store = TrailStore(linger=2.0)
        for i in range(20):
            store.add(1, (20 + i * 10, 100), timestamp=i * 0.1)
        # Last recorded at t=1.9, so it should survive until t=3.9.
        self.assertNotEqual(store.path(1), ())
        store.prune(3.5)
        self.assertNotEqual(store.path(1), (), "trail vanished too early")
        store.prune(4.5)
        self.assertEqual(store.path(1), (), "trail outlived its linger")

    def test_linger_zero_clears_at_once(self):
        store = TrailStore(linger=0.0)
        store.add(1, (10, 10), timestamp=0.0)
        store.prune(0.001)
        self.assertEqual(store.path(1), ())

    def test_the_point_count_is_bounded(self):
        store = TrailStore(min_move=1.0, max_points=20, seconds=1000.0)
        for i in range(200):
            store.add(1, (i * 5, 100), timestamp=i * 0.01)
        self.assertLessEqual(len(store.recorded(1)), 20)

    def test_stats_describe_the_work_done(self):
        store = TrailStore(min_move=6.0)
        for i in range(20):
            store.add(1, (20 + i * 10, 100), timestamp=i * 0.1)
        stats = store.stats()
        self.assertEqual(stats["tracks"], 1)
        self.assertEqual(stats["points"], 20)
        self.assertGreater(stats["drawn"], stats["points"])
        self.assertEqual(stats["seconds"], 6.0)

    def test_bad_configuration_is_rejected(self):
        with self.assertRaises(ValueError):
            TrailStore(min_move=-1)
        with self.assertRaises(ValueError):
            TrailStore(max_points=1)
        with self.assertRaises(ValueError):
            TrailStore(seconds=0)
        with self.assertRaises(ValueError):
            TrailStore(linger=-1)

    def test_a_noisy_stationary_target_records_few_points(self):
        """The case the dead band is for: a still target must not scribble."""
        rng = random.Random(9)
        store = TrailStore(min_move=6.0)
        for i in range(200):
            store.add(1, (100 + rng.uniform(-2, 2), 120 + rng.uniform(-2, 2)),
                      timestamp=i / 15.0)
        self.assertLess(len(store.recorded(1)), 8,
                        f"recorded {len(store.recorded(1))} points while still")


class ContinuityTests(unittest.TestCase):
    """How far the drawn trail's end sits behind the target.

    A large dead band makes the trail grow in visible steps -- it is a DISTANCE,
    not a time, but the effect the user sees is that the trail looks
    disconnected from the box. These pin the two numbers that decide it.
    """

    def test_the_trail_end_stays_within_the_dead_band_of_the_target(self):
        store = TrailStore(min_move=3.0)
        rng = random.Random(2)
        for i in range(120):
            point = (20 + i * 2 + rng.uniform(-1, 1),
                     100 + rng.uniform(-3, 3))
            store.add(1, point, timestamp=i / 15.0)
            last = store.recorded(1)[-1]
            gap = math.dist((last[1], last[2]), point)
            self.assertLess(
                gap, store.min_move + 1e-6,
                f"trail ends {gap:.1f} px behind the target at frame {i}",
            )

    def test_a_smaller_dead_band_records_more_often(self):
        """More frequent recording is what makes the growth look continuous."""
        moving = staircase(count=60, drift=1.2, jitter=8.0)
        coarse = simplify(moving, 6.0)
        fine = simplify(moving, 3.0)
        self.assertGreater(len(fine), len(coarse) * 1.5)

    def test_a_tighter_dead_band_does_not_make_the_curve_worse(self):
        """Measured, it makes it better: the curve hugs the route more closely.

        Tightening the dead band adds control points, which sounds like it
        should add wiggle. It does not: the extra points are real positions, so
        the fitted curve has less room to cut corners.
        """
        raw = staircase(count=60, drift=1.2, jitter=8.0)
        coarse_points = simplify(raw, 6.0)
        fine_points = simplify(raw, 3.0)
        coarse = build(raw, min_move=6.0, smooth_window=7)
        fine = build(raw, min_move=3.0, smooth_window=7)
        self.assertLessEqual(trail.turning(fine), trail.turning(coarse) + 2.0)
        self.assertLess(
            trail.max_deviation(fine, fine_points),
            trail.max_deviation(coarse, coarse_points),
        )

    def test_the_defaults_are_the_tuned_ones(self):
        store = TrailStore()
        self.assertEqual(store.min_move, 3.0)
        self.assertEqual(store.smooth_window, 7)
        self.assertEqual(store.linger, 1.0)
        self.assertEqual(store.seconds, 6.0)


class DurationTests(unittest.TestCase):
    """How long the trail lasts, which is the whole point of the change.

    The old setting was a point count, so the trail's duration depended on how
    fast the target moved: measured, a fast target kept about 7 s of history
    and a slow one over 20 s from the same number.
    """

    def _span(self, store, track_id):
        points = store.recorded(track_id)
        return points[-1][0] - points[0][0]

    def _feed(self, store, track_id, seconds, pixels_per_second, dt=1 / 15.0):
        elapsed = 0.0
        x = 20.0
        step = pixels_per_second * dt
        while elapsed < seconds:
            store.add(track_id, (x, 100), timestamp=elapsed)
            x += step
            elapsed += dt

    def test_the_trail_span_is_about_the_configured_seconds(self):
        store = TrailStore(min_move=1.0, seconds=6.0, linger=0.0)
        self._feed(store, 1, seconds=20.0, pixels_per_second=400.0)
        span = self._span(store, 1)
        self.assertGreater(span, 4.0)
        self.assertLess(span, 7.0,
                        f"trail spans {span:.1f} s for a 6 s setting")

    def test_a_slow_target_gets_the_same_duration(self):
        """The regression: duration must not follow the target's speed."""
        fast = TrailStore(min_move=1.0, seconds=6.0, linger=0.0)
        slow = TrailStore(min_move=1.0, seconds=6.0, linger=0.0)
        self._feed(fast, 1, seconds=20.0, pixels_per_second=400.0)
        self._feed(slow, 1, seconds=20.0, pixels_per_second=10.0)
        self.assertAlmostEqual(self._span(fast, 1), self._span(slow, 1),
                               delta=1.5)

    def test_a_longer_setting_gives_a_longer_trail(self):
        short = TrailStore(min_move=1.0, seconds=3.0, linger=0.0)
        long = TrailStore(min_move=1.0, seconds=9.0, linger=0.0)
        for store in (short, long):
            self._feed(store, 1, seconds=20.0, pixels_per_second=200.0)
        self.assertGreater(self._span(long, 1), self._span(short, 1) * 2)

    def test_the_cost_stays_affordable_at_the_long_setting(self):
        store = TrailStore(min_move=1.0, seconds=12.0, max_points=400)
        self._feed(store, 1, seconds=20.0, pixels_per_second=400.0)
        points = len(store.recorded(1))
        cost = trail.measure_cost(points=points)
        limit = BOARD_FRAME_MS / 2.0
        self.assertLess(
            cost, limit,
            f"{points} points cost {cost:.1f} ms, over half of the board's "
            f"{BOARD_FRAME_MS:.0f} ms frame ({limit:.0f} ms)",
        )


class IntegrationTests(unittest.TestCase):
    """Drive it from a real tracker, which is where the jitter comes from."""

    def test_trail_from_tracker_output_is_smoother_than_the_history(self):
        from target_tracker import TargetTracker
        tracker = TargetTracker()
        store = TrailStore(min_move=6.0)
        dt = 1 / 15.0
        rng = random.Random(4)

        raw_history = []
        for i in range(90):
            box = (20 + i * 3 + rng.uniform(-4, 4),
                   100 + rng.uniform(-6, 6), 40, 60)
            tracks = tracker.update([box], timestamp=i * dt)
            for t in tracks:
                store.add(t.id, t.center, timestamp=i * dt)
                raw_history.append(t.center)

        track_ids = list(store.trail_ids())
        self.assertEqual(len(track_ids), 1)
        dense = store.path(track_ids[0])
        self.assertGreater(len(dense), 2)
        self.assertLess(
            trail.turning(dense), trail.turning(raw_history) * 0.3,
            f"smoothed turn {trail.turning(dense):.0f} deg vs raw "
            f"{trail.turning(raw_history):.0f} deg",
        )

    def test_the_cost_is_a_small_fraction_of_a_frame(self):
        """Guards the 'it cannot delay the picture' claim with a number.

        The budget is a third of a frame on the slowest platform this runs on.
        The fit as first written cost 51 ms for 120 points there and would fail
        this; it is 8 ms now.
        """
        cost = trail.measure_cost(points=120)
        limit = BOARD_FRAME_MS / 3.0
        self.assertLess(
            cost, limit,
            f"a 120-point fit takes {cost:.1f} ms, over a third of the board's "
            f"{BOARD_FRAME_MS:.0f} ms frame ({limit:.0f} ms)",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
