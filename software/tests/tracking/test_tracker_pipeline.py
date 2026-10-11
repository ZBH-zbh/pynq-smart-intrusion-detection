"""Tests for TrackerPipeline, mainly the merge step.

Regression test for a real bug: merge_fragments_2d unions boxes, so the union
can exceed the frame-size limit even though every input box satisfied it. A
283x162 box (60% of the frame) reached the tracker that way, and its centre sits
in the middle of the picture regardless of the object -- exactly the "tracked
point floats around" symptom.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
DEPLOY = os.path.abspath(os.path.join(HERE, "..", "pynq_deploy"))
sys.path.insert(0, DEPLOY)
sys.path.insert(0, HERE)

from tracker_server import (  # noqa: E402
    MIN_ZONE_SIZE,
    TRAIL_DEFAULTS,
    TRAIL_LIMITS,
    TrackerPipeline,
    normalise_trail,
    normalise_zone,
)


FRAME_AREA = 320 * 240


class TrailSettingTests(unittest.TestCase):
    """The trail sliders arrive from a browser, so they need validating."""

    def test_a_full_configuration_passes_through(self):
        result = normalise_trail({"min_move": 4, "smooth_window": 5,
                                  "seconds": 8, "linger": 2})
        self.assertEqual(result, {"min_move": 4.0, "smooth_window": 5,
                                  "seconds": 8.0, "linger": 2.0})

    def test_a_partial_update_only_changes_what_was_sent(self):
        self.assertEqual(normalise_trail({"min_move": 9}), {"min_move": 9.0})

    def test_numbers_may_arrive_as_text(self):
        """A form value or a JSON number; both are seen in practice."""
        self.assertEqual(normalise_trail({"seconds": "10"})["seconds"], 10.0)

    def test_an_even_smoothing_window_becomes_odd(self):
        """A centred average needs a middle sample."""
        self.assertEqual(normalise_trail({"smooth_window": 8})["smooth_window"], 7)
        self.assertEqual(normalise_trail({"smooth_window": 4})["smooth_window"], 3)

    def test_values_are_clamped_to_the_offered_range(self):
        self.assertEqual(normalise_trail({"min_move": 999})["min_move"],
                         TRAIL_LIMITS["min_move"][1])
        self.assertEqual(normalise_trail({"min_move": -5})["min_move"],
                         TRAIL_LIMITS["min_move"][0])
        self.assertEqual(normalise_trail({"seconds": -1})["seconds"],
                         TRAIL_LIMITS["seconds"][0])
        self.assertEqual(normalise_trail({"linger": -1})["linger"], 0.0)

    def test_bad_input_is_rejected(self):
        for bad in (None, {}, {"min_move": "abc"},
                    {"seconds": None, "linger": None}):
            with self.assertRaises(ValueError):
                normalise_trail(bad)

    def test_the_defaults_are_inside_the_limits(self):
        for key, value in TRAIL_DEFAULTS.items():
            low, high = TRAIL_LIMITS[key]
            self.assertGreaterEqual(value, low, key)
            self.assertLessEqual(value, high, key)

    def test_unknown_keys_are_ignored(self):
        """The page must not be able to set something that does not exist."""
        result = normalise_trail({"min_move": 5, "max_points": 9999})
        self.assertEqual(result, {"min_move": 5.0})


class TrailPipelineTests(unittest.TestCase):
    def _frame(self):
        import numpy as np
        return np.zeros((240, 320, 3), dtype=np.uint8)

    def test_the_pipeline_starts_from_the_tuned_defaults(self):
        pipeline = TrackerPipeline(detector="bg")
        settings = pipeline.trails.settings()
        for key, value in TRAIL_DEFAULTS.items():
            self.assertEqual(settings[key], value, key)

    def test_the_settings_can_be_changed_while_running(self):
        pipeline = TrackerPipeline(detector="bg")
        pipeline.trails.configure(seconds=12, linger=3, min_move=8)
        settings = pipeline.trails.settings()
        self.assertEqual(settings["seconds"], 12.0)
        self.assertEqual(settings["linger"], 3.0)
        self.assertEqual(settings["min_move"], 8.0)

    def test_changing_the_smoothing_redraws_an_existing_trail(self):
        """Otherwise the slider would appear to do nothing until a new point
        happens to be recorded."""
        pipeline = TrackerPipeline(detector="bg")
        for i in range(20):
            pipeline.trails.add(1, (20 + i * 8, 100 + (i % 2) * 10),
                                timestamp=i * 0.1)
        before = pipeline.trails.path(1)
        pipeline.trails.configure(smooth_window=3, samples=3)
        after = pipeline.trails.path(1)
        self.assertNotEqual(len(before), len(after))
        self.assertIsNot(after, before)

    def test_configuring_does_not_drop_recorded_points(self):
        pipeline = TrackerPipeline(detector="bg")
        for i in range(20):
            pipeline.trails.add(1, (20 + i * 8, 100), timestamp=i * 0.1)
        recorded = len(pipeline.trails.recorded(1))
        pipeline.trails.configure(min_move=15, smooth_window=3)
        self.assertEqual(len(pipeline.trails.recorded(1)), recorded)

    def test_bad_settings_are_rejected_and_leave_the_store_alone(self):
        pipeline = TrackerPipeline(detector="bg")
        before = pipeline.trails.settings()
        for bad in ({"seconds": 0}, {"linger": -1}, {"smooth_window": 0}):
            with self.assertRaises(ValueError):
                pipeline.trails.configure(**bad)
        self.assertEqual(pipeline.trails.settings(), before)

    def test_the_trail_settings_are_reported(self):
        pipeline = TrackerPipeline(detector="bg")
        _, info, _ = pipeline.step(
            self._frame(), [], timestamp=0.0, fps=15.0
        )
        self.assertIn("trail", info)
        self.assertEqual(info["trail"]["seconds"], TRAIL_DEFAULTS["seconds"])


class ZoneTests(unittest.TestCase):
    """The zone comes from a browser drag, so it can be malformed."""

    def test_a_normal_zone_passes_through(self):
        self.assertEqual(normalise_zone([85, 45, 150, 150]), (85, 45, 150, 150))

    def test_a_right_to_left_drag_is_normalised(self):
        """Dragging leftwards gives a negative width, not a bad request."""
        self.assertEqual(normalise_zone([235, 195, -150, -150]),
                         (85, 45, 150, 150))

    def test_a_zone_off_the_right_edge_is_clamped(self):
        x, y, w, h = normalise_zone([300, 100, 200, 100])
        self.assertLessEqual(x + w, 320)
        self.assertGreaterEqual(w, MIN_ZONE_SIZE)

    def test_a_negative_origin_is_clamped(self):
        x, y, w, h = normalise_zone([-50, -50, 100, 100])
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)

    def test_a_degenerate_zone_is_enlarged(self):
        x, y, w, h = normalise_zone([100, 100, 1, 0])
        self.assertGreaterEqual(w, MIN_ZONE_SIZE)
        self.assertGreaterEqual(h, MIN_ZONE_SIZE)

    def test_string_numbers_are_accepted(self):
        """Form values and JSON both arrive as text often enough."""
        self.assertEqual(normalise_zone(["10", "20", "30", "40"]),
                         (10, 20, 30, 40))

    def test_bad_input_is_rejected(self):
        for bad in (None, [1, 2, 3], [1, 2, 3, 4, 5], ["a", "b", "c", "d"]):
            with self.assertRaises((ValueError, TypeError)):
                normalise_zone(bad)

    def test_the_whole_frame_is_usable(self):
        self.assertEqual(normalise_zone([0, 0, 320, 240]), (0, 0, 320, 240))


class ZonePipelineTests(unittest.TestCase):
    def _frame(self):
        import numpy as np
        return np.zeros((240, 320, 3), dtype=np.uint8)

    def test_the_zone_is_reported(self):
        pipeline = TrackerPipeline(detector="bg", zone=(10, 20, 30, 40))
        _, info, _ = pipeline.step(
            self._frame(), [], timestamp=0.0, fps=15.0
        )
        self.assertEqual(info["zone"], [10, 20, 30, 40])

    def test_a_custom_zone_drives_the_alarm(self):
        """A target outside the default zone but inside a custom one must alarm."""
        outside_default = (10, 10, 30, 30)     # ALARM_ZONE is (85, 45, 150, 150)
        pipeline = TrackerPipeline(
            detector="bg", alarm_on="detections", zone=(5, 5, 60, 60)
        )
        _, _, alarm = pipeline.step(
            self._frame(), [outside_default], timestamp=0.0, fps=15.0
        )
        self.assertTrue(alarm, "target inside the custom zone should alarm")

        pipeline2 = TrackerPipeline(
            detector="bg", alarm_on="detections", zone=(200, 150, 60, 60)
        )
        _, _, alarm2 = pipeline2.step(
            self._frame(), [outside_default], timestamp=0.0, fps=15.0
        )
        self.assertFalse(alarm2, "target outside the custom zone should not alarm")

    def test_the_zone_can_be_changed_while_running(self):
        pipeline = TrackerPipeline(detector="bg", alarm_on="detections")
        box = (10, 10, 30, 30)
        _, _, before = pipeline.step(
            self._frame(), [box], timestamp=0.0, fps=15.0
        )
        pipeline.zone = (5, 5, 60, 60)
        _, info, after = pipeline.step(
            self._frame(), [box], timestamp=1 / 15.0, fps=15.0
        )
        self.assertFalse(before)
        self.assertTrue(after)
        self.assertEqual(info["zone"], [5, 5, 60, 60])


class MergeSettingTests(unittest.TestCase):
    """The run-time fragment-join controls.

    These exist because the defaults cannot be right for every scene: how far
    apart an object's fragments land depends on what it is and what is behind
    it, which is why they are adjustable from the page instead of guessed at.
    """

    def test_normalise_clamps_instead_of_rejecting(self):
        from tracker_server import normalise_merge
        self.assertEqual(normalise_merge({"bg_merge_gap": 500}),
                         {"bg_merge_gap": 120.0})
        self.assertEqual(normalise_merge({"bg_merge_gap": -5}),
                         {"bg_merge_gap": 0.0})
        self.assertEqual(normalise_merge({"bg_merge_scale": 9}),
                         {"bg_merge_scale": 2.0})

    def test_normalise_rejects_junk(self):
        from tracker_server import normalise_merge
        for bad in ({}, {"bg_merge_gap": "abc"}, {"unknown": 1}):
            with self.assertRaises(ValueError):
                normalise_merge(bad)

    def test_a_partial_update_leaves_the_other_value_alone(self):
        from tracker_server import normalise_merge
        self.assertEqual(normalise_merge({"bg_merge_scale": 0.4}),
                         {"bg_merge_scale": 0.4})

    def test_configure_changes_one_and_keeps_the_other(self):
        pipeline = TrackerPipeline(detector="bg")
        before = pipeline.merge_settings()
        after = pipeline.configure_merge(bg_merge_gap=7.0)
        self.assertEqual(after["bg_merge_gap"], 7.0)
        self.assertEqual(after["bg_merge_scale"], before["bg_merge_scale"])

    def test_configure_can_switch_the_joining_off(self):
        pipeline = TrackerPipeline(detector="bg")
        pipeline.configure_merge(bg_merge_gap=0.0)
        source = [(10, 60, 40, 40), (10, 100, 40, 40)]
        boxes, _ = pipeline.merge(source)
        self.assertEqual(boxes, source)

    def test_configure_can_make_it_join_more(self):
        """Two fragments too far apart at a tight setting must join when raised.

        The reach is ``min(bg_merge_gap, bg_merge_scale * short_side)`` applied
        to each box, so both knobs matter and the scale is what reaches small
        fragments -- a 10 px fragment at scale 0.6 only bridges 12 px whatever
        the gap is set to.
        """
        gap = 40.0
        source = [(10, 60, 40, 40), (10, 100 + gap, 40, 40)]
        tight = TrackerPipeline(detector="bg", bg_merge_gap=10.0,
                                bg_merge_scale=0.1)
        self.assertEqual(len(tight.merge(source)[0]), 2,
                         "a tight setting joined boxes it should not have")

        loose = TrackerPipeline(detector="bg", bg_merge_gap=120.0,
                                bg_merge_scale=2.0)
        self.assertEqual(len(loose.merge(source)[0]), 1,
                         "a loose setting failed to join two fragments")


class MergeTests(unittest.TestCase):
    def test_bg_mode_joins_fragments(self):
        pipeline = TrackerPipeline(detector="bg")
        boxes, centers = pipeline.merge([(10, 60, 40, 40), (10, 100, 40, 40)])
        self.assertEqual(len(boxes), 1)
        self.assertEqual(boxes[0], (10, 60, 40, 80))
        self.assertEqual(len(centers), 1)

    def test_pl_mode_joins_edge_strips(self):
        pipeline = TrackerPipeline(detector="pl")
        boxes, centers = pipeline.merge([(17, 67, 6, 69), (73, 67, 6, 69)])
        self.assertEqual(len(boxes), 1)
        self.assertEqual(boxes[0], (17, 67, 62, 69))

    def test_merge_gap_zero_leaves_the_frozen_chain_alone(self):
        """The shipped detector's boxes must pass through untouched.

        This is the guarantee that matters: --merge-gap defaults to 0 and the
        PL chain's output is then exactly the teammate's.
        """
        pipeline = TrackerPipeline(detector="pl", merge_gap=0)
        source = [(17, 67, 6, 69), (73, 67, 6, 69)]
        boxes, _ = pipeline.merge(source)
        self.assertEqual(boxes, source)

    def test_bg_merging_has_its_own_switch(self):
        """bg must not need the PL switch turned on to repair its fragments.

        These used to be coupled: the bg rule sat inside ``merge_gap > 0``, so
        with the default --merge-gap 0 a background model reported one object as
        up to 14 unjoined boxes and --bg-merge-gap was dead configuration.
        """
        source = [(10, 60, 40, 40), (10, 110, 40, 40)]
        joined, _ = TrackerPipeline(detector="bg", merge_gap=0).merge(source)
        self.assertEqual(len(joined), 1,
                         f"bg fragments were left unjoined: {joined}")

        split, _ = TrackerPipeline(detector="bg", merge_gap=0,
                                   bg_merge_gap=0).merge(source)
        self.assertEqual(split, source,
                         "bg_merge_gap=0 must switch the joining off")

    def test_supplied_centres_are_carried_through(self):
        pipeline = TrackerPipeline(detector="bg", merge_gap=0)
        boxes, centers = pipeline.merge(
            [(100, 100, 40, 60)], [(107.5, 133.25)]
        )
        self.assertEqual(boxes, [(100, 100, 40, 60)])
        self.assertEqual(centers, [(107.5, 133.25)])

    def test_merged_centre_is_the_area_weighted_average(self):
        pipeline = TrackerPipeline(detector="bg")
        boxes, centers = pipeline.merge(
            [(10, 60, 40, 40), (10, 100, 40, 40)],
            [(30.0, 80.0), (30.0, 120.0)],
        )
        self.assertEqual(len(centers), 1)
        self.assertAlmostEqual(centers[0][0], 30.0)
        self.assertAlmostEqual(centers[0][1], 100.0)

    def test_oversized_merged_box_is_dropped(self):
        """The union of legal boxes must still obey the size limit."""
        pipeline = TrackerPipeline(detector="bg", max_box_fraction=0.5)
        # Two boxes that are each fine but whose union covers most of the frame.
        left = (0, 0, 150, 200)          # 30000 px, inside the 38400 limit
        right = (170, 0, 150, 200)       # 30000 px, inside the limit
        boxes, _ = pipeline.merge([left, right])
        for x, y, w, h in boxes:
            self.assertLessEqual(
                w * h, 0.5 * FRAME_AREA,
                f"box {(x, y, w, h)} is "
                f"{100.0 * w * h / FRAME_AREA:.0f}% of the frame",
            )

    def test_oversized_input_box_is_dropped(self):
        pipeline = TrackerPipeline(detector="bg")
        boxes, _ = pipeline.merge([(0, 0, 300, 220)])
        self.assertEqual(boxes, [])

    def test_normal_boxes_survive(self):
        pipeline = TrackerPipeline(detector="bg")
        boxes = [(10, 10, 40, 60), (200, 100, 30, 40)]
        merged, _ = pipeline.merge(boxes)
        self.assertEqual(len(merged), 2)

    def test_centres_stay_parallel_to_boxes(self):
        pipeline = TrackerPipeline(detector="bg")
        boxes, centers = pipeline.merge(
            [(0, 0, 150, 200), (170, 0, 150, 200)]
        )
        self.assertEqual(len(boxes), len(centers))


class StepTests(unittest.TestCase):
    def _frame(self):
        import numpy as np
        return np.zeros((240, 320, 3), dtype=np.uint8)

    def test_detector_info_is_surfaced(self):
        """The page reads the adapted threshold from /status."""
        pipeline = TrackerPipeline(detector="bg")
        _, info, _ = pipeline.step(
            self._frame(), [], timestamp=0.0, fps=15.0,
            detector_info={"threshold": 12.5, "relearning": False},
        )
        self.assertEqual(info["threshold"], 12.5)
        self.assertFalse(info["relearning"])
        self.assertEqual(info["tracks"], 0)

    def test_step_reports_no_alarm_without_tracks(self):
        pipeline = TrackerPipeline(detector="bg")
        info = None
        for i in range(10):
            _, info, alarm = pipeline.step(
                self._frame(), [], timestamp=i / 15.0, fps=15.0
            )
        self.assertFalse(alarm)
        self.assertEqual(info["tracks"], 0)

    def test_alarm_follows_confirmed_tracks_not_single_frames(self):
        """One frame of a box inside the zone must not raise the alarm."""
        pipeline = TrackerPipeline(detector="bg", alarm_on="tracks")
        zone_box = (100, 100, 40, 40)          # inside ALARM_ZONE
        _, _, alarm_first = pipeline.step(
            self._frame(), [zone_box], timestamp=0.0, fps=15.0
        )
        self.assertFalse(alarm_first, "unconfirmed track should not alarm")
        _, _, alarm_second = pipeline.step(
            self._frame(), [zone_box], timestamp=1 / 15.0, fps=15.0
        )
        self.assertTrue(alarm_second, "a confirmed track in the zone should alarm")

    def test_detections_mode_alarms_immediately(self):
        pipeline = TrackerPipeline(detector="bg", alarm_on="detections")
        _, _, alarm = pipeline.step(
            self._frame(), [(100, 100, 40, 40)], timestamp=0.0, fps=15.0
        )
        self.assertTrue(alarm)


if __name__ == "__main__":
    unittest.main(verbosity=2)
