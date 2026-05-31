#!/usr/bin/env python3
"""scatter placement (本番): companion スプライトシートを生成し、
個々のキャラに分割して入力の周囲の空白に散布する。

shift が「1 体を最大空白へ移動」 なのに対し、 scatter は「複数の小さな
キャラを入力の周りに撒く」 演出。 lineartLoRA char preset の
スプライトシート (複数ポーズ) を活かす。

使い方:
  # スプライトを新規生成して散布 (GPU)
  ./venv/bin/python -m scripts.scatter_companions \\
      --input sketch_variations/_inputs/scatter_input.png \\
      --output sketch_variations/disp_scatter_demo/scatter/v1_seed555 \\
      --seed 555 --resolution 704x1472

  # 既存スプライトシートを使う (GPU 不要、 再現用)
  ./venv/bin/python -m scripts.scatter_companions \\
      --input  sketch_variations/_inputs/scatter_input.png \\
      --sheet  assets/scatter_sheet_lineart_char.png \\
      --output sketch_variations/disp_scatter_demo/scatter/v1_seed555 \\
      --seed 555 --resolution 704x1472

出力 (webapp の disp pattern-2 が拾う構成):
  30_vectorized_strokes.png   散布合成 (入力 + 周囲キャラ)
  strokes.json                合成 strokes
  20_input_strokes.png        入力のみ
  vec_debug/06_strokes.png    skeleton (Frida 確認用)
  00_auto_prompt.txt          生成設定メモ

See: docs/20260601_scatter_and_transparent_board.md
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# scatter 生成のデフォルト prompt: 「複数の小さなダイナミックキャラ」 が
# 離散して並ぶスプライトシートを狙う (塗りシルエット → 後段で輪郭線化)。
DEFAULT_SHEET_PROMPT = (
    "person, manga character, full body, confident dynamic standing pose, "
    "clean bold outline lineart, single black line on white background, "
    "no shading, no fill, no hatching, no speed lines"
)
DEFAULT_PRESET = "illustrious_v2_lineart_char"


def _generate_sheet(input_path: Path, res_wh, seed: int, preset: str,
                    prompt: str):
    """preset でスプライトシートを生成して PIL.Image を返す (GPU)。"""
    from PIL import Image
    from modules.image_gen import ImageGenerator, MODEL_PRESETS

    cfg = MODEL_PRESETS[preset]
    full_prompt = prompt
    if cfg.get("style_hint"):
        full_prompt = f"{prompt}, {cfg['style_hint']}"
    guide = Image.open(input_path).convert("RGB")
    if guide.size != tuple(res_wh):
        guide = guide.resize(tuple(res_wh), Image.LANCZOS)
    gen = ImageGenerator.from_preset(preset, resolution=tuple(res_wh),
                                     verbose=True)
    gen.load()
    print(f"[scatter] generating sheet: preset={preset} seed={seed}")
    return gen.generate(full_prompt, guide, seed=seed)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", type=Path, required=True,
                    help="入力スケッチ (中央に置かれ、 周囲にキャラを散布)")
    ap.add_argument("--output", type=Path, required=True,
                    help="出力 disp dir")
    ap.add_argument("--sheet", type=Path, default=None,
                    help="既存スプライトシート png (指定で生成 skip = GPU 不要)")
    ap.add_argument("--resolution", type=str, default="704x1472",
                    help="canvas WxH (= ボード縦横比)")
    ap.add_argument("--seed", type=int, default=555)
    ap.add_argument("--preset", type=str, default=DEFAULT_PRESET)
    ap.add_argument("--prompt", type=str, default=DEFAULT_SHEET_PROMPT)
    ap.add_argument("--cols", type=int, default=3)
    ap.add_argument("--rows", type=int, default=7)
    ap.add_argument("--fill", type=float, default=0.8,
                    help="各セル内でキャラが占める割合 (0-1)")
    args = ap.parse_args()

    import numpy as np
    import cv2
    from PIL import Image
    from modules.vectorizer import Vectorizer, load_binarize_config, _skeletonize
    from modules.stroke_render import render_strokes_to_image
    from modules.blob_detect import detect_blobs, union_bbox
    from modules.stroke_scatter import scatter_companions
    from modules.panel_geometry import parse_resolution

    res_wh = parse_resolution(args.resolution) or (704, 1472)
    CW, CH = res_wh
    cfg = load_binarize_config()

    # --- 入力: strokes + 占有 bbox ---
    inp_img = Image.open(args.input).convert("RGB").resize((CW, CH))
    vec_bin = Vectorizer(**cfg)
    input_r = vec_bin.vectorize(generated_image=inp_img, user_image=None)
    input_strokes = input_r.strokes
    blobs = detect_blobs(Image.fromarray(np.array(inp_img.convert("L"))))
    input_bbox = union_bbox(blobs)
    print(f"[scatter] input: {len(input_strokes)} strokes, bbox={input_bbox}")

    # --- スプライトシート (生成 or 既存) ---
    if args.sheet:
        sheet = Image.open(args.sheet).convert("RGB")
        print(f"[scatter] using existing sheet: {args.sheet}")
    else:
        sheet = _generate_sheet(args.input, res_wh, args.seed,
                                args.preset, args.prompt)

    # --- 分割 → 輪郭化 → 散布 ---
    vec_canny = Vectorizer(gen_line_mode="canny", **cfg)
    combined, n_placed, n_found, n_cells = scatter_companions(
        sheet, input_strokes, input_bbox, (CW, CH),
        cols=args.cols, rows=args.rows, fill=args.fill, seed=args.seed,
        vectorizer=vec_canny)
    print(f"[scatter] found {n_found} chars, {n_cells} free cells, "
          f"placed {n_placed} around input. total strokes={len(combined)}")

    # --- 出力 ---
    out = args.output
    (out / "vec_debug").mkdir(parents=True, exist_ok=True)
    render_strokes_to_image(combined, width=CW, height=CH, line_width=2
                            ).save(out / "30_vectorized_strokes.png")
    render_strokes_to_image(input_strokes, width=CW, height=CH, line_width=2
                            ).save(out / "20_input_strokes.png")
    (out / "strokes.json").write_text(json.dumps({
        "image_shape": [CH, CW],
        "n_strokes": len(combined),
        "n_points": sum(len(s) for s in combined),
        "strokes": [[[float(x), float(y)] for x, y in st] for st in combined],
    }))
    # skeleton (Frida 確認用)
    arr = np.array(render_strokes_to_image(
        combined, width=CW, height=CH, line_width=2).convert("L"))
    m = (arr < 128).astype(np.uint8)
    canvas = np.full(arr.shape, 255, np.uint8)
    if m.sum() > 0:
        sk = _skeletonize(m).astype(np.uint8)
        canvas[cv2.dilate(sk, np.ones((2, 2), np.uint8)) > 0] = 0
    Image.fromarray(canvas).save(out / "vec_debug" / "06_strokes.png")
    (out / "00_auto_prompt.txt").write_text(
        f"mode=scatter\nseed={args.seed}\npreset={args.preset}\n"
        f"resolution={CW}x{CH}\ncols={args.cols} rows={args.rows} "
        f"fill={args.fill}\nchars_found={n_found} chars_placed={n_placed}\n"
        f"sheet_prompt={args.prompt}\n")
    print(f"[scatter] saved → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
