"""Run the frame capture on the board and pull the result back to the PC.

Stops the tracker server first (the camera allows one user), then records,
tars, downloads and unpacks into new_features/real_capture/.

    python run_capture.py 60
"""
import os
import shutil
import socket
import sys
import tarfile
import time

import paramiko

HOST, USER, PWD = "192.168.137.125", "xilinx", "xilinx"
REMOTE_DIR = "/tmp/capture"
REMOTE_TAR = "/tmp/capture.tar.gz"
LOCAL_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "FPGA2.0", "new_features", "real_capture")

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


def run(client, cmd, timeout=300):
    _, out, err = client.exec_command(cmd, timeout=timeout)
    chunks = []
    for stream in (out, err):
        try:
            chunks.append(stream.read().decode(errors="replace"))
        except Exception as exc:
            chunks.append(f"\n(read timed out: {type(exc).__name__})")
    return "".join(chunks).strip()


def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
    client = connect()
    print("SSH OK")

    print("stopping any running server (camera is single-user) ...")
    print(run(client, "echo xilinx | sudo -S pkill -f tracker_server.py; "
                      "echo xilinx | sudo -S pkill -f mjpeg_server.py; "
                      "sleep 3; pgrep -fa 'tracker_server|mjpeg_server' "
                      "|| echo 'camera free'", 40))
    print(run(client, "echo xilinx | sudo -S rm -rf /tmp/capture "
                      "/tmp/capture.tar.gz; echo cleaned", 40))

    sftp = client.open_sftp()
    sftp.put(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "capture_frames.py"),
             "/home/xilinx/intrusion_demo/capture_frames.py")
    sftp.close()
    print("uploaded capture_frames.py")

    print(f"\n=== RECORDING {seconds:.0f} s — do the actions now ===")
    # Must run as root: loading the overlay touches /dev/mem.
    print(run(
        client,
        "cd /home/xilinx/intrusion_demo && "
        "echo xilinx | sudo -S env XILINX_XRT=/usr "
        "/usr/local/share/pynq-venv/bin/python3 -u capture_frames.py "
        f"--seconds {seconds:.0f} --out {REMOTE_DIR}",
        timeout=seconds + 180,
    ))

    print("\npacking ...")
    print(run(client, f"tar czf {REMOTE_TAR} -C /tmp capture && "
                      f"ls -l {REMOTE_TAR}", 180))

    os.makedirs(LOCAL_DIR, exist_ok=True)
    local_tar = os.path.join(os.path.dirname(LOCAL_DIR), "capture.tar.gz")
    sftp = client.open_sftp()
    sftp.get(REMOTE_TAR, local_tar)
    sftp.close()
    print(f"downloaded {local_tar} "
          f"({os.path.getsize(local_tar) / 1e6:.1f} MB)")

    if os.path.isdir(LOCAL_DIR):
        shutil.rmtree(LOCAL_DIR)      # never mix an older capture into this one
    os.makedirs(LOCAL_DIR, exist_ok=True)
    with tarfile.open(local_tar, "r:gz") as tar:
        tar.extractall(LOCAL_DIR, filter="data")
    extracted = os.path.join(LOCAL_DIR, "capture")
    count = len([f for f in os.listdir(extracted) if f.endswith(".jpg")])
    print(f"unpacked {count} frames into {extracted}")

    client.close()


if __name__ == "__main__":
    main()
