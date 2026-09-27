# -*- coding: utf-8 -*-
"""生成 exe 用的亮度主题图标。

程序化绘制，不依赖外部图片资源。需求：Pillow。

绘制策略：
- 先在 8 倍尺寸下画一遍再 LANCZOS 缩到目标尺寸（超采样抗锯齿）
- 小尺寸单独简化：16px 只留一个实心圆点，否则射线会糊成一团

用法：
    python make_icon.py [输出路径]
默认输出同目录下的 icon.ico。
"""

import math
import os
import sys

try:
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover
    sys.stderr.write("需要 Pillow：pip install pillow\n")
    raise

# 与 brightness.py 的界面同一套色系
BG_COLOR = (35, 39, 46, 255)      # 深色圆角底 #23272e
SUN_COLOR = (255, 201, 60, 255)   # 暖黄太阳 #ffc93c

# ICO 内嵌尺寸。256 是 Vista 以上的大图标，16/32 是任务栏与资源管理器列表
ICO_SIZES = (16, 20, 24, 32, 48, 64, 128, 256)

SUPERSAMPLE = 8  # 超采样倍数


def draw_icon(size):
    """按目标尺寸画一版图标，返回 RGBA Image。"""
    s = size * SUPERSAMPLE
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 圆角底板
    margin = s * 0.055
    d.rounded_rectangle(
        [margin, margin, s - margin, s - margin],
        radius=s * 0.215,
        fill=BG_COLOR,
    )

    cx = cy = s / 2.0

    # 尺寸越小，射线越容易糊，按档位简化
    if size <= 20:
        rays, sun_r = 0, 0.225
    elif size <= 40:
        rays, sun_r = 4, 0.175
    else:
        rays, sun_r = 8, 0.155

    # 中心太阳
    r = s * sun_r
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=SUN_COLOR)

    if not rays:
        return img.resize((size, size), Image.LANCZOS)

    # 环绕射线
    r_in = s * 0.245
    r_out = s * 0.345
    width = s * (0.075 if size <= 40 else 0.052)

    for i in range(rays):
        angle = math.radians(i * (360.0 / rays) - 90.0)
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        x1, y1 = cx + r_in * cos_a, cy + r_in * sin_a
        x2, y2 = cx + r_out * cos_a, cy + r_out * sin_a

        d.line([x1, y1, x2, y2], fill=SUN_COLOR, width=int(width))

        # 两端补圆点做出圆头效果
        half = width / 2.0
        for px, py in ((x1, y1), (x2, y2)):
            d.ellipse([px - half, py - half, px + half, py + half], fill=SUN_COLOR)

    return img.resize((size, size), Image.LANCZOS)


def build(out_path=None):
    """生成 icon.ico，返回输出路径。"""
    if out_path is None:
        out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")

    # Pillow 的 ICO 插件从一个基准图缩出各尺寸，用最大的那张当基准
    base = draw_icon(max(ICO_SIZES))
    base.save(out_path, format="ICO", sizes=[(n, n) for n in ICO_SIZES])
    return out_path


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    path = build(target)
    print("已生成:", path, "(%.1f KB)" % (os.path.getsize(path) / 1024.0))
