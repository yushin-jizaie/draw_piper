#!/usr/bin/env python3
"""Sketch を 5 位置 (中央/左上/右上/左下/右下) に置いて、
出力 (object mode) が位置を追従するか確認する。

ControlNet が soft hint (scale=0.65) なので、 位置は ある程度 保持されるはず。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


def make_positioned_sketch(
    base_sketch: Image.Image,
    canvas_size: int,
    position: str,
) -> Image.Image:
    """base sketch を canvas 上の指定位置に配置。
    position: "center" | "tl" (top-left) | "tr" | "bl" | "br"
    """
    # base を 中央近辺サイズに縮小 (canvas の ~30%)
    target_w = canvas_size // 3
    target_h = canvas_size // 3
    sk = base_sketch.copy()
    sk.thumbnail((target_w, target_h), Image.LANCZOS)

    canvas = Image.new("RGB", (canvas_size, canvas_size), (255, 255, 255))
    sk_w, sk_h = sk.size
    margin = canvas_size // 10
    positions = {
        "center": ((canvas_size - sk_w) // 2, (canvas_size - sk_h) // 2),
        "tl":     (margin, margin),
        "tr":     (canvas_size - sk_w - margin, margin),
        "bl":     (margin, canvas_size - sk_h - margin),
        "br":     (canvas_size - sk_w - margin, canvas_size - sk_h - margin),
    }
    canvas.paste(sk, positions[position])
    return canvas


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-sketch", type=Path, required=True,
                    help="トリミング元 sketch (e.g., sketch_cat.png)")
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--canvas-size", type=int, default=1024)
    ap.add_argument("--prompt", type=str, required=True)
    ap.add_argument("--category", type=str, default="object")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)

    base = Image.open(args.base_sketch).convert("RGB")
    print(f"[pos] base sketch: {args.base_sketch} ({base.size})")

    # 5 位置の sketch を作成
    positions = ["center", "tl", "tr", "bl", "br"]
    sketch_paths = {}
    for pos in positions:
        img = make_positioned_sketch(base, args.canvas_size, pos)
        out = args.output / f"input_{pos}.png"
        img.save(out)
        sketch_paths[pos] = out
        print(f"[pos]   {pos}: {out}")

    # 各 sketch で pipeline 実行
    import subprocess
    for pos, sk_path in sketch_paths.items():
        run_dir = args.output / f"result_{pos}"
        print(f"\n[pos] === {pos} ===")
        cmd = [
            "./venv/bin/python", "-m", "scripts.test_ip_adapter_two_stage",
            "--user-sketch", str(sk_path),
            "--category", args.category,
            "--output", str(run_dir),
            "--stage1-resolution", "1024",
            "--resolution", "768",
            "--stage1-prompt", args.prompt,
            "--seed", str(args.seed),
        ]
        rc = subprocess.run(cmd, cwd=str(_ROOT)).returncode
        if rc != 0:
            print(f"[pos]   FAILED rc={rc}")

    # grid 合成
    print(f"\n[pos] composing grid")
    cols = 5  # 5 位置
    rows = 3  # input / stage1 / strokes
    cell = 384
    grid = Image.new("RGB", (cols * cell, rows * cell + 30), (240, 240, 240))
    draw = ImageDraw.Draw(grid)
    draw.text((6, 6), f"Position fidelity: {Path(args.base_sketch).stem}",
              fill=(40, 40, 40))
    row_labels = ["input", "stage1 (generated)", "vectorized strokes"]
    for r, label in enumerate(row_labels):
        draw.text((6, 30 + r * cell + 6), label, fill=(0, 0, 80))
    for c, pos in enumerate(positions):
        # row 0: input
        sk_img = Image.open(sketch_paths[pos]).convert("RGB")
        grid.paste(sk_img.resize((cell, cell), Image.LANCZOS), (c * cell, 30))
        draw.text((c * cell + 6, 30 + 6), pos, fill=(80, 0, 0))
        # row 1: stage1 generated
        s1 = args.output / f"result_{pos}/stage1/illustrious_v2_object.png"
        if s1.exists():
            grid.paste(Image.open(s1).resize((cell, cell), Image.LANCZOS),
                       (c * cell, 30 + cell))
        # row 2: strokes
        st = args.output / f"result_{pos}/30_vectorized_strokes.png"
        if st.exists():
            grid.paste(Image.open(st).resize((cell, cell), Image.LANCZOS),
                       (c * cell, 30 + 2 * cell))
    grid_path = args.output / "position_grid.png"
    grid.save(grid_path)
    print(f"[pos] grid: {grid_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
