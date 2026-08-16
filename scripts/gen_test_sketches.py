#!/usr/bin/env python3
"""様々な input sketch を programmatic に生成して two-stage pipeline 検証用に。

ユーザのスケッチ自体は多様で、 個別に絵柄/位置/サイズが違う。 pipeline が
入力多様性に robust か確認する。
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw


def _new_canvas(size: int = 1024, bg: int = 255) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (size, size), (bg, bg, bg))
    return img, ImageDraw.Draw(img)


def sketch_a_oval_2dots(size: int = 1024) -> Image.Image:
    """A: original test_sketch.jpg 互換。 中央 oval + 2 dots (目)。"""
    img, draw = _new_canvas(size)
    cx, cy = size // 2, size // 2 - 30
    # 顔輪郭 (oval)
    rx, ry = int(size * 0.10), int(size * 0.13)
    draw.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), outline=(0, 0, 0), width=3)
    # 目 (2 dots)
    eye_r = int(size * 0.012)
    draw.ellipse((cx - 35 - eye_r, cy - eye_r, cx - 35 + eye_r, cy + eye_r), fill=(0, 0, 0))
    draw.ellipse((cx + 35 - eye_r, cy - eye_r, cx + 35 + eye_r, cy + eye_r), fill=(0, 0, 0))
    return img


def sketch_b_round_with_mouth(size: int = 1024) -> Image.Image:
    """B: 丸顔 + 目 + 口 (smiley face)。 顔のディテール多め。"""
    img, draw = _new_canvas(size)
    cx, cy = size // 2, size // 2 - 50
    r = int(size * 0.13)
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(0, 0, 0), width=3)
    # 目
    er = int(size * 0.012)
    draw.ellipse((cx - 40 - er, cy - 10 - er, cx - 40 + er, cy - 10 + er), fill=(0, 0, 0))
    draw.ellipse((cx + 40 - er, cy - 10 - er, cx + 40 + er, cy - 10 + er), fill=(0, 0, 0))
    # 口 (smile)
    mw, mh = int(size * 0.05), int(size * 0.025)
    draw.arc((cx - mw, cy + 20 - mh, cx + mw, cy + 20 + mh),
             start=20, end=160, fill=(0, 0, 0), width=3)
    return img


def sketch_c_face_with_neck(size: int = 1024) -> Image.Image:
    """C: 顔 oval + 簡素な首 + 肩のヒント。 体の位置を input で示唆。"""
    img, draw = _new_canvas(size)
    cx, cy = size // 2, size // 2 - 80
    rx, ry = int(size * 0.10), int(size * 0.13)
    # 顔
    draw.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), outline=(0, 0, 0), width=3)
    er = int(size * 0.012)
    draw.ellipse((cx - 35 - er, cy - er, cx - 35 + er, cy + er), fill=(0, 0, 0))
    draw.ellipse((cx + 35 - er, cy - er, cx + 35 + er, cy + er), fill=(0, 0, 0))
    # 首 (2 line)
    draw.line((cx - 30, cy + ry + 5, cx - 30, cy + ry + 60), fill=(0, 0, 0), width=3)
    draw.line((cx + 30, cy + ry + 5, cx + 30, cy + ry + 60), fill=(0, 0, 0), width=3)
    # 肩 (V 字)
    draw.line((cx - 30, cy + ry + 60, cx - 130, cy + ry + 120), fill=(0, 0, 0), width=3)
    draw.line((cx + 30, cy + ry + 60, cx + 130, cy + ry + 120), fill=(0, 0, 0), width=3)
    return img


def sketch_d_stick_figure(size: int = 1024) -> Image.Image:
    """D: 棒人間。 頭丸 + 体 + 腕 + 脚。 全身構図を input で固定。"""
    img, draw = _new_canvas(size)
    cx, cy = size // 2, int(size * 0.30)
    head_r = int(size * 0.08)
    # 頭
    draw.ellipse((cx - head_r, cy - head_r, cx + head_r, cy + head_r),
                 outline=(0, 0, 0), width=3)
    # 体
    body_y0 = cy + head_r
    body_y1 = body_y0 + int(size * 0.30)
    draw.line((cx, body_y0, cx, body_y1), fill=(0, 0, 0), width=3)
    # 腕
    arm_y = body_y0 + int(size * 0.06)
    draw.line((cx, arm_y, cx - int(size * 0.12), arm_y + int(size * 0.10)),
              fill=(0, 0, 0), width=3)
    draw.line((cx, arm_y, cx + int(size * 0.12), arm_y + int(size * 0.10)),
              fill=(0, 0, 0), width=3)
    # 脚
    draw.line((cx, body_y1, cx - int(size * 0.08), body_y1 + int(size * 0.20)),
              fill=(0, 0, 0), width=3)
    draw.line((cx, body_y1, cx + int(size * 0.08), body_y1 + int(size * 0.20)),
              fill=(0, 0, 0), width=3)
    return img


def sketch_e_small_face_top(size: int = 1024) -> Image.Image:
    """E: 顔が上部に小さく置かれる。 余白多め。 体の位置を下に開放する設計。"""
    img, draw = _new_canvas(size)
    cx, cy = size // 2, int(size * 0.22)
    rx, ry = int(size * 0.07), int(size * 0.09)
    draw.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), outline=(0, 0, 0), width=3)
    er = int(size * 0.008)
    draw.ellipse((cx - 24 - er, cy - er, cx - 24 + er, cy + er), fill=(0, 0, 0))
    draw.ellipse((cx + 24 - er, cy - er, cx + 24 + er, cy + er), fill=(0, 0, 0))
    return img


def sketch_f_angry_face(size: int = 1024) -> Image.Image:
    """F: 怒り顔 (眉毛 + 鋭い目 + 口) — 表情の多様性チェック。"""
    img, draw = _new_canvas(size)
    cx, cy = size // 2, size // 2 - 50
    rx, ry = int(size * 0.11), int(size * 0.13)
    draw.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), outline=(0, 0, 0), width=3)
    # 怒り眉
    draw.line((cx - 70, cy - 35, cx - 25, cy - 15), fill=(0, 0, 0), width=4)
    draw.line((cx + 25, cy - 15, cx + 70, cy - 35), fill=(0, 0, 0), width=4)
    # 鋭い目
    er = int(size * 0.012)
    draw.ellipse((cx - 40 - er, cy + 5 - er, cx - 40 + er, cy + 5 + er), fill=(0, 0, 0))
    draw.ellipse((cx + 40 - er, cy + 5 - er, cx + 40 + er, cy + 5 + er), fill=(0, 0, 0))
    # 口 (一直線 = ムスッ)
    draw.line((cx - 30, cy + 50, cx + 30, cy + 50), fill=(0, 0, 0), width=3)
    return img


SKETCHES = {
    "A_oval_2dots":       sketch_a_oval_2dots,
    "B_round_smiley":     sketch_b_round_with_mouth,
    "C_face_with_neck":   sketch_c_face_with_neck,
    "D_stick_figure":     sketch_d_stick_figure,
    "E_small_face_top":   sketch_e_small_face_top,
    "F_angry_face":       sketch_f_angry_face,
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
        print(f"saved {out} ({img.size})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
