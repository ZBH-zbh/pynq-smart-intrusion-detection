"""Deploy the tracking feature to the PYNQ-Z2 board and start it.

Usage:
    python deploy_tracker.py                     # board mode, bg detector
    python deploy_tracker.py board pl            # shipped PL frame-difference
    python deploy_tracker.py synthetic bg        # no camera needed
    python deploy_tracker.py stop

Stages: upload -> run the offline tests on the board -> stop the old instance
-> launch -> poll HTTP until it answers. Board files go to
/home/xilinx/intrusion_demo/.
"""
import argparse
import os
import socket
import sys
import time
import urllib.error
import urllib.request

import paramiko

HOST, USER, PWD = "192.168.137.125", "xilinx", "xilinx"
REMOTE = "/home/xilinx/intrusion_demo"
PORT = 8081

#: Where the feature modules live, found relative to this file rather than
#: hard-coded. The package is meant to be handed to another machine, and a
#: hard-coded absolute path is the first thing that breaks there.
LOCAL = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "software", "tracking")
)

#: Everything the board needs to run. grouping.py is NOT optional: the server
#: imports it, so omitting it stops the service from starting at all.
FILES = [
    "target_tracker.py",
    "strip_merge.py",
    "bg_detector.py",
    "localisation.py",
    "line_crossing.py",
    "trail.py",
    "grouping.py",
    "tracker_server.py",
    "test_target_tracker.py",
    "test_strip_merge.py",
    "test_bg_detector.py",
    "test_tracker_pipeline.py",
    "test_localisation.py",
    "test_line_crossing.py",
    "test_trail.py",
    "test_grouping.py",
]

socket.setdefaulttimeout(300)


def connect(retries=10):
    for i in range(retries):
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(HOST, 22, USER, PWD, timeout=15,
                           banner_timeout=30, auth_timeout=30)
            return client
        except Exception as exc:
            print(f"  retry {i + 1}: {type(exc).__name__}")
            time.sleep(5)
    raise RuntimeError("connect failed")


def run(client, cmd, timeout=120):
    """Run a command. A channel that never reaches EOF is not a failure:

    a detached background process can keep the channel open, so a timeout
    returns whatever was read instead of raising.
    """
    _, out, err = client.exec_command(cmd, timeout=timeout)
    chunks = []
    for stream in (out, err):
        try:
            chunks.append(stream.read().decode(errors="replace"))
        except Exception as exc:
            chunks.append(f"\n(read timed out: {type(exc).__name__})")
    return "".join(chunks).strip()


def http_json(path, timeout=12):
    with urllib.request.urlopen(
        f"http://{HOST}:{PORT}{path}", timeout=timeout
    ) as response:
        import json
        return json.loads(response.read().decode())


def start(client, source, detector):
    run(client, "echo xilinx | sudo -S pkill -f tracker_server.py; sleep 3",
        30)
    run(client, "echo xilinx | sudo -S rm -f /tmp/tracker.log", 20)
    run(
        client,
        "echo xilinx | sudo -S bash -c 'cd /home/xilinx/intrusion_demo && "
        "XILINX_XRT=/usr setsid nohup /usr/local/share/pynq-venv/bin/python3 -u "
        f"tracker_server.py --source {source} --detector {detector} "
        f"--port {PORT} "
        "> /tmp/tracker.log 2>&1 < /dev/null &'",
        20,
    )


def wait_for_service(seconds=90):
    print(f"polling http://{HOST}:{PORT}/status ...")
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            status = http_json("/status")
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(3)
            continue
        if status.get("frame", 0) > 5:
            return status
        time.sleep(3)
    return None


def main():
    parser = argparse.ArgumentParser(description="deploy the tracking feature")
    parser.add_argument("action", nargs="?", default="board",
                        choices=("board", "synthetic", "stop"))
    parser.add_argument("detector", nargs="?", default="pl",
                        choices=("bg", "pl"))
    args = parser.parse_args()

    client = connect()
    print("SSH OK")

    if args.action == "stop":
        print(run(client, "echo xilinx | sudo -S pkill -f tracker_server.py; "
                         "sleep 2; pgrep -fa tracker_server || echo stopped", 30))
        client.close()
        return

    sftp = client.open_sftp()
    for name in FILES:
        sftp.put(f"{LOCAL}\\{name}", f"{REMOTE}/{name}")
        print(f"  uploaded {name} ({sftp.stat(f'{REMOTE}/{name}').st_size} bytes)")
    sftp.close()

    print("\n--- offline tests on the board ---")
    print(run(
        client,
        "cd /home/xilinx/intrusion_demo && XILINX_XRT=/usr "
        "/usr/local/share/pynq-venv/bin/python3 -m unittest "
        "test_target_tracker test_strip_merge test_bg_detector "
        "test_tracker_pipeline test_localisation test_line_crossing "
        "test_trail test_grouping 2>&1 | tail -4",
        900,          # the board's ARM core needs ~340 s for this suite; a 300 s
                      # limit reported a confusing "(read timed out)" instead
    ))

    print(f"\n--- starting source={args.action} detector={args.detector} "
          f"on port {PORT} ---")
    start(client, args.action, args.detector)

    status = wait_for_service()
    if status is None:
        print("\nservice did not come up; log follows:")
        print(run(client, "cat /tmp/tracker.log", 30))
        client.close()
        sys.exit(1)

    print(f"\nservice is up: frame={status['frame']} fps={status['fps']:.2f} "
          f"source={status['source']} detector={status['detector']} "
          f"raw={status['raw_targets']} tracks={status['tracks']} "
          f"error={status['error']}")
    print(f"open  http://{HOST}:{PORT}/")
    print("\n--- board log ---")
    print(run(client, "cat /tmp/tracker.log", 30))
    client.close()


if __name__ == "__main__":
    main()
