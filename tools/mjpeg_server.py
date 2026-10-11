#!/usr/bin/env python3
"""Live view for the PYNQ-Z2 motion detection project, over plain HTTP.

Background
----------
Every long-lived display channel proved unreliable on this link:

  * Jupyter's notebook output (WebSocket -> ZMQ -> IOPub) -- board log shows
    "Replacing stale connection" / "Starting buffering" / "Restoring connection"
  * A plain-HTTP MJPEG stream -- dies after roughly 800-850 KB every time,
    regardless of how long that takes

Short-lived requests, however, are rock solid: 40 consecutive single-frame
``/snapshot`` fetches all succeeded.

So the page uses **self-paced short-request polling**: each frame is one
``GET /snapshot`` and the next request is scheduled from the image's own
``onload``. There is no long-lived connection to be reset, and if a request
fails the retry simply happens 300 ms later.

Endpoints
---------
  /            HTML page (polls /snapshot)
  /snapshot    single JPEG, no-store
  /status      JSON: fps / targets / alarm / clients
  /stream      MJPEG multipart stream (kept for diagnostics)
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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

PORT = int(os.environ.get("PORT", "8080"))
JPEG_QUALITY = int(os.environ.get("JPEG_QUALITY", "75"))
PUSH_HZ = float(os.environ.get("PUSH_HZ", "10"))   # frames produced per second
WRITE_TIMEOUT = float(os.environ.get("WRITE_TIMEOUT", "5"))
POLL_MS = int(os.environ.get("POLL_MS", "140"))    # page polling interval

_lock = threading.Lock()
_state = {
    "jpeg": None,
    "seq": 0,
    "fps": 0.0,
    "targets": 0,
    "alarm": False,
    "frame": 0,
    "error": None,
    "started": time.time(),
}
_stop = threading.Event()


def _snapshot_state():
    with _lock:
        return dict(_state)


def detection_loop():
    """The project's real-time pipeline, minus any Jupyter dependency."""
    # PYNQ's Overlay/PL server uses asyncio; a worker thread has no event loop
    # by default and Python 3.10 raises instead of creating one.
    import asyncio

    asyncio.set_event_loop(asyncio.new_event_loop())

    buzzer = None
    cam = None
    inbuf = outbuf = None
    try:
        print("[detect] loading overlay ...", flush=True)
        ol = Overlay(demo.BITSTREAM, download=False)
        ol.download()

        dma = demo.SimpleAxiDMA(
            demo.DMA_BASE_ADDR, poll_sleep_seconds=demo.DMA_POLL_SLEEP_SECONDS
        )
        ips = [
            MMIO(demo.RGB2GRAY_BASE, 0x10000),
            MMIO(demo.FRAME_DIFF_BASE, 0x10000),
            MMIO(demo.THRESHOLD_BASE, 0x10000),
            MMIO(demo.MORPHOLOGY_BASE, 0x10000),
        ]
        demo.configure_ip(ips, ips[2])

        pixel_count = WIDTH * HEIGHT
        nbytes = pixel_count * np.dtype(np.uint32).itemsize
        inbuf = allocate(shape=(pixel_count,), dtype=np.uint32)
        outbuf = allocate(shape=(pixel_count,), dtype=np.uint32, cacheable=True)
        in_bytes = inbuf.view(np.uint8).reshape(HEIGHT, WIDTH, 4)
        in_bytes[:, :, 3] = 0
        out_bytes = outbuf.view(np.uint8).reshape(HEIGHT, WIDTH, 4)
        raw_mask = np.empty((HEIGHT, WIDTH), dtype=np.uint8)

        buzzer = demo.BuzzerGPIO(demo.BUZZER_BASE, enabled=True)
        print(f"[detect] buzzer enabled: {buzzer.enabled}", flush=True)

        cam = cv2.VideoCapture(0)
        cam.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
        cam.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
        cam.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not cam.isOpened():
            raise RuntimeError("cannot open camera 0")

        for _ in range(3):    # flush whatever the driver already buffered
            cam.read()
        print("[detect] camera open, entering loop", flush=True)

        params = [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]
        times = []            # full frame period, including camera read
        n = 0
        last_push = 0.0
        min_interval = 1.0 / PUSH_HZ if PUSH_HZ > 0 else 0.0

        while not _stop.is_set():
            t_frame = time.monotonic()
            ok, frame = cam.read()
            if not ok:
                time.sleep(0.02)
                continue
            if frame.shape[:2] != (HEIGHT, WIDTH):
                frame = cv2.resize(frame, (WIDTH, HEIGHT))

            cv2.mixChannels([frame], [in_bytes], [0, 0, 1, 1, 2, 2])
            for ip in ips:
                ip.write(demo.AP_CTRL, 0x01)
            dma.transfer(inbuf, outbuf, nbytes)
            cv2.mixChannels([out_bytes], [raw_mask], [0, 0])

            mask = process_mask(raw_mask)
            targets = detect_targets(mask)
            alarm = check_alarm(targets)
            buzzer.update(alarm)

            n += 1
            # FPS over the whole frame period, camera read included, so the
            # number shown matches what the user actually gets.
            elapsed = time.monotonic() - t_frame
            if elapsed > 0:
                times.append(elapsed)
            if len(times) > 30:
                times.pop(0)
            total = sum(times)
            # Guard against a zero window: on a coarse monotonic clock a very
            # fast frame can measure as 0.0 s and dividing would raise.
            fps = len(times) / total if total > 0 else 0.0

            now = time.monotonic()
            if now - last_push >= min_interval:
                last_push = now
                vis = draw_overlay(frame.copy(), targets, fps, alarm)
                ok2, jpg = cv2.imencode(".jpg", vis, params)
                if ok2:
                    with _lock:
                        _state["jpeg"] = jpg.tobytes()
                        _state["seq"] += 1
                        _state["fps"] = fps
                        _state["targets"] = len(targets)
                        _state["alarm"] = bool(alarm)
                        _state["frame"] = n
                        _state["error"] = None

    except Exception as exc:
        import traceback

        traceback.print_exc()
        with _lock:
            _state["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        for fn in (
            lambda: buzzer.off() if buzzer is not None else None,
            lambda: cam.release() if cam is not None else None,
            lambda: inbuf.freebuffer() if inbuf is not None else None,
            lambda: outbuf.freebuffer() if outbuf is not None else None,
        ):
            try:
                fn()
            except Exception:
                pass
        print("[detect] loop exited", flush=True)


PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>PYNQ-Z2 运动检测</title>
<style>
  html,body{margin:0;padding:0;background:#111;color:#ddd;
            font-family:system-ui,"Microsoft YaHei",sans-serif}
  .wrap{max-width:1000px;margin:0 auto;padding:14px}
  h1{font-size:16px;font-weight:600;margin:0 0 10px}
  img{width:100%;image-rendering:pixelated;border:1px solid #333;display:block;
      background:#000;min-height:180px}
  #st{margin-top:10px;font-family:ui-monospace,Consolas,monospace;font-size:14px}
  .n{color:#4ade80}.a{color:#f87171}.w{color:#fbbf24}
  .tip{color:#888;font-size:13px;margin-top:8px;line-height:1.6}
</style></head>
<body><div class="wrap">
  <h1>PYNQ-Z2 智能入侵检测 —— 实时画面</h1>
  <img id="v" alt="live">
  <div id="st">连接中 ...</div>
  <div class="tip">
    手在镜头前移动 → 绿色目标框；伸进中间方框 → 方框变红 + 蜂鸣器响。<br>
    每帧一次短 HTTP 请求（不依赖长连接 / WebSocket），失败会自动重试。
  </div>
</div>
<script>
const POLL_MS = __POLL_MS__;
const img = document.getElementById('v');
const st  = document.getElementById('st');
let okCount = 0, errCount = 0, lastOk = Date.now();

function nextFetch(){ setTimeout(fetchFrame, POLL_MS); }

function fetchFrame(){
  const im = new Image();
  im.onload = () => {
    img.src = im.src;
    okCount++; lastOk = Date.now();
    nextFetch();
  };
  im.onerror = () => {
    errCount++;
    setTimeout(fetchFrame, 300);   // 失败就 300ms 后重试
  };
  im.src = '/snapshot?t=' + Date.now();
}

img.onerror = () => setTimeout(fetchFrame, 300);
fetchFrame();

setInterval(async () => {
  try {
    const r = await fetch('/status', {cache:'no-store'});
    const s = await r.json();
    const stale = (Date.now() - lastOk) / 1000;
    const al = s.alarm ? '<span class="a">ALARM</span>' : '<span class="n">Normal</span>';
    let extra = `　取帧 ${okCount} 失败 ${errCount}`;
    if (stale > 3) extra += `　<span class="w">画面已停 ${stale.toFixed(0)}s</span>`;
    if (s.error) extra += `　<span class="a">服务错误: ${s.error}</span>`;
    st.innerHTML = `帧 ${s.frame}　检测FPS ${s.fps.toFixed(1)}　目标 ${s.targets}　${al}` + extra;
  } catch(e) {}
}, 1000);
</script>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send_bytes(self, body, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]

        if path in ("/", "/index.html"):
            body = PAGE.replace("__POLL_MS__", str(POLL_MS)).encode("utf-8")
            self._send_bytes(body, "text/html; charset=utf-8")
            return

        if path == "/status":
            s = _snapshot_state()
            s["uptime"] = time.time() - s.pop("started", time.time())
            s.pop("jpeg", None)
            self._send_bytes(json.dumps(s).encode(), "application/json")
            return

        if path == "/snapshot":
            with _lock:
                jpg = _state["jpeg"]
            if not jpg:
                self.send_error(503, "no frame yet")
                return
            self._send_bytes(jpg, "image/jpeg")
            return

        if path == "/stream":
            self._stream()
            return

        self.send_error(404)

    def _stream(self):
        """MJPEG stream -- kept for diagnostics; unreliable on this link."""
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.connection.settimeout(WRITE_TIMEOUT)
        except Exception:
            pass

        last_seq = -1
        sent = 0
        try:
            while not _stop.is_set():
                with _lock:
                    seq = _state["seq"]
                    jpg = _state["jpeg"]
                if jpg is None or seq == last_seq:
                    time.sleep(0.004)
                    continue
                last_seq = seq
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n")
                self.wfile.write(
                    b"Content-Length: " + str(len(jpg)).encode() + b"\r\n\r\n"
                )
                self.wfile.write(jpg)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
                sent += 1
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError) as exc:
            print(f"[http] stream ended after {sent} frames: {type(exc).__name__}", flush=True)
        finally:
            print(f"[http] stream client done (sent {sent})", flush=True)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64
    allow_reuse_address = True


def main():
    print("=" * 58)
    print("PYNQ-Z2 实时预览服务（短请求轮询模式）")
    print(f"  端口        : {PORT}")
    print(f"  JPEG 质量   : {JPEG_QUALITY}")
    print(f"  产帧上限    : {PUSH_HZ} FPS")
    print(f"  页面轮询    : {POLL_MS} ms")
    print(f"  浏览器打开  : http://<板卡IP>:{PORT}/")
    print("=" * 58, flush=True)

    threading.Thread(target=detection_loop, daemon=True).start()

    server = Server(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _stop.set()
        server.server_close()
        print("stopped")


if __name__ == "__main__":
    main()
