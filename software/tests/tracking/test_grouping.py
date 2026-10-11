"""Tests for strip grouping.

The two failure modes being fixed pull in opposite directions -- merging two
edges of one object (or the count doubles) and merging two different people (or
the count halves) -- so most of these test the refusals, not the pairings.

The controlled cases use a stub with an exact velocity so the geometry is known;
the last one drives the real tracker so the offsets come from a real detector's
edge strips rather than from arithmetic.
"""
import math
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "pynq_deploy")))

from grouping import StripGrouper  # noqa: E402


class StubTrack:
    """Just enough of the tracker's Track for the grouper."""

    def __init__(self, track_id, center, velocity=(0.0, 0.0)):
        self.id = track_id
        self.center = center
        self._velocity = velocity

    def motion(self):
        vx, vy = self._velocity
        return (vx, vy, math.hypot(vx, vy), None)


def drive(grouper, frames):
    """frames: list of (timestamp, [StubTrack, ...])"""
    for timestamp, tracks in frames:
        grouper.observe(tracks, timestamp)
    return grouper


def pair_moving_together(gap=60.0, speed=40.0, count=8, wobble=0.0):
    """Two tracks holding `gap` px apart and moving identically."""
    dt = 1 / 15.0
    frames = []
    for index in range(count):
        t = index * dt
        x = 100.0 + speed * t
        jitter = wobble * (1 if index % 2 else -1)
        frames.append((t, [
            StubTrack(1, (x, 120.0), (speed, 0.0)),
            StubTrack(2, (x - gap + jitter, 120.0), (speed, 0.0)),
        ]))
    return frames


class PairingTests(unittest.TestCase):
    def test_two_edges_of_one_object_are_paired(self):
        grouper = drive(StripGrouper(), pair_moving_together())
        self.assertTrue(grouper.same_object(1, 2),
                        f"a rigid 60 px pair was not proven: {grouper.info()}")

    def test_the_pair_is_symmetric(self):
        grouper = drive(StripGrouper(), pair_moving_together())
        self.assertEqual(grouper.same_object(1, 2),
                         grouper.same_object(2, 1))

    def test_two_objects_moving_differently_are_not_paired(self):
        dt = 1 / 15.0
        frames = []
        for index in range(10):
            t = index * dt
            frames.append((t, [
                StubTrack(1, (60.0 + 60.0 * t, 120.0), (60.0, 0.0)),
                StubTrack(2, (200.0 - 50.0 * t, 120.0), (-50.0, 0.0)),
            ]))
        grouper = drive(StripGrouper(), frames)
        self.assertFalse(grouper.same_object(1, 2))

    def test_two_people_walking_apart_are_not_merged(self):
        """The under-count case: two objects that start close together."""
        dt = 1 / 15.0
        frames = []
        for index in range(10):
            t = index * dt
            frames.append((t, [
                StubTrack(1, (100.0 + 50.0 * t, 120.0), (50.0, 0.0)),
                StubTrack(2, (120.0 + 30.0 * t, 120.0), (30.0, 0.0)),
            ]))
        grouper = drive(StripGrouper(), frames)
        self.assertFalse(grouper.same_object(1, 2))
        self.assertTrue(grouper.definitely_distinct(1, 2))

    def test_a_single_frame_is_not_evidence(self):
        grouper = drive(StripGrouper(), pair_moving_together(count=1))
        self.assertFalse(grouper.same_object(1, 2))

    def test_too_few_frames_are_not_evidence(self):
        grouper = drive(StripGrouper(), pair_moving_together(count=3))
        self.assertFalse(grouper.same_object(1, 2),
                         "three frames must not be enough to prove a pair")

    def test_a_separation_beyond_the_range_is_refused(self):
        """Stable, but 200 px apart is not one object."""
        grouper = drive(StripGrouper(), pair_moving_together(gap=200.0))
        self.assertFalse(grouper.same_object(1, 2))
        self.assertTrue(grouper.definitely_distinct(1, 2))

    def test_a_separation_inside_the_range_is_accepted(self):
        for gap in (30.0, 60.0, 100.0):
            grouper = drive(StripGrouper(), pair_moving_together(gap=gap))
            self.assertTrue(grouper.same_object(1, 2),
                            f"a rigid {gap:.0f} px pair was refused")

    def test_a_wandering_offset_is_not_proven(self):
        """Holding station but breathing apart is not a rigid pair."""
        grouper = drive(StripGrouper(),
                        pair_moving_together(wobble=12.0, count=10))
        self.assertFalse(grouper.same_object(1, 2))

    def test_matching_offset_but_different_speed_is_refused(self):
        dt = 1 / 15.0
        frames = []
        for index in range(8):
            t = index * dt
            # Same 60 px offset maintained, but one races ahead: impossible for
            # a rigid object unless the offset drifts, which it then does.
            frames.append((t, [
                StubTrack(1, (100.0 + 200.0 * t, 120.0), (200.0, 0.0)),
                StubTrack(2, (40.0 + 20.0 * t, 120.0), (20.0, 0.0)),
            ]))
        grouper = drive(StripGrouper(), frames)
        self.assertFalse(grouper.same_object(1, 2))


class GroupLabelTests(unittest.TestCase):
    def test_both_edges_report_the_same_group(self):
        grouper = drive(StripGrouper(), pair_moving_together())
        self.assertEqual(grouper.group_of(1), grouper.group_of(2))

    def test_an_unpaired_track_is_its_own_group(self):
        grouper = StripGrouper()
        self.assertEqual(grouper.group_of(99), 99)

    def test_the_label_is_the_smallest_member(self):
        grouper = drive(StripGrouper(), pair_moving_together())
        self.assertEqual(grouper.group_of(2), 1)
        self.assertEqual(grouper.groups(), {1: 1, 2: 1})


class HousekeepingTests(unittest.TestCase):
    def test_a_vanished_track_forgets_its_samples(self):
        grouper = StripGrouper()
        drive(grouper, pair_moving_together(count=2))
        self.assertEqual(grouper.info()["tracked"], 2)
        grouper.observe([], 1.0)
        self.assertEqual(grouper.info()["tracked"], 0)

    def test_a_re_used_id_does_not_inherit_old_samples(self):
        grouper = StripGrouper()
        drive(grouper, pair_moving_together(count=6))
        self.assertTrue(grouper.same_object(1, 2))
        grouper.observe([], 0.4)
        # Same ids, now far apart. One frame is enough: being 346 px apart is a
        # geometric fact, not a statistic.
        drive(grouper, [(0.5, [StubTrack(1, (10.0, 10.0)),
                               StubTrack(2, (300.0, 200.0))])])
        self.assertFalse(grouper.same_object(1, 2),
                         "a stale pairing survived the tracks reappearing")

    def test_a_brief_loss_of_one_edge_keeps_the_evidence(self):
        """The case the pairing exists for: a gap caused by a lost track."""
        grouper = StripGrouper(pair_memory_seconds=3.0)
        drive(grouper, pair_moving_together(count=6))
        self.assertTrue(grouper.same_object(1, 2))
        grouper.observe([StubTrack(1, (200.0, 120.0), (40.0, 0.0))], 0.4)
        self.assertTrue(grouper.same_object(1, 2),
                        "one missing edge threw away good evidence")

    def test_a_verdict_expires_so_the_tables_cannot_grow(self):
        grouper = StripGrouper(pair_memory_seconds=1.0)
        drive(grouper, pair_moving_together(count=6))
        self.assertEqual(grouper.info()["proven_pairs"], 1)
        grouper.observe([], 5.0)
        self.assertEqual(grouper.info()["proven_pairs"], 0,
                         "verdicts are never forgotten")
        self.assertFalse(grouper.same_object(1, 2))

    def test_validation_rejects_self_contradicting_thresholds(self):
        with self.assertRaises(ValueError):
            StripGrouper(min_offset_change=4.0, max_offset_std=6.0)
        with self.assertRaises(ValueError):
            StripGrouper(min_samples=1)
        with self.assertRaises(ValueError):
            StripGrouper(min_offset=100.0, max_offset=50.0)
        with self.assertRaises(ValueError):
            StripGrouper(pair_memory_seconds=0)

    def test_info_reports_the_proven_pairs(self):
        grouper = drive(StripGrouper(), pair_moving_together())
        info = grouper.info()
        self.assertEqual(info["proven_pairs"], 1)
        self.assertEqual(info["pairs"], [[1, 2]])


class RealTrackerTests(unittest.TestCase):
    """Drive the real tracker, so the offsets are a real detector's edges."""

    def _run(self, boxes_per_frame):
        from target_tracker import TargetTracker
        tracker = TargetTracker()
        grouper = StripGrouper()
        dt = 1 / 15.0
        for index, boxes in enumerate(boxes_per_frame):
            tracks = tracker.update(boxes, timestamp=index * dt)
            grouper.observe(tracks, index * dt)
        return grouper

    def test_one_object_reported_as_two_edges_is_paired(self):
        """The shipped chain's actual behaviour: a leading and trailing strip."""
        frames = []
        for index in range(12):
            x = 40.0 + index * 12.0
            frames.append([(x, 100.0, 40.0, 60.0),
                           (x - 58.0, 100.0, 40.0, 60.0)])
        grouper = self._run(frames)
        self.assertEqual(grouper.info()["proven_pairs"], 1,
                         f"the two edges were not paired: {grouper.info()}")

    def test_two_objects_moving_apart_are_not_paired(self):
        frames = []
        for index in range(12):
            frames.append([(40.0 + index * 10.0, 60.0, 30.0, 40.0),
                           (200.0 - index * 9.0, 180.0, 30.0, 40.0)])
        grouper = self._run(frames)
        self.assertEqual(grouper.info()["proven_pairs"], 0,
                         f"two independent objects were merged: {grouper.info()}")


class EndToEndTests(unittest.TestCase):
    """Grouping plus the counter, on the failure it was built for.

    The gap used here is the one actually measured on real footage: two edges
    crossing 1.47 s apart. The counter's time window is 0.8 s and cannot reach
    that, so without grouping the object is counted twice -- which is exactly
    the double count this was built to remove.
    """

    LINE = (100.0, 40.0, 100.0, 220.0)

    def _run(self, group_strips, gap=58.0, speed=40.0, frames=70):
        from target_tracker import TargetTracker
        from line_crossing import LineCrossingCounter

        tracker = TargetTracker()
        grouper = StripGrouper() if group_strips else None
        counter = LineCrossingCounter(self.LINE, grouping=grouper)
        dt = 1 / 15.0
        for index in range(frames):
            t = index * dt
            x = 60.0 + speed * t
            boxes = [(x, 120.0, 40.0, 60.0),
                     (x - gap, 120.0, 40.0, 60.0)]
            tracks = tracker.update(boxes, timestamp=t)
            if grouper is not None:
                grouper.observe(tracks, t)
            counter.update([(tr.id, tr.center) for tr in tracks], timestamp=t)
        return counter, grouper

    def test_a_long_gap_double_count_needs_grouping(self):
        """Establishes the problem: without grouping it counts twice."""
        counter, _ = self._run(group_strips=False)
        self.assertEqual(
            counter.total(), 2,
            f"the premise of this feature no longer holds -- one object with a "
            f"1.45 s edge gap counted {counter.total()} times, expected 2")

    def test_grouping_makes_it_count_once(self):
        counter, grouper = self._run(group_strips=True)
        self.assertEqual(
            counter.total(), 1,
            f"one object counted {counter.total()} times: {counter.info()}")
        self.assertEqual(grouper.info()["proven_pairs"], 1,
                         "the two edges were never proven to be one object")

    def test_the_suppressed_crossing_is_explained_in_the_log(self):
        counter, _ = self._run(group_strips=True)
        duplicates = [e for e in counter.events if e["outcome"] == "duplicate"]
        self.assertEqual(len(duplicates), 1, counter.events)
        self.assertIn("track", duplicates[0]["detail"])

    def test_two_separate_objects_still_count_twice(self):
        """The opposite error: grouping must not halve a genuine pair."""
        from target_tracker import TargetTracker
        from line_crossing import LineCrossingCounter

        tracker = TargetTracker()
        grouper = StripGrouper()
        counter = LineCrossingCounter(self.LINE, grouping=grouper)
        dt = 1 / 15.0
        for index in range(70):
            t = index * dt
            # Two objects far apart in y, crossing at different times.
            boxes = [(60.0 + 40.0 * t, 60.0, 40.0, 50.0),
                     (20.0 + 40.0 * t, 190.0, 40.0, 50.0)]
            tracks = tracker.update(boxes, timestamp=t)
            grouper.observe(tracks, t)
            counter.update([(tr.id, tr.center) for tr in tracks], timestamp=t)
        self.assertEqual(counter.total(), 2,
                         f"two separate objects counted {counter.total()} "
                         f"times: {counter.info()}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
