"""Analyse the real static capture: how noisy is this camera, really?

The synthetic sequences used to pick the background-model defaults had a noise
amplitude of +-1 grey level. A real webcam in a dim room is far noisier, and the
3x3 erosion plus the 7x7 close that were dropped as "unnecessary" were doing
real noise suppression. This measures the actual noise and counts the false
positives each configuration produces on a scene where nothing is moving.
"""
import json
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DEPLOY = os.path.abspath(os.path.join(HERE, "..", "pynq_deploy"))
CAPTURE = os.path.join(HERE, "real_capture", "capture")
sys.path.insert(0, DEPLOY)
sys.path.insert(0, HERE)

from bg_detector import BackgroundDetector  # noqa: E402


def load_frames():
    with open(os.path.join(CAPTURE, "manifest.json")) as handle:
        manifest = json.load(handle)
    count = manifest["frames"]
    frames = []
    for i in range(count):
        path = os.path.join(CAPTURE, f"f{i:04d}.jpg")
        image = cv2.imread(path)
        if image is None:
            break
        frames.append(image)
    grays = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames]
    return frames, grays, manifest


def noise_report(grays, start=40):
    """Frame-to-frame difference statistics once the scene is settled."""
    diffs = []
    for i in range(start, len(grays)):
        diffs.append(cv2.absdiff(grays[i], grays[i - 1]).ravel())
    allpix = np.concatenate(diffs)
    print(f"frame-to-frame noise over {len(diffs)} frame pairs "
          f"(after frame {start}):")
    print(f"  mean {allpix.mean():.2f}  p50 {np.percentile(allpix, 50):.0f}  "
          f"p95 {np.percentile(allpix, 95):.0f}  "
          f"p99 {np.percentile(allpix, 99):.0f}  max {allpix.max()}")
    for t in (10, 20, 30, 40, 50, 60):
        frac = 100.0 * (allpix > t).mean()
        print(f"  pixels differing by more than {t:3d}: {frac:6.3f}%  "
              f"({int((allpix > t).sum() / len(diffs))} per frame)")
    return allpix


def config_report(frames, label, warmup=40, days=160, **options):
    detector = BackgroundDetector(**options)
    boxes_per_frame = []
    mask_pixels = []
    for i, frame in enumerate(frames):
        mask, boxes = detector.process(frame)
        if i >= warmup:
            boxes_per_frame.append(len(boxes))
            mask_pixels.append(int(np.count_nonzero(mask)))
        if i >= warmup + days:
            break

    frames_scored = len(boxes_per_frame)
    clean = sum(1 for b in boxes_per_frame if b == 0)
    print(f"{label:<44} boxes/frame mean {np.mean(boxes_per_frame):6.2f}  "
          f"max {max(boxes_per_frame):3d}  "
          f"clean frames {100.0 * clean / frames_scored:5.1f}%  "
          f"mask px {np.mean(mask_pixels):7.0f}")
    return np.mean(boxes_per_frame)


def main():
    frames, grays, manifest = load_frames()
    print(f"loaded {len(frames)} frames, recorded {manifest['duration']}s "
          f"({manifest['fps']} FPS)")
    print(f"scene brightness: mean {np.mean([g.mean() for g in grays]):.1f}\n")

    noise_report(grays)
    print("\nfalse positives on this STATIC scene "
          "(lower is better, 0 is correct):")

    config_report(frames, "deployed: no erosion, close=1, thresh=30, a300")
    config_report(frames, "  + erosion", erosion=True)
    config_report(frames, "  + close7", close_ksize=7)
    config_report(frames, "  + erosion + close7", erosion=True, close_ksize=7)
    print()
    config_report(frames, "  + erosion + close7, thresh=40",
                  erosion=True, close_ksize=7, threshold=40)
    config_report(frames, "  + erosion + close7, thresh=50",
                  erosion=True, close_ksize=7, threshold=50)
    config_report(frames, "  + erosion + close7, min_area=800",
                  erosion=True, close_ksize=7, min_area=800)
    config_report(frames, "  + erosion + close7, thresh=40, a800",
                  erosion=True, close_ksize=7, threshold=40, min_area=800)
    config_report(frames, "  + erosion + close7, thresh=50, a1500",
                  erosion=True, close_ksize=7, threshold=50, min_area=1500)
    print()
    config_report(frames, "  + open5 + close7 + erosion, thresh=40",
                  erosion=True, close_ksize=7, open_ksize=5, threshold=40)


if __name__ == "__main__":
    main()
