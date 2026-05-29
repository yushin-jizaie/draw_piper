#!/usr/bin/env python3
"""Companion mode: 位置ずらし (shift) と 位置合わせ (align) の 2 モード統合 script。

--placement で 2 つの経路を切替 (2026-05-28 統合):

  shift (default、 M16 位置ずらしモード):
    1. 入力 sketch を object preset (M16) で生成 (中央 detailed)
    2. Vectorizer で生成 strokes (centered)
    3. 入力 sketch から cv2 で blob 検出 (input 占有領域)
    4. 入力 bbox を避けた 最大空白矩形を計算
    5. 生成 strokes を bbox → 空白矩形 に translate + scale
    6. 入力 sketch も Vectorize → 入力 strokes (元位置)
    7. 入力 strokes + transformed 生成 strokes = composite output
    → 入力 sketch 位置を変えずに、 隣に M16 画風 detailed object を 追加。

  align (位置合わせモード = F_angry_face 経路):
    入力 sketch をそのまま 2-stage IP-Adapter で stylize (位置キープ)。
    内部で test_ip_adapter_two_stage --category character を subprocess で呼ぶ。
    Stage 1 = illustrious_v2_inpaint (Plan E inpaint で構図確定)。
    Stage 2 = img2img + IP-Adapter (松本 style 転写、 strength 0.45 / ip_scale 0.6)。
    入力構図を保ったまま 画風だけ変換。

使用:
  # 位置ずらし (M16、 既存挙動)
  ./venv/bin/python -m scripts.test_companion_mode \\
      --user-sketch logs/sketch_X.png \\
      --auto-prompt \\
      --output logs/companion_<ts>

  # 位置合わせ (F_angry_face 経路、 character pool から auto pick)
  ./venv/bin/python -m scripts.test_companion_mode \\
      --user-sketch logs/sketch_X.png \\
      --placement align --auto-prompt \\
      --output logs/align_<ts>

  # 位置合わせ + style ref 明示指定 (F_angry_face 再現コマンドと等価)
  ./venv/bin/python -m scripts.test_companion_mode \\
      --user-sketch SKETCH.png \\
      --placement align \\
      --style-ref training/matsumoto_taiyo/raw/IMG_4311.JPG \\
      --prompt "1boy, solo, ..." \\
      --output logs/align_<ts> --seed 42
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
                    help="生成 prompt。 placement=shift なら object 生成 (M16)、 "
                         "placement=align なら Stage 1 (Plan E inpaint) の prompt。 "
                         "--auto-prompt 指定時は無視。")
    ap.add_argument("--auto-prompt", action="store_true",
                    help="VLM (Qwen2.5-VL) で sketch を識別して prompt を自動生成。 "
                         "placement=shift → COMPANION_TEMPLATE (Matsumoto companion)、 "
                         "placement=align → CHARACTER_TEMPLATE (M15/M16 character)。")
    ap.add_argument("--confidence-threshold", type=float, default=0.3,
                    help="VLM 信頼度がこの値未満なら fallback prompt を使う。")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--resolution", type=int, default=1024,
                    help="Stage 1 解像度 (shift モード) / align モードは 768 固定 "
                         "(F_angry_face 時と同じ 1024 → 768 経路で test_ip_adapter_two_stage 内で処理)")
    # ============================================================
    # 位置合わせ vs 位置ずらし モード切替 (2026-05-28 統合)
    # shift = M16 object preset で生成 → cv2 blob で 入力の空白地帯に配置
    # align = test_ip_adapter_two_stage --category character 経路
    #         (F_angry_face 時の Plan E inpaint → IP-Adapter 2-stage)
    # ============================================================
    ap.add_argument("--placement", type=str, default="shift",
                    choices=["shift", "align"],
                    help="shift (default) = 位置ずらしモード: M16 object 生成 → "
                         "cv2 blob で 入力の空白地帯に配置。 "
                         "align = 位置合わせモード: 入力 sketch をそのまま 2-stage "
                         "IP-Adapter で stylize、 位置キープ (F_angry_face 経路、 "
                         "内部で test_ip_adapter_two_stage --category character)")
    # align モード専用 引数 (shift では無視される)
    ap.add_argument("--style-ref", type=Path, default=None,
                    help="[align] style ref 画像。 省略時は character pool から auto pick "
                         "(test_ip_adapter_two_stage の STYLE_REF_POOLS['character'])")
    ap.add_argument("--stage2-strength", type=float, default=0.45,
                    help="[align] img2img strength (F_angry_face 時と同じ 0.45 default)")
    ap.add_argument("--ip-scale", type=float, default=0.6,
                    help="[align] IP-Adapter scale (F_angry_face 時と同じ 0.6 default)")
    ap.add_argument("--skip-stage2", action="store_true",
                    help="[align] Stage 2 (IP-Adapter) を skip し Stage 1 inpaint "
                         "出力をそのまま 最終結果に。 character pool の人物 ref が "
                         "object 入力で anthropomorphic 化させる副作用を回避できる。")
    ap.add_argument("--companion-prompt-version", type=str, default="best",
                    choices=["v1", "v2", "v3", "best"],
                    help="[shift] VLM の companion 提案 prompt パターン: "
                         "v1/v2/v3 = 単独使用、 "
                         "best (default) = 3 つ全部呼んで VLM judge で 1 つに絞る")
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
    # placement に応じた template を選択:
    #   shift  → COMPANION_TEMPLATE  (Matsumoto companion = M16 object)
    #   align  → CHARACTER_TEMPLATE  (M15/M16 character = 2-stage Plan E)
    # ============================================================
    if args.auto_prompt:
        print(f"[companion] Step 0: --auto-prompt → VLM で prompt 自動生成 "
              f"(placement={args.placement})")
        from modules.vlm import VLM
        from modules.prompt_builder import (
            build_prompt,
            COMPANION_TEMPLATE, COMPANION_FALLBACK_TEMPLATE,
            CHARACTER_TEMPLATE, CHARACTER_FALLBACK_TEMPLATE,
        )
        sketch_img = Image.open(args.user_sketch).convert("RGB")
        # 2026-05-29 shift モード改修: 入力主題と「同じもの」 を生成するのではなく、
        # 入力主題から連想される「別の subject」 を VLM に提案させて生成する。
        # 例えば: 木 → 鳥や雲、 ハサミ → 持つ手、 自転車 → 乗る人、 傘 → 雨。
        # VLM への prompt は一般化されており、 ハードコード例ではなく方向性ヒント。
        companion_subject = None
        companion_candidates = []   # 全候補 [(version, name)]
        companion_judge_idx = -1    # judge が選んだ index (0 始まり)
        with VLM(verbose=True) as vlm:
            guess = vlm.predict_intent(sketch_img)
            if args.placement == "shift":
                if args.companion_prompt_version == "best":
                    # 3 候補生成 → VLM judge で 1 つ選定
                    for v in ("v1", "v2", "v3"):
                        c = vlm.predict_companion_subject(
                            sketch_img, prompt_version=v)
                        companion_candidates.append((v, c))
                    idx, chosen, _ = vlm.pick_best_companion(
                        sketch_img, companion_candidates)
                    companion_judge_idx = idx
                    companion_subject = chosen
                else:
                    companion_subject = vlm.predict_companion_subject(
                        sketch_img,
                        prompt_version=args.companion_prompt_version,
                    )
                    companion_candidates = [
                        (args.companion_prompt_version, companion_subject)
                    ]
                    companion_judge_idx = 0
        # VLM unload は with の __exit__ で。 SDXL を subprocess で
        # 起動するためここで VRAM を解放しておく必要がある。
        print(f"[companion]   guess: {guess.to_text()} "
              f"(conf={guess.confidence:.2f})")
        if args.placement == "align":
            base_tpl, fb_tpl = CHARACTER_TEMPLATE, CHARACTER_FALLBACK_TEMPLATE
            args.prompt = build_prompt(
                guess,
                confidence_threshold=args.confidence_threshold,
                base_template=base_tpl,
                fallback_template=fb_tpl,
            )
        else:   # placement == "shift"
            print(f"[companion]   companion subject (VLM 提案): {companion_subject}")
            # shift モード: 入力主題ではなく companion subject を SDXL に渡す
            # SDXL prompt は Matsumoto style + companion subject で構築
            args.prompt = (
                f"a detailed Matsumoto-style {companion_subject}, "
                f"manga style, expressive ink lines, "
                f"single continuous black line on plain white background, "
                f"clean smooth strokes, illustrative, no shading"
            )
        print(f"[companion]   prompt: {args.prompt}")
        # 後段の参照用に prompt メタも残す
        candidates_text = "\n".join(
            f"  {v}: {c}" for v, c in companion_candidates
        ) if companion_candidates else "  (none)"
        (args.output / "00_auto_prompt.txt").write_text(
            f"placement={args.placement}\n"
            f"subject_ja={guess.subject.ja}\n"
            f"location_ja={guess.location.ja}\n"
            f"action_ja={guess.action.ja}\n"
            f"confidence={guess.confidence:.3f}\n"
            f"companion_subject={companion_subject or ''}\n"
            f"companion_prompt_version={args.companion_prompt_version}\n"
            f"companion_candidates:\n{candidates_text}\n"
            f"companion_judge_idx={companion_judge_idx}\n"
            f"prompt={args.prompt}\n",
            encoding="utf-8",
        )

    # ============================================================
    # placement=align: F_angry_face 経路 (test_ip_adapter_two_stage --category
    # character) を subprocess で呼んで、 そこで完結 (Stage 1 + Stage 2 + Vectorize)
    # ============================================================
    if args.placement == "align":
        print(f"[companion] === ALIGN MODE (位置合わせ、 F_angry_face 経路) ===")
        cmd = [
            "./venv/bin/python", "-m", "scripts.test_ip_adapter_two_stage",
            "--user-sketch", str(args.user_sketch),
            "--category", "character",
            "--output", str(args.output),
            "--stage1-prompt", args.prompt,
            "--stage2-strength", str(args.stage2_strength),
            "--ip-scale", str(args.ip_scale),
            "--seed", str(args.seed),
            "--stage1-resolution", "1024",
            "--resolution", "768",
        ]
        if args.style_ref is not None:
            cmd += ["--style-ref", str(args.style_ref)]
        if args.skip_stage2:
            cmd.append("--skip-stage2")
        print(f"[companion]   subprocess: {' '.join(cmd)}")
        res_code = subprocess.run(cmd, cwd=str(_ROOT)).returncode
        if res_code != 0:
            print(f"[companion] align mode failed (exit {res_code})")
            return res_code
        print(f"\n[companion] ALIGN DONE.")
        print(f"  出力: {args.output}/ (stage1/ + 20_final_stage2_*.png + "
              f"30_vectorized_strokes.png)")
        return 0

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
