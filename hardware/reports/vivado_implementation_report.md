# Vivado 实现对比报告

更新时间：2026-09-13  
工具：Vivado 2022.1  
器件：PYNQ-Z2，`xc7z020clg400-1`  
时钟目标：100 MHz（10 ns）

## 1. 结论

优化后的 4 个 HLS IP 已导出并替换到独立 Vivado 工程中。Block Design 验证通过，所有 AXI-Lite 地址保持不变。完成综合、布局布线和 Post-Route Physical Optimization 后，建立/保持时序均通过，路由错误为 0，DRC 没有 Error 或 Critical Warning。

与队长基线实现相比，优化工程减少 231 个 LUT、448 个寄存器和 13 个 DSP，BRAM 保持 27 Tile。经用户单独批准后，已从通过时序的 Post-Route Physical Optimization checkpoint 生成独立 bit/hwh；原基线工程和 bit/hwh 未被覆盖。

## 2. 工程隔离与 IP 状态

- 优化工程脚本：`hardware/scripts/vivado_optimization/`
- 优化 IP 仓库：`hardware/scripts/vivado_optimization/ip_repo_optimized/`
- 验证结果：`LOCKED_IP_COUNT=0`

4 个优化 IP 均以原 VLNV `xilinx.com:hls:<top>:1.0` 升级原 Block Design 实例，因此外围连接不变。

## 3. AXI-Lite 地址复核

| 模块 | 实际基地址 | 结果 |
|---|---:|---|
| frame_diff | `0x40000000` | 保持不变 |
| morphology | `0x40010000` | 保持不变 |
| rgb2gray | `0x40020000` | 保持不变 |
| threshold | `0x40030000` | 保持不变 |
| AXI GPIO | `0x40040000` | 保持不变 |
| AXI DMA | `0x41E00000` | 保持不变 |

这与交接文档及 MMIO 脚本使用的实际地址一致。

## 4. 实现资源对比

| 指标 | 队长基线 | 优化实现 | 变化 |
|---|---:|---:|---:|
| Slice LUTs | 3956 | 3725 | -231（-5.84%） |
| Slice Registers | 5143 | 4695 | -448（-8.71%） |
| Block RAM Tile | 27 | 27 | 0 |
| DSP Blocks | 16 | 3 | -13（-81.25%） |

优化实现中 4 个 HLS 实例的布局后资源为：

| HLS 实例 | LUT | FF | RAMB36 | RAMB18 | DSP |
|---|---:|---:|---:|---:|---:|
| frame_diff | 213 | 206 | 24 | 0 | 0 |
| morphology | 195 | 266 | 0 | 2 | 0 |
| rgb2gray | 175 | 233 | 0 | 0 | 3 |
| threshold | 205 | 247 | 0 | 0 | 0 |

注：HLS 综合估算的 rgb2gray 为 2 DSP，而 Vivado 最终映射为 3 DSP；最终验收以 Vivado 实现报告为准。系统级 DSP 从 16 降到 3，说明原先由动态 `width * height` 引入的乘法器已消除。

## 5. 时序、路由与 DRC

| 指标 | 队长基线 | 优化实现（Post-Route Phys Opt） | 判定 |
|---|---:|---:|---|
| WNS | +0.611 ns | +0.225 ns | 均通过 |
| TNS | 0 ns | 0 ns | 通过 |
| WHS | +0.031 ns | +0.031 ns | 通过 |
| THS | 0 ns | 0 ns | 通过 |
| 完整路由 | 8528/8528 | 7526/7526 | 通过 |
| 路由错误 | 0 | 0 | 通过 |
| DRC 违规总数 | 24 | 6 | 减少 18 |
| DRC Error/Critical | 0 | 0 | 通过 |

优化工程普通 `route_design` 后曾出现 WNS=-0.115 ns，关键路径位于 `frame_diff` 的前帧 BRAM 到输出寄存器。随后启用 Vivado 的 Post-Route Physical Optimization，WNS 改善 0.340 ns 至 +0.225 ns。最终报告使用物理优化后的状态。

剩余 6 条 DRC 为：PDCN-1569 Warning ×3、RTSTAT-10 Warning ×1、REQP-181 Advisory ×2；没有阻塞 bitstream 的 Error/Critical。基线中与 DSP 输出流水相关的 DPOP-1/2 共 18 条 Warning 在优化实现中消失。

## 6. 可复现命令与证据

在 Vivado 2022.1 命令环境中，从仓库根目录执行：

```powershell
& 'D:\Xilinx\Vivado\2022.1\bin\vivado.bat' -mode batch -source 'hardware/scripts/vivado_optimization/verify_prepared_project.tcl'
& 'D:\Xilinx\Vivado\2022.1\bin\vivado.bat' -mode batch -source 'hardware/scripts/vivado_optimization/run_synthesis_and_implementation.tcl'
& 'D:\Xilinx\Vivado\2022.1\bin\vivado.bat' -mode batch -source 'hardware/scripts/vivado_optimization/run_post_route_phys_opt.tcl'
```

主要证据：

- `hardware/scripts/vivado_optimization/verify_prepared_project.log`
- `hardware/scripts/vivado_optimization/run_synthesis_and_implementation.log`
- `hardware/scripts/vivado_optimization/run_post_route_phys_opt_retry.log`
- `hardware/reports/optimized_post_route_phys_opt/timing_summary.rpt`
- `hardware/reports/optimized_post_route_phys_opt/utilization.rpt`
- `hardware/reports/optimized_post_route_phys_opt/drc.rpt`
- `hardware/reports/optimized_post_route_phys_opt/route_status.rpt`
- `hardware/scripts/vivado_optimization/vivado_implementation_comparison.csv`

## 7. 限制与下一门禁

- 优化工程顶层综合使用了原工程携带的顶层增量检查点；4 个 HLS OOC 综合运行均重新生成，Vivado 层级资源也确认使用了优化 IP。脚本已补充清空 `INCREMENTAL_CHECKPOINT`，供后续从零复跑时使用。
- 优化 overlay 已生成在 `hardware/overlay/`，bit/hwh 使用相同文件名主干；Bitgen 报告 0 Error、0 Critical Warning。
- 板卡无法连接，因此尚不能做 overlay 加载、摄像头、蜂鸣器及真实 PL/OpenCV FPS、CPU 和一致性验证。
- 上板加载 overlay 前仍需用户单独确认，当前未执行任何板卡操作。
