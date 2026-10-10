"""Headless real-time pipeline test for the PYNQ-Z2 motion detection project.

Reuses the project's own modules so it exercises exactly the same code path as
pl_motion_detection_optimized.py, minus the Jupyter widget display.

Run on the board as root with XILINX_XRT set:
  echo xilinx | sudo -S env XILINX_XRT=/usr \
    /usr/local/share/pynq-venv/bin/python3 headless_realtime_test.py
"""
import os
import sys
import time

sys.path.insert(0, "/home/xilinx/intrusion_demo")

import cv2
import numpy as np
from pynq import MMIO, Overlay, allocate

import pl_motion_detection_optimized as demo
from motion_common import (
    HEIGHT,
    WIDTH,
    check_alarm,
    detect_targets,
    draw_overlay,
    process_mask,
)

N_FRAMES = int(os.environ.get("N_FRAMES", "120"))
OUTDIR = "/home/xilinx/intrusion_demo"


def main():
    print("=" * 56)
    print("1. 加载 overlay")
    overlay = Overlay(demo.BITSTREAM, download=False)
    overlay.download()
    print("   overlay 加载成功")

    print("2. 初始化 DMA 与 4 个 HLS IP")
    dma = demo.SimpleAxiDMA(
        demo.DMA_BASE_ADDR, poll_sleep_seconds=demo.DMA_POLL_SLEEP_SECONDS
    )
    rgb2gray = MMIO(demo.RGB2GRAY_BASE, 0x10000)
    frame_diff = MMIO(demo.FRAME_DIFF_BASE, 0x10000)
    morphology = MMIO(demo.MORPHOLOGY_BASE, 0x10000)
    threshold = MMIO(demo.THRESHOLD_BASE, 0x10000)
    ips = [rgb2gray, frame_diff, threshold, morphology]
    demo.configure_ip(ips, threshold)
    print("   完成")

    print("3. 分配 DMA 缓冲")
    pixel_count = WIDTH * HEIGHT
    transfer_bytes = pixel_count * np.dtype(np.uint32).itemsize
    inbuf = allocate(shape=(pixel_count,), dtype=np.uint32)
    outbuf = allocate(shape=(pixel_count,), dtype=np.uint32, cacheable=True)
    in_bytes = inbuf.view(np.uint8).reshape(HEIGHT, WIDTH, 4)
    in_bytes[:, :, 3] = 0
    out_bytes = outbuf.view(np.uint8).reshape(HEIGHT, WIDTH, 4)
    raw_mask = np.empty((HEIGHT, WIDTH), dtype=np.uint8)
    print(f"   {WIDTH}x{HEIGHT}, 每帧 {transfer_bytes} 字节")

    print("4. 初始化蜂鸣器 GPIO (0x40040000)")
    buzzer = demo.BuzzerGPIO(demo.BUZZER_BASE, enabled=True)
    print("   蜂鸣器:", "OK" if buzzer.enabled else "初始化失败")

    print("5. 打开摄像头")
    cam = cv2.VideoCapture(0)
    cam.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
    cam.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
    if not cam.isOpened():
        raise RuntimeError("摄像头打开失败")
    ok, probe = cam.read()
    print(f"   打开成功，首帧尺寸 {probe.shape if ok else '读取失败'}")

    print(f"6. 连续处理 {N_FRAMES} 帧")
    times = []
    total_targets = 0
    alarm_frames = 0
    snapshots = []
    for n in range(1, N_FRAMES + 1):
        t0 = time.monotonic()
        ok, frame = cam.read()
        if not ok:
            print(f"   第 {n} 帧采集失败")
            break
        if frame.shape[:2] != (HEIGHT, WIDTH):
            frame = cv2.resize(frame, (WIDTH, HEIGHT))

        cv2.mixChannels([frame], [in_bytes], [0, 0, 1, 1, 2, 2])
        for ip in ips:
            ip.write(demo.AP_CTRL, 0x01)
        dma.transfer(inbuf, outbuf, transfer_bytes)
        cv2.mixChannels([out_bytes], [raw_mask], [0, 0])

        mask = process_mask(raw_mask)
        targets = detect_targets(mask)
        alarm = check_alarm(targets)
        buzzer.update(alarm)

        total_targets += len(targets)
        if alarm:
            alarm_frames += 1
        times.append(time.monotonic() - t0)

        if n % 30 == 0:
            fps = len(times) / sum(times)
            vis = draw_overlay(frame.copy(), targets, fps, alarm)
            path = os.path.join(OUTDIR, f"snapshot_{n:03d}.jpg")
            if cv2.imwrite(path, vis):
                snapshots.append(path)
            print(
                f"   帧 {n:3d}: fps={fps:5.1f}  目标数={len(targets)}  "
                f"报警={alarm}  掩码非零={int(np.count_nonzero(mask))}"
            )

    avg_fps = len(times) / sum(times) if times else 0.0
    print("7. 收尾")
    buzzer.off()
    cam.release()
    inbuf.freebuffer()
    outbuf.freebuffer()

    print("=" * 56)
    print(f"处理帧数      : {len(times)}")
    print(f"平均 FPS      : {avg_fps:.2f}")
    print(f"平均单帧延迟  : {sum(times)/len(times)*1000:.1f} ms")
    print(f"目标数累计    : {total_targets}")
    print(f"报警帧数      : {alarm_frames}/{len(times)}")
    print(f"快照          : {snapshots}")
    print("REALTIME_TEST_OK")


if __name__ == "__main__":
    main()
