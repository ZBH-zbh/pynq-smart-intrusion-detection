# 优化 Overlay 交付说明

生成时间：2026-10-11  
目标器件：PYNQ-Z2，`xc7z020clg400-1`  
生成工具：Vivado 2025.2（4 个 HLS IP 由 `v++ --mode hls` 重建）

## 文件

- `intrusion_detection_optimized.bit`：优化 PL bitstream。
- `intrusion_detection_optimized.hwh`：与 bitstream 对应的硬件描述。两个文件必须保持相同主文件名并放在同一目录。
- `timing_summary.rpt`：实现后时序报告。
- `utilization.rpt`：实现后资源利用率报告。
- `SHA256SUMS.txt`：交付文件校验值。

## 生成验收

- Vivado：`Bitgen Completed Successfully`
- 时序：`All user specified timing constraints are met.`
- WNS：+0.227 ns
- 资源：LUT 3582 (6.73%)、FF 4607 (4.33%)、BRAM 27 (19.29%)

## 固定接口

| 模块 | AXI-Lite 基地址 |
|---|---:|
| frame_diff | `0x40000000` |
| morphology | `0x40010000` |
| rgb2gray | `0x40020000` |
| threshold | `0x40030000` |
| AXI GPIO（蜂鸣器） | `0x40040000` |
| AXI DMA | `0x41E00000` |

`AP_CTRL` 偏移均为 `0x00`，写 `0x01` 启动 IP。
