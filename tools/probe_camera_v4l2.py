"""What frame rates does the C270 actually advertise to this host?

The empirical result is strange: the driver reports 30 FPS for every mode, but
every capture lands on 66.7 ms -- 15.0 FPS -- and it does not move when the
resolution or the pixel format changes. 320x240 MJPG is a few KB per frame, so
that is not a bandwidth wall.

Two very different worlds produce that:
  * the device only offers 1/15 for these modes, and 30 was never real, or
  * the device offers 1/30 but the host cannot sustain it.

This asks the device directly, with VIDIOC_ENUM_FRAMEINTERVALS, and also reads
back the negotiated interval with VIDIOC_G_PARM. v4l2-ctl is not installed on
PynqLinux, so the ioctls are done by hand.
"""
import fcntl
import os
import struct
import sys

_IOWR = 3


def ioc(direction, letter, number, size):
    return ((direction << 30) | (size << 16) | (ord(letter) << 8) | number)


VIDIOC_ENUM_FMT = ioc(_IOWR, "V", 2, 64)
# sizeof(struct v4l2_streamparm) is 204, not 44: the union carries a
# raw_data[200] member, and the ioctl number encodes the whole struct. Asking
# with the wrong size makes the kernel reject the call outright.
VIDIOC_G_PARM = ioc(_IOWR, "V", 21, 204)
VIDIOC_S_PARM = ioc(_IOWR, "V", 22, 204)
VIDIOC_ENUM_FRAMESIZES = ioc(_IOWR, "V", 74, 44)
VIDIOC_ENUM_FRAMEINTERVALS = ioc(_IOWR, "V", 75, 52)

VIDEO_CAPTURE = 1
FRMSIZE_DISCRETE = 0
FRMSIZE_STEPWISE = 1
FRMIVAL_DISCRETE = 0
FRMIVAL_STEPWISE = 1


def fourcc(value):
    return "".join(chr((value >> (8 * i)) & 0xFF) for i in range(4))


def call(fd, request, buffer):
    """Return None instead of raising when the ioctl runs out of entries."""
    try:
        fcntl.ioctl(fd, request, buffer)
    except OSError:
        return None
    return buffer


def enum_formats(fd):
    index = 0
    while True:
        buffer = bytearray(64)
        struct.pack_into("II", buffer, 0, index, VIDEO_CAPTURE)
        if call(fd, VIDIOC_ENUM_FMT, buffer) is None:
            return
        description = bytes(buffer[12:44]).split(b"\0")[0].decode(
            "utf-8", "replace")
        pixel_format = struct.unpack_from("I", buffer, 44)[0]
        yield pixel_format, description
        index += 1


def enum_sizes(fd, pixel_format):
    """Yield (width, height) for each size the device offers.

    Discrete and stepwise describe the same thing two ways; this camera uses
    one of them and it is not worth guessing which, so both are accepted.
    """
    index = 0
    while True:
        buffer = bytearray(44)
        struct.pack_into("III", buffer, 0, index, pixel_format, 0)
        if call(fd, VIDIOC_ENUM_FRAMESIZES, buffer) is None:
            return
        kind = struct.unpack_from("I", buffer, 8)[0]
        if kind == FRMSIZE_DISCRETE:
            yield struct.unpack_from("II", buffer, 12)
        elif kind == FRMSIZE_STEPWISE:
            min_w, max_w, step_w, min_h, max_h, step_h = struct.unpack_from(
                "IIIIII", buffer, 12)
            print(f"      stepwise: w {min_w}..{max_w} step {step_w}, "
                  f"h {min_h}..{max_h} step {step_h}")
            # Probe the two sizes that matter for this project.
            for width, height in ((320, 240), (640, 480)):
                if min_w <= width <= max_w and min_h <= height <= max_h:
                    yield width, height
        else:
            print(f"      unknown frame-size type {kind}")
            return
        index += 1


def enum_intervals(fd, pixel_format, width, height):
    index = 0
    while True:
        buffer = bytearray(52)
        struct.pack_into("IIIII", buffer, 0, index, pixel_format, width,
                         height, 0)
        if call(fd, VIDIOC_ENUM_FRAMEINTERVALS, buffer) is None:
            return
        kind = struct.unpack_from("I", buffer, 16)[0]
        if kind == FRMIVAL_DISCRETE:
            numerator, denominator = struct.unpack_from("II", buffer, 20)
            if denominator:
                yield numerator, denominator, denominator / float(numerator)
        elif kind == FRMIVAL_STEPWISE:
            values = struct.unpack_from("IIIIII", buffer, 20)
            # min, max and step, each a time-per-frame fraction.
            lo = values[1] / float(values[0]) if values[0] else 0.0
            hi = values[3] / float(values[2]) if values[2] else 0.0
            step = values[5] / float(values[4]) if values[4] else 0.0
            print(f"      stepwise intervals: {lo:.2f}..{hi:.2f} fps "
                  f"step {step:.2f}")
            yield 1, 1, hi
        else:
            print(f"      unknown interval type {kind}")
            return
        index += 1


def current_interval(fd):
    buffer = bytearray(204)
    struct.pack_into("I", buffer, 0, VIDEO_CAPTURE)
    if call(fd, VIDIOC_G_PARM, buffer) is None:
        return None
    numerator, denominator = struct.unpack_from("II", buffer, 12)
    if not denominator or not numerator:
        return None
    return numerator, denominator, denominator / float(numerator)


def try_set_interval(fd, numerator, denominator):
    buffer = bytearray(204)
    struct.pack_into("I", buffer, 0, VIDEO_CAPTURE)
    current = bytearray(204)
    struct.pack_into("I", current, 0, VIDEO_CAPTURE)
    if call(fd, VIDIOC_G_PARM, current) is not None:
        # Keep the capability/capturemode fields the getter filled in.
        buffer[4:12] = current[4:12]
    struct.pack_into("II", buffer, 12, numerator, denominator)
    if call(fd, VIDIOC_S_PARM, buffer) is None:
        return None
    got = struct.unpack_from("II", buffer, 12)
    return got[1] / float(got[0]) if got[0] else None


def main():
    fd = os.open("/dev/video0", os.O_RDWR)
    try:
        print("=== modes the camera advertises ===")
        for pixel_format, description in enum_formats(fd):
            print(f"\n{pixel_format}  ({fourcc(pixel_format)})  {description}")
            for width, height in enum_sizes(fd, pixel_format):
                rates = [f"{fps:.1f}" for _, _, fps in
                         enum_intervals(fd, pixel_format, width, height)]
                shown = ", ".join(rates) if rates else "none reported"
                print(f"    {width:>5}x{height:<5} fps: {shown}")

        current = current_interval(fd)
        print(f"\n=== negotiated right now ===\n  {current}")

        print("\n=== asking for 1/30 explicitly via VIDIOC_S_PARM ===")
        for numerator, denominator in ((1, 30), (1, 15), (1, 10)):
            got = try_set_interval(fd, numerator, denominator)
            after = current_interval(fd)
            print(f"  asked {denominator}/{numerator} fps "
                  f"-> driver reports {got}, G_PARM now {after}")
    finally:
        os.close(fd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
