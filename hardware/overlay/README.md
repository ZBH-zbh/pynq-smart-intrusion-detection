# 优化 Overlay 交付说明

生成时间：2026-09-13  
目标器件：PYNQ-Z2，`xc7z020clg400-1`  
生成工具：Vivado 2022.1

## 文件

- `intrusion_detection_optimized.bit`：优化 PL bitstream。
- `intrusion_detection_optimized.hwh`：与 bitstream 对应的硬件描述。两个文件必须保持相同主文件名并放在同一目录。
- `timing_summary_before_bitstream.rpt`：写 bitstream 前的时序复核。
- `drc_before_bitstream.rpt`：写 bitstream 前的 DRC 复核。
- `SHA256SUMS.txt`：交付文件校验值。

## 生成验收

- Vivado：`Bitgen Completed Successfully`
- bitstream 大小：4,045,676 字节
- WNS：+0.225 ns
- WHS：+0.031 ns
- DRC：0 Error、0 Critical Warning、4 Warning、2 Advisory

## 固定接口

| 模块 | AXI-Lite 基地址 |
|---|---:|
| frame_diff | `0x40000000` |
| morphology | `0x40010000` |
| rgb2gray | `0x40020000` |
| threshold | `0x40030000` |
| AXI GPIO | `0x40040000` |
| AXI DMA | `0x41E00000` |

分辨率固定 320×240。PS→PL 为每像素 32-bit `0x00RRGGBB`；PL→PS 二值掩码低 8 位有效。

## 上板方式

将本目录中的 `.bit` 与 `.hwh` 复制到板卡 `/home/xilinx/intrusion_demo/`，
再复制 `software/deploy/*.py` 与 `software/tracking/` 中的核心模块，
然后按 [`docs/reproduction.md`](../../docs/reproduction.md) 运行。

2026-09-14 已完成上板验收：bit/hwh 哈希匹配，overlay 下载、DMA、摄像头、检测框、报警、G1 蜂鸣器和资源释放均正常。正式 benchmark 结果见 [`hardware/reports/runtime_benchmark_report.md`](../reports/runtime_benchmark_report.md)。
