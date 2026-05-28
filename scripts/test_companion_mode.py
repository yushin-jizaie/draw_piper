#!/usr/bin/env python3
"""Companion mode: M16 画風で生成した object を **input の空白地帯に配置**。

ユーザ要望 (2026-05-28):
- 入力画像の角度違いの場合は、 むしろ入力画像から位置をずらす
- cv2 blob で重心 + 空白地帯に描く

実装:
  1. 入力 sketch を object preset (v5) で生成 (中央に detailed 出力)
  2. Vectorizer で生成 strokes (centered)
  3. 入力 sketch から cv2 で blob 検出 (input 占有領域)
  4. 入力 bbox を避けた 最大空白矩形を計算
  5. 生成 strokes を bbox → 空白矩形 に translate + scale
  6. 入力 sketch も Vectorize → 入力 strokes (元位置)
  7. 入力 strokes + transformed 生成 strokes = composite output

これにより 入力 sketch 位置を変えずに、 隣に M16 画風 detailed object を 追加。

使用:
  # 手動 prompt
  ./venv/bin/python -m scripts.test_companion_mode \\
      --user-sketch logs/sketch_X.png \\
      --prompt "a cat, detailed Matsumoto style, ..." \\
      --output logs/companion_<ts>

  # VLM 自動 prompt (sketch → Qwen2.5-VL → Matsumoto companion prompt)
  ./venv/bin/python -m scripts.test_companion_mode \\
      --user-sketch logs/sketch_X.png \\
      --auto-prompt \\
      --output logs/companion_<ts>
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user-sketch", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--prompt", type=str, default=None,
                    help="object 生成の prompt (M16 detail style)。 "
                         "--auto-prompt 指定時は無視。")
    ap.add_argument("--auto-prompt", action="store_true",
                    help="VLM (Qwen2.5-VL) で sketch を識別して "
                         "Matsumoto-style companion prompt を自動生成する。")
    ap.add_argument("--confidence-threshold", type=float, default=0.3,
                    help="VLM 信頼度がこの値未満なら fallback prompt を使う。")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--resolution", type=int, default=1024,
                    help="Stage 1 解像度")
    args = ap.parse_args()

    if not args.auto_prompt and not args.prompt:
        ap.error("either --prompt or --auto-prompt is required")

    args.output.mkdir(parents=True, exist_ok=True)
    res = args.resolution

    from PIL import Image
    from modules.vectorizer import Vectorizer
    from modules.stroke_render import render_strokes_to_image
    from modules.blob_detect import (
        detect_blobs, union_bbox, find_largest_empty_rect)
    from modules.stroke_transform import (
        compute_strokes_bbox, transform_strokes, combine_strokes)

    # ============================================================
    # Step 0: --auto-prompt なら VLM で prompt 生成 (SDXL の前に unload)
    # ============================================================
    if args.auto_prompt:
        print(f"[companion] Step 0: --auto-prompt → VLM で prompt 自動生成")
        from modules.vlm import VLM
        from modules.prompt_builder import (
            build_prompt,
            COMPANION_TEMPLATE,
            COMPANION_FALLBACK_TEMPLATE,
        )
        sketch_img = Image.open(args.user_sketch).convert("RGB")
        with VLM(verbose=True) as vlm:
            guess = vlm.predict_intent(sketch_img)
        # VLM unload は with の __exit__ で。 SDXL を subprocess で
        # 起動するためここで VRAM を解放しておく必要がある。
        print(f"[companion]   guess: {guess.to_text()} "
              f"(conf={guess.confidence:.2f})")
        args.prompt = build_prompt(
            guess,
            confidence_threshold=args.confidence_threshold,
            base_template=COMPANION_TEMPLATE,
            fallback_template=COMPANION_FALLBACK_TEMPLATE,
        )
        print(f"[companion]   prompt: {args.prompt}")
        # 後段の参照用に prompt メタも残す
        (args.output / "00_auto_prompt.txt").write_text(
            f"subject_ja={guess.subject.ja}\n"
            f"location_ja={guess.location.ja}\n"
            f"action_ja={guess.action.ja}\n"
            f"confidence={guess.confidence:.3f}\n"
            f"prompt={args.prompt}\n",
            encoding="utf-8",
        )

    # ============================================================
    # Step 1: object mode v5 (M16) で 生成 (中央 detailed)
    # ============================================================
    print(f"[companion] Step 1: object mode 生成 (M16 detail)")
    s1_dir = args.output / "stage1"
    s1_dir.mkdir(exist_ok=True)
    res_code = subprocess.run([
        "./venv/bin/python", "-m", "scripts.compare_imagegen_models",
        "--guide", str(args.user_sketch),
        "--prompt", args.prompt,
        "--presets", "illustrious_v2_object",
        "--seed", str(args.seed),
        "--resolution", str(res),
        "--out", str(s1_dir),
    ], cwd=str(_ROOT)).returncode
    if res_code != 0:
        print(f"[companion] stage1 failed")
        return res_code
    gen_path = s1_dir / "illustrious_v2_object.png"
    print(f"[companion]   generated: {gen_path}")

    # ============================================================
    # Step 2: Vectorize 生成画像 → centered strokes
    # ============================================================
    print(f"[companion] Step 2: Vectorize 生成画像")
    vec = Vectorizer()
    gen_img = Image.open(gen_path)
    gen_r = vec.vectorize(generated_image=gen_img, user_image=None)
    gen_strokes = gen_r.strokes
    print(f"[companion]   generated strokes: {gen_r.n_strokes} / {gen_r.n_points} pts")

    # ============================================================
    # Step 3: 入力 sketch から blob 検出 + 空白矩形計算
    # ============================================================
    print(f"[companion] Step 3: input blob 検出 + 空白地帯")
    input_img = Image.open(args.user_sketch).convert("L").resize((res, res))
    blobs = detect_blobs(input_img)
    print(f"[companion]   {len(blobs)} blobs in input:")
    for i, b in enumerate(blobs[:5]):
        print(f"[companion]     #{i+1} bbox={b.bbox} centroid=({b.centroid[0]:.0f},{b.centroid[1]:.0f}) area={b.area}")
    input_bbox = union_bbox(blobs)
    empty_rect = find_largest_empty_rect(input_bbox, res, res, padding=40, expand_bbox=30)
    print(f"[companion]   input union bbox: {input_bbox}")
    print(f"[companion]   empty rect (target): {empty_rect}")

    # ============================================================
    # Step 4: 生成 strokes の bbox → 空白矩形 に変換
    # ============================================================
    print(f"[companion] Step 4: 生成 strokes を 空白地帯に移動")
    gen_bbox = compute_strokes_bbox(gen_strokes)
    print(f"[companion]   gen strokes bbox: {gen_bbox}")
    transformed = transform_strokes(
        gen_strokes, gen_bbox, empty_rect, fit="contain")
    transformed_bbox = compute_strokes_bbox(transformed)
    print(f"[companion]   transformed bbox: {transformed_bbox}")

    # ============================================================
    # Step 5: 入力 sketch も Vectorize → input strokes (元位置)
    # ============================================================
    print(f"[companion] Step 5: input sketch を Vectorize")
    input_rgb = Image.open(args.user_sketch).convert("RGB").resize((res, res))
    input_r = vec.vectorize(generated_image=input_rgb, user_image=None)
    input_strokes = input_r.strokes
    print(f"[companion]   input strokes: {input_r.n_strokes} / {input_r.n_points} pts")

    # ============================================================
    # Step 6: combine + render
    # ============================================================
    print(f"[companion] Step 6: combine + render")
    combined = combine_strokes(input_strokes, transformed)
    combined_render = render_strokes_to_image(
        combined, width=res, height=res, line_width=2)
    combined_render.save(args.output / "30_companion_strokes.png")
    print(f"[companion]   saved: {args.output / '30_companion_strokes.png'}")
    # 個別保存も
    render_strokes_to_image(input_strokes, width=res, height=res, line_width=2
                            ).save(args.output / "20_input_strokes.png")
    render_strokes_to_image(transformed, width=res, height=res, line_width=2
                            ).save(args.output / "21_transformed_gen_strokes.png")

    print(f"\n[companion] DONE.")
    print(f"  total strokes: {len(combined)}")
    print(f"  total points : {sum(len(s) for s in combined)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
