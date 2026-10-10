"""Profile the PS-side stages around the currently loaded PYNQ PL pipeline."""

import argparse
import json
from pathlib import Path
import statistics
import time

import cv2
import numpy as np
import psutil

from benchmark_motion import generate_synthetic_frames
from motion_common import (
    HEIGHT,
    WIDTH,
    check_alarm,
    detect_targets,
    extract_low_byte_mask,
    pack_bgr_to_rgb32,
    process_mask,
)
from pl_motion_detection_optimized import (
    DMA_BASE_ADDR,
    FRAME_DIFF_BASE,
    MORPHOLOGY_BASE,
    RGB2GRAY_BASE,
    THRESHOLD_BASE,
    AP_CTRL,
    SimpleAxiDMA,
    configure_ip,
)


def summarize(samples):
    values = np.asarray(samples, dtype=np.float64)
    return {
        "mean_ms": statistics.fmean(samples),
        "p50_ms": float(np.percentile(values, 50)),
        "p95_ms": float(np.percentile(values, 95)),
    }


def parse_args():
    parser = argparse.ArgumentParser(
        description="Profile the currently loaded PL motion detector without reloading it"
    )
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--frames", type=int, default=200)
    parser.add_argument(
        "--buffer-mode",
        choices=(
            "uncached_direct",
            "uncached_cached_pack",
            "uncached_mixchannels_io",
            "split_mixchannels_io",
            "cacheable_direct",
            "cacheable_mixchannels",
            "cacheable_mixchannels_io",
            "cacheable_bgra",
        ),
        default="uncached_direct",
        help="Select how DMA buffers are allocated and populated",
    )
    parser.add_argument("--poll-sleep-us", type=float, default=0.0)
    parser.add_argument("--output", default="profile_results.json")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.warmup < 1 or args.frames < 1 or args.poll_sleep_us < 0:
        raise ValueError("warmup and frames must be positive; poll sleep must not be negative")

    try:
        from pynq import MMIO, allocate
    except ImportError as exc:
        raise RuntimeError("This profiler must run on a PYNQ image") from exc

    frames = generate_synthetic_frames(args.warmup + args.frames)
    pixel_count = WIDTH * HEIGHT
    transfer_bytes = pixel_count * np.dtype(np.uint32).itemsize

    input_buffer = None
    output_buffer = None
    stages = {
        "pack_rgb32": [],
        "start_hls_ips": [],
        "input_flush": [],
        "dma_registers": [],
        "dma_wait": [],
        "output_invalidate": [],
        "extract_mask": [],
        "morphology": [],
        "contours_alarm": [],
        "frame_total": [],
    }

    try:
        dma = SimpleAxiDMA(
            DMA_BASE_ADDR, poll_sleep_seconds=args.poll_sleep_us / 1_000_000.0
        )
        ip_blocks = [
            MMIO(RGB2GRAY_BASE, 0x10000),
            MMIO(FRAME_DIFF_BASE, 0x10000),
            MMIO(THRESHOLD_BASE, 0x10000),
            MMIO(MORPHOLOGY_BASE, 0x10000),
        ]
        configure_ip(ip_blocks, ip_blocks[2])

        input_allocation_options = {}
        output_allocation_options = {}
        if args.buffer_mode.startswith("cacheable_"):
            input_allocation_options["cacheable"] = True
            output_allocation_options["cacheable"] = True
        elif args.buffer_mode == "split_mixchannels_io":
            output_allocation_options["cacheable"] = True
        input_buffer = allocate(
            shape=(pixel_count,), dtype=np.uint32, **input_allocation_options
        )
        output_buffer = allocate(
            shape=(pixel_count,), dtype=np.uint32, **output_allocation_options
        )
        pack_output = input_buffer
        if args.buffer_mode == "uncached_cached_pack":
            pack_output = np.empty(pixel_count, dtype=np.uint32)
        pack_scratch = np.empty(pixel_count, dtype=np.uint32)
        input_bytes = input_buffer.view(np.uint8).reshape(HEIGHT, WIDTH, 4)
        output_bytes = output_buffer.view(np.uint8).reshape(HEIGHT, WIDTH, 4)
        raw_mask_buffer = np.empty((HEIGHT, WIDTH), dtype=np.uint8)
        if args.buffer_mode in (
            "uncached_mixchannels_io",
            "split_mixchannels_io",
            "cacheable_mixchannels",
            "cacheable_mixchannels_io",
        ):
            input_bytes[:, :, 3] = 0

        process = psutil.Process()
        process_before = None
        measured_wall_started = None

        for index, frame in enumerate(frames):
            if index == args.warmup:
                process_before = process.cpu_times()
                measured_wall_started = time.perf_counter()
            frame_started = time.perf_counter()

            started = time.perf_counter()
            if args.buffer_mode in (
                "uncached_mixchannels_io",
                "split_mixchannels_io",
                "cacheable_mixchannels",
                "cacheable_mixchannels_io",
            ):
                cv2.mixChannels([frame], [input_bytes], [0, 0, 1, 1, 2, 2])
            elif args.buffer_mode == "cacheable_bgra":
                cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA, dst=input_bytes)
                input_bytes[:, :, 3] = 0
            else:
                pack_bgr_to_rgb32(frame, output=pack_output, scratch=pack_scratch)
                if pack_output is not input_buffer:
                    np.copyto(input_buffer, pack_output)

            if index == 0:
                expected = pack_bgr_to_rgb32(frame)
                if not np.array_equal(input_buffer, expected):
                    raise AssertionError(
                        "Packed DMA input does not match 0x00RRGGBB"
                    )
            pack_done = time.perf_counter()

            for ip in ip_blocks:
                ip.write(AP_CTRL, 0x01)
            ips_started = time.perf_counter()

            if hasattr(input_buffer, "flush"):
                input_buffer.flush()
            flush_done = time.perf_counter()

            dma.mmio.write(dma.MM2S_DMASR, dma.IRQ_CLEAR_MASK)
            dma.mmio.write(dma.S2MM_DMASR, dma.IRQ_CLEAR_MASK)
            dma.mmio.write(dma.S2MM_DA, output_buffer.physical_address)
            dma.mmio.write(dma.S2MM_LENGTH, transfer_bytes)
            dma.mmio.write(dma.MM2S_SA, input_buffer.physical_address)
            dma.mmio.write(dma.MM2S_LENGTH, transfer_bytes)
            registers_done = time.perf_counter()

            dma._wait_for_idle()
            wait_done = time.perf_counter()

            if hasattr(output_buffer, "invalidate"):
                output_buffer.invalidate()
            invalidate_done = time.perf_counter()

            if args.buffer_mode in (
                "uncached_mixchannels_io",
                "split_mixchannels_io",
                "cacheable_mixchannels_io",
            ):
                cv2.mixChannels([output_bytes], [raw_mask_buffer], [0, 0])
                raw_mask = raw_mask_buffer
                if index == 0:
                    expected_mask = extract_low_byte_mask(output_buffer)
                    if not np.array_equal(raw_mask, expected_mask):
                        raise AssertionError("Extracted low-byte mask is incorrect")
            else:
                raw_mask = extract_low_byte_mask(output_buffer)
            extract_done = time.perf_counter()

            mask = process_mask(raw_mask)
            morphology_done = time.perf_counter()

            targets = detect_targets(mask)
            check_alarm(targets)
            frame_done = time.perf_counter()

            if index >= args.warmup:
                stages["pack_rgb32"].append((pack_done - started) * 1000.0)
                stages["start_hls_ips"].append((ips_started - pack_done) * 1000.0)
                stages["input_flush"].append((flush_done - ips_started) * 1000.0)
                stages["dma_registers"].append(
                    (registers_done - flush_done) * 1000.0
                )
                stages["dma_wait"].append((wait_done - registers_done) * 1000.0)
                stages["output_invalidate"].append(
                    (invalidate_done - wait_done) * 1000.0
                )
                stages["extract_mask"].append(
                    (extract_done - invalidate_done) * 1000.0
                )
                stages["morphology"].append(
                    (morphology_done - extract_done) * 1000.0
                )
                stages["contours_alarm"].append(
                    (frame_done - morphology_done) * 1000.0
                )
                stages["frame_total"].append((frame_done - frame_started) * 1000.0)

        measured_wall_seconds = time.perf_counter() - measured_wall_started
        process_after = process.cpu_times()
        process_seconds = (
            process_after.user
            + process_after.system
            - process_before.user
            - process_before.system
        )
    finally:
        if input_buffer is not None:
            input_buffer.freebuffer()
        if output_buffer is not None:
            output_buffer.freebuffer()

    summary = {name: summarize(values) for name, values in stages.items()}
    total_mean = summary["frame_total"]["mean_ms"]
    for name, values in summary.items():
        values["percent_of_frame"] = 100.0 * values["mean_ms"] / total_mean

    payload = {
        "notice": "Uses the currently loaded overlay; this script does not download a bitstream.",
        "warmup_frames": args.warmup,
        "measured_frames": args.frames,
        "buffer_mode": args.buffer_mode,
        "poll_sleep_us": args.poll_sleep_us,
        "process_cpu_one_core_percent": (
            100.0 * process_seconds / measured_wall_seconds
        ),
        "process_cpu_total_capacity_percent": (
            100.0
            * process_seconds
            / measured_wall_seconds
            / (psutil.cpu_count(logical=True) or 1)
        ),
        "fps_from_mean_latency": 1000.0 / total_mean,
        "stages": summary,
    }
    output = Path(args.output)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("stage                     mean_ms     p50_ms     p95_ms   percent")
    for name, values in summary.items():
        print(
            "{:<24} {:>9.3f} {:>10.3f} {:>10.3f} {:>8.2f}%".format(
                name,
                values["mean_ms"],
                values["p50_ms"],
                values["p95_ms"],
                values["percent_of_frame"],
            )
        )
    print("fps_from_mean_latency={:.2f}".format(payload["fps_from_mean_latency"]))
    print(
        "process_cpu_one_core_percent={:.2f}".format(
            payload["process_cpu_one_core_percent"]
        )
    )
    print(
        "process_cpu_total_capacity_percent={:.2f}".format(
            payload["process_cpu_total_capacity_percent"]
        )
    )
    print("results={}".format(output.resolve()))


if __name__ == "__main__":
    main()
