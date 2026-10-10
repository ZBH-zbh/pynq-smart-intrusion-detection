#!/usr/bin/env python3
"""
生成项目系统架构图 PNG
运行方式：python draw_architecture.py
"""

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

fig, ax = plt.subplots(1, 1, figsize=(14, 8))
ax.set_xlim(0, 14)
ax.set_ylim(0, 10)
ax.axis('off')

# 颜色
color_camera = '#E3F2FD'
color_ps = '#FFF3E0'
color_pl = '#F3E5F5'
color_alarm = '#FFEBEE'
color_display = '#E8F5E9'
color_text = '#333333'

def draw_box(ax, x, y, w, h, text, color, fontsize=11):
    box = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.03,rounding_size=0.2",
                         facecolor=color, edgecolor='#666666', linewidth=1.5)
    ax.add_patch(box)
    ax.text(x + w/2, y + h/2, text, ha='center', va='center',
            fontsize=fontsize, color=color_text, weight='bold')

def draw_arrow(ax, x1, y1, x2, y2, label=''):
    arrow = FancyArrowPatch((x1, y1), (x2, y2),
                            arrowstyle='-|>', mutation_scale=15,
                            color='#555555', linewidth=2)
    ax.add_patch(arrow)
    if label:
        mid_x = (x1 + x2) / 2
        mid_y = (y1 + y2) / 2
        ax.text(mid_x, mid_y + 0.2, label, ha='center', va='bottom',
                fontsize=9, color='#555555',
                bbox=dict(boxstyle='round,pad=0.2', facecolor='white', edgecolor='none', alpha=0.8))

# 摄像头
draw_box(ax, 0.5, 6.5, 2.5, 1.5, 'USB 摄像头\n罗技 C270i', color_camera)

# PS 框
ps_box = FancyBboxPatch((3.5, 5), 4, 4, boxstyle="round,pad=0.05,rounding_size=0.3",
                        facecolor='#FFF8E1', edgecolor='#FF9800', linewidth=2, linestyle='--')
ax.add_patch(ps_box)
ax.text(5.5, 8.7, 'ARM 处理系统 PS', ha='center', va='center',
        fontsize=13, color='#E65100', weight='bold')

# PS 内部模块
draw_box(ax, 4, 6.5, 3, 1.2, 'Python + OpenCV\n图像采集与显示', color_ps)
draw_box(ax, 4, 5.2, 3, 1, '入侵判断 / 报警控制\n目标画框 / 人数统计', color_ps)

# PL 框
pl_box = FancyBboxPatch((8.5, 5), 4.5, 4, boxstyle="round,pad=0.05,rounding_size=0.3",
                        facecolor='#F3E5F5', edgecolor='#9C27B0', linewidth=2, linestyle='--')
ax.add_patch(pl_box)
ax.text(10.75, 8.7, 'FPGA 可编程逻辑 PL', ha='center', va='center',
        fontsize=13, color='#6A1B9A', weight='bold')

# PL 内部模块
pl_modules = [
    ('RGB2Gray', 8.8, 7.5),
    ('帧差法', 8.8, 6.6),
    ('二值化', 11.2, 7.5),
    ('形态学处理', 11.2, 6.6),
    ('目标坐标输出', 10, 5.3),
]
for text, x, y in pl_modules:
    draw_box(ax, x, y, 1.8, 0.7, text, color_pl, fontsize=9)

# 输出模块
draw_box(ax, 3.5, 2, 2.5, 1.2, 'Grove Buzzer\n声音报警', color_alarm)
draw_box(ax, 6.5, 2, 2.5, 1.2, 'Jupyter Notebook\n实时显示', color_display)

# 箭头
draw_arrow(ax, 3, 7.25, 4, 7.1, 'USB')
draw_arrow(ax, 7, 7.1, 8.5, 7.1, 'AXI-Stream')
draw_arrow(ax, 10.75, 5, 10.75, 4.5, 'AXI-Lite')
draw_arrow(ax, 5.5, 5.2, 5.5, 4.2, '')
draw_arrow(ax, 6.5, 5.2, 7.75, 3.2, '')

# PL 内部箭头
internal_arrows = [
    (9.7, 7.5, 9.7, 7.3),
    (10.75, 7.5, 10.75, 7.3),
    (9.7, 6.6, 9.7, 6.0),
    (11.2, 6.6, 11.2, 6.0),
    (10.0, 6.0, 10.0, 5.3),
]
for x1, y1, x2, y2 in internal_arrows:
    arrow = FancyArrowPatch((x1, y1), (x2, y2),
                            arrowstyle='-|>', mutation_scale=10,
                            color='#9C27B0', linewidth=1.2)
    ax.add_patch(arrow)

# 标题
ax.text(7, 9.5, '智能入侵检测系统架构图', ha='center', va='center',
        fontsize=18, weight='bold', color='#1A237E')

plt.tight_layout()
plt.savefig('系统架构图.png', dpi=150, bbox_inches='tight',
            facecolor='white', edgecolor='none')
print("已生成 系统架构图.png")
