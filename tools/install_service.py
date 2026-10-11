"""Install, enable and start a systemd unit so the live view survives reboots."""
import socket
import time
import paramiko

socket.setdefaulttimeout(180)


def connect(retries=12):
    for i in range(retries):
        try:
            c = paramiko.SSHClient()
            c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            c.connect("192.168.137.125", 22, "xilinx", "xilinx", timeout=12,
                      banner_timeout=25, auth_timeout=25)
            print(f"  SSH 连接成功（第 {i+1} 次）")
            return c
        except Exception:
            time.sleep(5)
    raise RuntimeError("connect failed")


c = connect()
S = "echo xilinx | sudo -S "


def run(cmd, t=60):
    try:
        _, o, e = c.exec_command(cmd, timeout=t)
        return (o.read().decode(errors="replace") + e.read().decode(errors="replace")).strip()
    except Exception as exc:
        return f"(timeout/{type(exc).__name__})"


sftp = c.open_sftp()
# Beside this script, so the folder can be moved to another machine as-is.
sftp.put(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "pynq-mjpeg.service"), "/tmp/pynq-mjpeg.service")
sftp.close()
print("已上传 service 文件")

print()
print("### 停掉手工启动的实例 ###")
print(run(S + "pkill -f mjpeg_server.py 2>/dev/null; sleep 2; echo stopped", 30))

print()
print("### 安装 service ###")
print(run(S + "cp -v /tmp/pynq-mjpeg.service /etc/systemd/system/pynq-mjpeg.service 2>&1 | grep -v sudo", 30))
print(run(S + "chmod 644 /etc/systemd/system/pynq-mjpeg.service; echo chmod-ok", 20))
print(run(S + "systemctl daemon-reload; echo reload-ok", 40))

print()
print("### 启用并启动 ###")
print(run(S + "systemctl enable pynq-mjpeg.service 2>&1 | grep -v sudo", 40))
print(run(S + "systemctl start pynq-mjpeg.service; sleep 3; echo started", 40))

print()
print("### 状态 ###")
print(run(S + "systemctl is-enabled pynq-mjpeg.service 2>&1 | grep -v sudo"))
print(run(S + "systemctl is-active pynq-mjpeg.service 2>&1 | grep -v sudo"))
print(run(S + "systemctl status pynq-mjpeg.service --no-pager 2>&1 | grep -v sudo | head -12", 40))
c.close()
print("等待服务就绪 ...")
time.sleep(22)
