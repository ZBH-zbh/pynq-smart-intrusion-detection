"""Does the trail actually cost frames on the board?

The static cost measurement says a 90-point trail takes ~40 ms to fit, and the
frame period is 60 ms. That is only a problem if the pipeline really rebuilds
on every frame, so this runs the real server with a moving target and reads the
frame rate back.

Synthetic source, because it guarantees a target that moves fast enough to hit
the trail's recorded point every frame.
"""
import json
import subprocess
import sys
import time
import urllib.request

HOST = "127.0.0.1"
PORT = 8081
PY = "/usr/local/share/pynq-venv/bin/python3"
DIR = "/home/xilinx/intrusion_demo"


def sh(cmd):
    return subprocess.run(["bash", "-lc", cmd], capture_output=True,
                          text=True).stdout


def stop():
    sh("echo xilinx | sudo -S pkill -f tracker_server.py; sleep 3")


def start(extra):
    sh(f"cd {DIR} && XILINX_XRT=/usr nohup {PY} tracker_server.py "
       f"--source synthetic --detector pl {extra} "
       f"> /tmp/trailcost.log 2>&1 & sleep 1")


def fps_samples(seconds=8.0):
    out = []
    end = time.time() + seconds
    while time.time() < end:
        try:
            with urllib.request.urlopen(
                    f"http://{HOST}:{PORT}/status", timeout=5) as r:
                out.append(json.load(r))
        except Exception:
            pass
        time.sleep(0.4)
    return out


def trail_stats():
    try:
        with urllib.request.urlopen(
                f"http://{HOST}:{PORT}/trail", timeout=5) as r:
            return json.load(r)["stats"]
    except Exception as exc:
        return {"error": str(exc)}


def run(label, extra):
    stop()
    start(extra)
    time.sleep(6)                       # let the trail fill up
    samples = fps_samples(10.0)
    stats = trail_stats()
    if not samples:
        print(f"{label:>22}: no response (see /tmp/trailcost.log)")
        return
    fps = [s["fps"] for s in samples if s.get("fps")]
    tracks = max((s.get("tracks", 0) for s in samples), default=0)
    fps.sort()
    mid = fps[len(fps) // 2] if fps else 0.0
    print(f"{label:>22}: max_tracks={tracks:>2}  fps median={mid:6.2f}  "
          f"min={fps[0]:6.2f}  max={fps[-1]:6.2f}  rebuilds={stats.get('rebuilds')} "
          f"points={stats.get('points')}")
    stop()


def main():
    print(f"frame period: {1000.0 / 16.7:.1f} ms\n")
    run("trail-seconds 1", "--trail-seconds 1")
    run("trail-seconds 6 (default)", "--trail-seconds 6")
    run("trail-min-move 60", "--trail-seconds 6 --trail-min-move 60")
    stop()


if __name__ == "__main__":
    sys.exit(main())
