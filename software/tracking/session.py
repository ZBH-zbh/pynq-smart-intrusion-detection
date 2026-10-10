"""Read a recorded session back.

A session is the camera's frames plus the detector's own output, captured
together so the tracking layer can be re-run against a fixed input.

The pieces, and why each is stored the way it is:

``masks.bin``
    The processed mask, zlib-compressed, each frame prefixed with its length.
    This is the lossless record: every box and every localisation centre is
    derived from it, so it is what makes a replay faithful. Compression matters
    because a thresholded mask is mostly zeros -- a real session came out at
    about 110 bytes per frame against 76,800 raw bytes.

``meta.jsonl``
    One JSON object per frame: timestamp, fps, the raw boxes and the
    localisation centres. The boxes here are BEFORE the pipeline's area filter,
    deliberately, so the replay exercises the same filtering the live run does
    instead of inheriting a filter that has already been applied.

``frames/``
    JPEG q90, for a human to look at. Not an algorithm input, and never to be
    treated as one: an earlier mistake in this project was drawing conclusions
    about pixel noise from JPEG data.
"""

import json
import os
import zlib

import numpy as np


class SessionError(Exception):
    """The recording is missing or incomplete."""


class Session:
    """A recorded session on disk."""

    def __init__(self, path):
        self.path = path
        header_path = os.path.join(path, "session.json")
        meta_path = os.path.join(path, "meta.jsonl")
        if not os.path.exists(header_path) or not os.path.exists(meta_path):
            raise SessionError(f"{path} does not look like a session "
                               f"(no session.json / meta.jsonl)")

        with open(header_path) as handle:
            self.header = json.load(handle)

        self.records = []
        with open(meta_path) as handle:
            for number, line in enumerate(handle):
                line = line.strip()
                if not line:
                    continue
                try:
                    self.records.append(json.loads(line))
                except ValueError as exc:
                    raise SessionError(
                        f"{meta_path} line {number + 1} is not JSON: {exc}")

        self.width = int(self.header.get("width", 320))
        self.height = int(self.header.get("height", 240))

    def __len__(self):
        return len(self.records)

    def __repr__(self):
        return (f"Session({len(self)} frames, {self.header.get('mean_fps')} "
                f"fps, {self.header.get('label')})")

    def boxes(self, index):
        return [tuple(box) for box in self.records[index].get("boxes", [])]

    def centers(self, index):
        """Localisation points, falling back to box centres if not recorded."""
        centers = self.records[index].get("centers")
        if centers:
            return [tuple(point) for point in centers]
        return [((box[0] + box[2] / 2.0), (box[1] + box[3] / 2.0))
                for box in self.boxes(index)]

    def timestamp(self, index):
        return self.records[index].get("t", float(index))

    def fps(self, index):
        return self.records[index].get("fps", 0.0)

    def masks(self):
        """Yield one mask per frame, in order, from the compressed stream.

        Streamed rather than loaded: a two-minute session is nearly 2000 frames
        and the board that writes it has 493 MB of RAM, so the reader should not
        assume more.
        """
        mask_path = os.path.join(self.path, "masks.bin")
        if not os.path.exists(mask_path):
            raise SessionError(f"{mask_path} is missing")
        expected = self.width * self.height
        with open(mask_path, "rb") as handle:
            for index in range(len(self.records)):
                header = handle.read(4)
                if len(header) < 4:
                    raise SessionError(
                        f"masks.bin ended after {index} frames but meta.jsonl "
                        f"lists {len(self.records)}")
                length = int.from_bytes(header, "little")
                blob = handle.read(length)
                if len(blob) < length:
                    raise SessionError(f"masks.bin frame {index} is truncated")
                raw = zlib.decompress(blob)
                if len(raw) != expected:
                    raise SessionError(
                        f"frame {index} decompressed to {len(raw)} bytes, "
                        f"expected {expected}")
                yield np.frombuffer(raw, dtype=np.uint8).reshape(
                    self.height, self.width)

    def frames(self):
        """Yield ``(index, mask, boxes, centers, timestamp, fps)`` per frame."""
        for index, mask in enumerate(self.masks()):
            yield (index, mask, self.boxes(index), self.centers(index),
                   self.timestamp(index), self.fps(index))

    def summary(self):
        boxes_per_frame = [len(r.get("boxes", [])) for r in self.records]
        return {
            "label": self.header.get("label"),
            "frames": len(self.records),
            "duration_s": self.header.get("duration_s"),
            "mean_fps": self.header.get("mean_fps"),
            "frames_with_boxes": sum(1 for n in boxes_per_frame if n),
            "max_boxes_in_a_frame": max(boxes_per_frame) if boxes_per_frame
            else 0,
        }
