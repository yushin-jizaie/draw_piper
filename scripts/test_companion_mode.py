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
    ap.add_argument("--resolution", type=str, default=None,
                    help="解像度。 'N' / 'WxH' 可。 省略時は panel aspect の "
                         "SDXL bucket (= ボードと同じ縦横比)。 shift モードの合成 "
                         "キャンバスと align モードの 2-stage 経路の両方に適用。")
    ap.add_argument("--gen-resolution", type=int, default=768,
                    help="[shift] companion 生成の正方形解像度 (既定 768)。 "
                         "縦長生成の歪み回避のため canvas と分離。")
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
    ap.add_argument("--with-composition", action="store_true",
                    help="VLM の predict_composition_refinement を呼び、 SDXL "
                         "prompt に「魅力的な構図」 phrase を embed (例: 'looking "
                         "back over shoulder')。 modules/vlm.py に method 必要。")
    ap.add_argument("--gacha-n", type=int, default=1,
                    help="Phase 2 (2026-05-30): align モードで N variants を seed "
                         "sweep 生成 (master-seed=100 で gacha と同じ seed 列)。 "
                         "default 1 = 単発。 n>=2 で N variants を v1_seed*/v2_seed*/... "
                         "サブ dir に出力 + gacha_grid.png 合成。 shift モードでは無視。")
    ap.add_argument("--gacha-master-seed", type=int, default=100,
                    help="[--gacha-n N>=2 時] seed 生成 base。 同じ master-seed なら "
                         "同じ seed 列。 default 100 (既存 gacha と一致)。")
    ap.add_argument("--companion-prompt-version", type=str, default="best",
                    choices=["v1", "v2", "v3", "best"],
                    help="[shift] VLM の companion 提案 prompt パターン: "
                         "v1/v2/v3 = 単独使用、 "
                         "best (default) = 3 つ全部呼んで VLM judge で 1 つに絞る")
    ap.add_argument("--literal-only", action="store_true",
                    help="[shift] テスト用: companion 推論をやめ、 VLM の "
                         "describe_literal (何に見えるか、 例: circle) を subject に "
                         "して shift 配置する (入力と別物ではなく、 入力そのものを "
                         "空白地帯に描く)。 適切なストロークが出るかの検証用。")
    args = ap.parse_args()

    if not args.auto_prompt and not args.prompt and not args.literal_only:
        ap.error("either --prompt, --auto-prompt, or --literal-only is required")
    if args.literal_only and args.placement != "shift":
        ap.error("--literal-only は placement=shift 専用です")

    args.output.mkdir(parents=True, exist_ok=True)

    # 解像度: 省略時は panel aspect の SDXL bucket (= ボードと同じ縦横比)。
    from modules.panel_geometry import parse_resolution, panel_image_resolution
    res_w, res_h = parse_resolution(args.resolution) or panel_image_resolution()
    res_arg = f"{res_w}x{res_h}"
    # companion (shift) は生成画像を「空白に置く」 だけなので、 生成は正方形で
    # 行い contain で配置する。 縦長で生成すると被写体が引き伸ばされて歪む
    # (2026-06-01 検証: 縦長生成の猫が細長く崩れた) 問題への対処。
    gen_res_arg = f"{args.gen_resolution}x{args.gen_resolution}"
    print(f"[companion] resolution: {res_w}x{res_h} (canvas) / "
          f"{gen_res_arg} (companion 生成)")

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
    if args.auto_prompt or args.literal_only:
        mode = "literal-only" if args.literal_only else "auto-prompt"
        print(f"[companion] Step 0: --{mode} → VLM で prompt 自動生成 "
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
        # Phase 1 (2026-05-30): VLM 構図 refinement (--with-composition フラグ時)
        composition_refinement = ""
        with VLM(verbose=True) as vlm:
            if args.literal_only:
                # テスト用: companion 推論をやめ、 「何に見えるか」 を subject に。
                # 円→"circle" を空白地帯に shift 配置する。
                companion_subject = vlm.describe_literal(sketch_img)
                companion_candidates = [("literal", companion_subject)]
                companion_judge_idx = 0
                guess = None
                print(f"[companion]   literal subject: {companion_subject!r} "
                      f"(companion 推論スキップ)")
                # 残りの predict_intent / companion 推論 / composition は skip
                # (literal_only は shift 専用)
                _skip_rest = True
            else:
                _skip_rest = False
                guess = vlm.predict_intent(sketch_img)
            if not _skip_rest and args.with_composition:
                _fn = getattr(vlm, "predict_composition_refinement", None)
                if callable(_fn):
                    try:
                        composition_refinement = _fn(sketch_img)
                    except Exception as _e:
                        print(f"[companion] composition skip: {_e}")
                else:
                    print("[companion] predict_composition_refinement not found "
                          "(skip --with-composition)")
            if not _skip_rest and args.placement == "shift":
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
        if guess is not None:
            print(f"[companion]   guess: {guess.to_text()} "
                  f"(conf={guess.confidence:.2f})")
        comp_frag = f", {composition_refinement}" if composition_refinement else ""
        if args.placement == "align":
            base_tpl, fb_tpl = CHARACTER_TEMPLATE, CHARACTER_FALLBACK_TEMPLATE
            base_prompt = build_prompt(
                guess,
                confidence_threshold=args.confidence_threshold,
                base_template=base_tpl,
                fallback_template=fb_tpl,
            )
            # composition を base_prompt の subject 直後に挿入
            if composition_refinement and "," in base_prompt:
                head, rest = base_prompt.split(",", 1)
                args.prompt = f"{head}{comp_frag},{rest}"
            else:
                args.prompt = base_prompt
        elif args.literal_only:   # placement == "shift" + literal-only
            # literal (例: circle) は単純図形。 companion 用の重いテンプレ
            # (「20-40 strokes」「filling 80%」「discrete contours per element」 等) は
            # 単純図形には過剰指定で SDXL が崩壊し 真っ白 を出す (実測)。
            # → シンプルな線画 prompt を使う。
            print(f"[companion]   literal subject: {companion_subject}")
            args.prompt = (
                f"a simple line drawing of a {companion_subject}, "
                f"large central subject, bold thick contours, "
                f"single continuous black line on plain white background, "
                f"clean smooth strokes, minimalist illustration, no shading"
            )
        else:   # placement == "shift"
            print(f"[companion]   companion subject (VLM 提案): {companion_subject}")
            # shift モード: 入力主題ではなく companion subject を SDXL に渡す
            # SDXL prompt は Matsumoto style + companion subject (+ composition) で構築
            # 2026-05-30: object × shift で細部消失する問題への対処として
            # 「large subject + bold thick contours」 と Frida 制約を追加。
            # 生成画像で subject を大きく + 線を太く描かせて、 vectorize で
            # 細部 (鳥の顔、 cat の目鼻) が残るようにする。
            args.prompt = (
                f"a stylish Matsumoto-style {companion_subject}{comp_frag}, "
                # 2026-05-31: 構図・デザイン性重視。 簡潔な線でも魅力的に。
                f"dynamic striking composition, appealing bold design, interesting angle, "
                f"large central subject filling 80% of the frame, "
                f"bold confident contours, simple iconic shape, "
                f"manga style, expressive ink lines, "
                f"single continuous black line on plain white background, "
                f"clean smooth strokes, illustrative, "
                f"discrete clean contours per element, no shading, "
                f"no hatching, no cross-hatching, "
                f"approximately 20 to 40 separate strokes"
            )
        print(f"[companion]   prompt: {args.prompt}")
        # 後段の参照用に prompt メタも残す
        candidates_text = "\n".join(
            f"  {v}: {c}" for v, c in companion_candidates
        ) if companion_candidates else "  (none)"
        (args.output / "00_auto_prompt.txt").write_text(
            f"placement={args.placement}\n"
            f"mode={mode}\n"
            f"subject_ja={guess.subject.ja if guess else '(literal-only)'}\n"
            f"location_ja={guess.location.ja if guess else ''}\n"
            f"action_ja={guess.action.ja if guess else ''}\n"
            f"confidence={guess.confidence if guess else 0.0:.3f}\n"
            f"companion_subject={companion_subject or ''}\n"
            f"companion_prompt_version={args.companion_prompt_version}\n"
            f"companion_candidates:\n{candidates_text}\n"
            f"companion_judge_idx={companion_judge_idx}\n"
            f"composition_refinement={composition_refinement or ''}\n"
            f"prompt={args.prompt}\n",
            encoding="utf-8",
        )

    # ============================================================
    # placement=align: F_angry_face 経路 (test_ip_adapter_two_stage --category
    # character) を subprocess で呼んで、 そこで完結 (Stage 1 + Stage 2 + Vectorize)
    # ============================================================
    if args.placement == "align":
        print(f"[companion] === ALIGN MODE (位置合わせ、 F_angry_face 経路) ===")
        # Phase 2: --gacha-n N で seed sweep variants 生成
        if args.gacha_n >= 2:
            import random as _random
            rng = _random.Random(args.gacha_master_seed)
            seeds = [rng.randint(0, 100000) for _ in range(args.gacha_n)]
            print(f"[companion] align gacha N={args.gacha_n}, seeds={seeds}")
            failed = 0
            for i, sd in enumerate(seeds):
                sub = args.output / f"v{i+1}_seed{sd}"
                sub.mkdir(parents=True, exist_ok=True)
                cmd = [
                    "./venv/bin/python", "-m", "scripts.test_ip_adapter_two_stage",
                    "--user-sketch", str(args.user_sketch),
                    "--category", "character",
                    "--output", str(sub),
                    "--stage1-prompt", args.prompt,
                    "--stage2-strength", str(args.stage2_strength),
                    "--ip-scale", str(args.ip_scale),
                    "--seed", str(sd),
                    "--stage1-resolution", res_arg,
                    "--resolution", res_arg,
                ]
                if args.style_ref is not None:
                    cmd += ["--style-ref", str(args.style_ref)]
                if args.skip_stage2:
                    cmd.append("--skip-stage2")
                print(f"[companion] === {i+1}/{args.gacha_n}: seed={sd} ===")
                rc = subprocess.run(cmd, cwd=str(_ROOT)).returncode
                if rc != 0:
                    print(f"[companion]   variant failed rc={rc}")
                    failed += 1
            print(f"\n[companion] ALIGN GACHA DONE. {args.gacha_n - failed}/"
                  f"{args.gacha_n} succeeded")
            print(f"  出力: {args.output}/v{{1..{args.gacha_n}}}_seed*/")
            return 0 if failed == 0 else 1
        cmd = [
            "./venv/bin/python", "-m", "scripts.test_ip_adapter_two_stage",
            "--user-sketch", str(args.user_sketch),
            "--category", "character",
            "--output", str(args.output),
            "--stage1-prompt", args.prompt,
            "--stage2-strength", str(args.stage2_strength),
            "--ip-scale", str(args.ip_scale),
            "--seed", str(args.seed),
            "--stage1-resolution", res_arg,
            "--resolution", res_arg,
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
        "--resolution", gen_res_arg,
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
    input_img = Image.open(args.user_sketch).convert("L").resize((res_w, res_h))
    blobs = detect_blobs(input_img)
    print(f"[companion]   {len(blobs)} blobs in input:")
    for i, b in enumerate(blobs[:5]):
        print(f"[companion]     #{i+1} bbox={b.bbox} centroid=({b.centroid[0]:.0f},{b.centroid[1]:.0f}) area={b.area}")
    input_bbox = union_bbox(blobs)
    empty_rect = find_largest_empty_rect(input_bbox, res_w, res_h, padding=15, expand_bbox=20)
    # object 系で input が canvas 中央に大きいと empty_rect が狭くなり companion が
    # 小さく描画 → vectorize で細部 (鳥の顔、 cat のヒゲ等) が消える問題への対処。
    # 配置先が canvas の 30% 未満なら canvas 全体に拡張 (input と重なる代わりに
    # companion を 大きく描画して 細部を保持)。
    min_area_ratio = 0.30
    if empty_rect[2] * empty_rect[3] < res_w * res_h * min_area_ratio:
        old = empty_rect
        empty_rect = (15, 15, res_w - 30, res_h - 30)
        print(f"[companion]   empty_rect {old} too small "
              f"(<{min_area_ratio:.0%} of canvas), expanded to full canvas: {empty_rect}")
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
    input_rgb = Image.open(args.user_sketch).convert("RGB").resize((res_w, res_h))
    input_r = vec.vectorize(generated_image=input_rgb, user_image=None)
    input_strokes = input_r.strokes
    print(f"[companion]   input strokes: {input_r.n_strokes} / {input_r.n_points} pts")

    # ============================================================
    # Step 6: render (companion のみ、 入力線は除外)
    # 本番フローではユーザーの絵は既にホワイトボード上にあるため、 ロボットは
    # 空白地帯の companion だけ描けばよい (入力線の再描画 = 二重描きを避ける)。
    # → 30_companion_strokes.png は transformed companion のみ。
    # ============================================================
    print(f"[companion] Step 6: render (companion のみ、 入力線は除外)")
    final_strokes = transformed
    render_strokes_to_image(
        final_strokes, width=res_w, height=res_h, line_width=2
    ).save(args.output / "30_companion_strokes.png")
    print(f"[companion]   saved: {args.output / '30_companion_strokes.png'}")
    # 参照用に input / transformed も個別保存
    render_strokes_to_image(input_strokes, width=res_w, height=res_h, line_width=2
                            ).save(args.output / "20_input_strokes.png")
    render_strokes_to_image(transformed, width=res_w, height=res_h, line_width=2
                            ).save(args.output / "21_transformed_gen_strokes.png")
    # 入力+companion の合成プレビュー (参考、 ロボットには送らない)
    render_strokes_to_image(
        combine_strokes(input_strokes, transformed),
        width=res_w, height=res_h, line_width=2
    ).save(args.output / "31_input_plus_companion_preview.png")

    print(f"\n[companion] DONE.")
    print(f"  companion strokes: {len(final_strokes)}")
    print(f"  companion points : {sum(len(s) for s in final_strokes)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
