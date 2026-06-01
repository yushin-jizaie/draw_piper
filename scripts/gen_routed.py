#!/usr/bin/env python3
"""占有率ルーティング + 勝ち筋レシピ (lineart_char cn0.5) の統合生成パイプライン。

2026-06-01 検証で確定した方針:
  - 入力の被写体占有率で route を自動選択 (modules.input_router)
  - 共通レシピ: illustrious_v2_lineart_char + controlnet_conditioning_scale=0.5
    (高CN は忠実トレースで退屈、 cn0.5 がポーズ保持しつつキャラ化する転換点)
  - stylize (いっぱいの入力): 縦長のまま単一キャラにスタイル化 → 位置保持
  - scatter (余白の多い入力): 縦長生成は自然にスプライト化するので、 それを
    個々に分割してユーザーの線の周囲 (空白) に散布 → 位置保持 + 空白充填

出力は webapp disp pattern-2 構成。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

CN_SCALE = 0.5
PRESET = "illustrious_v2_lineart_char"
DEFAULT_SEEDS = [123, 7, 555]

# 被写体に応じた生成 prompt (subject を差し込む)。
STYLIZE_PROMPT = ("{subj}, manga character, dynamic confident pose, expressive, "
                  "clean bold ink lineart, white background, appealing character design")
SCATTER_PROMPT = ("{subj}, manga style, dynamic pose, clean bold ink lineart, "
                  "white background, appealing, expressive")


def _save_candidate(out_dir, strokes, CW, CH, generated=None):
    import numpy as np
    import cv2
    from PIL import Image
    from modules.stroke_render import render_strokes_to_image
    from modules.vectorizer import _skeletonize
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "vec_debug").mkdir(exist_ok=True)
    poly = [[(float(x), float(y)) for x, y in st] for st in strokes]
    render_strokes_to_image(poly, width=CW, height=CH, line_width=2
                            ).save(out_dir / "30_vectorized_strokes.png")
    (out_dir / "strokes.json").write_text(json.dumps({
        "image_shape": [CH, CW], "n_strokes": len(strokes),
        "n_points": sum(len(s) for s in strokes),
        "strokes": [[[float(x), float(y)] for x, y in st] for st in strokes]}))
    if generated is not None:
        generated.save(out_dir / "generated.png")
    arr = np.array(render_strokes_to_image(
        poly, width=CW, height=CH, line_width=2).convert("L"))
    m = (arr < 128).astype(np.uint8)
    canvas = np.full(arr.shape, 255, np.uint8)
    if m.sum() > 0:
        canvas[cv2.dilate(_skeletonize(m).astype(np.uint8),
                          np.ones((2, 2), np.uint8)) > 0] = 0
    Image.fromarray(canvas).save(out_dir / "vec_debug" / "06_strokes.png")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--sid", type=str, required=True)
    ap.add_argument("--subject", type=str, default="character",
                    help="prompt に差し込む被写体語 (例: cat, 1boy)")
    ap.add_argument("--output-base", type=Path, required=True,
                    help="disp dir base (この下に <sid>/vN_seedS/)")
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--resolution", type=str, default="704x1472")
    ap.add_argument("--force-route", choices=["stylize", "companion"], default=None)
    args = ap.parse_args()

    import numpy as np
    from PIL import Image
    from modules.image_gen import ImageGenerator
    from modules.vectorizer import Vectorizer, load_binarize_config
    from modules.input_router import decide_route
    from modules.stroke_scatter import scatter_companions
    from modules.blob_detect import detect_blobs, union_bbox
    from modules.panel_geometry import parse_resolution

    CW, CH = parse_resolution(args.resolution) or (704, 1472)
    cfg = load_binarize_config()
    inp = Image.open(args.input).convert("RGB")
    route = args.force_route or decide_route(inp).route
    print(f"[routed] {args.sid}: route={route} subj={args.subject}")

    guide = inp.resize((CW, CH))
    gen = ImageGenerator.from_preset(PRESET, resolution=(CW, CH), verbose=False)
    gen.load()
    seeds = DEFAULT_SEEDS[:args.n]

    if route == "stylize":
        prompt = STYLIZE_PROMPT.format(subj=args.subject)
        vec = Vectorizer(gen_line_mode="canny", **cfg)
        for i, seed in enumerate(seeds):
            raster = gen.generate(prompt, guide,
                                  controlnet_conditioning_scale=CN_SCALE, seed=seed)
            r = vec.vectorize(generated_image=raster, user_image=None)
            d = args.output_base / args.sid / f"v{i+1}_seed{seed}"
            _save_candidate(d, r.strokes, CW, CH, generated=raster)
            print(f"[routed]   stylize v{i+1} seed{seed}: {r.n_strokes} strokes -> {d}")
    else:  # companion → scatter
        prompt = SCATTER_PROMPT.format(subj=args.subject)
        vec_bin = Vectorizer(**cfg)
        vec_canny = Vectorizer(gen_line_mode="canny", **cfg)
        input_strokes = vec_bin.vectorize(generated_image=guide, user_image=None).strokes
        bbox = union_bbox(detect_blobs(Image.fromarray(np.array(guide.convert("L")))))
        for i, seed in enumerate(seeds):
            sheet = gen.generate(prompt, guide,
                                 controlnet_conditioning_scale=CN_SCALE, seed=seed)
            combined, n_placed, n_found, n_cells = scatter_companions(
                sheet, input_strokes, bbox, (CW, CH), seed=seed, vectorizer=vec_canny)
            d = args.output_base / args.sid / f"v{i+1}_seed{seed}"
            _save_candidate(d, combined, CW, CH, generated=sheet)
            print(f"[routed]   scatter v{i+1} seed{seed}: {n_placed}/{n_found} chars, "
                  f"{len(combined)} strokes -> {d}")
    print(f"[routed] {args.sid} done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
