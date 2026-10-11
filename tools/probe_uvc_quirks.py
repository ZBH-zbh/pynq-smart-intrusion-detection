"""Can a uvcvideo quirk unlock the second 15 FPS?

The facts so far, all measured:
  * the C270 advertises 30 FPS at 320x240 and 640x480, YUYV and MJPG alike,
  * VIDIOC_S_PARM negotiates 30 and reads back 30,
  * the USB link is high speed (480 Mbit/s), so bandwidth is not scarce,
  * delivery is a very steady 66 ms with no short gaps -- genuinely 15 FPS.

Exactly half of the advertised rate, with everything agreeing it should be 30,
is the signature of a bandwidth negotiation the driver got wrong: it reserves
for a frame it never uses, so the device is polled at half rate. uvcvideo has
documented quirk flags for misbehaving devices, so this tries them and measures
the result rather than assuming.

Restores the module to its original state when done.
"""
import statistics
import subprocess
import sys
import time

CASES = [
    ("default (no quirks)", None),
    ("FIX_BANDWIDTH", 0x80),
    ("PROBE_DEF", 0x10),
    ("FORCE_ISOC", 0x08),
    ("FIX_BANDWIDTH|PROBE_DEF", 0x90),
    ("RESTRICT_FRAME_RATE", 0x200),
]


def shell(command):
    return subprocess.run(command, shell=True, capture_output=True, text=True)


def reload_module(quirks):
    shell("modprobe -r uvcvideo")
    time.sleep(1.0)
    if quirks is None:
        result = shell("modprobe uvcvideo")
    else:
        result = shell(f"modprobe uvcvideo quirks=0x{quirks:x}")
    time.sleep(1.5)
    return result.returncode == 0, (result.stderr or "").strip()


def measure(width=320, height=240, frames=20):
    import cv2

    cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
    if not cap.isOpened():
        return None
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, 30)
    for _ in range(6):
        cap.read()

    stamps = []
    for _ in range(frames):
        ok, _ = cap.read()
        if not ok:
            break
        stamps.append(time.perf_counter())
    cap.release()

    gaps = [(b - a) * 1000.0 for a, b in zip(stamps, stamps[1:])]
    if not gaps:
        return None
    return statistics.fmean(gaps), 1000.0 / statistics.fmean(gaps), len(gaps)


def main():
    print(f"{'quirk':<26} {'ms/frame':>9} {'fps':>7} {'frames':>7}")
    for label, quirks in CASES:
        ok, error = reload_module(quirks)
        if not ok:
            print(f"{label:<26} {'--':>9} {'--':>7} {'--':>7}  "
                  f"modprobe failed: {error}")
            continue
        result = measure()
        if result is None:
            print(f"{label:<26} {'--':>9} {'--':>7} {'--':>7}  no frames")
            continue
        ms, fps, count = result
        print(f"{label:<26} {ms:>9.2f} {fps:>7.2f} {count:>7}")

    # Leave the module exactly as it was found.
    reload_module(None)
    print("\nuvcvideo restored to default")
    return 0


if __name__ == "__main__":
    sys.exit(main())
