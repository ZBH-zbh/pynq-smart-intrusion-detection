"""Offline tests for target_tracker.

Two layers:

1. Unit tests feed the tracker *ideal* boxes with known motion, so id stability,
   direction and speed can be asserted exactly. This isolates the tracking logic
   from detection noise.
2. An integration smoke test runs the project's real detection chain
   (frame diff -> threshold -> morphology -> contours) on the synthetic
   sequence and checks the tracker survives real detections without creating an
   unbounded number of ids.

Run:  python test_target_tracker.py
"""
import os
import random
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
DEPLOY = os.path.abspath(os.path.join(HERE, "..", "pynq_deploy"))
sys.path.insert(0, DEPLOY)
sys.path.insert(0, HERE)

from target_tracker import TargetTracker, box_iou, direction_label

FPS = 15.0
DT = 1.0 / FPS


def feed(tracker, boxes, frame_index):
    """Convenience: step the tracker with a synthetic clock."""
    return tracker.update(boxes, timestamp=frame_index * DT)


class IouTests(unittest.TestCase):
    def test_identical_boxes(self):
        self.assertAlmostEqual(box_iou((0, 0, 10, 10), (0, 0, 10, 10)), 1.0)

    def test_disjoint_boxes(self):
        self.assertEqual(box_iou((0, 0, 10, 10), (50, 50, 10, 10)), 0.0)

    def test_half_overlap(self):
        # Shifted by half its width -> intersection 5x10, union 150.
        self.assertAlmostEqual(box_iou((0, 0, 10, 10), (5, 0, 10, 10)), 50 / 150)

    def test_direction_labels(self):
        self.assertEqual(direction_label(0), "E")
        self.assertEqual(direction_label(90), "N")
        self.assertEqual(direction_label(-90), "S")
        self.assertEqual(direction_label(180), "W")
        self.assertEqual(direction_label(45), "NE")
        self.assertEqual(direction_label(-45), "SE")
        self.assertEqual(direction_label(None), "?")


class IdStabilityTests(unittest.TestCase):
    def test_single_object_moving_right_keeps_one_id(self):
        tracker = TargetTracker()
        ids = set()
        for i in range(100):
            x = 10 + i * 8
            tracks = feed(tracker, [(x, 60, 50, 70)], i)
            for t in tracks:
                ids.add(t.id)
        self.assertEqual(len(ids), 1, f"expected a single stable id, got {ids}")

    def test_single_object_moving_up_keeps_one_id(self):
        tracker = TargetTracker()
        ids = set()
        for i in range(80):
            y = 200 - i * 4
            if y < 0:
                break
            tracks = feed(tracker, [(100, y, 50, 70)], i)
            for t in tracks:
                ids.add(t.id)
        self.assertEqual(len(ids), 1)

    def test_track_survives_a_brief_detection_dropout(self):
        """A few missed frames must not spawn a new id."""
        tracker = TargetTracker(max_misses=12)
        ids = set()
        for i in range(60):
            if 20 <= i < 25:
                boxes = []                      # detection gap
            else:
                boxes = [(10 + i * 4, 60, 50, 70)]
            for t in feed(tracker, boxes, i):
                ids.add(t.id)
        self.assertEqual(len(ids), 1, f"dropout created extra ids: {ids}")

    def test_long_absence_creates_a_new_id(self):
        tracker = TargetTracker(max_misses=5)
        seen = []
        for i in range(40):
            boxes = [(10 + i * 4, 60, 50, 70)] if (i < 10 or i >= 25) else []
            for t in feed(tracker, boxes, i):
                seen.append((i, t.id))
        ids = {i for _, i in seen}
        self.assertGreaterEqual(len(ids), 2, "long absence should start a new id")


class DirectionTests(unittest.TestCase):
    def test_moving_right_is_east(self):
        tracker = TargetTracker()
        track = None
        for i in range(60):
            tracks = feed(tracker, [(10 + i * 6, 100, 50, 70)], i)
            if tracks:
                track = tracks[0]
        self.assertIsNotNone(track)
        self.assertEqual(track.direction, "E")
        self.assertLess(abs(track.angle), 15.0)

    def test_moving_left_is_west(self):
        tracker = TargetTracker()
        track = None
        for i in range(60):
            tracks = feed(tracker, [(260 - i * 6, 100, 50, 70)], i)
            if tracks:
                track = tracks[0]
        self.assertEqual(track.direction, "W")
        self.assertGreater(abs(track.angle), 165.0)

    def test_moving_up_is_north(self):
        """Image y grows downward, so a decreasing y must read as 'up'."""
        tracker = TargetTracker()
        track = None
        for i in range(50):
            tracks = feed(tracker, [(100, 200 - i * 5, 50, 70)], i)
            if tracks:
                track = tracks[0]
        self.assertEqual(track.direction, "N")
        self.assertAlmostEqual(track.angle, 90.0, delta=5.0)

    def test_moving_down_is_south(self):
        tracker = TargetTracker()
        track = None
        for i in range(50):
            tracks = feed(tracker, [(100, 20 + i * 5, 50, 70)], i)
            if tracks:
                track = tracks[0]
        self.assertEqual(track.direction, "S")
        self.assertAlmostEqual(track.angle, -90.0, delta=5.0)


class SpeedTests(unittest.TestCase):
    def test_speed_matches_known_pixel_rate(self):
        """8 px per frame at 15 FPS => 120 px/s."""
        tracker = TargetTracker()
        track = None
        for i in range(80):
            tracks = feed(tracker, [(10 + i * 8, 100, 50, 70)], i)
            if tracks:
                track = tracks[0]
        self.assertIsNotNone(track)
        self.assertAlmostEqual(track.speed, 120.0, delta=12.0)

    def test_speed_is_scale_independent_of_framerate(self):
        """Same pixel rate at 30 FPS must give the same px/s."""
        tracker = TargetTracker()
        dt = 1.0 / 30.0
        track = None
        for i in range(80):
            # 4 px per frame at 30 FPS == 120 px/s
            tracks = tracker.update([(10 + i * 4, 100, 50, 70)], timestamp=i * dt)
            if tracks:
                track = tracks[0]
        self.assertAlmostEqual(track.speed, 120.0, delta=12.0)

    def test_stationary_object_reports_no_direction(self):
        tracker = TargetTracker()
        track = None
        for i in range(40):
            tracks = feed(tracker, [(100, 100, 50, 70)], i)
            if tracks:
                track = tracks[0]
        self.assertEqual(track.direction, "?")
        self.assertLess(track.speed, 5.0)


class FastMotionTests(unittest.TestCase):
    def test_displacement_larger_than_box_still_matches(self):
        """30 px/frame with a 30 px box: no IoU at all, distance gate must hold."""
        tracker = TargetTracker()
        ids = set()
        for i in range(40):
            for t in feed(tracker, [(10 + i * 30, 100, 30, 30)], i):
                ids.add(t.id)
        self.assertEqual(len(ids), 1, f"fast motion lost the id: {ids}")


class MultiObjectTests(unittest.TestCase):
    def test_two_objects_keep_separate_ids(self):
        tracker = TargetTracker()
        by_object = {"left": set(), "right": set()}
        for i in range(80):
            boxes = [
                (10 + i * 3, 50, 40, 60),     # moving right, upper
                (260 - i * 3, 150, 40, 60),   # moving left, lower
            ]
            tracks = feed(tracker, boxes, i)
            for t in tracks:
                key = "left" if t.centroid[1] < 120 else "right"
                by_object[key].add(t.id)
        self.assertEqual(len(by_object["left"]), 1, by_object)
        self.assertEqual(len(by_object["right"]), 1, by_object)
        self.assertNotEqual(
            list(by_object["left"])[0], list(by_object["right"])[0],
            "the two objects must not share an id",
        )

    def test_objects_moving_apart_are_not_swapped(self):
        tracker = TargetTracker()
        first_ids = set()
        for i in range(60):
            boxes = [(40 - i * 2, 100, 30, 50), (250 + i * 2, 100, 30, 50)]
            for t in feed(tracker, boxes, i):
                first_ids.add(t.id)
        self.assertEqual(len(first_ids), 2, "objects moving apart should keep 2 ids")


class NoiseTests(unittest.TestCase):
    def test_box_jitter_does_not_flip_direction(self):
        """+-2 px of box jitter must not turn steady rightward motion into noise."""
        import random
        rng = random.Random(7)
        tracker = TargetTracker()
        labels = []
        for i in range(120):
            jx = rng.randint(-2, 2)
            jy = rng.randint(-2, 2)
            tracks = feed(tracker, [(10 + i * 8 + jx, 100 + jy, 50, 70)], i)
            if tracks and i > 40:
                labels.append(tracks[0].direction)
        east = sum(1 for l in labels if l == "E")
        self.assertGreater(east / len(labels), 0.9,
                           f"direction should be stable, got {set(labels)}")


class LifecycleTests(unittest.TestCase):
    def test_tracks_are_dropped_after_max_misses(self):
        tracker = TargetTracker(max_misses=5)
        for i in range(10):
            feed(tracker, [(10 + i * 4, 60, 50, 70)], i)
        self.assertEqual(len(tracker.tracks), 1)
        for i in range(10, 30):
            feed(tracker, [], i)
        self.assertEqual(len(tracker.tracks), 0, "stale track was not dropped")

    def test_min_hits_requires_confirmation(self):
        tracker = TargetTracker(min_hits=3)
        first = feed(tracker, [(10, 10, 50, 70)], 0)
        self.assertEqual(len(first), 0, "single-frame detection must not confirm")
        second = feed(tracker, [(10, 10, 50, 70)], 1)
        self.assertEqual(len(second), 0)
        third = feed(tracker, [(10, 10, 50, 70)], 2)
        self.assertEqual(len(third), 1, "should confirm on the third hit")

    def test_summary_is_reported(self):
        tracker = TargetTracker()
        for i in range(20):
            feed(tracker, [(10 + i * 4, 60, 50, 70)], i)
        s = tracker.summary()
        self.assertEqual(s["frames"], 20)
        self.assertGreaterEqual(s["ids_created"], 1)


class MinSpeedTests(unittest.TestCase):
    """A curtain stirred by air is a real but almost motionless target."""

    def test_a_nearly_static_track_is_not_reported(self):
        tracker = TargetTracker()
        for i in range(60):
            # Drifts by 0.07 px per frame -> about 1 px/s, like the curtain
            # that kept a box alive in the corner of the live picture.
            tracks = tracker.update(
                [(280, 190, 23, 45 + i % 2)], timestamp=i * DT,
                min_speed=4.0,
            )
        self.assertEqual(tracks, [], f"near-static track reported: {tracks}")

    def test_a_moving_track_is_still_reported(self):
        tracker = TargetTracker()
        tracks = []
        for i in range(40):
            tracks = tracker.update(
                [(10 + i * 5, 100, 40, 60)], timestamp=i * DT, min_speed=4.0
            )
        self.assertEqual(len(tracks), 1)

    def test_zero_disables_the_filter(self):
        tracker = TargetTracker()
        tracks = []
        for i in range(60):
            tracks = tracker.update(
                [(280, 190, 23, 45 + i % 2)], timestamp=i * DT, min_speed=0.0
            )
        self.assertEqual(len(tracks), 1)

    def test_speed_thresholds_are_respected(self):
        for min_speed, expected in ((0.0, 1), (5.0, 1), (500.0, 0)):
            tracker = TargetTracker()
            tracks = []
            for i in range(40):
                tracks = tracker.update(
                    [(10 + i * 5, 100, 40, 60)], timestamp=i * DT,
                    min_speed=min_speed,
                )
            self.assertEqual(
                len(tracks), expected,
                f"min_speed={min_speed} gave {len(tracks)} tracks",
            )


class StabilityTests(unittest.TestCase):
    """Does the tracked box stay ON the object when other detections compete?

    Real detections jitter and there is usually more than one of them: the
    background model also reports fragments and threshold flicker elsewhere in
    the frame. A matching rule that fails on a jittery frame does not simply
    miss -- it hands the track to whichever competing detection scores better,
    which is what "the green box is unstable" looks like.

    A single-object test cannot see this: with nothing to compete against, even
    a failed IoU falls through to the distance gate and the box is still
    reported. The distractors are what make the test meaningful.
    """

    DISTRACTORS = ((250, 20, 40, 50), (30, 190, 50, 40))

    def _run(self, tracker, count=140, jitter=3.0, size_jitter=4, speed=5,
             miss_every=0):
        """Score only the frames where the object was actually detected.

        Counting the missed frames too would just be measuring the detector, not
        the tracker: on a frame with no detection there is nothing to be on.
        """
        rng = random.Random(4)
        on_object = 0
        scored = 0
        ids = set()
        for i in range(count):
            x = 20 + i * speed
            boxes = list(self.DISTRACTORS)
            object_present = (miss_every == 0 or i % miss_every != miss_every - 1)
            if object_present:
                boxes.append((
                    x + rng.uniform(-jitter, jitter),
                    100 + rng.uniform(-jitter, jitter),
                    40 + rng.uniform(-size_jitter, size_jitter),
                    60 + rng.uniform(-size_jitter, size_jitter),
                ))
            tracks = tracker.update(boxes, timestamp=i * DT)
            if i <= 25 or not object_present:
                continue
            scored += 1
            follows = [
                t for t in tracks
                if t.centroid[1] > 80 and t.centroid[1] < 180
                and abs(t.centroid[0] - (x + 20)) < 45
            ]
            if follows:
                on_object += 1
                ids.add(follows[0].id)
        return on_object, scored, ids

    def test_box_stays_on_the_object_with_competing_detections(self):
        on_object, scored, ids = self._run(TargetTracker())
        ratio = on_object / scored
        self.assertGreater(
            ratio, 0.9,
            f"the box was on the object in only {on_object}/{scored} frames "
            f"({100 * ratio:.0f}%)",
        )

    def test_id_does_not_churn_with_competing_detections(self):
        _, _, ids = self._run(TargetTracker())
        self.assertEqual(len(ids), 1, f"id churned through {sorted(ids)}")

    def test_survives_larger_jitter(self):
        on_object, scored, _ = self._run(
            TargetTracker(), jitter=7.0, size_jitter=8
        )
        self.assertGreater(on_object / scored, 0.85)

    def test_survives_a_missed_frame_every_third(self):
        """The object is dropped a third of the time; distractors are not."""
        on_object, scored, ids = self._run(TargetTracker(), miss_every=3)
        self.assertGreater(on_object / scored, 0.9)
        self.assertLessEqual(len(ids), 1, f"id churned through {sorted(ids)}")

    def test_survives_an_occlusion_gap(self):
        """A few consecutive missed frames must not cost the id."""
        tracker = TargetTracker()
        ids = set()
        reported = scored = 0
        for i in range(140):
            boxes = list(self.DISTRACTORS)
            present = not 60 <= i < 66
            if present:
                boxes.append((20 + i * 5, 100, 40, 60))
            tracks = tracker.update(boxes, timestamp=i * DT)
            if i <= 25 or not present:
                continue
            scored += 1
            follows = [
                t for t in tracks
                if t.centroid[1] > 80 and t.centroid[1] < 180
                and abs(t.centroid[0] - (20 + i * 5 + 20)) < 45
            ]
            if follows:
                reported += 1
                ids.add(follows[0].id)
        self.assertEqual(len(ids), 1, f"id churned: {sorted(ids)}")
        self.assertGreater(reported / scored, 0.9)


class TrackedPointTests(unittest.TestCase):
    """The localisation point must be steady and must not lag.

    The steadiness is NOT supposed to come from smoothing in time: measured on a
    simulated wave, every temporal smoother lost to the raw measurement because
    its error was dominated by lag. It comes from the detector reporting the
    region's pixel centroid instead of its bounding-box centre, and from the
    tracker passing that value through unchanged.
    """

    def test_the_point_is_the_value_the_detector_reported(self):
        """A supplied centre must be used verbatim, not re-derived."""
        tracker = TargetTracker()
        boxes = [(100, 100, 40, 60)]
        centers = [(107.5, 133.25)]
        tracks = tracker.update(boxes, timestamp=0.0, centers=centers)
        self.assertFalse(tracks)          # needs min_hits
        track = tracker.tracks[0]
        self.assertEqual(track.center, (107.5, 133.25))
        self.assertNotEqual(track.centroid, (107.5, 133.25))

    def test_the_point_falls_back_to_the_box_centre(self):
        tracker = TargetTracker()
        tracker.update([(100, 100, 40, 60)], timestamp=0.0)
        self.assertEqual(tracker.tracks[0].center, (120.0, 130.0))

    def test_a_steady_centroid_gives_a_steady_point(self):
        """With the centroid reported, a still object's point does not wander."""
        rng = random.Random(11)
        tracker = TargetTracker()
        points = []
        for i in range(140):
            # The box jitters, the mass centre does not.
            box = (100 + rng.uniform(-4, 4), 100 + rng.uniform(-4, 4),
                   40 + rng.uniform(-4, 4), 60 + rng.uniform(-4, 4))
            tracks = tracker.update(
                [box], timestamp=i * DT, centers=[(120.0, 130.0)]
            )
            if i > 30 and tracks:
                points.append(tracks[0].center)
        spread = max(
            max(p[0] for p in points) - min(p[0] for p in points),
            max(p[1] for p in points) - min(p[1] for p in points),
        )
        self.assertLess(spread, 0.001,
                        f"the point wandered over {spread:.2f} px")

    def test_the_point_does_not_lag_a_steadily_moving_object(self):
        tracker = TargetTracker()
        errors = []
        for i in range(120):
            x = 20 + i * 6
            tracks = tracker.update(
                [(x, 100, 40, 60)], timestamp=i * DT,
                centers=[(x + 20.0, 130.0)],
            )
            if i > 40 and tracks:
                cx, cy = tracks[0].center
                errors.append(abs(cx - (x + 20)))
        self.assertTrue(errors)
        self.assertLess(sum(errors) / len(errors), 0.001)

    def test_velocity_is_steadier_than_the_end_sample_difference(self):
        """The fit is kept for velocity, where it does help."""
        rng = random.Random(3)
        tracker = TargetTracker()
        speeds = []
        for i in range(140):
            x = 20 + i * 6 + rng.uniform(-3, 3)
            tracks = tracker.update([(x, 100, 40, 60)], timestamp=i * DT)
            if i > 40 and tracks:
                speeds.append(tracks[0].speed)
        spread = max(speeds) - min(speeds)
        self.assertLess(spread, 90.0,
                        f"speed readout swings over {spread:.0f} px/s")

    def test_the_drawn_box_size_is_smoothed(self):
        tracker = TargetTracker()
        widths = []
        for i in range(80):
            w = 40 + ((i % 2) * 8)          # alternates 40/48
            tracks = tracker.update([(20 + i * 5, 100, w, 60)],
                                    timestamp=i * DT)
            if i > 30 and tracks:
                widths.append(tracks[0].display_box[2])
        self.assertLess(max(widths) - min(widths), 4.0,
                        f"drawn width still swings over "
                        f"{max(widths) - min(widths):.1f} px")
        self.assertAlmostEqual(sum(widths) / len(widths), 44.0, delta=2.0)

    def test_the_point_is_available_immediately(self):
        tracker = TargetTracker()
        tracker.update([(10, 10, 40, 60)], timestamp=0.0)
        track = tracker.tracks[0]
        self.assertEqual(len(track.center), 2)
        self.assertEqual(len(track.display_box), 4)

    def test_centers_must_be_parallel_to_boxes(self):
        tracker = TargetTracker()
        with self.assertRaises(ValueError):
            tracker.update([(1, 2, 3, 4)], timestamp=0.0,
                           centers=[(1.0, 2.0), (3.0, 4.0)])


class DetachTests(unittest.TestCase):
    """A track must not migrate from a moving object onto still background.

    Reported symptom: "the green box easily detaches from the moving object and
    locks onto the static background". The mechanism is that the object is
    missed for a frame or two -- low contrast, motion blur, a colour that
    matches its background -- and a stationary false detection nearby is then
    the only candidate, so the track takes it and its speed collapses to zero.
    """

    def _run(self, miss_every, distractor_at, frames=60, tracker=None):
        """One object crossing a stationary distractor, missed periodically."""
        tracker = tracker or TargetTracker()
        seen = []
        for i in range(frames):
            x = 20 + i * 6
            boxes = []
            if miss_every == 0 or i % miss_every != miss_every - 1:
                boxes.append((x, 100, 40, 60))          # the real object
            boxes.append((distractor_at, 100, 40, 60))  # stationary distractor
            for t in tracker.update(boxes, timestamp=i * DT):
                seen.append((i, t.id, t.centroid, x + 20))
        return seen

    def test_two_objects_stay_separate(self):
        """Baseline: with no misses, each is tracked by its own id."""
        seen = self._run(miss_every=0, distractor_at=200)
        by_id = {}
        for i, tid, centroid, truth_x in seen:
            by_id.setdefault(tid, []).append(centroid[0])
        self.assertEqual(len(by_id), 2, f"expected 2 tracks, got {by_id.keys()}")

    def test_track_does_not_stick_to_the_distractor(self):
        """The track following the object must keep following it.

        The object is missed every third frame, so on those frames the only
        detection near the track is the stationary one.
        """
        seen = self._run(miss_every=3, distractor_at=200)
        if not seen:
            self.fail("no tracks reported at all")

        # Follow the id that starts on the object and see where it ends up.
        by_id = {}
        for i, tid, centroid, truth_x in seen:
            by_id.setdefault(tid, []).append((i, centroid[0], truth_x))

        moving_ids = [tid for tid, rows in by_id.items()
                      if len(rows) >= 10 and rows[-1][1] - rows[0][1] > 50]
        self.assertTrue(
            moving_ids,
            "no track followed the moving object; ids seen: "
            + ", ".join(f"{k}:{len(v)}" for k, v in by_id.items()),
        )

        for tid in moving_ids:
            row = by_id[tid]
            errors = [abs(cx - tx) for _, cx, tx in row]
            final_error = errors[-1]
            self.assertLess(
                final_error, 40,
                f"track {tid} ended {final_error:.0f} px from the object -- it "
                f"detached (last centroid {row[-1][1]:.0f}, object at "
                f"{row[-1][2]:.0f})",
            )

    def test_speed_does_not_collapse_while_following_the_object(self):
        tracker = TargetTracker()
        speeds = []
        for i in range(60):
            x = 20 + i * 6
            boxes = []
            if i % 3 != 2:
                boxes.append((x, 100, 40, 60))
            boxes.append((200, 100, 40, 60))
            tracks = tracker.update(boxes, timestamp=i * DT, min_speed=4.0)
            for t in tracks:
                if t.centroid[0] > 100:      # the one on the object
                    speeds.append(t.speed)
        self.assertTrue(speeds, "no track on the object")
        collapsed = sum(1 for s in speeds if s < 30)
        self.assertLess(
            collapsed / len(speeds), 0.2,
            f"speed collapsed below 30 px/s in {collapsed}/{len(speeds)} frames",
        )


class GhostTrackTests(unittest.TestCase):
    """A track that stopped being detected must not be reported as present."""

    def test_missing_track_is_not_reported(self):
        tracker = TargetTracker(min_hits=2, max_misses=12)
        for i in range(10):
            tracks = feed(tracker, [(10 + i * 4, 60, 50, 70)], i)
        self.assertEqual(len(tracks), 1, "track should be confirmed while detected")

        # Object vanishes; the track is still inside max_misses but is not real.
        for i in range(10, 16):
            tracks = feed(tracker, [], i)
            self.assertEqual(
                len(tracks), 0,
                f"frame {i}: ghost track reported as present: {tracks}",
            )

    def test_coasting_is_opt_in(self):
        tracker = TargetTracker(min_hits=2, max_misses=12)
        for i in range(10):
            tracker.update([(10 + i * 4, 60, 50, 70)], timestamp=i * DT)
        coasted = tracker.update([], timestamp=10 * DT, coast=3)
        self.assertEqual(len(coasted), 1, "coast=3 should keep the track visible")
        for t in coasted:
            self.assertEqual(t.misses, 1)

    def test_active_tracks_excludes_coasting(self):
        tracker = TargetTracker(min_hits=2, max_misses=12)
        for i in range(10):
            tracker.update([(10 + i * 4, 60, 50, 70)], timestamp=i * DT)
        tracker.update([], timestamp=10 * DT)
        self.assertEqual(len(tracker.active_tracks()), 0)
        self.assertEqual(len(tracker.confirmed_tracks()), 1)
        self.assertEqual(tracker.summary()["coasting"], 1)

    def test_two_objects_do_not_report_ghosts_on_real_chain(self):
        """Real detections: reported tracks must never exceed detected boxes."""
        from benchmark_motion import generate_synthetic_frames
        from opencv_software_motion import SoftwareMotionDetector

        frames = generate_synthetic_frames(140)
        detector = SoftwareMotionDetector()
        tracker = TargetTracker()

        for i, frame in enumerate(frames):
            _, targets, _ = detector.process(frame)
            tracks = tracker.update(targets, timestamp=i * DT)
            self.assertLessEqual(
                len(tracks), len(targets),
                f"frame {i}: {len(tracks)} tracks from {len(targets)} detections",
            )


class IntegrationTests(unittest.TestCase):
    """Run the project's real detection chain and check the tracker copes."""

    def test_real_detections_do_not_explode_ids(self):
        from benchmark_motion import generate_synthetic_frames
        from opencv_software_motion import SoftwareMotionDetector

        frames = generate_synthetic_frames(140)
        detector = SoftwareMotionDetector()
        tracker = TargetTracker()

        per_frame_counts = []
        for i, frame in enumerate(frames):
            _, targets, _ = detector.process(frame)
            tracks = tracker.update(targets, timestamp=i * DT)
            per_frame_counts.append(len(tracks))

        summary = tracker.summary()
        # The synthetic object is a solid rectangle, so frame differencing only
        # reveals its leading/trailing edges (two thin strips per object).
        # Two strips x two objects is the expected order of magnitude.
        self.assertLess(
            summary["ids_created"], 40,
            f"id churn too high: {summary}",
        )
        self.assertLess(
            max(per_frame_counts), 8,
            f"too many concurrent tracks: {per_frame_counts}",
        )

    def test_tracker_reports_something_on_real_detections(self):
        from benchmark_motion import generate_synthetic_frames
        from opencv_software_motion import SoftwareMotionDetector

        frames = generate_synthetic_frames(140)
        detector = SoftwareMotionDetector()
        tracker = TargetTracker()

        seen_ids = set()
        for i, frame in enumerate(frames):
            _, targets, _ = detector.process(frame)
            for t in tracker.update(targets, timestamp=i * DT):
                seen_ids.add(t.id)
        self.assertGreater(len(seen_ids), 0, "tracker produced no tracks at all")


if __name__ == "__main__":
    unittest.main(verbosity=2)
