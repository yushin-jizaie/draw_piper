#!/usr/bin/env python3
"""Guide image (lineart 入力) の dilate 効果プレビュー。

ksize を 0 / 3 / 5 / 7 で並べて、 入力線が ControlNet にとって
どれくらい強い signal になるかを目視確認する。 SDXL を走らせる前の
sanity check 用。

使用:
    python3 -m scripts.preview_guide_dilate scripts/test_sketch.jpg
    python3 -m scripts.preview_guide_dilate path/to/sketch.png --out /tmp/dilate.png
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from modules.image_gen import _dilate_guide_lines, _normalize_image    # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", type=Path, help="入力 lineart (jpg / png)")
    ap.add_argument("--out", type=Path,
                    default=Path("/tmp/guide_dilate_preview.png"))
    ap.add_argument("--ksizes", nargs="+", type=int, default=[0, 3, 5, 7])
    ap.add_argument("--resolution", type=int, default=1024)
    args = ap.parse_args()

    if not args.input.exists():
        print(f"input not found: {args.input}", file=sys.stderr)
        return 2

    from PIL import Image, ImageDraw, ImageFont

    base = _normalize_image(args.input, size=args.resolution)
    print(f"input: {args.input}  resized to {base.size}")

    # 横並び grid
    w, h = base.size
    n = len(args.ksizes)
    pad = 8
    label_h = 36
    grid_w = w * n + pad * (n + 1)
    grid_h = h + label_h + pad * 2
    grid = Image.new("RGB", (grid_w, grid_h), (240, 240, 240))
    draw = ImageDraw.Draw(grid)
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
    except OSError:
        font = ImageFont.load_default()

    for i, k in enumerate(args.ksizes):
        x = pad + i * (w + pad)
        y = pad + label_h
        if k <= 1:
            out = base
            label = "原画 (ksize=0)"
        else:
            out = _dilate_guide_lines(base, ksize=k)
            label = f"dilate ksize={k}"
        grid.paste(out, (x, y))
        draw.text((x + 8, pad), label, fill=(0, 0, 0), font=font)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    grid.save(args.out)
    print(f"saved -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
