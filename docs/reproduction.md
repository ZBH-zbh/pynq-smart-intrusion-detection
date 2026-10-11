# 复现指南

## 1. 环境与硬件

| 项目 | 版本 / 型号 |
|---|---|
| 开发板 | PYNQ-Z2 (`xc7z020clg400-1`) |
| PYNQ 镜像 | v3.0.1 |
| 摄像头 | 罗技 C270i（UVC） |
| 蜂鸣器 | Grove Buzzer，接 Grove Base Shield G1 |
| 板端 Python | 3.10.4（PYNQ venv） |
| 关键库 | OpenCV 4.5.4、NumPy 1.21.5、pynq 3.0.1 |

**电源**：推荐 12V 圆头适配器；micro-USB 供电可能在蜂鸣器工作时掉压重启。

**网络**：板子静态 IP `192.168.137.125/24`，电脑以太网口静态 IP `192.168.137.1/24`。

## 2. PC 端准备

```powershell
# 1. 自检（仓库根目录）
powershell -ExecutionPolicy Bypass -File .\check_env.ps1

# 2. 安装部署依赖
pip install paramiko

# 3. 可选：安装 PC 测试依赖
pip install numpy opencv-python pytest
```

## 3. 一键部署到板子

```bash
cd tools
python deploy_tracker.py board pl
```

脚本会自动：
- 通过 SSH 连接 `192.168.137.125`
- 上传 `software/tracking/` 中的跟踪模块
- 上传 `hardware/overlay/` 中的 2025.2 位流
- 在板子上跑 312 项离线测试（约 6.5 分钟）
- 启动 `tracker_server.py --source board --detector pl --port 8081`

浏览器打开 `http://192.168.137.125:8081`：
- 拖拽黄色方框调整警戒区。
- 拖拽量线两端调整越线位置/角度。
- 底部滑块调整轨迹消抖、平滑、历史长度。

停止服务：

```bash
python deploy_tracker.py stop
```

## 4. 手动部署（备选）

若不想用部署脚本，可手动上传：

```bash
scp hardware/overlay/intrusion_detection_optimized.bit \
   hardware/overlay/intrusion_detection_optimized.hwh \
   software/deploy/*.py \
   software/tracking/*.py \
   xilinx@192.168.137.125:/home/xilinx/intrusion_demo/

ssh xilinx@192.168.137.125
cd /home/xilinx/intrusion_demo
echo xilinx | sudo -S env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 \
  tracker_server.py --source board --detector pl --port 8081
```

## 5. 位流哈希校验

```bash
cd /home/xilinx/intrusion_demo
sha256sum intrusion_detection_optimized.bit intrusion_detection_optimized.hwh
```

| 版本 | 路径 | bit 哈希 | hwh 哈希 |
|---|---|---|---|
| 2025.2（默认） | `hardware/overlay/` | `b93031cc4d6d65f8…` | `6933fdf5cac0376b…` |
| 2022.1（回退） | `hardware/overlay_2022_1/` | `78e325fcb43c73fe…` | `7e5b68b2906ab999…` |

哈希不一致不要加载 overlay。

## 6. 运行原始实时演示

不启动 Web 服务，只跑基础检测+报警：

```bash
cd /home/xilinx/intrusion_demo
echo xilinx | sudo -S env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 \
  pl_motion_detection_optimized.py --camera 0 --display 0
```

## 7. PC 端测试

```bash
cd software
cd tests && python -m pytest -q        # 单元测试
cd ../tracking && python -m pytest -q  # 跟踪模块测试
```

## 8. PL-PS 接口协议

### 8.1 数据格式

- **PS → PL**：每像素 32 bit，格式 `0x00RRGGBB`，分辨率固定 **320×240**。
- **PL → PS**：每像素 32 bit，低 8 位为二值掩码（`0` 或 `255`）。

### 8.2 AXI-Lite 地址映射

| IP | 基地址 | width 偏移 | height 偏移 | threshold 偏移 |
|---|---|---|---|---|
| `frame_diff` | `0x40000000` | `0x10` | `0x18` | — |
| `morphology` | `0x40010000` | `0x10` | `0x18` | — |
| `rgb2gray` | `0x40020000` | `0x10` | `0x18` | — |
| `threshold` | `0x40030000` | `0x10` | `0x18` | `0x20` |
| AXI GPIO（蜂鸣器） | `0x40040000` | — | — | — |
| AXI DMA | `0x41E00000` | — | — | — |

- `AP_CTRL` 偏移均为 `0x00`，写 `0x01` 启动 IP。
- 蜂鸣器控制：写 AXI GPIO `0x00` 寄存器 bit0，`1` 响，`0` 停。

## 9. 常见问题

| 现象 | 原因 / 解决 |
|---|---|
| `RuntimeError: No Devices Found` | SSH 非登录 shell 缺少 `XILINX_XRT=/usr`，命令前加 `env XILINX_XRT=/usr`。 |
| 加载 overlay 报权限错误 | 需要 root：`sudo -S env XILINX_XRT=/usr python3 ...` |
| `ModuleNotFoundError: No module named 'pynq'` | 必须使用 `/usr/local/share/pynq-venv/bin/python3`。 |
| 摄像头打不开 | 摄像头只能被一个进程占用；`/dev/video0` 需要 root。 |
| DMA 错误或蜂鸣器常响 | 立即停止，检查 bit/hwh 哈希、电源、摄像头是否被其他进程占用。 |

## 10. 重建位流

位流已包含，**不需要重建**。如需修改 PL：

1. 安装 Vivado 2025.2（Vitis HLS 已并入 `v++`，无需单独安装）。
2. 打开 `hardware/vivado/project_1intrusion_detection_optimized/project_1intrusion_detection_optimized.xpr`。
3. 按 `hardware/scripts/upgrade_in_place.tcl` 自动升级 IP 并核对 AXI 地址。
4. 运行 `hardware/scripts/build_2025_2.tcl` 综合、实现、生成 bit/hwh。

Windows 路径超过 260 字符时，先用 `subst X: <path>` 映射再打开工程。
