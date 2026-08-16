#!/usr/bin/env python3
"""16 組 (8 入力 × 2 モード) の比較画像を生成。

各組について:
  ① ストローク画像 (白背景 + 黒線、 30_*.png)
  ② インプット sketch + ストローク を重ね合わせ画像 (input = 青、 stroke = 黒)
を 横並び 1 枚 にした PNG を出力。

ファイル名: <sketch_id>_<mode>.png
出力先: sketch_variations/grid_16_<ts>/
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

_ROOT = Path(__file__).resolve().parent.parent

# 16 組 = 8 入力 × 2 モード (shift / align)
# (sketch_id, input_path, stroke_path, mode)
COMBOS = [
    # キャラ系 4 種 (dual_mode_multi_20260528_181532)
    ("B_round_smiley",
     "logs/sketch_variations_20260528_084706/inputs/sketch_B_round_smiley.png",
     "sketch_variations/dual_mode_multi_20260528_181532/B_round_smiley/shift/30_companion_strokes.png",
     "shift"),
    ("B_round_smiley",
     "logs/sketch_variations_20260528_084706/inputs/sketch_B_round_smiley.png",
     "sketch_variations/dual_mode_multi_20260528_181532/B_round_smiley/align/30_vectorized_strokes.png",
     "align"),
    ("C_face_with_neck",
     "logs/sketch_variations_20260528_084706/inputs/sketch_C_face_with_neck.png",
     "sketch_variations/dual_mode_multi_20260528_181532/C_face_with_neck/shift/30_companion_strokes.png",
     "shift"),
    ("C_face_with_neck",
     "logs/sketch_variations_20260528_084706/inputs/sketch_C_face_with_neck.png",
     "sketch_variations/dual_mode_multi_20260528_181532/C_face_with_neck/align/30_vectorized_strokes.png",
     "align"),
    ("D_stick_figure",
     "logs/sketch_variations_20260528_084706/inputs/sketch_D_stick_figure.png",
     "sketch_variations/dual_mode_multi_20260528_181532/D_stick_figure/shift/30_companion_strokes.png",
     "shift"),
    ("D_stick_figure",
     "logs/sketch_variations_20260528_084706/inputs/sketch_D_stick_figure.png",
     "sketch_variations/dual_mode_multi_20260528_181532/D_stick_figure/align/30_vectorized_strokes.png",
     "align"),
    ("F_angry_face",
     "logs/sketch_variations_20260528_084706/inputs/sketch_F_angry_face.png",
     "sketch_variations/dual_mode_multi_20260528_181532/F_angry_face/shift/30_companion_strokes.png",
     "shift"),
    ("F_angry_face",
     "logs/sketch_variations_20260528_084706/inputs/sketch_F_angry_face.png",
     "sketch_variations/dual_mode_multi_20260528_181532/F_angry_face/align/30_vectorized_strokes.png",
     "align"),
    # object 系 4 種
    ("house",
     "logs/sketches_objects_20260528_183943/sketch_house.png",
     "sketch_variations/objects_shift_20260528_183956/house/30_companion_strokes.png",
     "shift"),
    ("house",
     "logs/sketches_objects_20260528_183943/sketch_house.png",
     "sketch_variations/objects_align_20260528_185144/house/30_vectorized_strokes.png",
     "align"),
    ("tree",
     "logs/sketches_objects_20260528_183943/sketch_tree.png",
     "sketch_variations/objects_shift_20260528_183956/tree/30_companion_strokes.png",
     "shift"),
    ("tree",
     "logs/sketches_objects_20260528_183943/sketch_tree.png",
     "sketch_variations/objects_align_20260528_185144/tree/30_vectorized_strokes.png",
     "align"),
    ("cat",
     "logs/sketches_objects_20260528_183943/sketch_cat.png",
     "sketch_variations/objects_shift_20260528_183956/cat/30_companion_strokes.png",
     "shift"),
    ("cat",
     "logs/sketches_objects_20260528_183943/sketch_cat.png",
     "sketch_variations/objects_align_20260528_185144/cat/30_vectorized_strokes.png",
     "align"),
    ("car",
     "logs/sketches_objects_20260528_183943/sketch_car.png",
     "sketch_variations/objects_shift_20260528_183956/car/30_companion_strokes.png",
     "shift"),
    ("car",
     "logs/sketches_objects_20260528_183943/sketch_car.png",
     "sketch_variations/objects_align_20260528_185144/car/30_vectorized_strokes.png",
     "align"),
]

SIZE = 512        # 各パネルの一辺
GAP = 10          # ストローク / overlay 間の隙間
LABEL_H = 30      # ラベル帯の高さ


def make_overlay(input_path: Path, stroke_path: Path) -> Image.Image:
    """input sketch を青、 stroke を黒で同一 SIZE 上に重ね合わせ。"""
    inp = Image.open(input_path).convert("L").resize((SIZE, SIZE))
    stroke = Image.open(stroke_path).convert("L").resize((SIZE, SIZE))
    inp_arr = np.array(inp)        # 0 = 黒線、 255 = 白背景
    stroke_arr = np.array(stroke)
    # 白背景 RGB
    out = np.full((SIZE, SIZE, 3), 255, dtype=np.uint8)
    # input の線 (gray < 128) を 青 (100, 150, 255) で
    inp_mask = inp_arr < 128
    out[inp_mask] = [100, 150, 255]
    # stroke の線 (gray < 128) を 黒で上書き (input の青より優先)
    stroke_mask = stroke_arr < 128
    out[stroke_mask] = [0, 0, 0]
    return Image.fromarray(out)


def make_pair(sketch_id: str, input_path: Path, stroke_path: Path,
              mode: str) -> Image.Image:
    """① stroke 単独 + ② overlay を 横並び + 上部にラベル帯。"""
    stroke = Image.open(stroke_path).convert("RGB").resize((SIZE, SIZE))
    overlay = make_overlay(input_path, stroke_path)
    # 上部ラベル帯 + 2 panel
    W = SIZE * 2 + GAP
    H = LABEL_H + SIZE
    canvas = Image.new("RGB", (W, H), (255, 255, 255))
    # ラベル
    from PIL import ImageDraw, ImageFont
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
    except Exception:
        font = ImageFont.load_default()
    # ① ストロークのみ
    draw.text((10, 6), f"{sketch_id} / {mode}  -  (1) strokes only",
              fill=(0, 0, 0), font=font)
    # ② 重ね合わせ
    draw.text((SIZE + GAP + 10, 6),
              "(2) input (blue) + strokes (black)",
              fill=(0, 0, 0), font=font)
    # 画像 paste
    canvas.paste(stroke, (0, LABEL_H))
    canvas.paste(overlay, (SIZE + GAP, LABEL_H))
    return canvas


def main() -> int:
    ts = time.strftime("%Y%m%d_%H%M%S")
    outdir = _ROOT / f"sketch_variations/grid_16_{ts}"
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"OUTDIR={outdir}")
    failed = []
    for sketch_id, ip, sp, mode in COMBOS:
        ip_p = _ROOT / ip
        sp_p = _ROOT / sp
        if not ip_p.exists():
            print(f"  SKIP {sketch_id} {mode}: input missing -> {ip_p}")
            failed.append((sketch_id, mode, "input missing"))
            continue
        if not sp_p.exists():
            print(f"  SKIP {sketch_id} {mode}: stroke missing -> {sp_p}")
            failed.append((sketch_id, mode, "stroke missing"))
            continue
        pair = make_pair(sketch_id, ip_p, sp_p, mode)
        out_path = outdir / f"{sketch_id}_{mode}.png"
        pair.save(out_path)
        print(f"  saved {out_path.relative_to(_ROOT)}")
    print(f"\n生成 {len(COMBOS) - len(failed)} 件 / 失敗 {len(failed)} 件")
    if failed:
        for s, m, r in failed:
            print(f"  - {s} / {m}: {r}")
    print(f"\nOUTDIR={outdir}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
