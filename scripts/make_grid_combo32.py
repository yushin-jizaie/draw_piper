#!/usr/bin/env python3
"""8 入力 × 4 経路 = 32 通り の grid 化。

各入力につき 1 ファイル: 4 経路の出力を 横並びに、 各 (stroke only + overlay) を縦に。
ファイル名: <sketch_id>.png
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

_ROOT = Path(__file__).resolve().parent.parent

# (sketch_id, input_path, sketch_type)
INPUTS = [
    ("B_round_smiley",   "logs/sketch_variations_20260528_084706/inputs/sketch_B_round_smiley.png",   "character"),
    ("C_face_with_neck", "logs/sketch_variations_20260528_084706/inputs/sketch_C_face_with_neck.png", "character"),
    ("D_stick_figure",   "logs/sketch_variations_20260528_084706/inputs/sketch_D_stick_figure.png",   "character"),
    ("F_angry_face",     "logs/sketch_variations_20260528_084706/inputs/sketch_F_angry_face.png",     "character"),
    ("house",            "logs/sketches_objects_20260528_183943/sketch_house.png",                    "object"),
    ("tree",             "logs/sketches_objects_20260528_183943/sketch_tree.png",                     "object"),
    ("cat",              "logs/sketches_objects_20260528_183943/sketch_cat.png",                      "object"),
    ("car",              "logs/sketches_objects_20260528_183943/sketch_car.png",                      "object"),
]

# 4 経路 (col)、 各経路の stroke ファイル探索パス候補
PATHS = [
    ("char+S2",  ["char_S2/30_vectorized_strokes.png"]),
    ("obj only", ["obj_only/30_vectorized_strokes.png"]),
    ("align",    ["align/30_vectorized_strokes.png"]),
    ("shift",    ["shift/30_companion_strokes.png"]),
]

CELL = 384      # 各 panel サイズ
GAP = 6
LABEL_H = 24


def make_overlay(input_path: Path, stroke_path: Path) -> Image.Image:
    inp = Image.open(input_path).convert("L").resize((CELL, CELL))
    stroke = Image.open(stroke_path).convert("L").resize((CELL, CELL))
    inp_arr = np.array(inp)
    stroke_arr = np.array(stroke)
    out = np.full((CELL, CELL, 3), 255, dtype=np.uint8)
    out[inp_arr < 128] = [100, 150, 255]
    out[stroke_arr < 128] = [0, 0, 0]
    return Image.fromarray(out)


def make_input_panel(input_path: Path) -> Image.Image:
    """Input sketch を 1 panel に。"""
    inp = Image.open(input_path).convert("RGB").resize((CELL, CELL))
    return inp


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-dir", required=True,
                    help="combo32 出力 dir (各 <sketch_id>/{char_S2,obj_only,align,shift}/)")
    args = ap.parse_args()
    base_dir = _ROOT / args.base_dir
    ts = time.strftime("%Y%m%d_%H%M%S")
    outdir = _ROOT / f"sketch_variations/grid_combo32_{ts}"
    outdir.mkdir(parents=True, exist_ok=True)
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
    except Exception:
        font = ImageFont.load_default()
    # 各 input につき、 5 列 (input + 4 paths) × 2 行 (stroke、 overlay)
    # ただし input は 1 列のみ、 stroke 行のみ
    # 縦の sketch + 横の経路 でグリッド
    N_COLS = 5    # input + 4 paths
    N_ROWS = 2    # stroke / overlay
    grid_w = N_COLS * CELL + (N_COLS + 1) * GAP
    grid_h = LABEL_H + N_ROWS * CELL + (N_ROWS + 1) * GAP

    for sid, ip, stype in INPUTS:
        ip_p = _ROOT / ip
        if not ip_p.exists():
            print(f"  SKIP {sid}: input missing -> {ip_p}")
            continue
        canvas = Image.new("RGB", (grid_w, grid_h), (245, 245, 245))
        draw = ImageDraw.Draw(canvas)
        draw.text((10, 4), f"{sid} ({stype} input) — seed=42",
                  fill=(0, 0, 0), font=font)
        # 行ラベル (左端、 細い帯)
        col_labels = ["INPUT"] + [p[0] for p in PATHS]
        for ci, lbl in enumerate(col_labels):
            x = GAP + ci * (CELL + GAP)
            draw.text((x + 6, LABEL_H - 4), lbl, fill=(40, 40, 90), font=font)
        # 1 行目: stroke only
        # 2 行目: input + overlay
        # input panel (col 0)
        inp_panel = make_input_panel(ip_p)
        canvas.paste(inp_panel, (GAP, LABEL_H + GAP))
        canvas.paste(inp_panel, (GAP, LABEL_H + GAP + CELL + GAP))
        for pi, (lbl, candidates) in enumerate(PATHS):
            col_x = GAP + (pi + 1) * (CELL + GAP)
            # stroke 画像探索
            stroke_p = None
            for cand in candidates:
                p = base_dir / sid / cand
                if p.exists():
                    stroke_p = p
                    break
            if stroke_p is None:
                # 該当ファイル無 (実行失敗)、 グレー panel + ラベル
                miss = Image.new("RGB", (CELL, CELL), (220, 220, 220))
                d2 = ImageDraw.Draw(miss)
                d2.text((10, CELL // 2 - 10), f"missing\n{lbl}",
                        fill=(120, 0, 0), font=font)
                canvas.paste(miss, (col_x, LABEL_H + GAP))
                canvas.paste(miss, (col_x, LABEL_H + GAP + CELL + GAP))
                continue
            stroke_img = Image.open(stroke_p).convert("RGB").resize((CELL, CELL))
            overlay = make_overlay(ip_p, stroke_p)
            canvas.paste(stroke_img, (col_x, LABEL_H + GAP))
            canvas.paste(overlay, (col_x, LABEL_H + GAP + CELL + GAP))
        out_path = outdir / f"{sid}.png"
        canvas.save(out_path)
        print(f"  saved {out_path.relative_to(_ROOT)}")
    print(f"\nOUTDIR={outdir.relative_to(_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
