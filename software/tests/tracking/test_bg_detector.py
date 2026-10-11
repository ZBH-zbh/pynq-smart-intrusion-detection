"""Tests for the background-model detector.

The first two tests in DetectionTests are the regression tests for the two
reported problems: the original frame-difference chain scores 0.000 recall on a
small slow object and returns exactly two boxes per object.
"""
import os
import sys
import unittest

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DEPLOY = os.path.abspath(os.path.join(HERE, "..", "pynq_deploy"))
sys.path.insert(0, DEPLOY)
sys.path.insert(0, HERE)

from bg_detector import BackgroundDetector  # noqa: E402
from motion_common import HEIGHT, WIDTH  # noqa: E402


def scene(count, warmup, obj_w, obj_h, speed, seed=5):
    """An empty scene for ``warmup`` frames, then one object moving right.

    The object must not be present while the background is learnt, or it would
    be baked into the background -- which is what a real camera sees too.
    Returns (frames, truth) with truth=None during the warmup.
    """
    rng = np.random.default_rng(seed)
    background = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    background[:, :, 0] = np.linspace(20, 60, WIDTH, dtype=np.uint8)[None, :]
    background[:, :, 1] = np.linspace(30, 90, WIDTH, dtype=np.uint8)[None, :]
    background[:, :, 2] = np.linspace(40, 120, WIDTH, dtype=np.uint8)[None, :]

    frames, truth = [], []
    x = 10
    y = (HEIGHT - obj_h) // 2
    for i in range(count):
        frame = background.copy()
        if i >= warmup:
            cv2.rectangle(
                frame, (x, y), (x + obj_w, y + obj_h), (40, 180, 240), -1
            )
            # Clip the truth to the visible area: once the object reaches the
            # right edge cv2 draws only the visible part, so a full-width truth
            # box would be unreachable by any correct detector.
            visible_right = min(x + obj_w + 1, WIDTH)
            truth.append((x, y, visible_right - x, obj_h + 1))
            x += speed
        else:
            truth.append(None)
        noise = rng.integers(-1, 2, size=frame.shape, dtype=np.int16)
        frames.append(
            np.clip(frame.astype(np.int16) + noise, 0, 255).astype(np.uint8)
        )
    return frames, truth


def iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = min(ax + aw, bx + bw) - max(ax, bx)
    iy = min(ay + ah, by + bh) - max(ay, by)
    if ix <= 0 or iy <= 0:
        return 0.0
    inter = float(ix * iy)
    return inter / (aw * ah + bw * bh - inter)


def run(detector, frames):
    return [detector.process(f)[1] for f in frames]


class ParameterTests(unittest.TestCase):
    def test_alpha_bounds(self):
        with self.assertRaises(ValueError):
            BackgroundDetector(alpha=0.0)
        with self.assertRaises(ValueError):
            BackgroundDetector(alpha=1.5)

    def test_init_frames_bounds(self):
        with self.assertRaises(ValueError):
            BackgroundDetector(init_frames=0)

    def test_reset_clears_background(self):
        detector = BackgroundDetector()
        frames, _ = scene(20, 8, 30, 38, 4)
        run(detector, frames)
        self.assertIsNotNone(detector.background)
        detector.reset()
        self.assertIsNone(detector.background)
        self.assertEqual(detector.frames, 0)

    def test_info_reports_configuration(self):
        info = BackgroundDetector(alpha=0.1, min_area=50).info()
        self.assertEqual(info["detector"], "bg")
        self.assertEqual(info["alpha"], 0.1)
        self.assertEqual(info["min_area"], 50)


class WarmupTests(unittest.TestCase):
    def test_first_frame_has_no_detection(self):
        detector = BackgroundDetector()
        mask, boxes = detector.process(np.zeros((HEIGHT, WIDTH, 3), np.uint8))
        self.assertEqual(boxes, [])
        self.assertEqual(int(mask.sum()), 0)

    def test_static_scene_settles_to_no_boxes(self):
        detector = BackgroundDetector()
        frames, _ = scene(40, 40, 30, 38, 0)   # warmup == count: nothing moves
        detections = run(detector, frames)
        tail = detections[15:]
        self.assertEqual(
            sum(len(b) for b in tail), 0,
            f"static scene produced false positives: {tail}",
        )


class DetectionTests(unittest.TestCase):
    """The two regression tests for the reported problems."""

    def test_small_slow_object_is_detected(self):
        """Problem 1: a 16x20 object at 2 px/frame is invisible to frame diff.

        Measured recall for the original chain is 0.000 in this configuration.
        """
        detector = BackgroundDetector()
        frames, truth = scene(60, 12, 16, 20, 2)
        detections = run(detector, frames)

        scored = [
            (boxes, gt)
            for boxes, gt in zip(detections, truth)
            if gt is not None
        ]
        hits = sum(
            1 for boxes, gt in scored
            if max((iou(b, gt) for b in boxes), default=0.0) >= 0.5
        )
        recall = hits / len(scored)
        self.assertGreater(recall, 0.8, f"recall {recall:.3f} is too low")

    def test_one_object_yields_one_box(self):
        """Problem 2: frame diff always returns two boxes (leading+trailing)."""
        detector = BackgroundDetector()
        frames, truth = scene(60, 12, 56, 71, 8)
        detections = run(detector, frames)

        counted = [
            len(boxes) for boxes, gt in zip(detections, truth)
            if gt is not None and boxes
        ]
        self.assertGreater(len(counted), 20)
        mean_boxes = sum(counted) / len(counted)
        self.assertLess(
            mean_boxes, 1.5,
            f"object split into {mean_boxes:.2f} boxes on average",
        )

    def test_recall_across_sizes_and_speeds(self):
        """The original chain collapses over most of this grid."""
        for obj_w, obj_h in ((56, 71), (30, 38), (16, 20)):
            for speed in (2, 4, 8):
                detector = BackgroundDetector()
                frames, truth = scene(60, 12, obj_w, obj_h, speed)
                detections = run(detector, frames)
                scored = [
                    (b, gt) for b, gt in zip(detections, truth)
                    if gt is not None
                ]
                hits = sum(
                    1 for boxes, gt in scored
                    if max((iou(x, gt) for x in boxes), default=0.0) >= 0.5
                )
                recall = hits / len(scored)
                self.assertGreater(
                    recall, 0.6,
                    f"{obj_w}x{obj_h} at {speed} px/frame: "
                    f"recall {recall:.3f}",
                )

    def test_box_tracks_the_object_not_the_edges(self):
        """The reported box must cover the object, not one of its edges."""
        detector = BackgroundDetector()
        frames, truth = scene(50, 12, 40, 60, 6)
        detections = run(detector, frames)
        for boxes, gt in zip(detections, truth):
            if gt is None or not boxes:
                continue
            best = max(iou(b, gt) for b in boxes)
            self.assertGreater(
                best, 0.6,
                f"box {boxes} does not cover truth {gt} (best IoU {best:.2f})",
            )


class AreaFloorTests(unittest.TestCase):
    """Why the erosion is off: it forces the area floor down."""

    def _hits(self, **options):
        detector = BackgroundDetector(**options)
        frames, truth = scene(50, 12, 16, 20, 4)
        detections = [detector.process(f)[1] for f in frames]
        return sum(
            1 for boxes, gt in zip(detections, truth)
            if gt is not None
            and max((iou(b, gt) for b in boxes), default=0.0) >= 0.5
        )

    def test_erosion_shrinks_a_small_object_below_the_project_floor(self):
        """16x20 erodes to 14x18 = 252, under MIN_CONTOUR_AREA = 300."""
        with_erosion = self._hits(
            erosion=True, close_ksize=7, min_area=300
        )
        self.assertLess(
            with_erosion, 10,
            "erosion plus a 300 px floor should reject most of a 16x20 object",
        )

    def test_without_erosion_the_project_floor_is_fine(self):
        without = self._hits(
            erosion=False, close_ksize=1, min_area=300
        )
        self.assertGreater(without, 25)

    def test_default_config_uses_the_project_floor(self):
        from bg_detector import DEFAULT_MIN_AREA
        self.assertEqual(DEFAULT_MIN_AREA, 300)
        self.assertGreater(self._hits(), 25)


class LimitationTests(unittest.TestCase):
    """The conditional update trades absorption speed for a clean background."""

    def _hold_still(self, detector, frames, count):
        seq = list(frames)
        for _ in range(count):
            frame = frames[-1].copy()
            cv2.rectangle(frame, (100, 80), (140, 140), (40, 180, 240), -1)
            seq.append(frame)
        return run(detector, seq)

    def test_a_stationary_object_is_reported_for_several_seconds(self):
        """A still object is held, which is what an intrusion detector wants.

        ``alpha_foreground`` is low so that a moving object leaves no ghost
        behind it; the same setting means a still object is not absorbed within
        seconds. Both are the desired behaviour here: the ghost was a bug, and
        an intruder who stops walking should keep being reported.
        """
        base, _ = scene(12, 12, 40, 60, 0)
        detector = BackgroundDetector()
        detections = self._hold_still(detector, base, 200)

        early = detections[len(base):len(base) + 45]
        reported = sum(1 for d in early if d)
        self.assertGreater(
            reported / len(early), 0.9,
            "a still object should be reported",
        )
        self.assertTrue(
            detections[-1],
            "a still object should still be reported after 200 frames",
        )

    def test_a_high_foreground_rate_eventually_absorbs_it(self):
        """The knob still works for anyone who wants fast absorption."""
        base, _ = scene(12, 12, 40, 60, 0)
        detector = BackgroundDetector(alpha_foreground=0.05)
        detections = self._hold_still(detector, base, 200)
        self.assertTrue(detections[len(base) + 2])
        self.assertEqual(detections[-1], [])

    def test_a_slower_foreground_rate_holds_a_still_object_longer(self):
        def held_frames(rate):
            base, _ = scene(12, 12, 40, 60, 0)
            detector = BackgroundDetector(alpha_foreground=rate)
            detections = self._hold_still(detector, base, 900)
            held = 0
            for d in detections[len(base):]:
                if d:
                    held += 1
                else:
                    break
            return held

        fast = held_frames(0.03)
        slow = held_frames(0.003)
        self.assertGreater(slow, fast * 3)

    def test_moving_object_leaves_no_ghost_trail(self):
        """The reported box must not include where the object used to be."""
        detector = BackgroundDetector()
        frames, truth = scene(60, 12, 40, 60, 6)
        detections = run(detector, frames)
        for boxes, gt in zip(detections, truth):
            if gt is None or not boxes:
                continue
            best = max(iou(b, gt) for b in boxes)
            self.assertGreater(
                best, 0.7,
                f"box {boxes} trails the object at {gt} (best IoU {best:.2f})",
            )

    def test_a_local_change_keeps_being_reported(self):
        """An object that appears and stays IS the thing this system looks for.

        ``alpha_foreground`` was lowered to 0.002 to remove ghost trails, which
        also means a localised change is no longer absorbed within seconds. That
        is the right behaviour for an intrusion detector -- a person standing in
        the zone should keep being reported -- and genuine scene changes are
        handled by the relearn state instead of by slow absorption.
        """
        base, _ = scene(12, 12, 40, 60, 0)
        detector = BackgroundDetector()          # shipped defaults
        seq = list(base)
        for _ in range(400):
            frame = base[-1].copy()
            cv2.rectangle(frame, (30, 30), (90, 90), (200, 200, 200), -1)
            seq.append(frame)
        detections = run(detector, seq)
        self.assertTrue(detections[len(base) + 2], "change should be seen at first")
        self.assertTrue(
            detections[-1],
            "an object that is still there should still be reported",
        )

    def test_a_higher_foreground_rate_absorbs_it(self):
        """The old behaviour is still available, and is what it costs."""
        base, _ = scene(12, 12, 40, 60, 0)
        detector = BackgroundDetector(alpha_foreground=0.05)
        for frame in base:
            detector.process(frame)
        changed = base[-1].copy()
        cv2.rectangle(changed, (30, 30), (90, 90), (200, 200, 200), -1)
        detections = [len(detector.process(changed)[1]) for _ in range(400)]
        self.assertTrue(detections[2])
        self.assertEqual(detections[-1], 0)

    def test_convergence_is_monotonic_after_a_scene_change(self):
        """The residual must keep falling, not plateau above the threshold."""
        base, _ = scene(12, 12, 40, 60, 0)
        detector = BackgroundDetector()
        for frame in base:
            detector.process(frame)

        changed = base[-1].copy()
        cv2.rectangle(changed, (30, 30), (90, 90), (200, 200, 200), -1)

        residuals = []
        for i in range(400):
            detector.process(changed)
            if i % 100 == 99:
                residuals.append(float(np.abs(
                    cv2.cvtColor(changed, cv2.COLOR_BGR2GRAY).astype(np.int16)
                    - detector.background.astype(np.int16)
                ).mean()))

        self.assertLess(residuals[-1], residuals[0])
        self.assertLess(
            residuals[-1], residuals[-2] + 1e-6,
            f"residual plateaued: {residuals}",
        )
        self.assertLess(residuals[-1], 30.0, f"residual stuck: {residuals}")

    def test_slower_foreground_rate_holds_longer(self):
        def frames_until_loss(rate):
            base, _ = scene(12, 12, 40, 60, 0)
            detector = BackgroundDetector(alpha_foreground=rate)
            detections = self._hold_still(detector, base, 900)
            for i in range(len(base), len(detections)):
                if not detections[i]:
                    return i - len(base)
            return 999

        self.assertGreater(frames_until_loss(0.003), frames_until_loss(0.03) * 3)


class GlobalChangeTests(unittest.TestCase):
    """A light switching on must not leave boxes behind for hundreds of frames.

    Live symptom this reproduces: consecutive frames differed in 1-3 pixels out
    of 76800, yet a box kept being reported in the same corner. The conditional
    update had classified the whole frame as foreground during the lighting
    change and then protected the stale background from all of it at
    alpha_foreground, so it never caught up.
    """

    def _run_with_light_change(self, **options):
        base, _ = scene(30, 30, 40, 60, 0)
        detector = BackgroundDetector(**options)
        for frame in base:
            detector.process(frame)

        bright = np.clip(base[-1].astype(np.int16) + 35, 0, 255).astype(np.uint8)
        detections = []
        for _ in range(300):
            _, boxes = detector.process(bright)
            detections.append(len(boxes))
        return detections, detector

    def test_brightness_step_does_not_report_targets(self):
        detections, _ = self._run_with_light_change()
        offenders = [i for i, c in enumerate(detections) if c]
        self.assertEqual(
            offenders[:5], [],
            f"a global brightness step produced targets at frames {offenders[:5]}",
        )

    def test_background_recovers_quickly_after_a_light_step(self):
        detections, detector = self._run_with_light_change()
        # With the old behaviour the stale background persisted for hundreds of
        # frames; the whole 300-frame tail should be clean after recovery.
        tail = detections[20:]
        self.assertEqual(
            sum(1 for c in tail if c), 0,
            f"background did not recover: {sum(1 for c in tail if c)} of "
            f"{len(tail)} frames still reported targets",
        )
        # Either recovery path is acceptable: the one-frame global-change guard
        # or a full relearn. What matters is that one of them ran.
        self.assertGreaterEqual(
            detector.global_changes + detector.relearn_events, 1,
            "neither recovery path was taken",
        )

    def test_darkening_is_handled_too(self):
        base, _ = scene(30, 30, 40, 60, 0)
        detector = BackgroundDetector()
        for frame in base:
            detector.process(frame)
        dark = np.clip(base[-1].astype(np.int16) - 35, 0, 255).astype(np.uint8)
        detections = [len(detector.process(dark)[1]) for _ in range(300)]
        self.assertEqual(sum(1 for c in detections[20:] if c), 0)

    def test_a_large_real_object_is_still_detected(self):
        """The global-change guard must not swallow ordinary target motion."""
        detector = BackgroundDetector()
        frames, truth = scene(60, 12, 56, 71, 8)
        detections = [detector.process(f)[1] for f in frames]
        scored = [(b, gt) for b, gt in zip(detections, truth) if gt is not None]
        hits = sum(
            1 for boxes, gt in scored
            if max((iou(x, gt) for x in boxes), default=0.0) >= 0.5
        )
        self.assertGreater(hits / len(scored), 0.8)
        self.assertEqual(detector.global_changes, 0)

    def test_fraction_bounds(self):
        with self.assertRaises(ValueError):
            BackgroundDetector(global_change_fraction=0.0)
        with self.assertRaises(ValueError):
            BackgroundDetector(global_change_fraction=1.5)


def fragmented_scene(count, warmup, obj_w, obj_h, speed, band, seed=5):
    """One object whose middle band is background-coloured, so the difference
    mask splits in two.

    This is the case the earlier validation missed: the synthetic object used to
    choose the defaults was a solid rectangle, which never fragments, so
    dropping the morphological closing looked free. A real object -- a white
    sleeve against a white wall -- does fragment, and the tracker then sees two
    targets and its centre jumps between them.

    ``band`` is (start_fraction, end_fraction) of the object's height painted in
    the background colour.
    """
    rng = np.random.default_rng(seed)
    background = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    background[:, :, 0] = np.linspace(20, 60, WIDTH, dtype=np.uint8)[None, :]
    background[:, :, 1] = np.linspace(30, 90, WIDTH, dtype=np.uint8)[None, :]
    background[:, :, 2] = np.linspace(40, 120, WIDTH, dtype=np.uint8)[None, :]

    frames, truth = [], []
    x = 10
    y = (HEIGHT - obj_h) // 2
    b0 = int(y + band[0] * obj_h)
    b1 = int(y + band[1] * obj_h)
    for i in range(count):
        frame = background.copy()
        if i >= warmup:
            cv2.rectangle(
                frame, (x, y), (x + obj_w, y + obj_h), (40, 180, 240), -1
            )
            # Paint the band back to the background so it produces no change.
            frame[b0:b1, x:x + obj_w] = background[b0:b1, x:x + obj_w]
            visible_right = min(x + obj_w + 1, WIDTH)
            truth.append((x, y, visible_right - x, obj_h + 1))
            x += speed
        else:
            truth.append(None)
        noise = rng.integers(-1, 2, size=frame.shape, dtype=np.int16)
        frames.append(
            np.clip(frame.astype(np.int16) + noise, 0, 255).astype(np.uint8)
        )
    return frames, truth


class FragmentationTests(unittest.TestCase):
    """One object must come back as one box, not several."""

    def _box_counts(self, merge, **options):
        from strip_merge import merge_fragments_2d
        detector = BackgroundDetector(**options)
        frames, truth = fragmented_scene(60, 12, 40, 120, 4, (0.42, 0.58))
        counts = []
        for frame, gt in zip(frames, truth):
            _, boxes = detector.process(frame)
            if merge:
                boxes = merge_fragments_2d(boxes)
            if gt is not None:
                counts.append(len(boxes))
        return counts

    def test_the_object_really_fragments_without_grouping(self):
        counts = self._box_counts(merge=False)
        self.assertGreater(
            sum(counts) / len(counts), 1.5,
            f"expected fragmentation, got {counts[:12]}",
        )

    def test_grouping_rejoins_the_fragments(self):
        counts = self._box_counts(merge=True)
        self.assertLess(
            sum(counts) / len(counts), 1.2,
            f"grouping should give about one box per object, got {counts[:12]}",
        )

    def test_morphological_closing_cannot_bridge_the_hole(self):
        """Documents why grouping is used instead of a closing kernel.

        The hole is 19 px; closing would need a 21x21 kernel, which would cost
        far more than grouping and would merge unrelated neighbours.
        """
        counts = self._box_counts(merge=False, close_ksize=5)
        self.assertGreaterEqual(
            sum(counts) / len(counts), 1.5,
            f"closing should leave the object fragmented, got {counts[:12]}",
        )


class SprawlFilterTests(unittest.TestCase):
    """Thin stripes and frame-spanning boxes must not be reported as targets."""

    def _learn_background(self, detector, count=20):
        """Feed the flat background so the model is settled before the change.

        The stripes must arrive as a sudden change. Feeding many stripe frames
        in a row lets the background absorb them (0.94^n collapses quickly) and
        then nothing is detectable -- which is a property of any background
        model, not of the filter under test.
        """
        background = np.zeros((HEIGHT, WIDTH, 3), np.uint8)
        background[:, :, :] = 80
        for _ in range(count):
            detector.process(background)
        return background

    def _stripe_frame(self, background, offset=0):
        frame = background.copy()
        for x in range(-8 + offset, WIDTH, 12):
            cv2.line(frame, (x, 0), (x, HEIGHT - 1), (130, 130, 130), 3)
        return frame

    def test_thin_stripes_are_rejected(self):
        detector = BackgroundDetector()
        background = self._learn_background(detector)
        _, boxes = detector.process(self._stripe_frame(background))
        self.assertEqual(
            boxes, [],
            f"curtain-like stripes produced {len(boxes)} targets: {boxes[:5]}",
        )

    def test_without_the_thickness_filter_the_stripes_are_reported(self):
        """Shows the thickness filter is what removes them."""
        detector = BackgroundDetector(min_thickness=1, min_fill_ratio=0.0)
        background = self._learn_background(detector)
        _, boxes = detector.process(self._stripe_frame(background))
        self.assertGreater(
            len(boxes), 3,
            f"expected the stripes to pass when unfiltered, got {boxes}",
        )

    def _recall(self, obj_w, obj_h, speed, warmup=12):
        detector = BackgroundDetector()
        frames, truth = scene(50, warmup, obj_w, obj_h, speed)
        # Every frame must go through the detector, including the warm-up,
        # otherwise the model starts with the object already in view.
        detections = [detector.process(f)[1] for f in frames]
        hits = scored = 0
        for boxes, gt in zip(detections, truth):
            if gt is None:
                continue
            scored += 1
            if max((iou(b, gt) for b in boxes), default=0.0) >= 0.5:
                hits += 1
        return hits, scored

    def test_a_solid_object_is_not_rejected(self):
        hits, scored = self._recall(40, 60, 6)
        self.assertGreater(
            hits / scored, 0.9,
            f"only {hits}/{scored} frames detected the solid object",
        )

    def test_a_small_distant_object_is_not_rejected_by_thickness(self):
        """A 16x20 object is the smallest this system is expected to see."""
        hits, scored = self._recall(16, 20, 4)
        self.assertGreater(
            hits / scored, 0.8,
            f"only {hits}/{scored} frames detected the small object",
        )

    def test_bounds(self):
        with self.assertRaises(ValueError):
            BackgroundDetector(min_fill_ratio=1.5)
        with self.assertRaises(ValueError):
            BackgroundDetector(max_box_fraction=0.0)
        with self.assertRaises(ValueError):
            BackgroundDetector(min_thickness=0)


class AdaptiveThresholdTests(unittest.TestCase):
    """The threshold must follow the scene, not stay at a fixed 30."""

    def _run(self, noise, frames=60, **options):
        rng = np.random.default_rng(11)
        base = np.full((HEIGHT, WIDTH, 3), 110, np.uint8)
        detector = BackgroundDetector(**options)
        for _ in range(frames):
            frame = base
            if noise > 0:
                values = rng.normal(0, noise, size=base.shape)
                frame = np.clip(base.astype(np.float32) + values,
                                0, 255).astype(np.uint8)
            detector.process(frame)
        return detector

    def test_a_quiet_scene_drives_the_threshold_down(self):
        detector = self._run(0.0)
        self.assertLess(
            detector.threshold, 15,
            f"a noise-free scene should allow a low threshold, got "
            f"{detector.threshold:.1f}",
        )

    def test_a_noisy_scene_drives_the_threshold_up(self):
        quiet = self._run(0.0).threshold
        noisy = self._run(6.0).threshold
        self.assertGreater(
            noisy, quiet,
            f"noisy scene threshold {noisy:.1f} should exceed quiet "
            f"{quiet:.1f}",
        )

    def test_the_threshold_stays_inside_its_clamps(self):
        for noise in (0.0, 3.0, 20.0):
            detector = self._run(noise)
            self.assertGreaterEqual(detector.threshold, detector.min_threshold)
            self.assertLessEqual(detector.threshold, detector.max_threshold)

    def test_a_noisy_scene_does_not_produce_false_targets(self):
        detector = self._run(6.0)
        self.assertEqual(
            detector.process(np.full((HEIGHT, WIDTH, 3), 110, np.uint8))[1], []
        )

    def test_calibration_keeps_running_while_an_object_is_present(self):
        """The threshold must not freeze just because something is moving.

        Calibrating on the whole frame would either be skipped whenever an
        object is present, or would let that object raise the threshold until it
        hid itself. The histogram is therefore taken over the background pixels
        only.
        """
        rng = np.random.default_rng(3)
        base = np.full((HEIGHT, WIDTH, 3), 110, np.uint8)
        detector = BackgroundDetector()
        for _ in range(20):
            detector.process(base)
        settled = detector.threshold

        moving = []
        for i in range(120):
            frame = base.copy()
            noise = rng.normal(0, 5.0, size=frame.shape)
            frame = np.clip(frame.astype(np.float32) + noise, 0, 255
                            ).astype(np.uint8)
            x = 10 + (i * 6) % 250
            cv2.rectangle(frame, (x, 80), (x + 60, 160), (150, 150, 150), -1)
            detector.process(frame)
            moving.append(detector.threshold)

        self.assertGreater(abs(moving[-1] - settled), 0.5,
                           "threshold did not move at all while an object moved")
        self.assertGreater(moving[-1], 10,
                           "a noisier scene should have raised the threshold")

    def test_disabling_adaptation_keeps_the_fixed_threshold(self):
        detector = self._run(0.0, adaptive_threshold=False, threshold=30)
        self.assertEqual(detector.threshold, 30)

    def test_a_large_object_does_not_raise_the_threshold(self):
        """Regression: an object must not raise the threshold that sees it.

        The calibration target is a fraction of the BACKGROUND population, so a
        large object shrinks both the population and the target. Measured live,
        an object covering 42% of the frame pushed the threshold from 15 to 32,
        which then missed faint targets.
        """
        rng = np.random.default_rng(9)
        base = np.full((HEIGHT, WIDTH, 3), 110, np.uint8)

        def noisy():
            values = rng.normal(0, 2.0, size=base.shape)
            return np.clip(base.astype(np.float32) + values, 0, 255
                           ).astype(np.uint8)

        detector = BackgroundDetector()
        for _ in range(30):
            detector.process(noisy())
        settled = detector.threshold

        for i in range(60):
            frame = noisy()
            x = 20 + (i * 3) % 40
            cv2.rectangle(frame, (x, 10), (x + 200, 230), (60, 60, 60), -1)
            detector.process(frame)

        self.assertLess(
            detector.threshold, settled + 4,
            f"threshold climbed from {settled:.1f} to {detector.threshold:.1f} "
            f"while a large object was on screen",
        )

    def test_bounds(self):
        with self.assertRaises(ValueError):
            BackgroundDetector(target_foreground_fraction=0.0)
        with self.assertRaises(ValueError):
            BackgroundDetector(min_threshold=0)
        with self.assertRaises(ValueError):
            BackgroundDetector(min_threshold=30, max_threshold=10)


class RelearnTests(unittest.TestCase):
    """A changed scene must trigger a rebuild, not a screen full of boxes."""

    def _settle(self, detector, value=110, count=25):
        base = np.full((HEIGHT, WIDTH, 3), value, np.uint8)
        for _ in range(count):
            detector.process(base)
        return base

    def test_a_moved_camera_triggers_relearning(self):
        detector = BackgroundDetector()
        self._settle(detector)
        # A genuinely different room: most pixels change. An earlier version of
        # this test used a smooth gradient, which only differed from the stored
        # background over ~27% of the frame -- not what moving a camera looks
        # like, and it hid the fact that the trigger was far too easy to hit.
        rng = np.random.default_rng(17)
        other = rng.integers(0, 255, size=(HEIGHT, WIDTH, 3), dtype=np.uint8)

        detections = []
        for _ in range(200):
            detections.append(len(detector.process(other)[1]))

        self.assertGreaterEqual(detector.relearn_events, 1,
                                "the scene change was not noticed")
        self.assertEqual(detections[0], 0,
                         "relearning should suppress reporting immediately")
        self.assertEqual(detections[-1], 0,
                         "reporting should stop once the new scene is learnt")
        self.assertFalse(detector.relearning, "relearning never finished")

    def test_a_large_close_object_does_not_trigger_relearning(self):
        """The regression that broke detection: a hand near the lens.

        Live, the relearn state fired 14 times during one round of hand waving,
        and each time it stopped reporting and leant the hand into the
        background. Size alone cannot distinguish that from a moved camera, so
        the trigger now also requires a near-total change.
        """
        detector = BackgroundDetector()
        base = self._settle(detector)
        for i in range(60):
            frame = base.copy()
            x = 30 + (i * 7) % 60
            cv2.rectangle(frame, (x, 15), (x + 210, 225), (30, 30, 30), -1)
            detector.process(frame)
        self.assertEqual(
            detector.relearn_events, 0,
            "a large object close to the camera was mistaken for a scene change",
        )

    def test_a_large_object_is_still_detected(self):
        """And it must actually be reported, not filtered away."""
        detector = BackgroundDetector()
        base = self._settle(detector)
        hits = 0
        for i in range(40):
            frame = base.copy()
            x = 40 + i * 4
            cv2.rectangle(frame, (x, 40), (x + 200, 200), (30, 30, 30), -1)
            _, boxes = detector.process(frame)
            if boxes:
                hits += 1
        self.assertGreater(hits, 30, f"only {hits}/40 frames reported it")

    def test_reporting_resumes_after_relearning(self):
        detector = BackgroundDetector()
        self._settle(detector)
        other = np.full((HEIGHT, WIDTH, 3), 60, np.uint8)
        for _ in range(200):
            detector.process(other)
        self.assertFalse(detector.relearning)

        # Now move an object through the new scene: it must be seen again.
        hits = 0
        for i in range(40):
            frame = other.copy()
            x = 10 + i * 5
            cv2.rectangle(frame, (x, 80), (x + 50, 160), (200, 200, 200), -1)
            _, boxes = detector.process(frame)
            if boxes:
                hits += 1
        self.assertGreater(hits, 25, f"only {hits}/40 frames detected")

    def test_relearning_clears_the_stale_background(self):
        detector = BackgroundDetector()
        self._settle(detector)
        other = np.full((HEIGHT, WIDTH, 3), 40, np.uint8)
        for _ in range(200):
            detector.process(other)
        residual = float(np.abs(
            cv2.cvtColor(other, cv2.COLOR_BGR2GRAY).astype(np.int16)
            - detector.background.astype(np.int16)
        ).mean())
        self.assertLess(residual, 5.0,
                        f"background did not follow the new scene "
                        f"(residual {residual:.1f})")

    def test_a_moving_object_does_not_trigger_relearning(self):
        """Only a scene-wide change counts, not a target crossing the view."""
        detector = BackgroundDetector()
        base = self._settle(detector)
        for i in range(80):
            frame = base.copy()
            x = 10 + (i * 6) % 250
            cv2.rectangle(frame, (x, 60), (x + 70, 180), (40, 40, 40), -1)
            detector.process(frame)
        self.assertEqual(detector.relearn_events, 0,
                         "a moving object was mistaken for a scene change")

    def test_startup_does_not_count_as_relearning(self):
        """The background is being built at start-up, not re-learnt."""
        detector = BackgroundDetector()
        rng = np.random.default_rng(5)
        noise = rng.normal(0, 30, size=(HEIGHT, WIDTH, 3))
        for _ in range(20):
            frame = np.clip(
                np.full((HEIGHT, WIDTH, 3), 110, np.float32) + noise,
                0, 255).astype(np.uint8)
            detector.process(frame)
        self.assertEqual(detector.relearn_events, 0)

    def test_bounds(self):
        with self.assertRaises(ValueError):
            BackgroundDetector(scene_change_frames=0)
        with self.assertRaises(ValueError):
            BackgroundDetector(stable_frames=0)
        with self.assertRaises(ValueError):
            BackgroundDetector(relearn_alpha=0.0)


class LocalisationTests(unittest.TestCase):
    """The reported centre must be the region's centroid, not the box centre.

    The bounding-box centre is the least steady estimate available: one stray
    pixel on the mask edge moves it by half that pixel's distance. The pixel
    centroid moves by d/N instead, which for a couple of thousand foreground
    pixels is two orders of magnitude smaller -- and it costs no lag, because it
    is a per-frame spatial estimate rather than an average over time.
    """

    def _centres_for(self, protrusion=0):
        """Run one frame with a blob, optionally with a thin protrusion.

        A single stray pixel would prove nothing: the 3x3 opening already
        removes isolated pixels. The shape that survives and still stretches the
        bounding box is a thin spur -- 3 px wide, so the opening keeps it, but
        only a few dozen pixels, so the mass centre barely notices.
        """
        background = np.zeros((HEIGHT, WIDTH, 3), np.uint8)
        background[:, :, :] = 80
        detector = BackgroundDetector()
        for _ in range(20):
            detector.process(background)

        frame = background.copy()
        cv2.rectangle(frame, (100, 80), (160, 170), (200, 200, 200), -1)
        if protrusion:
            cv2.rectangle(frame, (160, 120), (160 + protrusion, 122),
                          (200, 200, 200), -1)
        _, boxes = detector.process(frame)
        self.assertEqual(len(boxes), 1, f"expected one target, got {boxes}")
        self.assertEqual(len(detector.last_centers), len(boxes))
        return boxes[0], detector.last_centers[0]

    def test_the_centre_is_the_pixel_centroid(self):
        box, center = self._centres_for()
        # The rectangle is 61 x 91, so the centroid is at its middle.
        self.assertAlmostEqual(center[0], 130.0, delta=0.5)
        self.assertAlmostEqual(center[1], 124.5, delta=0.5)

    def test_a_thin_protrusion_moves_the_box_more_than_the_centroid(self):
        box_a, center_a = self._centres_for()
        box_b, center_b = self._centres_for(protrusion=30)

        box_shift = abs((box_b[0] + box_b[2] / 2.0)
                        - (box_a[0] + box_a[2] / 2.0))
        center_shift = abs(center_b[0] - center_a[0])

        self.assertGreater(
            box_shift, 8.0,
            f"the spur should visibly widen the box, moved {box_shift:.2f}",
        )
        self.assertLess(
            center_shift, box_shift / 5.0,
            f"centroid moved {center_shift:.2f} px while the box centre moved "
            f"{box_shift:.2f} px",
        )

    def test_centres_are_cleared_when_no_boxes_are_returned(self):
        """Stale centres would desynchronise the tracker from the boxes."""
        background = np.zeros((HEIGHT, WIDTH, 3), np.uint8)
        background[:, :, :] = 80
        detector = BackgroundDetector()
        for _ in range(20):
            detector.process(background)
        frame = background.copy()
        cv2.rectangle(frame, (100, 80), (160, 170), (200, 200, 200), -1)
        _, boxes = detector.process(frame)
        self.assertTrue(boxes)
        self.assertTrue(detector.last_centers)

        # A frame the detector suppresses must not leave the old centres behind.
        detector.scene_change_fraction = 0.0      # force the relearn path
        detector.frames = 100
        _, boxes = detector.process(frame)
        self.assertEqual(boxes, [])
        self.assertEqual(detector.last_centers, [])

    def test_merged_centre_follows_the_mass(self):
        from strip_merge import merge_centers
        parts = [(0, 0, 20, 20), (40, 0, 20, 20)]
        merged = [(0, 0, 60, 20)]
        centers = merge_centers(parts, [(10.0, 10.0), (50.0, 10.0)], merged)
        self.assertEqual(len(centers), 1)
        # Equal areas -> the midpoint.
        self.assertAlmostEqual(centers[0][0], 30.0)

        weighted = merge_centers(
            [(0, 0, 40, 20), (40, 0, 10, 20)],
            [(20.0, 10.0), (45.0, 10.0)],
            [(0, 0, 50, 20)],
        )
        # The big part dominates: (20*800 + 45*200) / 1000.
        self.assertAlmostEqual(weighted[0][0], 25.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
