# HLS 优化源码

该目录包含最终交付的 4 个 HLS IP 源码，固定分辨率为 320×240。

- 不再使用运行时 `width * height` 作为循环边界，消除四个动态乘法器。
- `width` 或 `height` 不等于 320×240 时，仍消费并输出固定 76800 个像素，但输出数据清零，避免写入帧缓冲区之外。
- morphology 保持 3×3 腐蚀、右下锚点以及前两行/列清零的行为。

## 文件

| 文件 | 说明 |
|---|---|
| `rgb2gray.cpp` | RGB 转灰度 |
| `frame_diff.cpp` | 帧差法 |
| `threshold.cpp` | 二值化阈值 |
| `morphology.cpp` | 3×3 腐蚀 |

## 验证

HLS C 仿真与综合脚本见 [`hardware/scripts/hls_verification/`](../scripts/hls_verification/)。
