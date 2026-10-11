# PYNQ 最小部署目录

将本目录内 README 之外的 8 个运行文件全部上传到 `/home/xilinx/`。其中 bit/hwh 必须同名，6 个 Python 源码必须保持在同一目录，供模块间导入。

先校验：

```bash
cd /home/xilinx
sha256sum intrusion_detection_optimized.bit intrusion_detection_optimized.hwh
```

期望值：

```text
78e325fcb43c73fe2c45fbe6f0c21a86dcb2db1acc8a50384810f9c954771ee4  intrusion_detection_optimized.bit
7e5b68b2906ab9995801db006d94fa067833a727075d2588dd689c64a64be716  intrusion_detection_optimized.hwh
```

实时运行：

```python
%run /home/xilinx/pl_motion_detection_optimized.py
```

OpenCV 纯软件运行：

```python
%run /home/xilinx/opencv_software_motion.py --source 0 --display jupyter
```

正式无显示对比：

```bash
cd /home/xilinx
python3 pynq_compare_motion.py --bitstream /home/xilinx/intrusion_detection_optimized.bit --source synthetic --warmup 20 --frames 200 --repeats 3 --min-seconds 5 --opencv-threads 1 --dma-poll-sleep-us 100 --output-root /home/xilinx/pynq_results
```

已经在 PYNQ 3.0.1 上验证。出现哈希不符、DMA 错误或超时时停止运行，不要通过反复加载 overlay 掩盖故障。
