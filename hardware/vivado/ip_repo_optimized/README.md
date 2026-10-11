# 优化 HLS IP 仓库

该目录仅存放由 `D:\FPGA2.0\pl\pl\hls_optimized` 导出的优化 IP，和队长原仓库 `D:\FPGA2.0\pl\pl\vivado\ip_repo` 分离。

四个 IP 保持原 VLNV：

- `xilinx.com:hls:rgb2gray:1.0`
- `xilinx.com:hls:frame_diff:1.0`
- `xilinx.com:hls:threshold:1.0`
- `xilinx.com:hls:morphology:1.0`

Vivado 优化工程副本只应把本目录加入 `ip_repo_paths`，避免与原仓库中同名同版本 IP 混用。
