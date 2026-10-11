"""Tests for the localisation points read out of an existing mask.

This module observes; it must never change which boxes the shipped chain
produced. The tests therefore check both that the points are sensible and that
the boxes are untouched.
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

from localisation import region_centers  # noqa: E402

HEIGHT, WIDTH = 240, 320


def blank():
    return np.zeros((HEIGHT, WIDTH), np.uint8)


class BasicTests(unittest.TestCase):
    def test_no_boxes_gives_no_points(self):
        self.assertEqual(region_centers(blank(), []), [])

    def test_a_solid_blob_centres_on_its_middle(self):
        mask = blank()
        cv2.rectangle(mask, (100, 80), (160, 170), 255, -1)
        centers = region_centers(mask, [(100, 80, 61, 91)])
        self.assertEqual(len(centers), 1)
        self.assertAlmostEqual(centers[0][0], 130.0, delta=0.5)
        self.assertAlmostEqual(centers[0][1], 124.5, delta=0.5)

    def test_the_point_count_always_matches_the_box_count(self):
        mask = blank()
        cv2.rectangle(mask, (10, 10), (40, 40), 255, -1)
        boxes = [(10, 10, 31, 31), (200, 200, 20, 20), (0, 0, 5, 5)]
        self.assertEqual(len(region_centers(mask, boxes)), len(boxes))

    def test_a_box_with_no_foreground_falls_back_to_its_centre(self):
        mask = blank()
        cv2.rectangle(mask, (10, 10), (40, 40), 255, -1)
        centers = region_centers(mask, [(200, 200, 20, 20)])
        self.assertEqual(centers[0], (210.0, 210.0))

    def test_a_box_outside_the_frame_does_not_crash(self):
        mask = blank()
        centers = region_centers(mask, [(330, 250, 20, 20)])
        self.assertEqual(len(centers), 1)


class RobustnessTests(unittest.TestCase):
    """Why the point is the centroid and not the box centre."""

    def _mask_with_spur(self, spur=0):
        mask = blank()
        cv2.rectangle(mask, (100, 80), (160, 170), 255, -1)
        if spur:
            cv2.rectangle(mask, (160, 120), (160 + spur, 122), 255, -1)
        return mask

    def test_a_thin_spur_moves_the_point_much_less_than_the_box(self):
        plain = self._mask_with_spur()
        spurred = self._mask_with_spur(30)

        box_plain = cv2.boundingRect(cv2.findContours(
            plain, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0][0])
        box_spurred = cv2.boundingRect(cv2.findContours(
            spurred, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0][0])

        box_shift = abs((box_spurred[0] + box_spurred[2] / 2.0)
                        - (box_plain[0] + box_plain[2] / 2.0))
        point_shift = abs(
            region_centers(spurred, [box_spurred])[0][0]
            - region_centers(plain, [box_plain])[0][0]
        )

        self.assertGreater(box_shift, 8.0)
        self.assertLess(point_shift, box_shift / 4.0,
                        f"point moved {point_shift:.2f} px, box centre "
                        f"{box_shift:.2f} px")

    def test_a_split_region_points_at_the_middle_of_the_pair(self):
        """Frame differencing reports two edge strips; the point lands between
        them, which is where the object actually is."""
        mask = blank()
        cv2.rectangle(mask, (100, 80), (105, 170), 255, -1)   # leading strip
        cv2.rectangle(mask, (150, 80), (155, 170), 255, -1)   # trailing strip
        centers = region_centers(mask, [(100, 80, 56, 91)])
        self.assertAlmostEqual(centers[0][0], 127.5, delta=2.0)
        self.assertAlmostEqual(centers[0][1], 125.0, delta=2.0)


class IntegrationTests(unittest.TestCase):
    """Run it against the shipped detection chain, not just hand-made masks."""

    def test_points_are_produced_for_the_shipped_chain(self):
        from benchmark_motion import generate_synthetic_frames
        from opencv_software_motion import SoftwareMotionDetector

        frames = generate_synthetic_frames(40)
        detector = SoftwareMotionDetector()
        checked = 0
        for frame in frames:
            mask, boxes, _ = detector.process(frame)
            centers = region_centers(mask, boxes)
            self.assertEqual(len(centers), len(boxes))
            for (x, y, w, h), (cx, cy) in zip(boxes, centers):
                self.assertTrue(x - 1 <= cx <= x + w + 1,
                                f"point {cx} outside box {x}..{x + w}")
                self.assertTrue(y - 1 <= cy <= y + h + 1,
                                f"point {cy} outside box {y}..{y + h}")
            checked += len(boxes)
        self.assertGreater(checked, 0, "the chain produced no boxes to check")

    def test_the_boxes_are_not_modified(self):
        """The module must only read; boxes come back identical."""
        from benchmark_motion import generate_synthetic_frames
        from opencv_software_motion import SoftwareMotionDetector

        frames = generate_synthetic_frames(20)
        detector = SoftwareMotionDetector()
        for frame in frames:
            _, boxes, _ = detector.process(frame)
            before = list(boxes)
            region_centers(np.zeros((HEIGHT, WIDTH), np.uint8), boxes)
            self.assertEqual(list(boxes), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
