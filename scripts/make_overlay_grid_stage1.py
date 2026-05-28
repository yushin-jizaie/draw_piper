#!/usr/bin/env python3
"""Stage 1 + LoRA (新 preset) の 8 入力結果を overlay grid 化。

各組について 横並び 1 枚:
  ① ストローク画像 (30_vectorized_strokes.png)
  ② インプット + ストローク 重ね合わせ (input=青、 stroke=黒)
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

_ROOT = Path(__file__).resolve().parent.parent
BASE_DIR = _ROOT / "sketch_variations/stage1_lora_20260529_005952"

COMBOS = [
    # (sketch_id, input_path, category)
    ("B_round_smiley",   "logs/sketch_variations_20260528_084706/inputs/sketch_B_round_smiley.png",   "character"),
    ("C_face_with_neck", "logs/sketch_variations_20260528_084706/inputs/sketch_C_face_with_neck.png", "character"),
    ("D_stick_figure",   "logs/sketch_variations_20260528_084706/inputs/sketch_D_stick_figure.png",   "character"),
    ("F_angry_face",     "logs/sketch_variations_20260528_084706/inputs/sketch_F_angry_face.png",     "character"),
    ("house",            "logs/sketches_objects_20260528_183943/sketch_house.png",                    "object"),
    ("tree",             "logs/sketches_objects_20260528_183943/sketch_tree.png",                     "object"),
    ("cat",              "logs/sketches_objects_20260528_183943/sketch_cat.png",                      "object"),
    ("car",              "logs/sketches_objects_20260528_183943/sketch_car.png",                      "object"),
]

SIZE = 512
GAP = 10
LABEL_H = 30


def make_overlay(input_path: Path, stroke_path: Path) -> Image.Image:
    inp = Image.open(input_path).convert("L").resize((SIZE, SIZE))
    stroke = Image.open(stroke_path).convert("L").resize((SIZE, SIZE))
    inp_arr = np.array(inp)
    stroke_arr = np.array(stroke)
    out = np.full((SIZE, SIZE, 3), 255, dtype=np.uint8)
    out[inp_arr < 128] = [100, 150, 255]
    out[stroke_arr < 128] = [0, 0, 0]
    return Image.fromarray(out)


def main() -> int:
    ts = time.strftime("%Y%m%d_%H%M%S")
    outdir = _ROOT / f"sketch_variations/grid_stage1_lora_{ts}"
    outdir.mkdir(parents=True, exist_ok=True)
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
    except Exception:
        font = ImageFont.load_default()
    for sid, ip, cat in COMBOS:
        stroke_p = BASE_DIR / sid / "30_vectorized_strokes.png"
        ip_p = _ROOT / ip
        if not stroke_p.exists() or not ip_p.exists():
            print(f"  SKIP {sid}: missing files")
            continue
        stroke = Image.open(stroke_p).convert("RGB").resize((SIZE, SIZE))
        overlay = make_overlay(ip_p, stroke_p)
        W = SIZE * 2 + GAP
        H = LABEL_H + SIZE
        canvas = Image.new("RGB", (W, H), (255, 255, 255))
        draw = ImageDraw.Draw(canvas)
        draw.text((10, 6),
                  f"{sid} / {cat} (Stage1 LoRA mt 0.4, skip-stage2)",
                  fill=(0, 0, 0), font=font)
        draw.text((SIZE + GAP + 10, 6),
                  "input (blue) + strokes (black)",
                  fill=(0, 0, 0), font=font)
        canvas.paste(stroke, (0, LABEL_H))
        canvas.paste(overlay, (SIZE + GAP, LABEL_H))
        out_path = outdir / f"{sid}_{cat}_stage1lora.png"
        canvas.save(out_path)
        print(f"  saved {out_path.relative_to(_ROOT)}")
    print(f"\nOUTDIR={outdir.relative_to(_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
