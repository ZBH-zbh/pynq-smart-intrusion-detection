"""统一运动检测基准；本地阶段不代表 PYNQ/PL 正式性能。"""

import argparse
import csv
from datetime import datetime
import json
import platform
from pathlib import Path
import statistics
import sys
import time

import cv2
import numpy as np
import psutil

from motion_common import HEIGHT, WIDTH, hls_bgr2gray_reference
from opencv_software_motion import SoftwareMotionDetector, opencv_bgr2gray


def generate_synthetic_frames(count, seed=20260913):
    """生成确定性的 320×240 运动序列，不把生成时间计入基准。"""
    rng = np.random.default_rng(seed)
    x_gradient = np.linspace(12, 48, WIDTH, dtype=np.uint8)
    background = np.repeat(x_gradient[np.newaxis, :], HEIGHT, axis=0)
    base = np.dstack((background, background, background))

    frames = []
    for index in range(count):
        frame = base.copy()
        x = 15 + (index * 8) % 230
        y = 65 + int(12 * np.sin(index / 13.0))
        cv2.rectangle(frame, (x, y), (x + 55, y + 70), (40, 180, 240), -1)

        if 55 <= index % 120 < 100:
            x2 = 250 - ((index - 55) * 5) % 190
            cv2.rectangle(frame, (x2, 150), (x2 + 35, 205), (220, 80, 45), -1)

        # 这组颜色在 OpenCV 中灰度差为 31，在当前 HLS 整数公式中为 30。
        # 固定闪烁块用于验证阈值边界差异确实能被一致性指标发现。
        edge_color = (81, 131, 38) if index % 2 == 0 else (88, 172, 58)
        cv2.rectangle(frame, (270, 10), (305, 45), edge_color, -1)

        noise = rng.integers(-2, 3, size=frame.shape, dtype=np.int16)
        frame = np.clip(frame.astype(np.int16) + noise, 0, 255).astype(np.uint8)
        frames.append(frame)
    return frames


def load_frames(source, count):
    if source == "synthetic":
        return generate_synthetic_frames(count)

    capture_source = int(source) if source.isdecimal() else source
    capture = cv2.VideoCapture(capture_source)
    capture.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
    if not capture.isOpened():
        raise RuntimeError(f"Cannot open input source: {source}")

    frames = []
    try:
        while len(frames) < count:
            ok, frame = capture.read()
            if not ok:
                break
            if frame.shape[:2] != (HEIGHT, WIDTH):
                frame = cv2.resize(frame, (WIDTH, HEIGHT))
            frames.append(frame)
    finally:
        capture.release()

    if len(frames) < count:
        raise RuntimeError(f"Input has only {len(frames)} frames; {count} required")
    return frames


def _system_cpu_percent(before, after):
    deltas = [new - old for old, new in zip(before, after)]
    total = sum(deltas)
    if total <= 0:
        return 0.0
    idle_index = before._fields.index("idle")
    idle = deltas[idle_index]
    if "iowait" in before._fields:
        idle += deltas[before._fields.index("iowait")]
    return 100.0 * (total - idle) / total


def run_backend(
    name, detector_factory, frames, warmup_frames, min_seconds, keep_outputs
):
    detector = detector_factory()
    try:
        for frame in frames[:warmup_frames]:
            detector.process(frame)

        measured_frames = frames[warmup_frames:]
        process = psutil.Process()
        process_before = process.cpu_times()
        system_before = psutil.cpu_times()
        wall_start = time.perf_counter()

        latencies_ms = []
        outputs = []
        processed_frames = 0
        while True:
            for frame in measured_frames:
                frame_start = time.perf_counter()
                mask, targets, alarm = detector.process(frame)
                latencies_ms.append((time.perf_counter() - frame_start) * 1000.0)
                if keep_outputs and len(outputs) < len(measured_frames):
                    outputs.append((mask.copy(), list(targets), bool(alarm)))
                processed_frames += 1
            if time.perf_counter() - wall_start >= min_seconds:
                break

        wall_seconds = time.perf_counter() - wall_start
        process_after = process.cpu_times()
        system_after = psutil.cpu_times()
        process_seconds = (
            process_after.user
            + process_after.system
            - process_before.user
            - process_before.system
        )
        logical_cpus = psutil.cpu_count(logical=True) or 1

        record = {
            "backend": name,
            "input_sequence_frames": len(measured_frames),
            "performance_frames": processed_frames,
            "sequence_passes": processed_frames // len(measured_frames),
            "wall_seconds": wall_seconds,
            "fps": processed_frames / wall_seconds,
            "latency_mean_ms": statistics.fmean(latencies_ms),
            "latency_p50_ms": float(np.percentile(latencies_ms, 50)),
            "latency_p95_ms": float(np.percentile(latencies_ms, 95)),
            "process_cpu_one_core_percent": 100.0 * process_seconds / wall_seconds,
            "process_cpu_total_capacity_percent": (
                100.0 * process_seconds / wall_seconds / logical_cpus
            ),
            "system_cpu_percent": _system_cpu_percent(system_before, system_after),
        }
        return record, outputs
    finally:
        close = getattr(detector, "close", None)
        if close is not None:
            close()


def _mask_iou(left, right):
    left_foreground = left != 0
    right_foreground = right != 0
    union = np.count_nonzero(left_foreground | right_foreground)
    if union == 0:
        return 1.0
    intersection = np.count_nonzero(left_foreground & right_foreground)
    return intersection / union


def _box_iou(left, right):
    left_x, left_y, left_w, left_h = left
    right_x, right_y, right_w, right_h = right
    intersection_w = max(
        0, min(left_x + left_w, right_x + right_w) - max(left_x, right_x)
    )
    intersection_h = max(
        0, min(left_y + left_h, right_y + right_h) - max(left_y, right_y)
    )
    intersection = intersection_w * intersection_h
    union = left_w * left_h + right_w * right_h - intersection
    return intersection / union if union else 1.0


def _matched_box_iou(left_boxes, right_boxes):
    if not left_boxes and not right_boxes:
        return 1.0
    if not left_boxes or not right_boxes:
        return 0.0

    candidates = []
    for left_index, left in enumerate(left_boxes):
        for right_index, right in enumerate(right_boxes):
            candidates.append((_box_iou(left, right), left_index, right_index))
    candidates.sort(reverse=True)

    used_left = set()
    used_right = set()
    matches = []
    for iou, left_index, right_index in candidates:
        if left_index in used_left or right_index in used_right:
            continue
        used_left.add(left_index)
        used_right.add(right_index)
        matches.append(iou)

    denominator = max(len(left_boxes), len(right_boxes))
    return sum(matches) / denominator


def compare_outputs(opencv_outputs, reference_outputs):
    if len(opencv_outputs) != len(reference_outputs):
        raise ValueError("Output frame counts do not match")

    pixel_agreements = []
    mask_ious = []
    count_agreements = []
    box_ious = []
    alarm_agreements = []

    for opencv_result, reference_result in zip(opencv_outputs, reference_outputs):
        opencv_mask, opencv_boxes, opencv_alarm = opencv_result
        reference_mask, reference_boxes, reference_alarm = reference_result
        pixel_agreements.append(float(np.mean(opencv_mask == reference_mask)))
        mask_ious.append(_mask_iou(opencv_mask, reference_mask))
        count_agreements.append(len(opencv_boxes) == len(reference_boxes))
        box_ious.append(_matched_box_iou(opencv_boxes, reference_boxes))
        alarm_agreements.append(opencv_alarm == reference_alarm)

    return {
        "frames": len(opencv_outputs),
        "mask_pixel_agreement": statistics.fmean(pixel_agreements),
        "mask_mean_iou": statistics.fmean(mask_ious),
        "target_count_agreement": statistics.fmean(count_agreements),
        "bbox_mean_matched_iou": statistics.fmean(box_ious),
        "alarm_agreement": statistics.fmean(alarm_agreements),
    }


def summarize_runs(records, backend):
    backend_records = [record for record in records if record["backend"] == backend]
    return {
        "runs": len(backend_records),
        "fps_mean": statistics.fmean(record["fps"] for record in backend_records),
        "fps_stdev": (
            statistics.stdev(record["fps"] for record in backend_records)
            if len(backend_records) > 1
            else 0.0
        ),
        "latency_p50_ms_mean": statistics.fmean(
            record["latency_p50_ms"] for record in backend_records
        ),
        "latency_p95_ms_mean": statistics.fmean(
            record["latency_p95_ms"] for record in backend_records
        ),
        "process_cpu_one_core_percent_mean": statistics.fmean(
            record["process_cpu_one_core_percent"] for record in backend_records
        ),
        "process_cpu_total_capacity_percent_mean": statistics.fmean(
            record["process_cpu_total_capacity_percent"]
            for record in backend_records
        ),
        "system_cpu_percent_mean": statistics.fmean(
            record["system_cpu_percent"] for record in backend_records
        ),
    }


def write_results(output_root, config, records, summaries, agreement):
    run_dir = output_root / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=False)

    payload = {
        "notice": "Windows local validation only; not a PYNQ PL performance result.",
        "environment": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "opencv": cv2.__version__,
            "numpy": np.__version__,
            "psutil": psutil.__version__,
            "logical_cpus": psutil.cpu_count(logical=True),
            "opencv_threads": cv2.getNumThreads(),
        },
        "config": config,
        "runs": records,
        "summaries": summaries,
        "agreement": agreement,
    }

    json_path = run_dir / "benchmark_results.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    csv_path = run_dir / "benchmark_runs.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0].keys()))
        writer.writeheader()
        writer.writerows(records)

    report_path = run_dir / "benchmark_report.md"
    opencv_summary = summaries["opencv"]
    reference_summary = summaries["hls_gray_reference"]
    report = f"""# 本地运动检测基准报告

> 本报告只验证基准流程和指标计算，不代表 PYNQ PL 性能结果。

## 配置

- 输入：{config['source']}
- 分辨率：{WIDTH}×{HEIGHT}
- 预热帧：{config['warmup_frames']}
- 测量帧：{config['measure_frames']}
- 重复轮次：{config['repeats']}
- 每轮最短性能计时：{config['min_seconds']:.1f} 秒（完整循环 200 帧输入，效果指标只取首轮序列）
- OpenCV 线程数：{payload['environment']['opencv_threads']}

## 性能

| 后端 | 平均 FPS | FPS 标准差 | P50 延迟/ms | P95 延迟/ms | 进程 CPU/单核口径 | 进程 CPU/总容量口径 | 系统 CPU |
|---|---:|---:|---:|---:|---:|---:|---:|
| OpenCV 标准灰度 | {opencv_summary['fps_mean']:.2f} | {opencv_summary['fps_stdev']:.2f} | {opencv_summary['latency_p50_ms_mean']:.3f} | {opencv_summary['latency_p95_ms_mean']:.3f} | {opencv_summary['process_cpu_one_core_percent_mean']:.2f}% | {opencv_summary['process_cpu_total_capacity_percent_mean']:.2f}% | {opencv_summary['system_cpu_percent_mean']:.2f}% |
| HLS 整数灰度参考 | {reference_summary['fps_mean']:.2f} | {reference_summary['fps_stdev']:.2f} | {reference_summary['latency_p50_ms_mean']:.3f} | {reference_summary['latency_p95_ms_mean']:.3f} | {reference_summary['process_cpu_one_core_percent_mean']:.2f}% | {reference_summary['process_cpu_total_capacity_percent_mean']:.2f}% | {reference_summary['system_cpu_percent_mean']:.2f}% |

## 一致性

| 指标 | 结果 |
|---|---:|
| 掩码像素一致率 | {agreement['mask_pixel_agreement']:.6f} |
| 掩码平均 IoU | {agreement['mask_mean_iou']:.6f} |
| 目标数量一致率 | {agreement['target_count_agreement']:.6f} |
| bbox 平均匹配 IoU | {agreement['bbox_mean_matched_iou']:.6f} |
| 报警一致率 | {agreement['alarm_agreement']:.6f} |

## 解释

两个后端都在 Windows CPU 上运行。它们只在灰度转换方式上不同：一个使用 OpenCV `cvtColor`，另一个使用当前 HLS 的整数权重公式。正式 PL vs OpenCV 结果必须在 PYNQ-Z2 上、使用同一输入重新采集。
"""
    report_path.write_text(report, encoding="utf-8")
    return run_dir


def parse_args():
    parser = argparse.ArgumentParser(description="Motion detection benchmark harness")
    parser.add_argument(
        "--source", default="synthetic", help="synthetic, camera index, or video path"
    )
    parser.add_argument("--warmup", type=int, default=20, help="Warm-up frame count")
    parser.add_argument("--frames", type=int, default=200, help="Measured frame count")
    parser.add_argument("--repeats", type=int, default=3, help="Repeat count")
    parser.add_argument(
        "--min-seconds",
        type=float,
        default=1.0,
        help="Minimum timing duration per backend and repeat",
    )
    parser.add_argument(
        "--output-root", default="results", help="Directory for timestamped results"
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if (
        args.warmup < 1
        or args.frames < 1
        or args.repeats < 1
        or args.min_seconds <= 0
    ):
        raise ValueError("warmup, frames, repeats and min-seconds must be positive")

    total_frames = args.warmup + args.frames
    frames = load_frames(args.source, total_frames)
    factories = {
        "opencv": lambda: SoftwareMotionDetector(gray_converter=opencv_bgr2gray),
        "hls_gray_reference": lambda: SoftwareMotionDetector(
            gray_converter=hls_bgr2gray_reference
        ),
    }

    records = []
    retained_outputs = {}
    for repeat_index in range(args.repeats):
        order = list(factories)
        if repeat_index % 2:
            order.reverse()
        for backend in order:
            record, outputs = run_backend(
                backend,
                factories[backend],
                frames,
                args.warmup,
                args.min_seconds,
                keep_outputs=backend not in retained_outputs,
            )
            record["repeat"] = repeat_index + 1
            records.append(record)
            if outputs:
                retained_outputs[backend] = outputs
            print(
                f"repeat={repeat_index + 1} backend={backend} "
                f"fps={record['fps']:.2f} cpu={record['process_cpu_one_core_percent']:.2f}%"
            )

    summaries = {
        backend: summarize_runs(records, backend) for backend in factories
    }
    agreement = compare_outputs(
        retained_outputs["opencv"], retained_outputs["hls_gray_reference"]
    )
    config = {
        "source": args.source,
        "width": WIDTH,
        "height": HEIGHT,
        "warmup_frames": args.warmup,
        "measure_frames": args.frames,
        "repeats": args.repeats,
        "min_seconds": args.min_seconds,
    }
    run_dir = write_results(
        Path(args.output_root), config, records, summaries, agreement
    )
    print(f"results={run_dir.resolve()}")
    print(json.dumps(agreement, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
