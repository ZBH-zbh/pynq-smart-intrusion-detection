"""What does the track actually DO around a crossing, real or spurious?

The empty-room control produced 3 crossings with nobody present, and neither a
size floor nor a speed floor removes them: the blobs are large (4000-16400 px2)
and fast (centres jumping ~50 px between frames). What is left is persistence,
so the question this answers is: how many frames does a spurious crossing spend
on the far side of the line, and how far does it get, compared with a real one?

Picking a confirmation threshold without that number would be guessing, which is
the thing this harness exists to stop.

The counter's own inputs are captured by wrapping update(), so the numbers come
from the real pipeline rather than a re-implementation of it.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "pynq_deploy")))

from replay_session import LIVE_DEFAULTS, replay  # noqa: E402
from session import Session  # noqa: E402


def capture(session, skip_startup=5):
    """Replay, recording every point the counter was fed, per frame."""
    from tracker_server import TrackerPipeline

    pipeline = TrackerPipeline(**LIVE_DEFAULTS)
    frames = []

    original = pipeline.counter.update

    def wrapped(points, timestamp=None):
        frames.append((timestamp, dict(points)))
        return original(points, timestamp=timestamp)

    pipeline.counter.update = wrapped

    import numpy as np
    from tracker_server import HEIGHT, WIDTH
    blank = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)

    for index, _mask, boxes, centers, timestamp, fps in session.frames():
        if index < skip_startup:
            continue
        pipeline.step(blank, boxes, timestamp=timestamp, fps=fps,
                      centers=centers)
    return pipeline, frames


def tracks_from(frames):
    """id -> [(frame_index, timestamp, point)]"""
    series = {}
    for index, (timestamp, points) in enumerate(frames):
        for track_id, point in points.items():
            series.setdefault(track_id, []).append((index, timestamp, point))
    return series


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("session")
    parser.add_argument("--window", type=int, default=8,
                        help="frames to show either side")
    args = parser.parse_args()

    session = Session(args.session)
    pipeline, frames = capture(session)
    counter = pipeline.counter
    line = counter.line
    print(f"line = {tuple(round(v, 1) for v in line)}   "
          f"deadband = {counter.deadband}")
    print(f"recorded {len(frames)} frames, "
          f"{len(tracks_from(frames))} tracks reached the counter")
    print()

    series = tracks_from(frames)

    # Where each track crossed, using the recorded points and the real
    # signed-distance function.
    for track_id, samples in sorted(series.items()):
        signs = [(index, timestamp, counter.signed_distance(point))
                 for index, timestamp, point in samples]
        crossings = []
        for (i0, t0, d0), (i1, t1, d1) in zip(signs, signs[1:]):
            if d0 * d1 < 0 and abs(d0) >= counter.deadband \
                    and abs(d1) >= counter.deadband:
                crossings.append((i0, i1, d1))
        if not crossings:
            continue

        total = sum(abs(signed) for _, _, signed in signs)
        print(f"--- track {track_id}: {len(samples)} frames at the counter, "
              f"path length {total:.0f} px ---")
        for i0, i1, d1 in crossings:
            direction = "L>R" if d1 > 0 else "R>L"
            print(f"    crossing at frame {i0}->{i1} ({direction})")
            print(f"      signed distance by frame (deadband={counter.deadband}):")
            for index, timestamp, signed in signs:
                if not (i0 - 2 <= index <= i0 + args.window):
                    continue
                mark = "  <== crossing" if index == i1 else ""
                bar = " " * 24
                if signed >= 0:
                    bar = " " * 24 + "#" * min(20, int(abs(signed) / 4))
                else:
                    bar = " " * max(0, 24 - min(20, int(abs(signed) / 4))) \
                        + "#" * min(20, int(abs(signed) / 4))
                print(f"        f{index:5d} t={timestamp:7.2f} "
                      f"d={signed:8.2f} |{bar}|{mark}")
        print()


if __name__ == "__main__":
    sys.exit(main())
