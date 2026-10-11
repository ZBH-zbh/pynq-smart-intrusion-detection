"""Tests for the tripwire counter.

The interesting cases are not "a target walks across"; they are the ways a
naive implementation over-counts: a target resting on the line, a target
crossing the line's extension rather than the drawn segment, and a track that
is lost and re-acquired.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
DEPLOY = os.path.abspath(os.path.join(HERE, "..", "pynq_deploy"))
sys.path.insert(0, DEPLOY)
sys.path.insert(0, HERE)

from line_crossing import LineCrossingCounter  # noqa: E402


# A vertical tripwire at x=100, spanning y=50..190.
VERTICAL = (100, 50, 100, 190)
# A horizontal tripwire at y=100, spanning x=50..250.
HORIZONTAL = (50, 100, 250, 100)


def feed(counter, track_id, xs, y=120, start=0.0, dt=1 / 15.0):
    """Walk one track along a list of x positions."""
    events = []
    for i, x in enumerate(xs):
        events += counter.update([(track_id, (x, y))], timestamp=start + i * dt)
    return events


class GeometryTests(unittest.TestCase):
    def test_sides_of_a_vertical_line(self):
        counter = LineCrossingCounter(VERTICAL)
        self.assertLess(counter.signed_distance((50, 120)), 0)
        self.assertGreater(counter.signed_distance((150, 120)), 0)

    def test_sides_of_a_horizontal_line(self):
        counter = LineCrossingCounter(HORIZONTAL)
        # Image y grows downward, so "above" is negative y.
        self.assertGreater(counter.signed_distance((150, 50)), 0)
        self.assertLess(counter.signed_distance((150, 150)), 0)

    def test_labels_follow_the_orientation(self):
        self.assertEqual(LineCrossingCounter(VERTICAL).labels(),
                         ("L>R", "R>L"))
        self.assertEqual(LineCrossingCounter(HORIZONTAL).labels(),
                         ("D>U", "U>D"))

    def test_a_zero_length_line_is_rejected(self):
        with self.assertRaises(ValueError):
            LineCrossingCounter((10, 10, 10, 10))

    def test_a_malformed_line_is_rejected(self):
        for bad in (None, (1, 2, 3)):
            with self.assertRaises(ValueError):
                LineCrossingCounter(bad)

    def test_a_negative_deadband_is_rejected(self):
        with self.assertRaises(ValueError):
            LineCrossingCounter(VERTICAL, deadband=-1)


class CountingTests(unittest.TestCase):
    def test_one_crossing_counts_once(self):
        counter = LineCrossingCounter(VERTICAL)
        events = feed(counter, 1, [40, 60, 80, 100, 120, 140, 160])
        self.assertEqual(len(events), 1)
        self.assertEqual(counter.negative_to_positive, 1)
        self.assertEqual(counter.positive_to_negative, 0)

    def test_crossing_back_counts_the_other_direction(self):
        counter = LineCrossingCounter(VERTICAL)
        feed(counter, 1, [40, 80, 120, 160])          # rightwards
        feed(counter, 1, [120, 80, 40], start=1.0)    # leftwards
        self.assertEqual(counter.negative_to_positive, 1)
        self.assertEqual(counter.positive_to_negative, 1)
        self.assertEqual(counter.total(), 2)

    def test_two_tracks_count_independently(self):
        counter = LineCrossingCounter(VERTICAL)
        for i, x in enumerate([40, 80, 120, 160]):
            counter.update([(1, (x, 120)), (2, (200 - x, 120))],
                           timestamp=i / 15.0)
        self.assertEqual(counter.total(), 2)

    def test_appearing_inside_the_zone_does_not_count(self):
        """A track that starts to the left must not count until it crosses."""
        counter = LineCrossingCounter(VERTICAL)
        feed(counter, 1, [40, 60, 80, 90])
        self.assertEqual(counter.total(), 0)

    def test_re_acquisition_after_a_gap_is_not_a_crossing(self):
        """A lost track restarts with no side, so its return is not counted."""
        counter = LineCrossingCounter(VERTICAL)
        feed(counter, 1, [140, 160, 180])       # right of the line
        counter.update([], timestamp=1.0)       # track disappears
        events = feed(counter, 1, [140, 160], start=1.1)
        self.assertEqual(events, [])
        self.assertEqual(counter.total(), 0)


class OverCountingTests(unittest.TestCase):
    """The cases a naive implementation gets wrong."""

    def test_resting_on_the_line_does_not_count(self):
        counter = LineCrossingCounter(VERTICAL, deadband=4.0)
        # Jitter around x=100 by a couple of pixels for a hundred frames.
        jitter = [99, 101, 100, 102, 98, 100, 101, 99] * 12
        events = []
        for i, x in enumerate(jitter):
            events += counter.update([(1, (x, 120))], timestamp=i / 15.0)
        self.assertEqual(
            events, [],
            f"a target resting on the line produced {len(events)} crossings",
        )
        self.assertEqual(counter.total(), 0)

    def test_approaching_the_line_and_backing_off_does_not_count(self):
        counter = LineCrossingCounter(VERTICAL, deadband=4.0)
        events = feed(counter, 1, [40, 60, 80, 97, 60, 40] * 3)
        self.assertEqual(events, [])
        self.assertEqual(counter.total(), 0)

    def test_jitter_inside_the_band_then_a_real_crossing_counts_once(self):
        counter = LineCrossingCounter(VERTICAL, deadband=4.0)
        # Fidget within +-3 px of the line, none of which is a clear crossing,
        # and then actually move well past it.
        xs = [80, 97, 101, 99, 103, 98, 110, 130]
        events = feed(counter, 1, xs)
        self.assertEqual(len(events), 1, f"expected one crossing, got {events}")
        self.assertEqual(counter.total(), 1)

    def test_moving_within_the_band_is_never_a_crossing(self):
        """The band is what defines 'clearly on one side'."""
        counter = LineCrossingCounter(VERTICAL, deadband=4.0)
        events = feed(counter, 1, [97, 103, 97, 103, 99, 101])
        self.assertEqual(events, [])

    def test_crossing_the_extension_does_not_count(self):
        """Outside the drawn segment there is no tripwire."""
        counter = LineCrossingCounter(VERTICAL)      # spans y=50..190
        events = feed(counter, 1, [40, 80, 120, 160], y=220)
        self.assertEqual(events, [], "crossed above the segment's end")
        self.assertEqual(counter.total(), 0)

    def test_crossing_just_inside_the_segment_end_counts(self):
        counter = LineCrossingCounter(VERTICAL)      # spans y=50..190
        events = feed(counter, 1, [40, 80, 120, 160], y=188)
        self.assertEqual(len(events), 1)

    def test_a_fast_crossing_still_counts(self):
        """A large jump must not slip past the dead band logic."""
        counter = LineCrossingCounter(VERTICAL)
        events = feed(counter, 1, [40, 160])
        self.assertEqual(len(events), 1)

    def test_the_reported_point_is_on_the_line(self):
        counter = LineCrossingCounter(VERTICAL)
        # Three samples, because a crossing is only counted once the target has
        # committed to the far side; two would leave it still waiting.
        events = feed(counter, 1, [90, 110, 130])
        self.assertEqual(len(events), 1)
        x, y = events[0].point
        self.assertAlmostEqual(x, 100.0, delta=0.5)
        self.assertTrue(50 <= y <= 190)

    def test_the_crossing_point_is_interpolated(self):
        """A fast target's crossing point must be between the two samples."""
        counter = LineCrossingCounter(VERTICAL)
        events = feed(counter, 1, [80, 120])
        self.assertEqual(len(events), 1)
        # Half way in x, and y taken from the samples.
        self.assertAlmostEqual(events[0].point[0], 100.0, delta=0.5)


class ConfigurationTests(unittest.TestCase):
    def test_moving_the_line_resets_the_counts(self):
        counter = LineCrossingCounter(VERTICAL)
        feed(counter, 1, [40, 80, 120, 160])
        self.assertEqual(counter.total(), 1)
        counter.set_line((200, 50, 200, 190))
        self.assertEqual(counter.total(), 0)
        self.assertEqual(counter.crossings, [])

    def test_reset_counts_keeps_the_line(self):
        counter = LineCrossingCounter(VERTICAL)
        feed(counter, 1, [40, 80, 120, 160])
        counter.reset_counts()
        self.assertEqual(counter.total(), 0)
        self.assertEqual(counter.line, VERTICAL)

    def test_counts_are_labelled(self):
        counter = LineCrossingCounter(VERTICAL)
        feed(counter, 1, [40, 80, 120, 160])
        self.assertEqual(counter.counts(), {"L>R": 1, "R>L": 0})

    def test_info_reports_everything_the_page_needs(self):
        counter = LineCrossingCounter(VERTICAL)
        feed(counter, 1, [40, 80, 120, 160])
        info = counter.info()
        self.assertEqual(info["line"], list(VERTICAL))
        self.assertEqual(info["labels"], ["L>R", "R>L"])
        self.assertEqual(info["counts"], [1, 0])
        self.assertEqual(info["total"], 1)


class DedupTests(unittest.TestCase):
    """The shipped chain reports one object as two targets: its two edges.

    Both cross the line, so without this a walking person is counted twice.
    """

    def _two_edges(self, counter, gap=30, delay=0.2):
        """Leading and trailing edges of one object, gap px apart in time."""
        dt = 1 / 15.0
        events = []
        for i in range(40):
            x = 20 + i * 6
            points = [(1, (x, 120))]
            if i >= int(delay / dt):
                points.append((2, (x - gap, 120)))
            events += counter.update(points, timestamp=i * dt)
        return events

    def test_two_edges_of_one_object_count_once(self):
        counter = LineCrossingCounter(VERTICAL)
        events = self._two_edges(counter)
        self.assertEqual(
            len(events), 1,
            f"one object counted {len(events)} times: {events}",
        )
        self.assertEqual(counter.total(), 1)

    def test_dedup_can_be_switched_off(self):
        counter = LineCrossingCounter(VERTICAL, dedup_distance=0)
        events = self._two_edges(counter)
        self.assertEqual(len(events), 2)

    def test_the_same_track_crossing_back_and_forth_still_counts_twice(self):
        """A there-and-back movement is real and must not be deduplicated."""
        counter = LineCrossingCounter(VERTICAL)
        feed(counter, 1, [40, 80, 120, 160])
        feed(counter, 1, [120, 80, 40], start=1.0)
        self.assertEqual(counter.total(), 2)

    def test_two_objects_well_apart_both_count(self):
        counter = LineCrossingCounter(VERTICAL)
        dt = 1 / 15.0
        for i in range(40):
            x = 20 + i * 6
            counter.update([(1, (x, 70)), (2, (x, 175))],
                           timestamp=i * dt)
        self.assertEqual(counter.total(), 2,
                         "objects 105 px apart are not one object")

    def test_objects_crossing_in_opposite_directions_both_count(self):
        counter = LineCrossingCounter(VERTICAL)
        dt = 1 / 15.0
        for i in range(40):
            x = 20 + i * 6
            counter.update([(1, (x, 120)), (2, (240 - x, 120))],
                           timestamp=i * dt)
        self.assertEqual(counter.total(), 2)
        self.assertEqual(counter.negative_to_positive, 1)
        self.assertEqual(counter.positive_to_negative, 1)

    def test_a_second_object_following_later_counts(self):
        """Beyond the dedup window it is clearly a different object."""
        counter = LineCrossingCounter(VERTICAL)
        feed(counter, 1, [40, 80, 120, 160])
        feed(counter, 2, [40, 80, 120, 160], start=5.0)
        self.assertEqual(counter.total(), 2)

    def test_a_negative_dedup_window_is_rejected(self):
        with self.assertRaises(ValueError):
            LineCrossingCounter(VERTICAL, dedup_seconds=-1)


class ConfirmationTests(unittest.TestCase):
    """A crossing is counted only once the target commits to the far side.

    The thresholds here are not invented. Replaying a recorded session of an
    EMPTY ROOM produced three crossings, and the tracks behind them behaved like
    this, in signed distance from the line:

        +23.65 -> -14.62 -> -46.16 -> -99.06     counted (still counted: it
                                                 travels a long way, and is
                                                 dynamically a real crossing)
        -5.03  ->  +8.01 ->  -7.61  -> -51.21    phantom: only 8 px past, then
                                                 straight back
        -16.14 ->  +5.81 ->  -7.95  ->  -2.55    phantom: 5.8 px, then back

    So a threshold between 8 px and 14 px separates those cases, and 12 px is
    the middle of that gap. Nothing here is tuned to synthetic data.
    """

    def test_a_wobble_across_the_line_is_not_counted(self):
        counter = LineCrossingCounter(VERTICAL)
        feed(counter, 1, [90, 105], y=120)          # 5 px past, then back
        feed(counter, 1, [95], y=120, start=0.2)
        self.assertEqual(counter.total(), 0,
                         f"a 5 px wobble counted: {counter.info()}")

    def test_a_wobble_is_reported_as_reverted_not_silently_dropped(self):
        counter = LineCrossingCounter(VERTICAL)
        feed(counter, 1, [90, 105], y=120)
        feed(counter, 1, [95], y=120, start=0.2)
        outcomes = [e["outcome"] for e in counter.events]
        self.assertIn("pending", outcomes)
        self.assertIn("reverted", outcomes)
        self.assertNotIn("counted", outcomes)

    def test_a_committed_crossing_is_counted(self):
        counter = LineCrossingCounter(VERTICAL)
        feed(counter, 1, [90, 105, 130, 150], y=120)
        self.assertEqual(counter.total(), 1)
        self.assertEqual([e["outcome"] for e in counter.events][-1], "counted")

    def test_the_counted_point_is_where_it_met_the_line_not_where_it_committed(self):
        """The reported geometry must not drift to the confirming frame."""
        counter = LineCrossingCounter(VERTICAL)
        events = feed(counter, 1, [90, 110, 140], y=120)
        self.assertEqual(len(events), 1)
        self.assertAlmostEqual(events[0].point[0], 100.0, delta=0.5)

    def test_the_counted_time_is_when_it_met_the_line(self):
        counter = LineCrossingCounter(VERTICAL)
        events = feed(counter, 1, [90, 110, 140], y=120, start=0.0,
                      dt=1 / 15.0)
        self.assertEqual(len(events), 1)
        # The crossing happened on the second sample, not the third.
        self.assertAlmostEqual(events[0].timestamp, 1 / 15.0, delta=1e-9)

    def test_an_already_far_crossing_counts_immediately(self):
        """One huge jump past the threshold needs no extra frame."""
        counter = LineCrossingCounter(VERTICAL)
        events = feed(counter, 1, [40, 160], y=120)
        self.assertEqual(len(events), 1)
        self.assertEqual(counter.total(), 1)

    def test_a_track_lost_while_pending_is_reported_as_abandoned(self):
        counter = LineCrossingCounter(VERTICAL)
        feed(counter, 1, [90, 105], y=120)          # pending, only 5 px
        counter.update([], timestamp=1.0)           # track disappears
        self.assertEqual(counter.total(), 0)
        self.assertIn("abandoned", [e["outcome"] for e in counter.events])

    def test_a_crossing_that_never_commits_expires(self):
        counter = LineCrossingCounter(VERTICAL, confirm_seconds=0.5)
        feed(counter, 1, [90, 105], y=120, start=0.0)
        # Still on the far side, but not far enough, and time runs out.
        feed(counter, 1, [106], y=120, start=1.0)
        self.assertEqual(counter.total(), 0)
        self.assertIn("expired", [e["outcome"] for e in counter.events])

    def test_zero_threshold_restores_the_old_behaviour(self):
        """The escape hatch: previous behaviour is one argument away."""
        counter = LineCrossingCounter(VERTICAL, confirm_distance=0.0)
        events = feed(counter, 1, [90, 110], y=120)
        self.assertEqual(len(events), 1)
        self.assertEqual(counter.total(), 1)

    def test_a_negative_threshold_is_rejected(self):
        with self.assertRaises(ValueError):
            LineCrossingCounter(VERTICAL, confirm_distance=-1.0)
        with self.assertRaises(ValueError):
            LineCrossingCounter(VERTICAL, confirm_seconds=-1.0)

    def test_confirm_seconds_none_waits_as_long_as_the_track_lives(self):
        counter = LineCrossingCounter(VERTICAL, confirm_seconds=None)
        feed(counter, 1, [90, 105], y=120, start=0.0)
        events = feed(counter, 1, [130], y=120, start=30.0)
        self.assertEqual(len(events), 1, "should still commit when it gets there")

    def test_the_jitter_double_count_from_the_real_session_is_gone(self):
        """The measured case: one frame over, one frame back, twice.

        Track 11 crossed, returned on the very next frame, then crossed again
        for real. Previously that counted twice; now the first is refused and
        only the committed one counts.
        """
        counter = LineCrossingCounter(VERTICAL)
        dt = 1 / 15.0
        dots = [-5.03, 8.01, -7.61, -51.21, -54.50]   # signed distances
        for index, signed in enumerate(dots):
            counter.update([(11, (100.0 + signed, 120.0))],
                           timestamp=index * dt)
        self.assertEqual(counter.total(), 1,
                         f"expected one committed crossing, got "
                         f"{counter.info()}")
        outcomes = [e["outcome"] for e in counter.events]
        self.assertIn("reverted", outcomes)
        self.assertIn("counted", outcomes)


class DecisionLogTests(unittest.TestCase):
    """Every decision is recorded, including the crossings thrown away.

    This is the only way the count can be audited: if the page showed only the
    counted crossings, a double count and a genuine pair would look identical.
    """

    def test_a_counted_crossing_is_logged_as_counted(self):
        counter = LineCrossingCounter(VERTICAL)
        feed(counter, 7, [80, 90, 110, 120])
        counted = [e for e in counter.events if e["outcome"] == "counted"]
        self.assertEqual(len(counted), 1)
        self.assertEqual(counted[0]["track_id"], 7)
        self.assertEqual(counted[0]["direction"], 0)
        self.assertEqual(counter.total(), 1)

    def test_the_second_edge_is_logged_as_a_duplicate_naming_the_first(self):
        counter = LineCrossingCounter(VERTICAL)
        dt = 1 / 15.0
        for i in range(40):
            x = 20 + i * 6
            points = [(1, (x, 120))]
            if i >= 3:
                points.append((2, (x - 30, 120)))   # trailing edge
            counter.update(points, timestamp=i * dt)

        self.assertEqual(counter.total(), 1, "one object, one count")
        dupes = [e for e in counter.events if e["outcome"] == "duplicate"]
        self.assertEqual(len(dupes), 1, counter.events)
        self.assertEqual(dupes[0]["track_id"], 2)
        self.assertIn("track 1", dupes[0]["detail"])
        self.assertIn("px apart", dupes[0]["detail"])

    def test_a_crossing_past_the_end_of_the_line_is_logged_not_counted(self):
        """Beyond the drawn tripwire, but it did cross the infinite line."""
        counter = LineCrossingCounter(VERTICAL)   # spans y=50..190
        feed(counter, 3, [80, 90, 110, 120], y=230)
        self.assertEqual(counter.total(), 0)
        outside = [e for e in counter.events if e["outcome"] == "outside"]
        self.assertEqual(len(outside), 1, counter.events)
        self.assertEqual(outside[0]["track_id"], 3)

    def test_the_log_does_not_grow_without_bound(self):
        counter = LineCrossingCounter(VERTICAL)
        dt = 1 / 15.0
        x = 80
        # Walk back and forth across the line 60 times.
        for i in range(600):
            x = 80 + (i % 2) * 40
            counter.update([(1, (x, 120))], timestamp=i * dt)
        self.assertGreater(counter.total(), counter.EVENT_HISTORY)
        self.assertLessEqual(len(counter.events), counter.EVENT_HISTORY)

    def test_moving_the_line_and_resetting_clear_the_log(self):
        counter = LineCrossingCounter(VERTICAL)
        feed(counter, 1, [80, 90, 110, 120])
        self.assertTrue(counter.events)
        counter.set_line(HORIZONTAL)
        self.assertEqual(counter.events, [], "old decisions no longer apply")

        feed(counter, 1, [150, 150, 150, 150], y=120)
        counter.update([(1, (150, 80))], timestamp=1.0)
        self.assertTrue(counter.events)
        counter.reset_counts()
        self.assertEqual(counter.events, [])

    def test_info_exposes_the_log_with_readable_fields(self):
        counter = LineCrossingCounter(VERTICAL)
        feed(counter, 5, [80, 90, 110, 120])
        events = counter.info()["events"]
        # Two entries now: the crossing is announced as pending, then counted
        # once it commits. The counted one is the one that matters.
        self.assertEqual([e["outcome"] for e in events],
                         ["pending", "counted"])
        event = events[-1]
        self.assertEqual(event["id"], 5)
        self.assertEqual(event["outcome"], "counted")
        self.assertEqual(event["label"], "L>R")
        self.assertEqual(len(event["point"]), 2)
        self.assertIsInstance(event["t"], float)
        self.assertTrue(event["detail"])

    def test_unlabelled_outcomes_do_not_crash_info(self):
        counter = LineCrossingCounter(VERTICAL)
        counter._log(1, 1, (100.0, 120.0), None, "outside", "past the tip")
        event = counter.info()["events"][0]
        self.assertEqual(event["label"], "R>L")
        self.assertIsNone(event["t"])


class IntegrationTests(unittest.TestCase):
    """Drive it from a real tracker, not hand-made points."""

    def test_counting_works_on_tracker_output(self):
        from target_tracker import TargetTracker
        tracker = TargetTracker()
        counter = LineCrossingCounter(VERTICAL)
        dt = 1 / 15.0
        for i in range(70):
            x = 20 + i * 5
            tracks = tracker.update([(x, 100, 40, 60)], timestamp=i * dt)
            counter.update([(t.id, t.center) for t in tracks],
                           timestamp=i * dt)
        self.assertEqual(
            counter.total(), 1,
            f"expected exactly one crossing, got {counter.info()}",
        )

    def test_noise_does_not_produce_counts(self):
        """A stationary jittery box must never trigger the tripwire."""
        from target_tracker import TargetTracker
        import random
        rng = random.Random(5)
        tracker = TargetTracker()
        counter = LineCrossingCounter(VERTICAL, deadband=4.0)
        dt = 1 / 15.0
        for i in range(200):
            box = (95 + rng.uniform(-6, 6), 100 + rng.uniform(-6, 6),
                   40, 60)
            tracks = tracker.update([box], timestamp=i * dt)
            counter.update([(t.id, t.center) for t in tracks],
                           timestamp=i * dt)
        self.assertEqual(
            counter.total(), 0,
            f"a stationary target produced {counter.info()}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
