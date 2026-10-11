"""Tests for the session reader.

The reader is the foundation of every accuracy measurement from here on, so the
interesting cases are the ways a recording can be subtly broken: a stream that
stops early, a frame that decompresses to the wrong size, a header that
disagrees with the data. Silently returning fewer frames would turn a truncated
recording into a confident wrong answer.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
import zlib

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "pynq_deploy")))

from session import Session, SessionError  # noqa: E402

WIDTH, HEIGHT = 8, 6


def write_session(path, frames, width=WIDTH, height=HEIGHT, boxes=None):
    """Write a synthetic session; `frames` is a list of masks or None."""
    os.makedirs(os.path.join(path, "frames"), exist_ok=True)
    with open(os.path.join(path, "session.json"), "w") as handle:
        json.dump({"label": "test", "width": width, "height": height,
                   "frames": len(frames), "mean_fps": 15.0}, handle)
    with open(os.path.join(path, "meta.jsonl"), "w") as meta, \
            open(os.path.join(path, "masks.bin"), "wb") as masks:
        for index, mask in enumerate(frames):
            if mask is None:                       # write nothing at all
                continue
            blob = zlib.compress(np.ascontiguousarray(mask).tobytes())
            masks.write(len(blob).to_bytes(4, "little"))
            masks.write(blob)
            meta.write(json.dumps({
                "n": index, "t": index / 15.0, "fps": 15.0,
                "boxes": (boxes or {}).get(index, []),
                "centers": [],
            }) + "\n")
    return path


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="session_test_")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self, frames, **kwargs):
        return write_session(self.dir, frames, **kwargs)

    def test_round_trip_preserves_the_masks_exactly(self):
        frames = [np.full((HEIGHT, WIDTH), value, dtype=np.uint8)
                  for value in (0, 7, 128, 255)]
        self._write(frames)
        session = Session(self.dir)
        self.assertEqual(len(session), 4)
        for expected, got in zip(frames, session.masks()):
            self.assertTrue(np.array_equal(expected, got),
                            "mask did not survive the round trip")

    def test_frames_yields_everything_in_order(self):
        self._write([np.zeros((HEIGHT, WIDTH), np.uint8)] * 3,
                    boxes={1: [[1.0, 2.0, 3.0, 4.0]]})
        session = Session(self.dir)
        rows = list(session.frames())
        self.assertEqual([r[0] for r in rows], [0, 1, 2])
        self.assertEqual(rows[1][2], [(1.0, 2.0, 3.0, 4.0)])
        self.assertAlmostEqual(rows[2][4], 2 / 15.0)

    def test_centres_fall_back_to_the_box_centre(self):
        """Older recordings may predate the centres field."""
        self._write([np.zeros((HEIGHT, WIDTH), np.uint8)],
                    boxes={0: [[10.0, 20.0, 30.0, 60.0]]})
        session = Session(self.dir)
        # Boxes are (x, y, width, height), so the centre is x + w/2, y + h/2.
        self.assertEqual(session.centers(0), [(25.0, 50.0)])

    def test_recorded_centres_win_over_the_fallback(self):
        self._write([np.zeros((HEIGHT, WIDTH), np.uint8)],
                    boxes={0: [[10.0, 20.0, 30.0, 60.0]]})
        with open(os.path.join(self.dir, "meta.jsonl"), "w") as handle:
            handle.write(json.dumps({"t": 0.0, "boxes": [[10, 20, 30, 60]],
                                     "centers": [[99.0, 88.0]]}) + "\n")
        self.assertEqual(Session(self.dir).centers(0), [(99.0, 88.0)])

    def test_a_truncated_mask_stream_is_an_error_not_a_short_read(self):
        """The dangerous failure: fewer frames returned than meta claims."""
        self._write([np.zeros((HEIGHT, WIDTH), np.uint8)] * 3)
        with open(os.path.join(self.dir, "masks.bin"), "r+b") as handle:
            handle.truncate(6)                     # cut into the first frame
        with self.assertRaises(SessionError):
            list(Session(self.dir).masks())

    def test_a_stream_that_ends_early_is_an_error(self):
        self._write([np.zeros((HEIGHT, WIDTH), np.uint8)] * 3)
        with open(os.path.join(self.dir, "masks.bin"), "rb") as handle:
            blob = handle.read()
        first = int.from_bytes(blob[:4], "little") + 4
        with open(os.path.join(self.dir, "masks.bin"), "wb") as handle:
            handle.write(blob[:first])             # exactly one frame
        with self.assertRaises(SessionError) as caught:
            list(Session(self.dir).masks())
        self.assertIn("ended after 1", str(caught.exception))

    def test_a_wrong_sized_frame_is_an_error(self):
        """Guards against a header and a stream that disagree."""
        self._write([np.zeros((HEIGHT, WIDTH), np.uint8)])
        with open(os.path.join(self.dir, "session.json"), "w") as handle:
            json.dump({"width": 16, "height": 16}, handle)
        with self.assertRaises(SessionError) as caught:
            list(Session(self.dir).masks())
        self.assertIn("decompressed to", str(caught.exception))

    def test_missing_files_are_reported_clearly(self):
        empty = os.path.join(self.dir, "nope")
        os.makedirs(empty)
        with self.assertRaises(SessionError):
            Session(empty)

    def test_a_corrupt_meta_line_is_an_error(self):
        self._write([np.zeros((HEIGHT, WIDTH), np.uint8)])
        with open(os.path.join(self.dir, "meta.jsonl"), "a") as handle:
            handle.write("{not json}\n")
        with self.assertRaises(SessionError):
            Session(self.dir)

    def test_summary_counts_frames_with_boxes(self):
        self._write([np.zeros((HEIGHT, WIDTH), np.uint8)] * 3,
                    boxes={0: [[1, 2, 3, 4]], 2: [[1, 2, 3, 4], [5, 6, 7, 8]]})
        summary = Session(self.dir).summary()
        self.assertEqual(summary["frames"], 3)
        self.assertEqual(summary["frames_with_boxes"], 2)
        self.assertEqual(summary["max_boxes_in_a_frame"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
