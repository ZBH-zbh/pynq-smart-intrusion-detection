# 环境自检脚本 —— PYNQ-Z2 智能入侵检测工程（仓库版）
#
# 用法：powershell -ExecutionPolicy Bypass -File .\check_env.ps1
# 只读检查，不修改任何文件。
# 不需要装 Vivado：位流已经在仓库里。

$ErrorActionPreference = 'Continue'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pass = 0; $fail = 0; $todo = 0

function Ok  ($m) { Write-Host "  [OK]   $m" -ForegroundColor Green;  $script:pass++ }
function Bad ($m) { Write-Host "  [FAIL] $m" -ForegroundColor Red;    $script:fail++ }
function Tbd ($m) { Write-Host "  [TODO] $m" -ForegroundColor Yellow; $script:todo++ }
function Info($m) { Write-Host "  [--]   $m" -ForegroundColor Gray }

Write-Host "`n===== 0. 仓库位置 =====" -ForegroundColor Cyan
Info "脚本目录: $root"

Write-Host "`n===== 1. 关键交付文件 =====" -ForegroundColor Cyan
$need = @(
    'hardware\overlay\intrusion_detection_optimized.bit',
    'hardware\overlay\intrusion_detection_optimized.hwh',
    'hardware\overlay_2022_1\intrusion_detection_optimized.bit',
    'hardware\overlay_2022_1\intrusion_detection_optimized.hwh',
    'software\deploy\pl_motion_detection_optimized.py',
    'software\deploy\motion_common.py',
    'software\tracking\localisation.py',
    'software\tracking\tracker_server.py',
    'software\tracking\target_tracker.py',
    'software\tracking\line_crossing.py',
    'software\tracking\grouping.py',
    'tools\deploy_tracker.py',
    'hardware\hls\optimized\rgb2gray.cpp',
    'hardware\hls\optimized\frame_diff.cpp',
    'hardware\hls\optimized\threshold.cpp',
    'hardware\hls\optimized\morphology.cpp',
    'hardware\scripts\build_2025_2.tcl',
    'hardware\scripts\run_optimized_ip_csim.tcl'
)
foreach ($n in $need) {
    if (Test-Path (Join-Path $root $n)) { Ok $n } else { Bad "缺失: $n" }
}

Write-Host "`n===== 2. 位流哈希 =====" -ForegroundColor Cyan
$expected = @{
    'hardware\overlay\intrusion_detection_optimized.bit' =
        'b93031cc4d6d65f8ff9c1fa7650e89771315f4bd7ef1dac63f19a7c92c4f77ea'
    'hardware\overlay\intrusion_detection_optimized.hwh' =
        '6933fdf5cac0376bb921490511320a0739a85b76877db61e06aa2539840092b2'
    'hardware\overlay_2022_1\intrusion_detection_optimized.bit' =
        '78e325fcb43c73fe2c45fbe6f0c21a86dcb2db1acc8a50384810f9c954771ee4'
    'hardware\overlay_2022_1\intrusion_detection_optimized.hwh' =
        '7e5b68b2906ab9995801db006d94fa067833a727075d2588dd689c64a64be716'
}
foreach ($rel in $expected.Keys) {
    $f = Join-Path $root $rel
    if (-not (Test-Path $f)) { Bad "缺失: $rel"; continue }
    $h = (Get-FileHash $f -Algorithm SHA256).Hash.ToLower()
    if ($h -eq $expected[$rel]) {
        Ok "$(Split-Path $rel -Leaf) 哈希匹配"
    } else {
        Bad "$rel 哈希不符`n           实际 $h`n           期望 $($expected[$rel])"
    }
}

Write-Host "`n===== 3. 冻结文件（不要改）=====" -ForegroundColor Cyan
$frozen = @{
    'software\deploy\pl_motion_detection_optimized.py' = '95c16283bedfb596'
    'software\deploy\motion_common.py'                 = 'e962d02ae0c82882'
    'software\tracking\localisation.py'                 = 'b869b2d67d6d1493'
}
foreach ($rel in $frozen.Keys) {
    $f = Join-Path $root $rel
    if (-not (Test-Path $f)) { Bad "缺失: $rel"; continue }
    $h = (Get-FileHash $f -Algorithm SHA256).Hash.ToLower().Substring(0, 16)
    if ($h -eq $frozen[$rel]) { Ok "$(Split-Path $rel -Leaf) 未被修改" }
    else { Bad "$(Split-Path $rel -Leaf) 已被修改（$h ≠ $($frozen[$rel])）—— 这是冻结文件！" }
}

Write-Host "`n===== 4. Python 环境 =====" -ForegroundColor Cyan
$py = $null
foreach ($cand in @(
    (Join-Path $root 'venv\Scripts\python.exe'),
    (Join-Path $root '.venv\Scripts\python.exe')
)) { if (Test-Path $cand) { $py = $cand; break } }
if (-not $py) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $py = $cmd.Source }
}
if ($py) {
    Ok "Python: $py"
    $v = & $py -c "import sys;print(sys.version.split()[0])" 2>&1
    Info "版本 $v"
    $mods = & $py -c "import cv2,numpy;print('cv2',cv2.__version__,'numpy',numpy.__version__)" 2>&1
    if ($LASTEXITCODE -eq 0) { Ok "依赖可导入: $mods" } else { Tbd "缺 cv2/numpy（PC 测试用）: $mods" }
    $pm = & $py -c "import paramiko;print('paramiko',paramiko.__version__)" 2>&1
    if ($LASTEXITCODE -eq 0) { Ok "paramiko 可用（部署需要）: $pm" }
    else { Tbd "缺 paramiko（部署到板子需要）: pip install paramiko" }
} else {
    Tbd "没找到 Python。只在电脑上跑测试才需要，部署到板子不需要。"
}

Write-Host "`n===== 5. Xilinx 工具链（可选）=====" -ForegroundColor Cyan
$vivado = Get-Command vivado -ErrorAction SilentlyContinue
if ($vivado) { Info "PATH 上有 Vivado: $($vivado.Source)" }
else { Info "没有 Vivado —— 这是正常的，跑板子不需要它。" }
Info "只有「自己想重新构建位流」时才需要 Vivado 2025.2。"

Write-Host "`n===== 汇总 =====" -ForegroundColor Cyan
Write-Host "  通过 $pass 项 / 失败 $fail 项 / 待办 $todo 项"
if ($fail -eq 0) { Write-Host "  环境自检通过。`n" -ForegroundColor Green }
else { Write-Host "  存在失败项，请先处理。`n" -ForegroundColor Red }
