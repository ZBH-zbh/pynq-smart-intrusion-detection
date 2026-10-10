# 复现指南

## 1. 硬件与软件环境

| 项目 | 版本 / 型号 |
|---|---|
| 开发板 | PYNQ-Z2 (`xc7z020clg400-1`) |
| PYNQ 镜像 | v3.0.1 |
| 摄像头 | 罗技 C270i（UVC） |
| 蜂鸣器 | Grove Buzzer，接 Grove Base Shield G1 |
| PC 工具链 | Vivado / Vitis HLS 2022.1（后续可迁移到 2025.2） |
| 板端 Python | 3.10.4（PYNQ venv） |
| 关键库 | OpenCV 4.5.4、NumPy 1.21.5、pynq 3.0.1 |

## 2. 硬件连接

1. PYNQ-Z2 上电（推荐 12V 圆头电源）。
2. 罗技 C270i 插入 PYNQ-Z2 USB 口。
3. Grove Base Shield 插在 Arduino 排针上，Grove Buzzer 接 **G1**（黄线 = 信号）。

## 3. 上传文件

把以下文件传到 `/home/xilinx/intrusion_demo/`：

```bash
# 核心 overlay 与 PS 脚本
scp hardware/overlay/intrusion_detection_optimized.bit \
   hardware/overlay/intrusion_detection_optimized.hwh \
   software/deploy/*.py \
   xilinx@<board-ip>:/home/xilinx/intrusion_demo/

# 跟踪 / Web 配置模块
scp software/tracking/{tracker_server.py,bg_detector.py,grouping.py,line_crossing.py,localisation.py,strip_merge.py,target_tracker.py,trail.py} \
   xilinx@<board-ip>:/home/xilinx/intrusion_demo/
```

## 4. 校验 bitstream

```bash
cd /home/xilinx/intrusion_demo
sha256sum intrusion_detection_optimized.bit intrusion_detection_optimized.hwh
```

期望输出：

```text
78e325fcb43c73fe2c45fbe6f0c21a86dcb2db1acc8a50384810f9c954771ee4  intrusion_detection_optimized.bit
7e5b68b2906ab9995801db006d94fa067833a727075d2588dd689c64a64be716  intrusion_detection_optimized.hwh
```

哈希不一致不要加载 overlay。

## 5. 运行实时演示

```bash
cd /home/xilinx/intrusion_demo
echo xilinx | sudo -S env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 \
  pl_motion_detection_optimized.py --camera 0 --display 0
```

或在 Jupyter Notebook 中：

```python
%run /home/xilinx/intrusion_demo/pl_motion_detection_optimized.py
```

预期效果：
- 实时画面左上角显示 `Targets / FPS / Normal|ALARM`。
- 手在镜头前移动时出现绿色目标框。
- 目标进入红色警戒区后，屏幕显示 `ALARM!`，蜂鸣器间歇响。

## 6. 运行跟踪 / Web 配置服务

```bash
cd /home/xilinx/intrusion_demo
echo xilinx | sudo -S env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 \
  tracker_server.py --source board --detector pl --port 8081
```

浏览器打开 `http://<board-ip>:8081`：
- 拖拽黄色方框调整警戒区。
- 拖拽量线两端调整越线位置/角度。
- 底部滑块调整轨迹消抖、平滑、历史长度。

停止服务：

```bash
sudo pkill -f tracker_server.py
```

## 7. OpenCV vs PL 正式对比

```bash
cd /home/xilinx/intrusion_demo
echo xilinx | sudo -S env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 \
  pynq_compare_motion.py \
  --source synthetic --warmup 20 --frames 200 --repeats 3 \
  --min-seconds 5 --opencv-threads 1 --dma-poll-sleep-us 100 \
  --output-root /home/xilinx/pynq_results
```

> 若当前目录下没有 `intrusion_detection_optimized.bit`，脚本会自动使用仓库中的
> `hardware/overlay/intrusion_detection_optimized.bit`；也可通过 `--bitstream` 显式指定。

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
| DMA 错误或蜂鸣器常响 | 立即停止，检查 bit/hwh 哈希、电源、摄像头是否被其他进程占用。 |

## 10. Vivado 版本迁移说明

当前 bitstream 由 **Vivado / Vitis HLS 2022.1** 生成。若迁移到赛事方推荐的 2025.2：

1. 在 2025.2 中打开 Vivado 工程，按提示升级 IP。
2. 重新综合、实现、生成 `.bit` 和 `.hwh`。
3. 更新本目录中的 bit/hwh 及 SHA-256 校验值。
4. 上板重新运行 benchmark，更新 [`hardware/reports/runtime_benchmark_report.md`](../hardware/reports/runtime_benchmark_report.md) 中的数据。
