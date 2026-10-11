"""Does the trail rebuild really cost frames on the board?

Measured statically, fitting a 90-point trail takes ~40 ms on the board's ARM
core and a frame is 60 ms. That only matters if the pipeline rebuilds while the
target moves, so this drives the real server with a synthetic moving target and
reads the frame rate back.

Rather than restarting per setting, the trail window is changed through the
web API on one running server, so the only thing that differs between the
samples is the trail length.
"""
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from deploy_tracker import connect, http_json, run, start, wait_for_service

HOST, PORT = "192.168.137.125", 8081


def post(path, payload):
    request = urllib.request.Request(
        f"http://{HOST}:{PORT}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.read().decode()


def sample_fps(seconds=8.0):
    rates = []
    end = time.time() + seconds
    while time.time() < end:
        try:
            status = http_json("/status")
        except Exception:
            time.sleep(0.4)
            continue
        if status.get("fps"):
            rates.append(status["fps"])
        time.sleep(0.4)
    return sorted(rates)


def main():
    client = connect()
    print("SSH OK", flush=True)
    print("frame period at 15 FPS synthetic pacing: 66 ms", flush=True)

    start(client, "synthetic", "pl")
    status = wait_for_service()
    if status is None:
        print("service did not come up")
        print(run(client, "tail -20 /tmp/tracker.log", 30))
        client.close()
        return 1
    print(f"up: frame={status['frame']} fps={status['fps']:.2f}", flush=True)

    # Let the synthetic target produce tracks before measuring anything.
    time.sleep(8)

    print(f"\n{'trail seconds':>13} {'points':>7} {'fps median':>11} "
          f"{'min':>7} {'max':>7}", flush=True)
    for seconds in (1, 6, 20):
        post("/trail", {"seconds": seconds})
        time.sleep(6)                    # let the trail reach its new length
        rates = sample_fps(8.0)
        try:
            stats = http_json("/trail")["stats"]
        except Exception:
            stats = {}
        if not rates:
            print(f"{seconds:>13} {'-':>7} {'no samples':>11}", flush=True)
            continue
        mid = rates[len(rates) // 2]
        print(f"{seconds:>13} {stats.get('points', '?'):>7} {mid:>11.2f} "
              f"{rates[0]:>7.2f} {rates[-1]:>7.2f}", flush=True)

    run(client, "echo xilinx | sudo -S pkill -f tracker_server.py; sleep 2",
        30)
    client.close()
    print("\nserver stopped", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
