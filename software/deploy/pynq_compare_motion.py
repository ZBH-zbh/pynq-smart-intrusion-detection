"""在 PYNQ-Z2 上公平比较 OpenCV 纯软件版与真实 PL 版。"""

import argparse
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
import platform
import sys

import cv2
import numpy as np
import psutil

from benchmark_motion import compare_outputs, load_frames, run_backend, summarize_runs
from motion_common import (
    HEIGHT,
    WIDTH,
    check_alarm,
    detect_targets,
    process_mask,
)
from opencv_software_motion import SoftwareMotionDetector, opencv_bgr2gray


DEFAULT_BITSTREAM = "/home/xilinx/intrusion_detection_optimized.bit"
DMA_BASE_ADDR = 0x41E00000
FRAME_DIFF_BASE = 0x40000000
MORPHOLOGY_BASE = 0x40010000
RGB2GRAY_BASE = 0x40020000
THRESHOLD_BASE = 0x40030000

AP_CTRL = 0x00


class PlMotionDetector:
    def __init__(self, poll_sleep_seconds):
        try:
            from pynq import MMIO, allocate
            from pl_motion_detection_optimized import SimpleAxiDMA, configure_ip
        except ImportError as exc:
            raise RuntimeError("This benchmark must run on a PYNQ image") from exc

        self.input_buffer = None
        self.output_buffer = None
        self.input_bytes = None
        self.output_bytes = None
        self.raw_mask = None

        self.dma = SimpleAxiDMA(
            DMA_BASE_ADDR, poll_sleep_seconds=poll_sleep_seconds
        )
        self.ip_blocks = [
            MMIO(RGB2GRAY_BASE, 0x10000),
            MMIO(FRAME_DIFF_BASE, 0x10000),
            MMIO(THRESHOLD_BASE, 0x10000),
            MMIO(MORPHOLOGY_BASE, 0x10000),
        ]
        configure_ip(self.ip_blocks, self.ip_blocks[2])

        pixel_count = WIDTH * HEIGHT
        self.transfer_bytes = pixel_count * np.dtype(np.uint32).itemsize
        self.input_buffer = allocate(shape=(pixel_count,), dtype=np.uint32)
        self.output_buffer = allocate(
            shape=(pixel_count,), dtype=np.uint32, cacheable=True
        )
        self.input_bytes = self.input_buffer.view(np.uint8).reshape(
            HEIGHT, WIDTH, 4
        )
        self.input_bytes[:, :, 3] = 0
        self.output_bytes = self.output_buffer.view(np.uint8).reshape(
            HEIGHT, WIDTH, 4
        )
        self.raw_mask = np.empty((HEIGHT, WIDTH), dtype=np.uint8)

    def process(self, frame_bgr):
        if frame_bgr.shape[:2] != (HEIGHT, WIDTH):
            frame_bgr = cv2.resize(frame_bgr, (WIDTH, HEIGHT))
        cv2.mixChannels(
            [frame_bgr], [self.input_bytes], [0, 0, 1, 1, 2, 2]
        )
        for ip in self.ip_blocks:
            ip.write(AP_CTRL, 0x01)
        self.dma.transfer(
            self.input_buffer, self.output_buffer, self.transfer_bytes
        )
        cv2.mixChannels([self.output_bytes], [self.raw_mask], [0, 0])
        mask = process_mask(self.raw_mask)
        targets = detect_targets(mask)
        return mask, targets, check_alarm(targets)

    def close(self):
        self.input_bytes = None
        self.output_bytes = None
        self.raw_mask = None
        if self.input_buffer is not None:
            self.input_buffer.freebuffer()
            self.input_buffer = None
        if self.output_buffer is not None:
            self.output_buffer.freebuffer()
            self.output_buffer = None


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def write_results(output_root, bitstream, config, records, summaries, agreement):
    run_dir = output_root / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=False)

    try:
        import pynq

        pynq_version = getattr(pynq, "__version__", "unknown")
    except ImportError:
        pynq_version = "unavailable"

    payload = {
        "notice": "PYNQ-Z2 same-frame-set OpenCV vs real PL benchmark.",
        "environment": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "pynq": pynq_version,
            "opencv": cv2.__version__,
            "numpy": np.__version__,
            "psutil": psutil.__version__,
            "logical_cpus": psutil.cpu_count(logical=True),
            "opencv_threads": cv2.getNumThreads(),
        },
        "bitstream": {"path": str(bitstream), "sha256": _sha256(bitstream)},
        "config": config,
        "runs": records,
        "summaries": summaries,
        "agreement": agreement,
    }
    (run_dir / "benchmark_results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    with (run_dir / "benchmark_runs.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0].keys()))
        writer.writeheader()
        writer.writerows(records)

    sw = summaries["opencv_software"]
    pl = summaries["optimized_pl"]
    speedup = pl["fps_mean"] / sw["fps_mean"]
    report = """# PYNQ-Z2 OpenCV vs PL 正式对比报告

## 配置

- 输入：{source}（先完整载入内存，采集和显示不计时）
- 分辨率：{width}×{height}
- 预热：{warmup} 帧
- 测量序列：{frames} 帧
- 重复：{repeats} 轮
- 每后端每轮最短计时：{seconds:.1f} 秒
- OpenCV 线程数：{threads}
- DMA 轮询休眠：{poll_sleep_us:.1f} us
- bitstream SHA-256：`{bit_hash}`

## 性能

| 后端 | 平均 FPS | FPS 标准差 | P50/ms | P95/ms | 进程 CPU/单核口径 | 进程 CPU/总容量 | 系统 CPU |
|---|---:|---:|---:|---:|---:|---:|---:|
| OpenCV 纯软件 | {sw_fps:.2f} | {sw_std:.2f} | {sw_p50:.3f} | {sw_p95:.3f} | {sw_cpu:.2f}% | {sw_total:.2f}% | {sw_system:.2f}% |
| 优化 PL + 相同 PS 后处理 | {pl_fps:.2f} | {pl_std:.2f} | {pl_p50:.3f} | {pl_p95:.3f} | {pl_cpu:.2f}% | {pl_total:.2f}% | {pl_system:.2f}% |

PL/OpenCV FPS 倍率：**{speedup:.2f}×**。

## 效果一致性

| 指标 | 结果 |
|---|---:|
| 掩码像素一致率 | {pixel:.6f} |
| 掩码平均 IoU | {mask_iou:.6f} |
| 目标数量一致率 | {count:.6f} |
| bbox 平均匹配 IoU | {box_iou:.6f} |
| 报警一致率 | {alarm:.6f} |

说明：两端使用完全相同的预载帧数组、预热帧数和 PS 后处理。OpenCV 使用标准 `cvtColor` 灰度，PL 使用 HLS 整数灰度，因此阈值边缘允许极少量像素差异。
""".format(
        source=config["source"],
        width=WIDTH,
        height=HEIGHT,
        warmup=config["warmup_frames"],
        frames=config["measure_frames"],
        repeats=config["repeats"],
        seconds=config["min_seconds"],
        threads=cv2.getNumThreads(),
        poll_sleep_us=config["dma_poll_sleep_us"],
        bit_hash=payload["bitstream"]["sha256"],
        sw_fps=sw["fps_mean"], sw_std=sw["fps_stdev"],
        sw_p50=sw["latency_p50_ms_mean"], sw_p95=sw["latency_p95_ms_mean"],
        sw_cpu=sw["process_cpu_one_core_percent_mean"],
        sw_total=sw["process_cpu_total_capacity_percent_mean"],
        sw_system=sw["system_cpu_percent_mean"],
        pl_fps=pl["fps_mean"], pl_std=pl["fps_stdev"],
        pl_p50=pl["latency_p50_ms_mean"], pl_p95=pl["latency_p95_ms_mean"],
        pl_cpu=pl["process_cpu_one_core_percent_mean"],
        pl_total=pl["process_cpu_total_capacity_percent_mean"],
        pl_system=pl["system_cpu_percent_mean"], speedup=speedup,
        pixel=agreement["mask_pixel_agreement"],
        mask_iou=agreement["mask_mean_iou"],
        count=agreement["target_count_agreement"],
        box_iou=agreement["bbox_mean_matched_iou"],
        alarm=agreement["alarm_agreement"],
    )
    (run_dir / "benchmark_report.md").write_text(report, encoding="utf-8")
    return run_dir


def parse_args():
    parser = argparse.ArgumentParser(
        description="PYNQ-Z2 OpenCV software versus optimized PL benchmark"
    )
    parser.add_argument("--bitstream", default=DEFAULT_BITSTREAM)
    parser.add_argument("--source", default="synthetic")
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--frames", type=int, default=200)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--min-seconds", type=float, default=5.0)
    parser.add_argument("--opencv-threads", type=int, default=1)
    parser.add_argument("--dma-poll-sleep-us", type=float, default=100.0)
    parser.add_argument("--output-root", default="pynq_results")
    return parser.parse_args()


def main():
    args = parse_args()
    if (
        args.warmup < 1
        or args.frames < 1
        or args.repeats < 1
        or args.min_seconds <= 0
        or args.opencv_threads < 1
        or args.dma_poll_sleep_us < 0
    ):
        raise ValueError(
            "warmup, frames, repeats, min-seconds and threads must be positive; "
            "DMA poll sleep must not be negative"
        )
    bitstream = Path(args.bitstream)
    if not bitstream.is_file():
        raise FileNotFoundError("Bitstream not found: {}".format(bitstream))

    try:
        from pynq import Bitstream
    except ImportError as exc:
        raise RuntimeError("This benchmark must run on a PYNQ image") from exc
    Bitstream(str(bitstream)).download()

    cv2.setNumThreads(args.opencv_threads)
    frames = load_frames(args.source, args.warmup + args.frames)
    factories = {
        "opencv_software": lambda: SoftwareMotionDetector(
            gray_converter=opencv_bgr2gray
        ),
        "optimized_pl": lambda: PlMotionDetector(
            args.dma_poll_sleep_us / 1_000_000.0
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
                "repeat={} backend={} fps={:.2f} cpu={:.2f}%".format(
                    repeat_index + 1,
                    backend,
                    record["fps"],
                    record["process_cpu_one_core_percent"],
                )
            )

    summaries = {
        backend: summarize_runs(records, backend) for backend in factories
    }
    agreement = compare_outputs(
        retained_outputs["opencv_software"], retained_outputs["optimized_pl"]
    )
    config = {
        "source": args.source,
        "warmup_frames": args.warmup,
        "measure_frames": args.frames,
        "repeats": args.repeats,
        "min_seconds": args.min_seconds,
        "dma_poll_sleep_us": args.dma_poll_sleep_us,
    }
    run_dir = write_results(
        Path(args.output_root), bitstream, config, records, summaries, agreement
    )
    print("results={}".format(run_dir.resolve()))
    print(json.dumps(agreement, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
