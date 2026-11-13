#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 `packaging/assets/SGS.ico`（exe 图标）与一张预览 PNG。

**原创图形**：朱砂印面 + 白字「殺」，不引用任何第三方素材（仓里 `pic/` 是 gitignored 的
本机图片，NOTICE 也说明素材版权归原权利人，故不能拿来做包里资产）。

字体只借用系统里**已存在**的楷体来栅格化一个字（渲染出来的位图不再受字体许可约束）：
Windows 走 `C:\\Windows\\Fonts\\STKAITI.TTF` / `simkai.ttf`，Linux 退 `Noto Serif CJK`。
找不到任何中文字体时退回"纯几何印面"，不产出豆腐块。

用法（在能跑 PIL 的机器上，WSL 里可以直接读 `\\mnt\\c\\Windows\\Fonts`）：

    python packaging/assets/make_icon.py [--out packaging/assets]

产物：
    SGS.ico            16/24/32/48/64/128/256 七档（Windows 资源管理器 / 任务栏够用）
    SGS-preview.png    256 预览，供人眼验收
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SEAL = (166, 54, 43)        # 朱砂
SEAL_DARK = (120, 34, 27)   # 印边（压暗一档）
PAPER = (247, 241, 227)     # 宣纸白
SIZES = (16, 24, 32, 48, 64, 128, 256)
MASTER = 1024
GLYPH = "殺"

FONT_CANDIDATES = (
    "/mnt/c/Windows/Fonts/STKAITI.TTF",
    "/mnt/c/Windows/Fonts/simkai.ttf",
    "/mnt/c/Windows/Fonts/FZSTK.TTF",
    "/mnt/c/Windows/Fonts/simhei.ttf",
    "/mnt/c/Windows/Fonts/msyh.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "C:/Windows/Fonts/STKAITI.TTF",
    "C:/Windows/Fonts/simkai.ttf",
)


def find_font() -> str | None:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return None


def draw_seal(size: int, font_path: str | None) -> Image.Image:
    """画一枚印章：圆角印面 + 内细边 + 居中白字。"""
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    margin = round(size * 0.035)
    radius = round(size * 0.16)
    draw.rounded_rectangle(
        (margin, margin, size - margin - 1, size - margin - 1),
        radius=radius,
        fill=SEAL,
        outline=SEAL_DARK,
        width=max(2, round(size * 0.012)),
    )

    # 印面内的一道细边：让缩到 16px 时仍看得出"这是一枚印"，而不是一块红。
    inset = round(size * 0.085)
    draw.rounded_rectangle(
        (inset, inset, size - inset - 1, size - inset - 1),
        radius=round(radius * 0.82),
        outline=PAPER,
        width=max(1, round(size * 0.008)),
    )

    if font_path is None:
        return canvas

    # 字号先按面积取，再用 textbbox 量一遍真实外框来居中（楷体的「殺」不是方块字）。
    target = round(size * 0.60)
    font = ImageFont.truetype(font_path, target)
    box = draw.textbbox((0, 0), GLYPH, font=font)
    width, height = box[2] - box[0], box[3] - box[1]
    if width == 0 or height == 0:  # 字体缺这个字：不画豆腐块，留纯印面
        return canvas
    scale = min((size * 0.56) / width, (size * 0.56) / height, 1.0)
    if scale < 1.0:
        font = ImageFont.truetype(font_path, max(8, round(target * scale)))
        box = draw.textbbox((0, 0), GLYPH, font=font)
        width, height = box[2] - box[0], box[3] - box[1]
    x = (size - width) / 2 - box[0]
    y = (size - height) / 2 - box[1]
    draw.text((x, y), GLYPH, font=font, fill=PAPER)
    return canvas


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 SGS 图标")
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    font_path = find_font()
    print(f"字体：{font_path or '（没有中文字体，退回纯几何印面）'}")

    master = draw_seal(MASTER, font_path)
    preview_path = args.out / "SGS-preview.png"
    master.resize((256, 256), Image.LANCZOS).save(preview_path)

    frames = [master.resize((size, size), Image.LANCZOS) for size in SIZES]
    ico_path = args.out / "SGS.ico"
    frames[-1].save(ico_path, format="ICO", sizes=[(s, s) for s in SIZES], append_images=frames[:-1])

    print(f"写出：{ico_path}（{ico_path.stat().st_size} 字节）")
    print(f"写出：{preview_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
