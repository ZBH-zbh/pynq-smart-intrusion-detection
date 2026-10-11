# PYNQ-Z2 智能入侵检测系统

基于 **AMD/Xilinx PYNQ-Z2** 的实时运动目标检测与入侵报警边缘节点。PL（FPGA）完成视频预处理加速，PS（ARM + Python）运行目标跟踪、越线计数、Web 配置与声光报警。

---

## 核心指标（PYNQ-Z2 实测）

| 指标 | OpenCV 纯软件 | 优化 PL | 说明 |
|---|---|---|---|
| 平均 FPS | 48.40 | 50.18 | 固定 320×240 合成帧（2022.1 位流） |
| 相对加速比 | 1.00× | 1.04× | 功能等价，CPU 略降 |
| 单核 CPU | 99.98% | 96.25% | PL 分担部分预处理 |
| 五项效果一致性 | — | 全部通过 | 掩码像素一致率 > 0.9999，IoU > 0.998 |

> 默认交付位流已升级到 **Vivado 2025.2**，接口地址与 2022.1 完全一致，Python 代码无需修改。2022.1 实机验收版保留在 `hardware/overlay_2022_1/` 作回退。

完整性能报告见 [`hardware/reports/runtime_benchmark_report.md`](hardware/reports/runtime_benchmark_report.md)。

---

## 系统架构

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

### 1. 环境自检

```powershell
cd <仓库根目录>
powershell -ExecutionPolicy Bypass -File .\check_env.ps1
```

### 2. 安装部署依赖

```bash
pip install paramiko
```

### 3. 一键部署到 PYNQ

```bash
cd tools
python deploy_tracker.py board pl
```

脚本会：
- 通过 SSH 连接 `192.168.137.125`
- 上传 `software/tracking/` 中的跟踪模块
- 上传 `hardware/overlay/` 中的 2025.2 位流
- 在板子上跑 312 项离线测试（约 6.5 分钟）
- 启动 `tracker_server.py --source board --detector pl --port 8081`

浏览器打开 `http://192.168.137.125:8081` 即可拖拽警戒区、量线，查看跟踪与越线计数。

详细步骤见 [`docs/reproduction.md`](docs/reproduction.md)。

---

## 仓库结构

```text
.
├── docs/
│   ├── architecture.md            # 系统架构说明
│   ├── collaboration_log.md       # 大模型协作记录（精简版）
│   └── reproduction.md            # 复现指南与接口协议
├── hardware/
│   ├── hls/optimized/       # 4 个 HLS IP 源码
│   ├── overlay/             # 2025.2 默认位流（bit + hwh）
│   ├── overlay_2022_1/      # 2022.1 回退位流
│   ├── vivado/              # Vivado 工程（.xpr / .bd / ip_repo）
│   ├── scripts/             # HLS / Vivado 重建脚本
│   └── reports/             # HLS、Vivado、运行时报告
├── software/
│   ├── deploy/              # 上板最小部署集
│   ├── tracking/            # 跟踪、越线、Web 配置、录制回放
│   └── tests/               # 单元测试
├── tools/                   # 部署与辅助脚本
├── check_env.ps1            # Windows 环境自检脚本
├── reproduction_results/    # 实测结果与截图
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
