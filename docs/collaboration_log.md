# 大模型协作记录

本记录保留开发过程中与大模型（Claude）协作时具有工程价值的部分：关键 Bug 的根因分析，以及性能优化的方法论。基础操作问答（镜像下载、UI 点击、设备选购等）已剔除。

---

## 1. 关键 Bug 根因分析

### 1.1 Jupyter 加载 overlay 报 “No Devices Found”

**现象**
- 在 Jupyter 中调用 `Overlay()` 时抛出 `RuntimeError: No Devices Found`。
- 检查 `/dev/xdevcfg`：不存在。
- 检查 `/sys/class/fpga_manager`：以 `sudo -i` 启动 Jupyter 后存在 `fpga0`。
- `dmesg` 中可看到 `fpga_manager fpga0: writing base.bin to Xilinx Zynq FPGA Manager`。

**排查过程**
1. 直接用 `sudo jupyter notebook --allow-root` 启动时出错。
2. 改用 `sudo -i` 进入完整 root shell，再启动 Jupyter，则 PYNQ 能正常找到 `fpga_manager`。

**根本原因**
`sudo jupyter notebook` 与 `sudo -i` 的环境变量差异：
- `sudo -i` 会加载 root 的完整 profile，包含 `XILINX_XRT=/usr` 等关键变量。
- `sudo jupyter notebook` 继承当前 shell 的环境，但可能丢失或错误设置这些变量，导致 PYNQ 无法枚举 FPGA manager 设备。

**结论**
需要 root 权限加载 overlay 时，使用完整 root shell：

```bash
sudo -i
cd /home/xilinx/jupyter_notebooks
jupyter notebook --ip=0.0.0.0 --allow-root
```

SSH 非登录 shell 也要显式导出环境变量：

```bash
env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 ...
```

---

## 2. 性能优化方法论

### 2.1 从 20.25 FPS 到 50.18 FPS 的 DMA/PS 优化

**背景**
早期把 rgb2gray、frame_diff、threshold、morphology 全部放到 PL 后，实测 FPS 只有 20.25，远低于预期。瓶颈不在 PL，而在 PS 与 DMA 之间的数据搬运和同步。

**优化方向**
1. **统一像素格式**：PS → PL 每像素 32 bit `0x00RRGGBB`，PL → PS 低 8 bit 为二值掩码，避免中间转换。
2. **DMA 轮询替代中断**：设置 `--dma-poll-sleep-us` 在 50–100 μs 之间，降低上下文切换开销。
3. **缓冲复用**：使用 `pynq.allocate` 一次性分配输入/输出缓冲区，每帧仅更新数据区。
4. **减少 PS 后处理**：形态学在 PL 完成后直接输出干净掩码，PS 只做轮廓检测与画框。
5. **合成帧 benchmark**：固定 320×240 合成帧消除摄像头帧率波动，保证 OpenCV vs PL 对比的公平性。

**结果**
- PL 方案从 20.25 FPS 提升到 **50.18 FPS**。
- 单核 CPU 从 ~100% 降至 **96.25%**。
- 与 OpenCV 纯软件方案（48.40 FPS）相比，功能等价且略有加速。

完整数据见 [`hardware/reports/runtime_benchmark_report.md`](../hardware/reports/runtime_benchmark_report.md)。

### 2.2 公平对比 OpenCV 与 PL 的设计

为了保证 OpenCV 纯软件与 PL 加速的对比有意义：
- 使用 **完全相同的合成帧序列**，避免摄像头抖动、光照变化带来的随机性。
- OpenCV 强制单线程（`cv2.setNumThreads(1)`），与 PL 版在同一 CPU 核心上竞争。
- 每项后端跑多轮，并交替顺序以抵消热启动效应。
- 用五项指标验证一致性：像素一致率、IoU、轮廓数、目标框 IoU、报警状态一致率。

### 2.3 形态学全上 PL 的验证版（参考数据）

在探索阶段，曾把形态学操作也完整固化到 PL 流水线，并配合更激进的 DMA 优化，达到了 **138.07 FPS（2.85×）**、端到端延迟 **7.20 ms**。该版本因工程复杂度和稳定性原因未纳入最终交付包，相关数据仅作为性能潜力参考。
