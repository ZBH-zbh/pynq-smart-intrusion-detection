# deploy_tools —— 跑在**电脑**上、通过 SSH 控制板子的脚本

这些脚本**不在板子上运行**。板子只需要 PYNQ 镜像自带的 Python 环境。
需要我在电脑上装：

```bash
pip install numpy opencv-python paramiko pytest
```

**所有路径都是相对于本文件自动推导的**，整个 `project` 文件夹可以随便换位置，不用改代码。
唯一的硬编码是板子的地址和账号（`192.168.137.125` / `xilinx` / `xilinx`），
如果你的板子 IP 不同，改各脚本顶部的 `HOST, USER, PWD`。

---

## 最常用的两个

| 脚本 | 作用 |
|---|---|
| **`deploy_tracker.py`** | **主力**。上传代码 → 在板子上跑测试 → 启动服务。`python deploy_tracker.py board pl` |
| **`record_local.py`** | 录一段真实素材（带网页倒计时），录完自动下载。这是做准确度评估的前提 |

```bash
python deploy_tracker.py board pl     # 队友原版检测器（默认）
python deploy_tracker.py board bg     # 背景模型检测器（对比用，非默认）
python deploy_tracker.py stop         # 停止服务、释放摄像头
```

> 板子上跑测试约需 **6.5 分钟**（ARM 核慢），脚本的超时已经放宽到 900 秒。
> 完成后会打印 `service is up: frame=... fps=...`。浏览器打开 **http://192.168.137.125:8081/**

---

## 诊断 / 测量

| 脚本 | 作用 |
|---|---|
| `diagnose_tracking.py` | **pl 与 bg 检测器按速度分档 A/B**（带网页倒计时），回答"为什么锁不住目标" |
| `probe_camera_limit.py` | 实测各分辨率/格式的实际帧率，并检查 USB 链路速度与帧间隔分布 |
| `probe_camera_v4l2.py` | 用 V4L2 ioctl 枚举设备**真正提供**的模式与帧率 |
| `probe_uvc_quirks.py` | 逐个测试 `uvcvideo` 的 quirk 开关，看能否解开帧率 |
| `probe_camera_longrun.py` | 连续读几百帧，确认帧率无漂移 |
| `measure_trail_board.py` | 轨迹拟合在板子上的真实耗时曲线 |
| `measure_trail_compare.py` | 把"拟合耗时"和"每帧 add 耗时"放在**同一次运行**里对比 |
| `measure_trail_frame.py` | 每帧为轨迹付出多少毫秒 |
| `ab_trail_cost.py` | 通过网页 API 在线切换轨迹参数做 A/B |
| `profile_trail_convert.py` | 拆解 `catmull_rom` 的时间去向（数学 / 转换 / 其他） |
| `headless_realtime_test.py` | 无界面全链路实时测试 |

---

## 板子维护

| 脚本 | 作用 |
|---|---|
| `buzzer_off.py` | **强制关掉蜂鸣器并回读证明**。`pkill` 不会跑清理逻辑，报警时被杀会一直响 |
| `mjpeg_server.py` | 旧的 MJPEG 预览服务（已被 `tracker_server.py` 取代，保留备查） |
| `install_service.py` | 安装 `pynq-mjpeg.service`（旧的预览服务，**当前未启用**） |
| `eth0_static_v2` | 板子的静态网络配置（`/etc/network/interfaces.d/` 用） |
| `capture_frames.py` / `run_capture.py` | 早期的采帧工具（已被 `session_recorder.py` 取代） |

---

## 注意事项（都是踩过的坑）

1. **摄像头同一时刻只允许一个进程打开。** 跑服务时不要再跑采集脚本。
2. **`/dev/video0` 是 `root:video`，`xilinx` 用户不在 `video` 组**，必须 `sudo`。
3. **不要用 `ifdown eth0` 通过 SSH 操作网络**，会把你自己踢下线。
4. `deploy_tracker.py` 用 `pkill` 停服务，**不会跑清理逻辑** ——
   报警时被杀蜂鸣器会一直响，所以有 `buzzer_off.py`。
5. 所有脚本里的 Python 解释器路径要指向**装了 paramiko 的那个**。
