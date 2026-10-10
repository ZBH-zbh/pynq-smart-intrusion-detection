"""PL 与纯软件运动检测共用的后处理逻辑。"""

import cv2
import numpy as np


WIDTH = 320
HEIGHT = 240
THRESH = 30
MIN_CONTOUR_AREA = 300
MAX_CONTOUR_AREA = 50000
ALARM_ZONE = (85, 45, 150, 150)

_ERODE_KERNEL = np.ones((3, 3), np.uint8)
_CLOSE_KERNEL = np.ones((7, 7), np.uint8)
_OPEN_KERNEL = np.ones((3, 3), np.uint8)


def pack_bgr_to_rgb32(frame_bgr, output=None, scratch=None):
    """Pack a BGR uint8 image as 0x00RRGGBB uint32 words for the PL DMA input."""
    if (
        frame_bgr.dtype != np.uint8
        or frame_bgr.ndim != 3
        or frame_bgr.shape[2] != 3
    ):
        raise ValueError("frame_bgr must be an HxWx3 uint8 array")

    pixels = frame_bgr.reshape(-1, 3)
    pixel_count = pixels.shape[0]

    if output is None:
        output = np.empty(pixel_count, dtype=np.uint32)
    elif output.dtype != np.uint32 or output.size != pixel_count:
        raise ValueError("output must be a uint32 array with one word per pixel")
    else:
        output = output.reshape(-1)

    if scratch is None:
        scratch = np.empty(pixel_count, dtype=np.uint32)
    elif scratch.dtype != np.uint32 or scratch.size != pixel_count:
        raise ValueError("scratch must be a uint32 array with one word per pixel")
    else:
        scratch = scratch.reshape(-1)

    if np.shares_memory(output, scratch):
        raise ValueError("output and scratch must not share memory")

    np.copyto(output, pixels[:, 2], casting="unsafe")
    np.left_shift(output, 16, out=output)
    np.copyto(scratch, pixels[:, 1], casting="unsafe")
    np.left_shift(scratch, 8, out=scratch)
    np.bitwise_or(output, scratch, out=output)
    np.bitwise_or(output, pixels[:, 0], out=output, casting="unsafe")
    return output


def extract_low_byte_mask(words, width=WIDTH, height=HEIGHT):
    """Extract the valid low byte from each little-endian PL output word."""
    if words.dtype != np.uint32 or words.size != width * height:
        raise ValueError("words must be a uint32 array matching width * height")
    return (
        words.reshape(-1)
        .view(np.uint8)
        .reshape(-1, 4)[:, 0]
        .reshape(height, width)
        .copy()
    )


def hls_bgr2gray_reference(frame_bgr):
    """按当前 rgb2gray HLS 的整数公式计算灰度，用于等价性测试。"""
    pixels = frame_bgr.astype(np.uint16)
    blue = pixels[:, :, 0]
    green = pixels[:, :, 1]
    red = pixels[:, :, 2]
    return ((red * 76 + green * 150 + blue * 29) >> 8).astype(np.uint8)


def emulate_current_pl_erosion(binary_mask):
    """模拟当前 morphology HLS：3×3 腐蚀、右下锚点、顶部/左侧两行列清零。"""
    eroded = cv2.erode(
        binary_mask,
        _ERODE_KERNEL,
        anchor=(2, 2),
        borderType=cv2.BORDER_CONSTANT,
        borderValue=255,
    )
    eroded[:2, :] = 0
    eroded[:, :2] = 0
    return eroded


def process_mask(raw_mask):
    """复现队长 PL 脚本中的 PS 后处理：7×7 闭运算后接 3×3 开运算。"""
    mask = cv2.morphologyEx(raw_mask, cv2.MORPH_CLOSE, _CLOSE_KERNEL)
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, _OPEN_KERNEL)


def merge_nearby_boxes(boxes, distance_thresh=30):
    """保持队长现有规则，合并距离较近或重叠的边界框。"""
    if not boxes:
        return []

    boxes = sorted(boxes, key=lambda box: box[0])
    merged = [list(boxes[0])]

    for x, y, width, height in boxes[1:]:
        prev_x, prev_y, prev_width, prev_height = merged[-1]
        prev_x2 = prev_x + prev_width
        prev_y2 = prev_y + prev_height
        x2 = x + width
        y2 = y + height

        overlap_x = not (
            x2 + distance_thresh < prev_x
            or x - distance_thresh > prev_x2
        )
        overlap_y = not (
            y2 + distance_thresh < prev_y
            or y - distance_thresh > prev_y2
        )

        if overlap_x and overlap_y:
            new_x = min(prev_x, x)
            new_y = min(prev_y, y)
            new_x2 = max(prev_x2, x2)
            new_y2 = max(prev_y2, y2)
            merged[-1] = [new_x, new_y, new_x2 - new_x, new_y2 - new_y]
        else:
            merged.append([x, y, width, height])

    return [tuple(box) for box in merged]


def detect_targets(mask):
    """从二值掩码提取并合并目标框。"""
    contours, _ = cv2.findContours(
        mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    boxes = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if MIN_CONTOUR_AREA <= area <= MAX_CONTOUR_AREA:
            boxes.append(cv2.boundingRect(contour))
    return merge_nearby_boxes(boxes)


def check_alarm(targets, zone=ALARM_ZONE):
    """只要任一目标框与警戒区重叠就返回 True。"""
    zone_x, zone_y, zone_width, zone_height = zone
    zone_x2 = zone_x + zone_width
    zone_y2 = zone_y + zone_height

    for x, y, width, height in targets:
        if (
            x < zone_x2
            and x + width > zone_x
            and y < zone_y2
            and y + height > zone_y
        ):
            return True
    return False


def draw_overlay(frame, targets, fps, alarm_status):
    """绘制与队长 PL 脚本一致的警戒区、目标框和状态文字。"""
    zone_x, zone_y, zone_width, zone_height = ALARM_ZONE
    zone_color = (0, 0, 255) if alarm_status else (255, 0, 0)
    cv2.rectangle(
        frame,
        (zone_x, zone_y),
        (zone_x + zone_width, zone_y + zone_height),
        zone_color,
        2,
    )

    for x, y, width, height in targets:
        cv2.rectangle(frame, (x, y), (x + width, y + height), (0, 255, 0), 2)

    cv2.putText(
        frame,
        f"Targets: {len(targets)}",
        (10, 25),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (0, 255, 0),
        2,
    )
    cv2.putText(
        frame,
        f"FPS: {fps:.1f}",
        (10, 55),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (0, 255, 0),
        2,
    )

    status_text = "ALARM!" if alarm_status else "Normal"
    status_color = (0, 0, 255) if alarm_status else (0, 255, 0)
    cv2.putText(
        frame,
        status_text,
        (10, 85),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        status_color,
        2,
    )
    return frame
