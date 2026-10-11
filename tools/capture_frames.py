#!/usr/bin/env python3
"""Record real camera frames on the board for offline detector tuning.

Saves every frame as JPEG plus the board's own PL mask and boxes, so that after
downloading I can:

  * reproduce the shipped frame-difference chain offline and confirm it matches
    what the board actually computed
  * sweep background-model parameters over the user's real scene instead of
    synthetic data
  * see exactly what the camera saw when detection failed

Usage on the board:
    XILINX_XRT=/usr python3 capture_frames.py --seconds 60 --out /tmp/capture
"""
import argparse
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, "/home/xilinx/intrusion_demo")

import cv2
import numpy as np

WIDTH, HEIGHT = 320, 240


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--out", default="/tmp/capture")
    parser.add_argument("--quality", type=int, default=92)
    args = parser.parse_args()

    asyncio.set_event_loop(asyncio.new_event_loop())

    from pynq import MMIO, Overlay, allocate

    import pl_motion_detection_optimized as demo
    from motion_common import detect_targets, process_mask

    os.makedirs(args.out, exist_ok=True)
    for name in os.listdir(args.out):
        os.remove(os.path.join(args.out, name))

    print("[capture] loading overlay ...", flush=True)
    overlay = Overlay(demo.BITSTREAM, download=False)
    overlay.download()

    dma = demo.SimpleAxiDMA(
        demo.DMA_BASE_ADDR, poll_sleep_seconds=demo.DMA_POLL_SLEEP_SECONDS
    )
    ips = [
        MMIO(demo.RGB2GRAY_BASE, 0x10000),
        MMIO(demo.FRAME_DIFF_BASE, 0x10000),
        MMIO(demo.THRESHOLD_BASE, 0x10000),
        MMIO(demo.MORPHOLOGY_BASE, 0x10000),
    ]
    demo.configure_ip(ips, ips[2])

    pixel_count = WIDTH * HEIGHT
    nbytes = pixel_count * np.dtype(np.uint32).itemsize
    inbuf = allocate(shape=(pixel_count,), dtype=np.uint32)
    outbuf = allocate(shape=(pixel_count,), dtype=np.uint32, cacheable=True)
    in_bytes = inbuf.view(np.uint8).reshape(HEIGHT, WIDTH, 4)
    in_bytes[:, :, 3] = 0
    out_bytes = outbuf.view(np.uint8).reshape(HEIGHT, WIDTH, 4)
    raw_mask = np.empty((HEIGHT, WIDTH), dtype=np.uint8)

    # Buzzer stays off for the whole recording.
    buzzer = demo.BuzzerGPIO(demo.BUZZER_BASE, enabled=True)
    buzzer.off()

    cam = cv2.VideoCapture(0)
    cam.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
    cam.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
    cam.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    if not cam.isOpened():
        raise RuntimeError("cannot open camera 0")
    for _ in range(3):
        cam.read()

    print(f"[capture] recording {args.seconds:.0f}s into {args.out}", flush=True)
    params = [int(cv2.IMWRITE_JPEG_QUALITY), args.quality]
    manifest = []
    t0 = time.monotonic()
    n = 0

    try:
        while time.monotonic() - t0 < args.seconds:
            t_frame = time.monotonic()
            ok, frame = cam.read()
            if not ok:
                time.sleep(0.01)
                continue
            if frame.shape[:2] != (HEIGHT, WIDTH):
                frame = cv2.resize(frame, (WIDTH, HEIGHT))

            cv2.mixChannels([frame], [in_bytes], [0, 0, 1, 1, 2, 2])
            for ip in ips:
                ip.write(demo.AP_CTRL, 0x01)
            dma.transfer(inbuf, outbuf, nbytes)
            cv2.mixChannels([out_bytes], [raw_mask], [0, 0])

            pl_mask = process_mask(raw_mask)
            pl_boxes = detect_targets(pl_mask)

            cv2.imwrite(
                os.path.join(args.out, f"f{n:04d}.jpg"), frame, params
            )
            cv2.imwrite(
                os.path.join(args.out, f"m{n:04d}.png"), raw_mask
            )
            manifest.append({
                "n": n,
                "t": round(time.monotonic() - t0, 4),
                "read_ms": round((time.monotonic() - t_frame) * 1000, 2),
                "pl_mask_pixels": int(np.count_nonzero(pl_mask)),
                "pl_boxes": [list(map(int, b)) for b in pl_boxes],
            })
            n += 1
            if n % 100 == 0:
                print(f"[capture] {n} frames, t={time.monotonic() - t0:.1f}s",
                      flush=True)
    finally:
        buzzer.off()
        cam.release()
        inbuf.freebuffer()
        outbuf.freebuffer()

    with open(os.path.join(args.out, "manifest.json"), "w") as handle:
        json.dump({
            "frames": n,
            "duration": round(time.monotonic() - t0, 3),
            "fps": round(n / (time.monotonic() - t0), 2),
            "size": [WIDTH, HEIGHT],
            "per_frame": manifest,
        }, handle)

    with open(os.path.join(args.out, "DONE"), "w") as handle:
        handle.write(f"{n}\n")

    print(f"[capture] done: {n} frames in "
          f"{time.monotonic() - t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
