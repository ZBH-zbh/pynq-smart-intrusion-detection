# PYNQ-Z2 智能入侵检测系统

基于 **AMD/Xilinx PYNQ-Z2** 的实时运动目标检测与入侵报警边缘节点。PL（FPGA）完成视频预处理加速，PS（ARM + Python）运行目标跟踪、越线计数、Web 配置与声光报警。

---

## 核心指标（PYNQ-Z2 实测）

| 指标 | OpenCV 纯软件 | 优化 PL | 说明 |
|---|---|---|---|
| 平均 FPS | 48.40 | 50.18 | 固定 320×240 合成帧 |
| 相对加速比 | 1.00× | 1.04× | 功能等价，CPU 略降 |
| 单核 CPU | 99.98% | 96.25% | PL 分担部分预处理 |
| 五项效果一致性 | — | 全部通过 | 掩码像素一致率 > 0.9999，IoU > 0.998 |

完整性能报告见 [`hardware/reports/runtime_benchmark_report.md`](hardware/reports/runtime_benchmark_report.md)。

> 形态学全上 PL 的验证版曾达到 **138.07 FPS（2.85×）**，但该版本 bitstream 未包含在本交付包中，数据保留备查。

---

## 系统架构

系统框图见 [`docs/architecture.md`](docs/architecture.md)，源码在 [`assets/draw_architecture.py`](assets/draw_architecture.py)。

```text
USB 摄像头 (罗技 C270i, 320×240)
        │
        ▼
┌─────────────────────────────┐
│  PYNQ-Z2 ARM PS (Python)    │
│  - OpenCV 采集 / 打包       │
│  - AXI DMA 下发 / 回读      │
│  - 目标跟踪 / 越线计数      │
│  - 入侵判断 / 蜂鸣器 / Web  │
└──────────┬──────────────────┘
           │ AXI-Lite / AXI-Stream
           ▼
┌─────────────────────────────┐
│  PYNQ-Z2 FPGA PL            │
│  rgb2gray → frame_diff      │
│  → threshold → morphology   │
│  (固定 320×240，0x00RRGGBB) │
└─────────────────────────────┘
```

---

## 快速开始

### 1. 上传到 PYNQ

把 [`hardware/overlay/`](hardware/overlay/) 中的 bit/hwh 与 [`software/deploy/`](software/deploy/) 中的 Python 传到板卡 `/home/xilinx/intrusion_demo/`：

```bash
scp hardware/overlay/intrusion_detection_optimized.bit \
   hardware/overlay/intrusion_detection_optimized.hwh \
   software/deploy/*.py \
   xilinx@<board-ip>:/home/xilinx/intrusion_demo/
```

### 2. 运行实时演示

```bash
ssh xilinx@<board-ip>
cd /home/xilinx/intrusion_demo
echo xilinx | sudo -S env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 \
  pl_motion_detection_optimized.py --camera 0 --display 0
```

或在 Jupyter Notebook 中：

```python
%run /home/xilinx/intrusion_demo/pl_motion_detection_optimized.py
```

### 3. 运行跟踪 / Web 配置服务

先上传 [`software/tracking/`](software/tracking/) 中的核心模块：

```bash
scp software/tracking/{tracker_server.py,bg_detector.py,grouping.py,line_crossing.py,localisation.py,strip_merge.py,target_tracker.py,trail.py} \
   xilinx@<board-ip>:/home/xilinx/intrusion_demo/
```

再启动服务：

```bash
cd /home/xilinx/intrusion_demo
echo xilinx | sudo -S env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 \
  tracker_server.py --source board --detector pl --port 8081
```

浏览器打开 `http://<board-ip>:8081` 即可拖拽警戒区、量线，查看跟踪与越线计数。

详细步骤见 [`docs/reproduction.md`](docs/reproduction.md)。

---

## 仓库结构

```text
.
├── assets/                  # 架构图源文件
├── docs/
│   ├── architecture.md            # 系统架构图
│   ├── reproduction.md            # 复现指南与接口协议
│   ├── collaboration_log.md       # 大模型协作记录（精简版）
│   └── poster.md                  # 比赛海报内容
├── hardware/
│   ├── hls/optimized/       # 4 个 HLS IP 源码
│   ├── overlay/             # 最终 bitstream + hwh
│   ├── scripts/             # HLS / Vivado 重建脚本
│   └── reports/             # HLS、Vivado、运行时报告
├── software/
│   ├── deploy/              # 上板最小部署集
│   ├── tracking/            # 跟踪、越线、Web 配置
│   └── tests/               # 单元测试
├── tools/                   # 部署与辅助脚本
├── LICENSE
└── README.md
```

---

## 关键地址映射

| 模块 | 基地址 |
|---|---|
| `frame_diff` | `0x40000000` |
| `morphology` | `0x40010000` |
| `rgb2gray` | `0x40020000` |
| `threshold` | `0x40030000` |
| AXI GPIO（蜂鸣器） | `0x40040000` |
| AXI DMA | `0x41E00000` |

更多接口细节见 [`docs/reproduction.md`](docs/reproduction.md)。

---

## 许可证

本项目采用 [MIT 许可证](LICENSE) 开源。

---

## 致谢

- AMD/Xilinx 学生开发竞赛
- PYNQ 开源社区
