"""Where is the 15 FPS wall: the camera, the USB link, or the read path?

Established so far:
  * every format and resolution up to 640x480 delivers exactly 66.7 ms/frame,
  * 1280x720 delivers 79.4 ms, so the wall is not a simple resolution limit,
  * VIDIOC_S_PARM accepts 30 FPS and VIDIOC_G_PARM reads 30 FPS back.

A negotiated 30 FPS that delivers 15 means the loss is downstream of
negotiation. Three candidates, and this separates them:

  * the device really only produces 15 FPS,
  * the USB link is not running at high speed,
  * the device produces 30 but the read path returns each frame twice.

The third is worth ruling out explicitly: it is the one that would mean the
frames exist and we are throwing half of them away.
"""
import fcntl
import glob
import os
import statistics
import struct
import sys
import time

_IOWR = 3


def ioc(direction, letter, number, size):
    return ((direction << 30) | (size << 16) | (ord(letter) << 8) | number)


VIDIOC_ENUM_FMT = ioc(_IOWR, "V", 2, 64)
VIDIOC_ENUM_FRAMESIZES = ioc(_IOWR, "V", 74, 44)
VIDIOC_ENUM_FRAMEINTERVALS = ioc(_IOWR, "V", 75, 52)

VIDEO_CAPTURE = 1
# These are 1/2/3, not 0/1/2 -- reading them as zero-based silently sends every
# discrete entry down the stepwise branch and reports nonsense.
FRMSIZE_DISCRETE, FRMSIZE_CONTINUOUS, FRMSIZE_STEPWISE = 1, 2, 3
FRMIVAL_DISCRETE, FRMIVAL_CONTINUOUS, FRMIVAL_STEPWISE = 1, 2, 3


def fourcc(value):
    return "".join(chr((value >> (8 * i)) & 0xFF) for i in range(4))


def call(fd, request, buffer):
    try:
        fcntl.ioctl(fd, request, buffer)
    except OSError:
        return None
    return buffer


def usb_speed():
    for path in glob.glob("/sys/bus/usb/devices/*/idProduct"):
        try:
            with open(path) as handle:
                if handle.read().strip().lower() != "0825":
                    continue
            root = os.path.dirname(path)
            out = {}
            for field in ("speed", "version", "maxchild", "product"):
                try:
                    with open(os.path.join(root, field)) as handle:
                        out[field] = handle.read().strip()
                except OSError:
                    pass
            return os.path.basename(root), out
        except OSError:
            continue
    return None, {}


def advertised(fd):
    index = 0
    while True:
        buffer = bytearray(64)
        struct.pack_into("II", buffer, 0, index, VIDEO_CAPTURE)
        if call(fd, VIDIOC_ENUM_FMT, buffer) is None:
            return
        pixel_format = struct.unpack_from("I", buffer, 44)[0]
        print(f"\n{fourcc(pixel_format)}")
        size_index = 0
        while True:
            size_buffer = bytearray(44)
            struct.pack_into("III", size_buffer, 0, size_index, pixel_format, 0)
            if call(fd, VIDIOC_ENUM_FRAMESIZES, size_buffer) is None:
                break
            kind = struct.unpack_from("I", size_buffer, 8)[0]
            if kind != FRMSIZE_DISCRETE:
                print(f"    (frame-size type {kind})")
                break
            width, height = struct.unpack_from("II", size_buffer, 12)
            rates = []
            interval_index = 0
            while True:
                interval_buffer = bytearray(52)
                struct.pack_into("IIIII", interval_buffer, 0, interval_index,
                                 pixel_format, width, height, 0)
                if call(fd, VIDIOC_ENUM_FRAMEINTERVALS,
                        interval_buffer) is None:
                    break
                if struct.unpack_from("I", interval_buffer, 16)[0] \
                        != FRMIVAL_DISCRETE:
                    break
                num, den = struct.unpack_from("II", interval_buffer, 20)
                if num:
                    rates.append(den / float(num))
                interval_index += 1
            if (width, height) in ((320, 240), (640, 480), (1280, 720)):
                print(f"    {width:>5}x{height:<5} "
                      f"{', '.join(f'{r:.0f}' for r in rates)} fps")
            size_index += 1
        index += 1


def frame_intervals(width=320, height=240, wanted_fps=30, frames=40):
    """Inter-read gaps: a 30 FPS stream that reads as 15 shows up here."""
    import cv2

    cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, wanted_fps)
    for _ in range(8):
        cap.read()

    stamps = []
    for _ in range(frames):
        ok, _ = cap.read()
        stamps.append(time.perf_counter())
        if not ok:
            break
    reported = cap.get(cv2.CAP_PROP_FPS)
    cap.release()

    gaps = [(b - a) * 1000.0 for a, b in zip(stamps, stamps[1:])]
    if not gaps:
        print("  no frames")
        return
    fast = sum(1 for g in gaps if g < 20.0)
    print(f"  reported {reported:.1f} fps -> delivered "
          f"{1000.0 / statistics.fmean(gaps):.2f} fps")
    print(f"  gap ms: min {min(gaps):.2f}  median "
          f"{statistics.median(gaps):.2f}  mean "
          f"{statistics.fmean(gaps):.2f}  max {max(gaps):.2f}")
    print(f"  gaps under 20 ms: {fast}/{len(gaps)} "
          f"({'pairs -- frames are being delivered twice' if fast else 'none -- genuinely 15 fps'})")


def main():
    name, info = usb_speed()
    print(f"=== USB link ===\n  device {name}: {info}")
    print("  (speed 480 = high speed; 12 = full speed, ~1.2 MB/s)")

    fd = os.open("/dev/video0", os.O_RDWR)
    try:
        print("\n=== modes (the ones this project cares about) ===")
        advertised(fd)
    finally:
        os.close(fd)

    print("\n=== read pacing at 320x240 MJPG, 30 fps requested ===")
    frame_intervals(320, 240, 30)
    print("\n=== read pacing at 640x480 MJPG, 30 fps requested ===")
    frame_intervals(640, 480, 30)
    return 0


if __name__ == "__main__":
    sys.exit(main())
