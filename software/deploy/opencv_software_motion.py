"""PYNQ-Z2 智能入侵检测系统的 OpenCV 纯软件基准版。"""

import argparse
from collections import deque
import time

import cv2
import numpy as np

from motion_common import (
    HEIGHT,
    THRESH,
    WIDTH,
    check_alarm,
    detect_targets,
    draw_overlay,
    emulate_current_pl_erosion,
    process_mask,
)


def opencv_bgr2gray(frame_bgr):
    return cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)


class SoftwareMotionDetector:
    """在 CPU 上复现当前 PL 预处理和 PS 后处理。"""

    def __init__(self, threshold=THRESH, gray_converter=opencv_bgr2gray):
        self.threshold = threshold
        self.gray_converter = gray_converter
        self.previous_gray = None

    def reset(self):
        self.previous_gray = None

    def process(self, frame_bgr):
        if frame_bgr.shape[:2] != (HEIGHT, WIDTH):
            frame_bgr = cv2.resize(frame_bgr, (WIDTH, HEIGHT))

        gray = self.gray_converter(frame_bgr)
        if self.previous_gray is None:
            self.previous_gray = gray.copy()
            empty_mask = np.zeros((HEIGHT, WIDTH), dtype=np.uint8)
            return empty_mask, [], False

        difference = cv2.absdiff(gray, self.previous_gray)
        _, binary = cv2.threshold(
            difference, self.threshold, 255, cv2.THRESH_BINARY
        )

        current_pl_mask = emulate_current_pl_erosion(binary)
        final_mask = process_mask(current_pl_mask)
        targets = detect_targets(final_mask)
        alarm_status = check_alarm(targets)

        self.previous_gray = gray.copy()
        return final_mask, targets, alarm_status


class FpsMeter:
    def __init__(self, window_size=30):
        self.samples = deque(maxlen=window_size)

    def update(self, elapsed_seconds):
        if elapsed_seconds > 0:
            self.samples.append(elapsed_seconds)
        if not self.samples:
            return 0.0
        return len(self.samples) / sum(self.samples)


def _running_in_jupyter():
    try:
        shell = get_ipython()  # noqa: F821 - Jupyter 运行时提供
    except NameError:
        return False
    return shell is not None and shell.__class__.__name__ == "ZMQInteractiveShell"


def _parse_source(source):
    return int(source) if source.isdecimal() else source


def run_self_test():
    detector = SoftwareMotionDetector()
    background = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    moving = background.copy()
    cv2.rectangle(moving, (100, 80), (150, 140), (255, 255, 255), -1)

    first_mask, first_targets, first_alarm = detector.process(background)
    mask, targets, alarm = detector.process(moving)

    if np.any(first_mask) or first_targets or first_alarm:
        raise AssertionError("首帧背景初始化失败")
    if not np.any(mask) or len(targets) != 1 or not alarm:
        raise AssertionError(
            f"运动目标自测失败: targets={targets}, alarm={alarm}"
        )

    print("Self-test passed")
    print(f"mask_pixels={np.count_nonzero(mask)}")
    print(f"targets={targets}")
    print(f"alarm={alarm}")


def run_live(source, display_mode, max_frames):
    detector = SoftwareMotionDetector()
    capture = cv2.VideoCapture(_parse_source(source))
    capture.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
    if not capture.isOpened():
        raise RuntimeError(f"Cannot open video source: {source}")

    if display_mode == "auto":
        display_mode = "jupyter" if _running_in_jupyter() else "window"

    image_widget = None
    if display_mode == "jupyter":
        import ipywidgets as widgets
        from IPython.display import display

        image_widget = widgets.Image(format="jpeg", width=640, height=480)
        display(image_widget)

    fps_meter = FpsMeter()
    fps = 0.0
    frame_count = 0

    try:
        while max_frames <= 0 or frame_count < max_frames:
            loop_start = time.perf_counter()
            ok, frame = capture.read()
            if not ok:
                break

            if frame.shape[:2] != (HEIGHT, WIDTH):
                frame = cv2.resize(frame, (WIDTH, HEIGHT))

            _, targets, alarm_status = detector.process(frame)
            visualization = draw_overlay(frame.copy(), targets, fps, alarm_status)

            if display_mode == "jupyter":
                encoded_ok, encoded = cv2.imencode(".jpg", visualization)
                if not encoded_ok:
                    raise RuntimeError("JPEG encoding failed")
                image_widget.value = encoded.tobytes()
            elif display_mode == "window":
                cv2.imshow("OpenCV Software Motion Detection", visualization)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

            frame_count += 1
            fps = fps_meter.update(time.perf_counter() - loop_start)
    except KeyboardInterrupt:
        print("Stopped by user")
    finally:
        capture.release()
        if display_mode == "window":
            cv2.destroyAllWindows()

    print(f"frames={frame_count}")
    print(f"fps={fps:.2f}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="OpenCV pure-software motion detection baseline"
    )
    parser.add_argument(
        "--source",
        default="0",
        help="Camera index such as 0, or a video file path",
    )
    parser.add_argument(
        "--display",
        choices=("auto", "jupyter", "window", "none"),
        default="auto",
        help="Display backend; use none for non-interactive runs",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=0,
        help="Stop after N frames; 0 means run until input ends or user stops",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run a synthetic no-camera test and exit",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.self_test:
        run_self_test()
        return
    run_live(args.source, args.display, args.max_frames)


if __name__ == "__main__":
    main()
