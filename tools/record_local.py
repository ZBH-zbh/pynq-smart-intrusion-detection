"""Drive a recording session and tell the operator what to do, live.

The board records; this prints a timed script so the person in front of the
camera knows what to do at each moment. The schedule lives here because this is
where the countdown is displayed, and the two must agree on the total duration.

Two things learned the hard way and worth keeping:

  * The recorder runs in the FOREGROUND. Backgrounding it needed setsid, an
    output redirect, a /dev/null on stdin and a password pipe, and each of those
    was a separate way to end up with "sudo: no password was provided".
  * The password is piped with ``echo xilinx |`` exactly as
    ``deploy_tracker.start`` does, and the script is invoked by ABSOLUTE PATH so
    no ``cd`` is needed inside the sudo'd shell.

The countdown starts when the recorder says the camera is ready, not when the
command is issued, so the prompts line up with what is actually being recorded.
"""
import os
import sys
import threading
import time

# The prompts are Chinese and this runs in the operator's own terminal. Windows
# consoles default to a legacy code page, which turns every instruction into
# mojibake -- the one thing that must not be unreadable here.
if os.name == "nt":
    os.system("chcp 65001 > nul")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from deploy_tracker import connect, run  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REMOTE = "/home/xilinx/intrusion_demo"
#: Sessions land next to the package rather than at a hard-coded path, so the
#: folder can be handed to another machine without editing anything.
LOCAL_SESSIONS = os.path.join(os.path.dirname(HERE), "sessions")
PY = "/usr/local/share/pynq-venv/bin/python3"

# (start second, end second, instruction)
SCRIPT = [
    (0, 8, "离开画面 —— 什么都不要动，让程序采集背景"),
    (8, 53, "用手或一个物体横穿警戒线 10 次（每次之间停约 2 秒）"),
    (53, 65, "把手停在警戒线上不动，2 次，每次约 4 秒"),
    (65, 75, "碰到线立刻退回，2 次"),
    (75, 95, "两个目标先后从同一位置穿过，3 组（第二个比第一个晚约半秒）"),
    (95, 110, "干扰动作：在线的延长线外面走过、慢慢靠近又退开"),
    (110, 115, "结束 —— 离开画面"),
]
TOTAL = SCRIPT[-1][1]
BAR = 28


def show(elapsed):
    for start, end, text in SCRIPT:
        if start <= elapsed < end:
            left = end - elapsed
            done = int(BAR * (elapsed - start) / max(1, end - start))
            bar = "#" * done + "." * (BAR - done)
            line = f"  [{bar}] {left:5.1f}s  {text}"
            print(f"\r{line[:150]:<150}", end="", flush=True)
            return
    print(f"\r{'  录完了，正在收尾 ...':<150}", end="", flush=True)


def main():
    print("=" * 70)
    print("  越线计数 —— 真实素材录制")
    print("=" * 70)
    for start, end, text in SCRIPT:
        print(f"  {start:>3}-{end:<3}s  {text}")
    print("=" * 70)
    print("  倒计时是实时的；做错了不用管，继续往下做就行")
    print("=" * 70)

    client = connect()
    print("\n  SSH 已连接")
    run(client, "echo xilinx | sudo -S pkill -f tracker_server.py; sleep 1", 30)

    feats = os.path.join(os.path.dirname(HERE), "FPGA2.0", "new_features")
    deploy = os.path.join(os.path.dirname(HERE), "FPGA2.0", "pynq_deploy")
    here = HERE
    uploads = [
        (os.path.join(feats, "tracker_server.py"), "tracker_server.py"),
        (os.path.join(here, "session_recorder.py"), "session_recorder.py"),
        (os.path.join(feats, "target_tracker.py"), "target_tracker.py"),
        (os.path.join(feats, "strip_merge.py"), "strip_merge.py"),
        (os.path.join(feats, "localisation.py"), "localisation.py"),
        (os.path.join(feats, "line_crossing.py"), "line_crossing.py"),
        (os.path.join(feats, "trail.py"), "trail.py"),
        (os.path.join(feats, "grouping.py"), "grouping.py"),
        (os.path.join(feats, "bg_detector.py"), "bg_detector.py"),
        (os.path.join(deploy, "motion_common.py"), "motion_common.py"),
        (os.path.join(deploy, "pl_motion_detection_optimized.py"),
         "pl_motion_detection_optimized.py"),
    ]
    sftp = client.open_sftp()
    for local, remote in uploads:
        sftp.put(local, f"{REMOTE}/{remote}")
    sftp.close()
    print(f"  已上传 {len(uploads)} 个文件")

    command = (
        "echo xilinx | sudo -S env XILINX_XRT=/usr "
        f"{PY} -u {REMOTE}/session_recorder.py {TOTAL} session"
    )
    _, stdout, stderr = client.exec_command(command, timeout=TOTAL + 240)

    state = {"started": None}
    finished = threading.Event()

    def countdown():
        while not finished.is_set():
            started = state["started"]
            if started is not None:
                elapsed = time.time() - started
                if elapsed > TOTAL + 6:
                    return
                show(elapsed)
            time.sleep(0.25)

    thread = threading.Thread(target=countdown, daemon=True)
    thread.start()

    print("\n  等待摄像头就绪 ...", flush=True)
    while True:
        line = stdout.readline()
        if not line:
            break
        line = line.rstrip()
        if not line:
            continue
        if state["started"] is None and "source ready" in line:
            state["started"] = time.time()
            print("\r  >>> 开始！跟着倒计时做 <<<" + " " * 110)
            print()
        elif "done:" in line or "Error" in line or "Traceback" in line:
            # Only the milestones: the recorder's periodic progress lines fight
            # the countdown for the same line of the terminal.
            print(f"\r{'':<150}", end="")
            print(f"    [板端] {line}")

    finished.set()
    thread.join(timeout=2)
    print()
    error = stderr.read().decode(errors="replace").strip()
    if error and "password for" not in error:
        print(f"  stderr: {error[:400]}")

    os.makedirs(LOCAL_SESSIONS, exist_ok=True)
    target = os.path.join(LOCAL_SESSIONS, time.strftime("session_%Y%m%d_%H%M%S"))
    os.makedirs(os.path.join(target, "frames"), exist_ok=True)

    print(f"  下载到 {target}")
    sftp = client.open_sftp()
    for name in ("session.json", "meta.jsonl", "masks.bin"):
        try:
            sftp.get(f"{REMOTE}/session_data/{name}",
                     os.path.join(target, name))
            print(f"    {name:<14} {os.path.getsize(os.path.join(target, name)) / 1e6:8.2f} MB")
        except OSError as exc:
            print(f"    {name:<14} 下载失败: {exc}")

    names = sorted(sftp.listdir(f"{REMOTE}/session_data/frames"))
    print(f"    frames/        {len(names)} 帧 ...")
    for index, name in enumerate(names):
        sftp.get(f"{REMOTE}/session_data/frames/{name}",
                 os.path.join(target, "frames", name))
        if index and index % 400 == 0:
            print(f"      {index}/{len(names)}", flush=True)
    sftp.close()
    client.close()

    print(f"\n  完成：{target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
