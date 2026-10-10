# tools —— 跑在**电脑**上、通过 SSH 控制板子的脚本

这些脚本**不在板子上运行**。板子只需要 PYNQ 镜像自带的 Python 环境。
电脑上需要安装：

```bash
pip install numpy opencv-python paramiko pytest
```

各脚本顶部的 `HOST, USER, PWD` 默认是 `192.168.137.125` / `xilinx` / `xilinx`，
如果你的板子 IP 不同，修改对应常量即可。

---

## 常用脚本

| 脚本 | 作用 |
|---|---|
| **`deploy_tracker.py`** | 上传代码 → 在板子上跑测试 → 启动跟踪/Web 服务。`python deploy_tracker.py board pl` |
| **`record_local.py`** | 录一段真实素材（带网页倒计时），录完自动下载 |
| **`session_recorder.py`** | 录制完整会话，便于后续回放与分析 |
| **`headless_realtime_test.py`** | 无界面全链路实时测试 |
| **`buzzer_off.py`** | 强制关闭蜂鸣器并回读证明。服务被杀时报警状态可能不会自动清除 |

```bash
python deploy_tracker.py board pl     # PL 帧差检测器（默认）
python deploy_tracker.py board bg     # 背景模型检测器（可选，对比用）
python deploy_tracker.py stop         # 停止服务、释放摄像头
```

> 板子上跑测试约需数分钟（ARM 核慢），脚本超时已放宽。
> 完成后会打印服务状态。浏览器打开 **http://192.168.137.125:8081/**

---

## 注意事项

1. **摄像头同一时刻只允许一个进程打开。** 跑服务时不要再跑采集脚本。
2. **`/dev/video0` 是 `root:video`，`xilinx` 用户不在 `video` 组**，必须 `sudo`。
3. **不要用 `ifdown eth0` 通过 SSH 操作网络**，会把你自己踢下线。
4. `deploy_tracker.py` 用 `pkill` 停服务，**不会跑清理逻辑** ——
   报警时被杀蜂鸣器会一直响，所以有 `buzzer_off.py`。
5. 所有脚本里的 Python 解释器路径要指向**装了 paramiko 的那个**。
