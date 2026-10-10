import sys
from pathlib import Path
import unittest

import cv2
import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from motion_common import (  # noqa: E402
    HEIGHT,
    WIDTH,
    check_alarm,
    emulate_current_pl_erosion,
    extract_low_byte_mask,
    hls_bgr2gray_reference,
    merge_nearby_boxes,
    pack_bgr_to_rgb32,
)
from opencv_software_motion import SoftwareMotionDetector  # noqa: E402


class MotionCommonTests(unittest.TestCase):
    def test_bgr_packing_matches_existing_rgb_formula(self):
        rng = np.random.default_rng(20260913)
        frame = rng.integers(0, 256, size=(17, 19, 3), dtype=np.uint8)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB).reshape(-1, 3).astype(np.uint32)
        expected = (rgb[:, 0] << 16) | (rgb[:, 1] << 8) | rgb[:, 2]

        output = np.empty(frame.shape[0] * frame.shape[1], dtype=np.uint32)
        scratch = np.empty_like(output)
        actual = pack_bgr_to_rgb32(frame, output=output, scratch=scratch)

        self.assertTrue(np.shares_memory(actual, output))
        np.testing.assert_array_equal(actual, expected)

    def test_extracts_low_byte_from_pl_words(self):
        words = np.array(
            [0x12345600, 0xABCDEF55, 0x000000FF, 0xFFFFFFFF],
            dtype=np.uint32,
        )
        mask = extract_low_byte_mask(words, width=2, height=2)
        np.testing.assert_array_equal(
            mask,
            np.array([[0, 85], [255, 255]], dtype=np.uint8),
        )

    def test_hls_gray_is_close_to_opencv_gray(self):
        rng = np.random.default_rng(20260913)
        frame = rng.integers(0, 256, size=(HEIGHT, WIDTH, 3), dtype=np.uint8)
        hls_gray = hls_bgr2gray_reference(frame)
        opencv_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        error = np.abs(hls_gray.astype(np.int16) - opencv_gray.astype(np.int16))
        self.assertLessEqual(int(error.max()), 2)

    def test_current_pl_erosion_alignment(self):
        mask = np.zeros((20, 20), dtype=np.uint8)
        mask[5:10, 7:12] = 255
        eroded = emulate_current_pl_erosion(mask)
        points = cv2.findNonZero(eroded)
        self.assertIsNotNone(points)
        self.assertEqual(cv2.boundingRect(points), (9, 7, 3, 3))

    def test_merge_and_alarm_match_current_rules(self):
        boxes = [(10, 10, 20, 20), (45, 12, 20, 20), (250, 10, 10, 10)]
        merged = merge_nearby_boxes(boxes)
        self.assertEqual(merged, [(10, 10, 55, 22), (250, 10, 10, 10)])
        self.assertTrue(check_alarm([(100, 80, 20, 20)]))
        self.assertFalse(check_alarm([(0, 0, 20, 20)]))


class SoftwareMotionDetectorTests(unittest.TestCase):
    def test_first_frame_primes_background(self):
        detector = SoftwareMotionDetector()
        frame = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
        mask, targets, alarm = detector.process(frame)
        self.assertEqual(int(mask.sum()), 0)
        self.assertEqual(targets, [])
        self.assertFalse(alarm)

    def test_detects_one_target_in_alarm_zone(self):
        detector = SoftwareMotionDetector()
        background = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
        moving = background.copy()
        cv2.rectangle(moving, (100, 80), (150, 140), (255, 255, 255), -1)

        detector.process(background)
        mask, targets, alarm = detector.process(moving)

        self.assertGreater(int(np.count_nonzero(mask)), 0)
        self.assertEqual(len(targets), 1)
        self.assertTrue(alarm)

    def test_identical_next_frame_has_no_motion(self):
        detector = SoftwareMotionDetector()
        background = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
        moving = background.copy()
        cv2.rectangle(moving, (100, 80), (150, 140), (255, 255, 255), -1)

        detector.process(background)
        detector.process(moving)
        mask, targets, alarm = detector.process(moving)

        self.assertEqual(int(mask.sum()), 0)
        self.assertEqual(targets, [])
        self.assertFalse(alarm)


if __name__ == "__main__":
    unittest.main()
