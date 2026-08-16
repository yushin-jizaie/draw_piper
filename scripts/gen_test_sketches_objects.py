#!/usr/bin/env python3
"""非人間 sketches (家・木・猫・車) を programmatic 生成する。

Level 2 multi-mode の検証用: 都市 mode / その他 mode が機能するかチェック。
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw


def _canvas(size: int = 1024):
    img = Image.new("RGB", (size, size), (255, 255, 255))
    return img, ImageDraw.Draw(img)


def sketch_house(size: int = 1024) -> Image.Image:
    img, draw = _canvas(size)
    cx, cy = size // 2, size // 2
    # 屋根 (三角)
    roof_y = cy - 150
    draw.line((cx - 200, roof_y + 100, cx, roof_y, cx + 200, roof_y + 100),
              fill=(0, 0, 0), width=3, joint="curve")
    # 壁 (四角)
    draw.rectangle((cx - 180, roof_y + 100, cx + 180, cy + 200),
                   outline=(0, 0, 0), width=3)
    # ドア
    draw.rectangle((cx - 40, cy + 60, cx + 40, cy + 200),
                   outline=(0, 0, 0), width=3)
    # 窓 2 つ
    draw.rectangle((cx - 130, roof_y + 150, cx - 70, roof_y + 220),
                   outline=(0, 0, 0), width=3)
    draw.rectangle((cx + 70, roof_y + 150, cx + 130, roof_y + 220),
                   outline=(0, 0, 0), width=3)
    return img


def sketch_tree(size: int = 1024) -> Image.Image:
    img, draw = _canvas(size)
    cx, cy = size // 2, size // 2
    # 幹 (縦線、 太め)
    draw.line((cx, cy + 250, cx, cy - 50), fill=(0, 0, 0), width=12)
    # 葉 (円)
    leaf_r = 140
    draw.ellipse((cx - leaf_r, cy - 50 - leaf_r,
                  cx + leaf_r, cy - 50 + leaf_r),
                 outline=(0, 0, 0), width=3)
    return img


def sketch_cat(size: int = 1024) -> Image.Image:
    img, draw = _canvas(size)
    cx, cy = size // 2, size // 2
    # 顔 (丸)
    r = 110
    draw.ellipse((cx - r, cy - r, cx + r, cy + r),
                 outline=(0, 0, 0), width=3)
    # 三角耳 2 つ
    ear_h = 70
    draw.polygon([(cx - 90, cy - 100), (cx - 50, cy - r - ear_h),
                  (cx - 30, cy - 95)], outline=(0, 0, 0), width=3)
    draw.polygon([(cx + 30, cy - 95), (cx + 50, cy - r - ear_h),
                  (cx + 90, cy - 100)], outline=(0, 0, 0), width=3)
    # 目 (2 dots)
    er = 8
    draw.ellipse((cx - 40 - er, cy - 20 - er, cx - 40 + er, cy - 20 + er), fill=(0, 0, 0))
    draw.ellipse((cx + 40 - er, cy - 20 - er, cx + 40 + er, cy - 20 + er), fill=(0, 0, 0))
    # 鼻 (小さい三角)
    draw.polygon([(cx - 10, cy + 10), (cx, cy + 30), (cx + 10, cy + 10)],
                 outline=(0, 0, 0), width=2)
    # ヒゲ (左右 3 本ずつ)
    for dy in (-5, 5, 15):
        draw.line((cx - 30, cy + 25 + dy, cx - 110, cy + 25 + dy * 1.5),
                  fill=(0, 0, 0), width=2)
        draw.line((cx + 30, cy + 25 + dy, cx + 110, cy + 25 + dy * 1.5),
                  fill=(0, 0, 0), width=2)
    return img


def sketch_car(size: int = 1024) -> Image.Image:
    img, draw = _canvas(size)
    cx, cy = size // 2, size // 2
    # 車体 (四角 + 上に台形)
    body_top = cy - 30
    body_bot = cy + 80
    draw.rectangle((cx - 250, body_top, cx + 250, body_bot),
                   outline=(0, 0, 0), width=3)
    # 屋根 (台形)
    draw.polygon([(cx - 150, body_top), (cx - 100, body_top - 80),
                  (cx + 100, body_top - 80), (cx + 150, body_top)],
                 outline=(0, 0, 0), width=3)
    # 窓
    draw.polygon([(cx - 140, body_top - 5), (cx - 95, body_top - 70),
                  (cx + 95, body_top - 70), (cx + 140, body_top - 5)],
                 outline=(0, 0, 0), width=2)
    # タイヤ 2 つ
    tire_r = 60
    draw.ellipse((cx - 200 - tire_r, body_bot - 30,
                  cx - 200 + tire_r, body_bot + tire_r * 2 - 30),
                 outline=(0, 0, 0), width=3)
    draw.ellipse((cx + 200 - tire_r, body_bot - 30,
                  cx + 200 + tire_r, body_bot + tire_r * 2 - 30),
                 outline=(0, 0, 0), width=3)
    return img


SKETCHES = {
    "house": sketch_house,
    "tree":  sketch_tree,
    "cat":   sketch_cat,
    "car":   sketch_car,
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--size", type=int, default=1024)
    args = ap.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, fn in SKETCHES.items():
        img = fn(args.size)
        out = args.out_dir / f"sketch_{name}.png"
        img.save(out)
        print(f"saved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
