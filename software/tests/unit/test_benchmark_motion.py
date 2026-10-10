import sys
from pathlib import Path
import unittest

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmark_motion import (  # noqa: E402
    _box_iou,
    _mask_iou,
    compare_outputs,
    generate_synthetic_frames,
    run_backend,
)


class BenchmarkMetricTests(unittest.TestCase):
    def test_synthetic_frames_are_deterministic(self):
        first = generate_synthetic_frames(3, seed=7)
        second = generate_synthetic_frames(3, seed=7)
        self.assertEqual(len(first), 3)
        self.assertTrue(all(np.array_equal(a, b) for a, b in zip(first, second)))

    def test_mask_and_box_iou(self):
        empty = np.zeros((4, 4), dtype=np.uint8)
        full = np.full((4, 4), 255, dtype=np.uint8)
        self.assertEqual(_mask_iou(empty, empty), 1.0)
        self.assertEqual(_mask_iou(empty, full), 0.0)
        self.assertEqual(_box_iou((0, 0, 10, 10), (0, 0, 10, 10)), 1.0)
        self.assertEqual(_box_iou((0, 0, 5, 5), (10, 10, 5, 5)), 0.0)

    def test_identical_outputs_have_perfect_agreement(self):
        mask = np.zeros((4, 4), dtype=np.uint8)
        outputs = [(mask, [(0, 0, 2, 2)], True)]
        metrics = compare_outputs(outputs, outputs)
        self.assertTrue(all(value == 1.0 for key, value in metrics.items() if key != "frames"))

    def test_backend_resources_are_closed(self):
        class DummyDetector:
            def __init__(self):
                self.closed = False

            def process(self, frame):
                return np.zeros(frame.shape[:2], dtype=np.uint8), [], False

            def close(self):
                self.closed = True

        detector = DummyDetector()
        frame = np.zeros((4, 4, 3), dtype=np.uint8)
        record, outputs = run_backend(
            "dummy", lambda: detector, [frame, frame], 1, 0.01, True
        )
        self.assertTrue(detector.closed)
        self.assertGreater(record["performance_frames"], 0)
        self.assertEqual(len(outputs), 1)


if __name__ == "__main__":
    unittest.main()
