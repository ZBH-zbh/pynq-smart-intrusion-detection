# HLS 综合对比报告

更新时间：2026-09-13  
工具：Vitis HLS 2022.1  
器件：xc7z020clg400-1  
目标时钟：10 ns（100 MHz）

## 结论

R10/R11 的 HLS 综合目标已经达到：四个优化 IP 的像素循环均保持 II=1，HLS 估计 DSP 合计从 14 降到 2，减少 12 个（约 85.7%）；BRAM_18K 合计保持 66，不增加存储资源。

这些数据是 HLS 估计结果，不等同于 Vivado 实现后的全系统资源与时序。只有把优化 IP 集成到 Vivado 并完成综合/实现后，才能确认最终 bitstream 的真实结果。

## 逐 IP 数据

| IP | 版本 | BRAM_18K | DSP | FF | LUT | 估计周期/ns | 延迟/cycle | 启动间隔/cycle | II |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| rgb2gray | 基线 | 0 | 5 | 509 | 465 | 6.912 | undef | undef | 1 |
| rgb2gray | 优化 | 0 | 2 | 228 | 407 | 6.450 | 76806 | 76807 | 1 |
| frame_diff | 基线 | 64 | 3 | 409 | 417 | 6.912 | undef | undef | 1 |
| frame_diff | 优化 | 64 | 0 | 145 | 373 | 6.417 | 76803 | 76804 | 1 |
| threshold | 基线 | 0 | 3 | 486 | 472 | 6.912 | undef | undef | 1 |
| threshold | 优化 | 0 | 0 | 208 | 408 | 4.451 | 76803 | 76804 | 1 |
| morphology | 基线 | 2 | 3 | 958 | 936 | 7.031 | undef | undef | 1 |
| morphology | 优化 | 2 | 0 | 285 | 585 | 6.761 | 76804 | 76805 | 1 |

基线延迟显示 `undef` 是因为循环次数取决于运行时 width/height；不表示模块不能运行。优化版固定 320×240，因此工具能给出确定的完整帧延迟。

## 合计变化

| 资源 | 基线合计 | 优化合计 | 变化 | 变化比例 |
|---|---:|---:|---:|---:|
| BRAM_18K | 66 | 66 | 0 | 0% |
| DSP | 14 | 2 | -12 | -85.7% |
| FF | 2362 | 866 | -1496 | -63.3% |
| LUT | 2290 | 1773 | -517 | -22.6% |

HLS 资源估计不能简单替代 Vivado 全系统利用率，但足以证明动态帧尺寸乘法器已被消除。rgb2gray 保留的 2 个 DSP 来自灰度加权计算，不属于本轮要移除的 `width * height`。

## 性能解释

- 四个模块的内部像素循环 II 均为 1，因此每拍可接受一个像素。
- 优化版单帧启动间隔约 76804～76807 cycle，即 100 MHz 下约 0.768 ms。
- 四个 IP 在 AXI4-Stream 中级联运行，能够流水重叠，不能把四个单模块延迟简单相加作为端到端 PL 时间。
- 优化主要收益是资源、控制路径和鲁棒性；端到端 FPS 是否提高仍取决于 DMA、PS 打包、后处理和显示，必须上板实测。

## 接口兼容性

综合生成的驱动头文件确认：

- `AP_CTRL = 0x00`
- `WIDTH = 0x10`
- `HEIGHT = 0x18`
- threshold 的 `THRESH = 0x20`
- 顶层函数名、32 位 AXI4-Stream 和 AXI-Lite CTRL bundle 均保持不变。

这里只确认 IP 内部寄存器偏移。系统级 AXI 基地址仍由 Vivado Address Editor 决定，后续必须保持交接文档中的 `0x40000000`～`0x40030000` 不变。

## 产物与命令

- 综合入口：`hardware/scripts/hls_verification/run_optimized_ip_csynth.tcl`
- 完整日志：`hardware/scripts/hls_verification/optimized_ip_csynth_run.log`
- 综合工程：`hardware/scripts/hls_verification/build/optimized_csynth`
- 原始数据：`hardware/scripts/hls_verification/hls_csynth_comparison.csv`

运行命令（从仓库根目录）：

```powershell
& 'D:\Xilinx\Vitis_HLS\2022.1\bin\vitis_hls.bat' -f 'hardware/scripts/hls_verification/run_optimized_ip_csynth.tcl' -l 'optimized_ip_csynth_run.log'
```

脚本只执行 `csynth_design`，没有执行 `export_design`，没有修改 Vivado 工程，也没有生成 bitstream。

## 下一门禁

1. 导出四个优化 HLS IP 到新的工作区 IP 仓库，不覆盖队长原 IP。
2. 复制或另存 Vivado 工程，更新副本中的四个 IP。
3. 核对 AXI 基地址、流连接、时钟、复位和 XDC。
4. 经确认后执行 Vivado 综合和实现；再次确认后才生成 bitstream。
