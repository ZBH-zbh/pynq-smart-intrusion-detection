"""Record a session from the real PL chain, losslessly enough to replay.

Why this exists
---------------
Every accuracy change so far has been unverifiable. Synthetic scenes are ones
the current code already scores perfectly, so tuning against them is fitting
the test rather than fixing the problem. Real evaluation needs a real recording
that can be replayed through a modified pipeline and scored the same way twice.

What is recorded, and why each part
-----------------------------------
The detection layer is frozen -- it is the teammate's bitstream and stays as it
is -- so what will change is the tracking layer above it: the tracker, the
trails, the object grouping and the crossing counter. All of those consume
(boxes, localisation centres), and both are derived from the processed mask.

So the mask is saved losslessly. That is the whole point: a session can then be
replayed with no camera and no PL, and a change is scored against a FIXED input
instead of against whatever happens to walk past the lens next time.

  masks.bin    the processed mask, zlib-compressed, length-prefixed per frame
  meta.jsonl   one line per frame: time, boxes, centres, alarm, detector state
  frames/      JPEG q90, for HUMAN REVIEW ONLY -- never the algorithm's input
  session.json what was asked of the operator, so the labels are not folklore

The frames are deliberately lossy and labelled as such: they are for looking at,
and a previous mistake in this project was reasoning about noise from JPEG data.

Masks are thresholded images, so zlib takes them down roughly tenfold. They are
streamed out compressed rather than accumulated, because the board has 493 MB of
RAM and a two-minute session is about 1700 frames.
"""
import json
import os
import sys
import time
import zlib

import cv2
import numpy as np

sys.path.insert(0, "/home/xilinx/intrusion_demo")

import tracker_server as ts  # noqa: E402

OUT = "/home/xilinx/intrusion_demo/session_data"


def main():
    duration = float(sys.argv[1]) if len(sys.argv) > 1 else 115.0
    label = sys.argv[2] if len(sys.argv) > 2 else "session"

    frame_dir = os.path.join(OUT, "frames")
    os.makedirs(frame_dir, exist_ok=True)

    for stale in os.listdir(frame_dir):
        os.remove(os.path.join(frame_dir, stale))

    source = ts.BoardSource(detector="pl", lock=True)
    source.open()
    print(f"[rec] source ready, recording {duration:.0f} s into {OUT}",
          flush=True)

    started = time.time()
    deadline = started + duration
    periods = []
    last_start = None
    count = 0
    dropped = 0

    with open(os.path.join(OUT, "meta.jsonl"), "w") as meta, \
            open(os.path.join(OUT, "masks.bin"), "wb") as masks:
        while time.time() < deadline:
            t_frame = time.monotonic()
            result = source.read()
            if result is None:
                dropped += 1
                time.sleep(0.02)
                continue
            frame, boxes = result

            if last_start is not None:
                periods.append(t_frame - last_start)
                if len(periods) > 30:
                    periods.pop(0)
            last_start = t_frame
            total = sum(periods)
            fps = len(periods) / total if total > 0 else 0.0

            mask = source._mask
            if mask is None:
                mask = np.zeros((ts.HEIGHT, ts.WIDTH), dtype=np.uint8)
            blob = zlib.compress(np.ascontiguousarray(mask).tobytes(), 6)
            masks.write(len(blob).to_bytes(4, "little"))
            masks.write(blob)

            centers = source.last_centers or []
            meta.write(json.dumps({
                "n": count,
                "t": round(time.time() - started, 4),
                "fps": round(fps, 3),
                "boxes": [list(map(float, b)) for b in boxes],
                "centers": [list(map(float, c)) for c in centers],
                "raw": len(boxes),
            }) + "\n")

            cv2.imwrite(os.path.join(frame_dir, f"{count:05d}.jpg"), frame,
                        [int(cv2.IMWRITE_JPEG_QUALITY), 90])

            count += 1
            if count % 50 == 0:
                elapsed = time.time() - started
                print(f"[rec] {elapsed:5.1f}/{duration:.0f} s  "
                      f"{count} frames  {fps:.2f} fps", flush=True)

    elapsed = time.time() - started
    header = {
        "label": label,
        "started": started,
        "duration_s": round(elapsed, 2),
        "frames": count,
        "dropped_reads": dropped,
        "mean_fps": round(count / elapsed, 3) if elapsed else 0.0,
        "width": ts.WIDTH,
        "height": ts.HEIGHT,
        "detector": "pl",
        "note": "masks.bin is lossless; frames/ is JPEG q90 for review only",
    }
    with open(os.path.join(OUT, "session.json"), "w") as handle:
        json.dump(header, handle, indent=2)

    print(f"[rec] done: {count} frames in {elapsed:.1f} s "
          f"({count / elapsed:.2f} fps), {dropped} dropped reads", flush=True)
    print(f"[rec] {header}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
