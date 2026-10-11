"""PYNQ-Z2 PL motion detection with safer DMA handling and lower PS overhead.

This file deliberately keeps direct MMIO register access because the delivered
HWH cannot be relied on for ``ip_dict`` discovery. It does not enable HLS
auto-restart until the streaming chain has been verified on the board.
"""

from collections import deque
import time

import cv2
import ipywidgets as widgets
import numpy as np
from IPython.display import display
from pynq import MMIO, Overlay, allocate

from motion_common import (
    HEIGHT,
    THRESH,
    WIDTH,
    check_alarm,
    detect_targets,
    draw_overlay,
    process_mask,
)


BITSTREAM = "/home/xilinx/intrusion_detection_optimized.bit"

DMA_BASE_ADDR = 0x41E00000
FRAME_DIFF_BASE = 0x40000000
MORPHOLOGY_BASE = 0x40010000
RGB2GRAY_BASE = 0x40020000
THRESHOLD_BASE = 0x40030000
BUZZER_BASE = 0x40040000

USE_BUZZER = True
BUZZER_BEEP_HZ = 4
DISPLAY_EVERY_N_FRAMES = 2
DMA_TIMEOUT_SECONDS = 1.0
DMA_POLL_SLEEP_SECONDS = 0.0001

AP_CTRL = 0x00
WIDTH_REG = 0x10
HEIGHT_REG = 0x18
THRESH_REG = 0x20
GPIO_DATA_REG = 0x00


class DmaTransferError(RuntimeError):
    """Raised when the AXI DMA reports an error or does not finish in time."""


class SimpleAxiDMA:
    MM2S_DMACR = 0x00
    MM2S_DMASR = 0x04
    MM2S_SA = 0x18
    MM2S_LENGTH = 0x28
    S2MM_DMACR = 0x30
    S2MM_DMASR = 0x34
    S2MM_DA = 0x48
    S2MM_LENGTH = 0x58

    RESET = 0x04
    RUN_STOP = 0x01
    IDLE = 0x02
    ERROR_MASK = 0x4770
    IRQ_CLEAR_MASK = 0x7000

    def __init__(
        self,
        base_addr,
        addr_range=0x10000,
        timeout=DMA_TIMEOUT_SECONDS,
        poll_sleep_seconds=0.0,
    ):
        if poll_sleep_seconds < 0:
            raise ValueError("poll_sleep_seconds must not be negative")
        self.mmio = MMIO(base_addr, addr_range)
        self.timeout = timeout
        self.poll_sleep_seconds = poll_sleep_seconds
        self.reset()

    def _status(self):
        return (
            self.mmio.read(self.MM2S_DMASR),
            self.mmio.read(self.S2MM_DMASR),
        )

    def reset(self):
        self.mmio.write(self.MM2S_DMACR, self.RESET)
        self.mmio.write(self.S2MM_DMACR, self.RESET)
        deadline = time.monotonic() + self.timeout
        while (
            self.mmio.read(self.MM2S_DMACR) & self.RESET
            or self.mmio.read(self.S2MM_DMACR) & self.RESET
        ):
            if time.monotonic() >= deadline:
                raise DmaTransferError("AXI DMA reset timed out")
            if self.poll_sleep_seconds:
                time.sleep(self.poll_sleep_seconds)

        self.mmio.write(self.MM2S_DMACR, self.RUN_STOP)
        self.mmio.write(self.S2MM_DMACR, self.RUN_STOP)

    def _wait_for_idle(self):
        deadline = time.monotonic() + self.timeout
        while True:
            mm2s_status, s2mm_status = self._status()
            if mm2s_status & self.ERROR_MASK or s2mm_status & self.ERROR_MASK:
                raise DmaTransferError(
                    "AXI DMA error: "
                    f"MM2S_DMASR=0x{mm2s_status:08X}, "
                    f"S2MM_DMASR=0x{s2mm_status:08X}"
                )
            if mm2s_status & self.IDLE and s2mm_status & self.IDLE:
                return
            if time.monotonic() >= deadline:
                raise DmaTransferError(
                    "AXI DMA transfer timed out: "
                    f"MM2S_DMASR=0x{mm2s_status:08X}, "
                    f"S2MM_DMASR=0x{s2mm_status:08X}"
                )
            if self.poll_sleep_seconds:
                time.sleep(self.poll_sleep_seconds)

    def transfer(self, source, destination, length):
        if length <= 0 or length % 4:
            raise ValueError("DMA length must be a positive multiple of four")
        if length > source.nbytes or length > destination.nbytes:
            raise ValueError("DMA length exceeds an allocated buffer")

        if hasattr(source, "flush"):
            source.flush()

        self.mmio.write(self.MM2S_DMASR, self.IRQ_CLEAR_MASK)
        self.mmio.write(self.S2MM_DMASR, self.IRQ_CLEAR_MASK)

        # Arm receive first so the output stream always has somewhere to go.
        self.mmio.write(self.S2MM_DA, destination.physical_address)
        self.mmio.write(self.S2MM_LENGTH, length)
        self.mmio.write(self.MM2S_SA, source.physical_address)
        self.mmio.write(self.MM2S_LENGTH, length)
        self._wait_for_idle()

        if hasattr(destination, "invalidate"):
            destination.invalidate()


class BuzzerGPIO:
    def __init__(self, base_addr, enabled=True):
        self.enabled = enabled
        self.mmio = None
        if not enabled:
            return
        try:
            self.mmio = MMIO(base_addr, 0x10000)
            self.mmio.write(0x04, 0xFFFFFFFE)
            self.off()
        except Exception as exc:
            print(f"Buzzer disabled after GPIO initialization failed: {exc}")
            self.enabled = False

    def _write(self, value):
        if self.enabled and self.mmio is not None:
            self.mmio.write(GPIO_DATA_REG, value & 1)

    def off(self):
        self._write(0)

    def update(self, alarm_status):
        if alarm_status:
            self._write(int(time.time() * BUZZER_BEEP_HZ) & 1)
        else:
            self.off()


def configure_ip(ip_blocks, threshold_ip):
    for ip in ip_blocks:
        ip.write(WIDTH_REG, WIDTH)
        ip.write(HEIGHT_REG, HEIGHT)
    threshold_ip.write(THRESH_REG, THRESH)


def main():
    overlay = None
    camera = None
    input_buffer = None
    output_buffer = None
    buzzer = None

    try:
        print("Loading overlay once...")
        overlay = Overlay(BITSTREAM, download=False)
        overlay.download()
        print("Overlay loaded")

        dma = SimpleAxiDMA(
            DMA_BASE_ADDR, poll_sleep_seconds=DMA_POLL_SLEEP_SECONDS
        )
        rgb2gray = MMIO(RGB2GRAY_BASE, 0x10000)
        frame_diff = MMIO(FRAME_DIFF_BASE, 0x10000)
        morphology = MMIO(MORPHOLOGY_BASE, 0x10000)
        threshold = MMIO(THRESHOLD_BASE, 0x10000)
        ip_blocks = [rgb2gray, frame_diff, threshold, morphology]
        configure_ip(ip_blocks, threshold)

        pixel_count = WIDTH * HEIGHT
        transfer_bytes = pixel_count * np.dtype(np.uint32).itemsize
        input_buffer = allocate(shape=(pixel_count,), dtype=np.uint32)
        output_buffer = allocate(
            shape=(pixel_count,), dtype=np.uint32, cacheable=True
        )
        input_bytes = input_buffer.view(np.uint8).reshape(HEIGHT, WIDTH, 4)
        input_bytes[:, :, 3] = 0
        output_bytes = output_buffer.view(np.uint8).reshape(HEIGHT, WIDTH, 4)
        raw_mask = np.empty((HEIGHT, WIDTH), dtype=np.uint8)

        buzzer = BuzzerGPIO(BUZZER_BASE, enabled=USE_BUZZER)
        camera = cv2.VideoCapture(0)
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
        if not camera.isOpened():
            raise RuntimeError("Cannot open camera 0")

        image_widget = widgets.Image(format="jpeg", width=640, height=480)
        display(image_widget)

        frame_times = deque(maxlen=30)
        frame_number = 0
        while True:
            frame_started = time.monotonic()
            ok, frame = camera.read()
            if not ok:
                raise RuntimeError("Camera frame capture failed")
            if frame.shape[:2] != (HEIGHT, WIDTH):
                frame = cv2.resize(frame, (WIDTH, HEIGHT))

            cv2.mixChannels([frame], [input_bytes], [0, 0, 1, 1, 2, 2])
            for ip in ip_blocks:
                ip.write(AP_CTRL, 0x01)
            dma.transfer(input_buffer, output_buffer, transfer_bytes)

            cv2.mixChannels([output_bytes], [raw_mask], [0, 0])
            mask = process_mask(raw_mask)
            targets = detect_targets(mask)
            alarm_status = check_alarm(targets)
            buzzer.update(alarm_status)

            frame_times.append(time.monotonic() - frame_started)
            fps = len(frame_times) / sum(frame_times)
            frame_number += 1

            if frame_number % DISPLAY_EVERY_N_FRAMES == 0:
                visualization = draw_overlay(
                    frame.copy(), targets, fps, alarm_status
                )
                encoded, jpeg = cv2.imencode(".jpg", visualization)
                if encoded:
                    image_widget.value = jpeg.tobytes()

    except KeyboardInterrupt:
        print("Stopped by user")
    finally:
        if buzzer is not None:
            buzzer.off()
        if camera is not None:
            camera.release()
        if input_buffer is not None:
            input_buffer.freebuffer()
        if output_buffer is not None:
            output_buffer.freebuffer()
        print("Resources released")


if __name__ == "__main__":
    main()
