# 2022.1 回退 Overlay

生成时间：2026-09-13  
目标器件：PYNQ-Z2，`xc7z020clg400-1`  
生成工具：Vivado 2022.1

这是**唯一经过实机验收**的位流，作为 2025.2 新位流的回退。两个版本 AXI 地址完全一致，Python 代码无需修改。

## 文件

- `intrusion_detection_optimized.bit`
- `intrusion_detection_optimized.hwh`

## 固定接口

| 模块 | AXI-Lite 基地址 |
|---|---:|
| frame_diff | `0x40000000` |
| morphology | `0x40010000` |
| rgb2gray | `0x40020000` |
| threshold | `0x40030000` |
| AXI GPIO（蜂鸣器） | `0x40040000` |
| AXI DMA | `0x41E00000` |
