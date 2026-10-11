# 大模型协作记录

本记录保留开发过程中与大模型（Claude）协作时具有工程与学术价值的部分：关键 Bug 的根因分析、性能优化的方法论，以及 Vivado 2025.2 迁移的技术发现。基础操作问答、重复调试细节、泛泛的迭代记录已剔除。

---

## 1. 关键 Bug 根因分析

### 1.1 Jupyter / SSH 加载 overlay 报 “No Devices Found”

**现象**
- Jupyter 中调用 `Overlay()` 抛出 `RuntimeError: No Devices Found`。
- `/dev/xdevcfg` 不存在；以 `sudo -i` 启动 Jupyter 后 `/sys/class/fpga_manager/fpga0` 出现。

**根本原因**
PYNQ 的设备发现逻辑依赖 `XILINX_XRT` 环境变量：

```python
if "XILINX_XRT" in os.environ:
    from .embedded_device import EmbeddedDevice
    from .xrt_device import XrtDevice
```

`sudo -i` 会加载 `/etc/profile.d/xrt_setup.sh`（`export XILINX_XRT=/usr`），而 `sudo jupyter notebook` 继承当前 shell 环境，可能丢失该变量。SSH 的 `exec_command` 默认非登录 shell，同样不会加载 profile。

**结论**
- root 启动 Jupyter：`sudo -i` 后启动。
- SSH 跑板端脚本：命令前显式加 `env XILINX_XRT=/usr`。

---

### 1.2 实时画面周期性卡顿：DHCP 租约横跳

**现象**
- 画面流畅几秒后静止十几秒，循环往复。
- 蜂鸣器正常，说明内核循环未停。
- Jupyter 日志反复出现 `Replacing stale connection` / `Starting buffering`。

**排查过程**
- 排除 IOPub 速率限制、数据量、网卡节能、长连接等猜测。
- 在板子上每 2 秒采样 `ip -4 addr show eth0`，发现 IP 在 `192.168.137.125` 与 `192.168.137.61` 之间反复切换，甚至出现无地址阶段。

**根本原因**
`/etc/network/interfaces.d/eth0` 使用 DHCP。队友早期通过 Windows ICS 让板子拿过 `.61` 和 `.125` 两条租约；当前 PC 无 DHCP 服务器可续租，`dhclient` 在两条旧租约间反复尝试，每次切换都会拆掉/重加接口地址。

**修复**
将 `eth0` 改为静态 IP：

```bash
auto eth0
iface eth0 inet static
    address 192.168.137.125
    netmask 255.255.255.0
    broadcast 192.168.137.255
```

同时删除 `eth0:1` 别名与 `interfaces.d/` 内的备份文件，避免被 `source /etc/network/interfaces.d/*` 静默覆盖。

**验证**
修复前 60 秒轮询失败 5 次；修复后 426 次全部成功，最大帧间隔从 4015 ms 降至 148 ms。

---

### 1.3 背景模型误报：不要拿有损数据校准无损链路

**现象**
- 静止房间上线服务后持续误报、蜂鸣器长鸣。
- 离线重放同一录像却 0 误报，与线上行为系统性不一致。

**根因**
录制使用 JPEG q90，而 JPEG 压缩抹掉了约 97% 的脉冲噪声。离线回放看到的噪声水平远低于服务实际看到的原始帧，导致阈值/形态学参数在真实场景失效。

**结论**
- 算法输入必须无损；本工程后续改用 zlib 压缩 mask 做录像回放。
- 度量工具的配置必须与被度量系统一致。

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

---

### 2.2 公平对比 OpenCV 与 PL 的设计

为了保证 OpenCV 纯软件与 PL 加速的对比有意义：
- 使用 **完全相同的合成帧序列**，避免摄像头抖动、光照变化带来的随机性。
- OpenCV 强制单线程（`cv2.setNumThreads(1)`），与 PL 版在同一 CPU 核心上竞争。
- 每项后端跑多轮，并交替顺序以抵消热启动效应。
- 用五项指标验证一致性：像素一致率、IoU、轮廓数、目标框 IoU、报警状态一致率。

---

### 2.3 形态学全上 PL 的验证版（参考数据）

在探索阶段，曾把形态学操作也完整固化到 PL 流水线，并配合更激进的 DMA 优化，达到了 **138.07 FPS（2.85×）**、端到端延迟 **7.20 ms**。该版本因工程复杂度和稳定性原因未纳入最终交付包，相关数据仅作为性能潜力参考。

---

## 3. Vivado 2025.2 迁移的技术发现

### 3.1 2022.1 HLS IP 可直接在 2025.2 使用

迁移前预期 4 个自研 HLS IP 需要重新综合。实际验证：

```text
LOCKED_IP_BEFORE=6 → IPS_UPGRADED=6 → LOCKED_IP_AFTER=0
```

被升级的 6 个 IP 全是 Vivado 自带 IP（axi_dma、axi_gpio、axi_interconnect、proc_sys_reset、processing_system7）。**4 个 HLS IP 从未被锁**，2022.1 打包好的 IP 在 2025.2 里可直接使用。

### 3.2 AXI 地址完全不变

```text
ADDRESS_MISMATCHES=0
frame_diff 0x40000000  morphology 0x40010000
rgb2gray   0x40020000  threshold  0x40030000
axi_gpio   0x40040000  axi_dma    0x41E00000
```

因此 Python 代码一行未改即可跑通新位流。

### 3.3 目录与 zip 必须并存

把新 IP 的 zip 放进 `ip_repo_optimized` 并删掉原解压目录后，BD 报 `IP definition not found`。实测 `get_ipdefs` 在只有 zip 时查不到任何 IP；解压成同名目录后才正常加载。原因是 BD 中每个 HLS 实例钉着 `ip_revision`，同 VLNV 换内容后 revision 改变，必须重新 `upgrade_bd_cells`。

### 3.4 Windows 260 字符路径限制

2025.2 综合阶段写 checkpoint 时触发：

```text
ERROR: [Common 17-680] Path length exceeds 260-Byte maximum allowed by Windows
```

即使注册表已启用 `LongPathsEnabled`，Vivado 仍做 260 字符检查。官方解法是用 `subst X: <path>` 映射短路径打开工程，完成后再还原。

### 3.5 v++ 替代 Vitis HLS

2025.2 把 Vitis HLS 并入 `v++`：

```bash
v++ -c --mode hls --config hls_config.cfg
```

4 个 IP 重建无需单独安装 Vitis HLS，每个约 1.5 分钟。

---

## 4. 跟踪层算法设计取舍

### 4.1 帧差法输出的是“物体的两条边”

帧差只保留变化像素，纯色目标移动时前后沿重叠部分被减掉，只剩前沿和后沿两条细边。一帧示例：

```text
真实目标: (23, 65, 57, 72)
检测框  : [(17, 67, 6, 69), (73, 67, 6, 69)]
```

这导致速度/方向/计数都建立在错误框上。`strip_merge.py` 按垂直方向重叠度合并，把召回率从 0.027 提升到 0.746。

### 4.2 定位点晃动：平滑不是答案

早期用外接框中心做定位点，每帧抖动严重。尝试 EMA / 最小二乘拟合 / 中值滤波后，发现所有时间平滑都被滞后主导，对快速曲线运动反而更差。最终改为 **像素质心**（`cv2.moments`），零滞后且细长突起的影响降低为外接框中心的 1/5。

### 4.3 越线计数：同一物体被计两次

帧差链路把同一运动物体报成前沿/后沿两个目标，跟踪器给不同 ID，导致一人过线被计两次。计数器层按“不同轨迹 + 同方向 + 0.8 s 内 + 45 px 内”去重；后续 `grouping.py` 进一步按“间距恒定 + 速度一致”证明两条轨迹属于同一刚体。
