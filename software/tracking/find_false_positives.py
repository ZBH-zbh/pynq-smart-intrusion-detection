"""Find out exactly what the false positives are on the current real scene.

Prints where the boxes appear, how big they are, how often, and saves an
annotated frame plus the raw mask so the cause is visible instead of guessed.
"""
import json
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DEPLOY = os.path.abspath(os.path.join(HERE, "..", "pynq_deploy"))
CAPTURE = os.path.join(HERE, "real_capture", "capture")
OUT = os.path.join(HERE, "real_capture")
sys.path.insert(0, DEPLOY)
sys.path.insert(0, HERE)

from bg_detector import BackgroundDetector  # noqa: E402


def load():
    with open(os.path.join(CAPTURE, "manifest.json")) as handle:
        manifest = json.load(handle)
    frames = []
    for i in range(manifest["frames"]):
        image = cv2.imread(os.path.join(CAPTURE, f"f{i:04d}.jpg"))
        if image is None:
            break
        frames.append(image)
    return frames, manifest


def run_config(frames, label, warmup=40, **options):
    detector = BackgroundDetector(**options)
    events = []
    boxes_per_frame = []
    for i, frame in enumerate(frames):
        mask, boxes = detector.process(frame)
        if i < warmup:
            continue
        boxes_per_frame.append(len(boxes))
        for b in boxes:
            events.append((i, b, int(cv2.contourArea(
                np.array([[[b[0], b[1]], [b[0] + b[2], b[1]],
                           [b[0] + b[2], b[1] + b[3]],
                           [b[0], b[1] + b[3]]]], np.int32)))))
    n = len(boxes_per_frame)
    dirty = sum(1 for c in boxes_per_frame if c)
    print(f"\n=== {label} ===")
    print(f"  frames scored {n}, frames with detections {dirty} "
          f"({100.0 * dirty / n:.1f}%), boxes/frame mean "
          f"{np.mean(boxes_per_frame):.2f}")
    if events:
        # Where do they cluster?
        xs = [b[0] + b[2] / 2 for _, b, _ in events]
        ys = [b[1] + b[3] / 2 for _, b, _ in events]
        ws = [b[2] for _, b, _ in events]
        hs = [b[3] for _, b, _ in events]
        print(f"  box centre x: min {min(xs):.0f} max {max(xs):.0f} "
              f"mean {np.mean(xs):.0f}")
        print(f"  box centre y: min {min(ys):.0f} max {max(ys):.0f} "
              f"mean {np.mean(ys):.0f}")
        print(f"  box size w: min {min(ws)} max {max(ws)} mean {np.mean(ws):.1f}")
        print(f"  box size h: min {min(hs)} max {max(hs)} mean {np.mean(hs):.1f}")
        print("  first 12 detections (frame, x, y, w, h):")
        for i, b, _ in events[:12]:
            print(f"    f{i:4d}  {b}")
    return boxes_per_frame, events


def main():
    frames, manifest = load()
    print(f"loaded {len(frames)} frames of the current scene "
          f"({manifest['duration']}s at {manifest['fps']} FPS)")

    grays = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames]
    print(f"brightness mean {np.mean([g.mean() for g in grays]):.1f}")

    diffs = np.concatenate([
        cv2.absdiff(grays[i], grays[i - 1]).ravel()
        for i in range(40, len(grays))
    ])
    print(f"frame-to-frame noise: mean {diffs.mean():.2f} p95 "
          f"{np.percentile(diffs, 95):.0f} p99 {np.percentile(diffs, 99):.0f} "
          f"max {diffs.max()}")
    for t in (20, 30, 40, 50):
        print(f"  >{t}: {100.0 * (diffs > t).mean():.4f}%  "
              f"({(diffs > t).sum() / (len(grays) - 40):.1f} per frame)")

    _, events = run_config(frames, "deployed: no erosion, close=1, a300, t30")
    run_config(frames, "+ erosion", erosion=True)
    run_config(frames, "+ close7", close_ksize=7)
    run_config(frames, "+ erosion + close7", erosion=True, close_ksize=7)
    run_config(frames, "t40", threshold=40)
    run_config(frames, "+erosion+close7 t40", erosion=True, close_ksize=7,
               threshold=40)
    run_config(frames, "+erosion+close7 t40 a900", erosion=True, close_ksize=7,
               threshold=40, min_area=900)

    # Visualise the worst frame under the deployed config.
    detector = BackgroundDetector()
    worst = None
    for i, frame in enumerate(frames):
        mask, boxes = detector.process(frame)
        if i >= 40 and len(boxes) >= 2:
            worst = (i, frame.copy(), mask.copy(), boxes)
    if worst is not None:
        i, frame, mask, boxes = worst
        annotated = frame.copy()
        for x, y, w, h in boxes:
            cv2.rectangle(annotated, (x, y), (x + w, y + h), (0, 255, 0), 1)
        cv2.imwrite(os.path.join(OUT, "worst_frame.jpg"), annotated)
        cv2.imwrite(os.path.join(OUT, "worst_mask.png"), mask)
        cv2.imwrite(os.path.join(OUT, "worst_diff.png"),
                    cv2.applyColorMap(cv2.convertScaleAbs(
                        cv2.absdiff(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY),
                                    detector.background.astype(np.uint8)),
                        alpha=2), cv2.COLORMAP_JET))
        print(f"\nsaved worst frame f{i} with {len(boxes)} boxes "
              f"-> real_capture/worst_frame.jpg / worst_mask.png / worst_diff.png")


if __name__ == "__main__":
    main()
