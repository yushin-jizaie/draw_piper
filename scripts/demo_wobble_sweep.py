#!/usr/bin/env python3
"""現状のロボット適合 strokes (Vectorizer 出力) を 受け取って、
wobble 強度を振った 「手描き風」 grid を生成する。

LoRA / IP-Adapter での 松本タッチ獲得が時間かかる間、 即試せる代替アプローチ:
ロボット描画の strokes 自体に「手で描いたような不規則性」 を後段で加える。

使用:
  ./venv/bin/python -m scripts.demo_wobble_sweep \\
      --generated logs/imagegen_comparison_20260528_011016/illustrious_v2_inpaint.png \\
      --user-sketch scripts/test_sketch.jpg \\
      --output logs/wobble_demo_$(date +%Y%m%d_%H%M%S)

出力:
  output/
    ├ 00_original_vectorized.png   (wobble なし)
    ├ wobble_amp1_freq05.png       (穏やか)
    ├ wobble_amp3_freq05.png       (中)
    ├ wobble_amp5_freq05.png       (強い)
    ├ wobble_amp3_freq02.png       (低周波 = 大きな曲がり)
    ├ wobble_amp3_freq10.png       (高周波 = 細かい振動)
    └ compare_grid.png             (横並び比較)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--generated", type=Path, required=True,
                    help="ImageGen の出力画像 (Vectorize の入力になる)")
    ap.add_argument("--user-sketch", type=Path, default=None,
                    help="ユーザのスケッチ (diff 用、 省略すれば skip)")
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--line-width", type=int, default=3,
                    help="render 時の線幅 px")
    args = ap.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)

    from PIL import Image
    from modules.vectorizer import Vectorizer
    from modules.stroke_wobble import wobble_strokes
    from modules.stroke_render import (
        render_strokes_to_image, render_compare_grid)

    print(f"[wobble_demo] vectorize {args.generated}")
    gen = Image.open(args.generated)
    usr = Image.open(args.user_sketch) if args.user_sketch else None
    vec = Vectorizer()
    result = vec.vectorize(generated_image=gen, user_image=usr)
    strokes = result.strokes  # px 単位
    print(f"  base strokes: {len(strokes)}, n_points: {result.n_points}")

    # Render original (wobble なし)
    h, w = result.image_shape
    img0 = render_strokes_to_image(
        strokes, width=w, height=h, line_width=args.line_width)
    img0.save(args.output / "00_original_vectorized.png")

    # Sweep params (amplitude × frequency の組み合わせ)
    sweep = [
        (1.0, 0.05, "amp1_freq05"),
        (3.0, 0.05, "amp3_freq05"),
        (5.0, 0.05, "amp5_freq05"),
        (3.0, 0.02, "amp3_freq02"),
        (3.0, 0.10, "amp3_freq10"),
        (5.0, 0.10, "amp5_freq10"),
    ]
    rendered_imgs = [img0]
    labels = ["original"]
    for amp, freq, tag in sweep:
        wobbled = wobble_strokes(
            strokes, amplitude_px=amp, frequency_hz=freq, seed=42)
        img = render_strokes_to_image(
            wobbled, width=w, height=h, line_width=args.line_width)
        out = args.output / f"wobble_{tag}.png"
        img.save(out)
        rendered_imgs.append(img)
        labels.append(f"a={amp},f={freq}")
        print(f"  wobble a={amp} f={freq} -> {out.name}")

    # Grid 比較
    grid = render_compare_grid(
        [vec.vectorize(generated_image=gen).strokes  # placeholder
         for _ in rendered_imgs],   # 実は使わない、 image 直接 paste したい
        labels,
        width=w // 3, height=h // 3, cols=4,
    )
    # 上の grid 関数は strokes 受け取る設計だったので、 image 直接合成する版を
    # ad-hoc で作る:
    from PIL import Image as _PIL_Image, ImageDraw as _PIL_Draw
    cols = 4
    rows = (len(rendered_imgs) + cols - 1) // cols
    cell_w, cell_h = w // 3, h // 3
    grid_img = _PIL_Image.new("RGB", (cols * cell_w, rows * cell_h),
                              (240, 240, 240))
    gdraw = _PIL_Draw.Draw(grid_img)
    for i, (img, label) in enumerate(zip(rendered_imgs, labels)):
        r, c = i // cols, i % cols
        thumb = img.resize((cell_w, cell_h), _PIL_Image.LANCZOS)
        grid_img.paste(thumb, (c * cell_w, r * cell_h))
        gdraw.text((c * cell_w + 6, r * cell_h + 6), label,
                   fill=(40, 40, 40))
    grid_img.save(args.output / "compare_grid.png")
    print(f"[wobble_demo] grid saved -> {args.output / 'compare_grid.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
