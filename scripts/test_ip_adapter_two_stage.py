#!/usr/bin/env python3
"""IP-Adapter 2 段構成: 構図 (Plan E) + style (IP-Adapter) を分離する。

問題: 1 段で IP-Adapter を使うと style ref の 構図 (顔=頭、 残り=体) が
input sketch (中央に小さく face oval) と競合 → face が 胴体中央 に置かれる。

解法 (2 段):
  Stage 1: 既存 Plan E (illustrious_v2_inpaint, IP-Adapter なし) で
           「顔保持 + 体描き足し」 の構図確定版 clean line art を生成
  Stage 2: Stage 1 出力を init image として img2img + IP-Adapter
           低 strength (0.35-0.50) で style だけ転写、 構図保持

これにより:
- 顔位置 = Stage 1 で確定 (元 sketch と整合)
- 線質感 = Stage 2 で Matsumoto に近づく

使用:
  ./venv/bin/python -m scripts.test_ip_adapter_two_stage \\
      --user-sketch scripts/test_sketch.jpg \\
      --style-ref training/matsumoto_taiyo/raw/IMG_4311.JPG \\
      --output logs/ip_2stage_$(date +%Y%m%d_%H%M%S) \\
      --stage1-prompt "1boy, solo, young boy with full body, messy hair, surprised expression, simple t-shirt, standing" \\
      --stage2-strength 0.45 --ip-scale 0.6
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


# カテゴリ別 style ref pool (松本作品の raw 画像から curate)
# 改訂 2026-05-28: urban mode 廃止 (IP-Adapter ref が character composition を
# 学んでしまう構造的問題)。 object mode (img2img、 IP-Adapter off) に統合。
STYLE_REF_POOLS = {
    "character": [
        "training/matsumoto_taiyo/raw/IMG_4311.JPG",       # 花男表紙 2 選手
        "training/matsumoto_taiyo/raw/f341cbadd1aede96e2fdef7bc84cc3c6.jpg",  # 3 人正面
        "training/matsumoto_taiyo/raw/feccbf2756d31b496b18e31694969146.jpg",  # Peco
        "training/matsumoto_taiyo/raw/IMG_4324.JPG",       # ゴーグル少年
        "training/matsumoto_taiyo/raw/o0600045013450720343.jpg",  # 5 人並び
    ],
    # 2026-05-29 rebalance: object pool に raw から 5 枚試行投入
    # (style-pool-rebalance-20260529 branch)。 raw に純粋 object 画像は無いが、
    # キャラ + 街 / 建物 / 海岸 のシーン作品を試行。 テキスト / ロゴ混入リスクあり。
    "object": [
        "training/matsumoto_taiyo/raw/EdvzOK7U8AAqpwO.jpg",   # 建物群 + 漫画コマ (テキストあり)
        "training/matsumoto_taiyo/raw/IMG_4314.JPG",          # 街並み逆さま + 落下キャラ
        "training/matsumoto_taiyo/raw/IMG_4310.JPG",          # graniph collab、 街並み + キャラ
        "training/matsumoto_taiyo/raw/1090748_300.jpg",       # 海岸 + 子供 (raw 唯一のカラー画像)
        "training/matsumoto_taiyo/raw/IMG_4315.JPG",          # 街並み + キャラ 2 人
    ],
    "other": [],    # IP-Adapter off + Stage 1 は inpaint (元の動作維持)
}


# category → stage1_preset 対応
# 2026-05-29 (style-pool-rebalance) ROLLBACK: LoRA 入れたら detail がシンプル化過剰になり、
# phase-e multi_mode_v5 の理想出力 (LoRA 抜き) より劣化した。 ユーザー判断で
# phase-e v5 = illustrious_v2_object (text2img + CN 0.65、 LoRA なし) に戻し。
# v0 LoRA matsumoto_taiyo.safetensors は raw 36 枚の smiley face コマ等で
# シンプル化を強く誘導するバイアスを持つ、 が判明。 LoRA 再学習までは画像生成
# 経路では使わない。
CATEGORY_TO_STAGE1_PRESET = {
    "character": "illustrious_v2_inpaint",     # inpaint で構図確定 (phase-e v5 character mode)
    "object":    "illustrious_v2_object",      # text2img + CN 0.65 soft hint (phase-e v5)
    "other":     "illustrious_v2_inpaint",     # フォールバック
}


def _resolve_style_ref(args) -> Path:
    """--style-ref が指定されてればそれ、 なければ category から ランダム選択。"""
    if args.style_ref is not None:
        return args.style_ref
    if args.category not in STYLE_REF_POOLS:
        raise ValueError(f"unknown category: {args.category}")
    pool = STYLE_REF_POOLS[args.category]
    if not pool:
        return None    # other = IP-Adapter off
    import random
    rng = random.Random(args.seed)
    chosen = rng.choice(pool)
    return Path(chosen)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user-sketch", type=Path, required=True)
    ap.add_argument("--style-ref", type=Path, default=None,
                    help="明示指定の style ref。 未指定なら --category から ランダム選択")
    ap.add_argument("--category", type=str, default="character",
                    choices=["character", "object", "other"],
                    help="入力 sketch カテゴリ。 character=人間 (inpaint+IP-Adapter)、 "
                         "object=物体・動物・植物 (img2img、 IP-Adapter off)、 "
                         "other=その他 (inpaint、 IP-Adapter off)")
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--stage1-preset", type=str, default=None,
                    help="Stage 1 で使う preset。 None なら category に応じて自動選択 "
                         f"({CATEGORY_TO_STAGE1_PRESET})")
    ap.add_argument("--stage1-prompt", type=str,
                    default="1boy, solo, young boy with full body, "
                            "messy hair, surprised expression, simple t-shirt")
    ap.add_argument("--stage2-prompt", type=str, default=None,
                    help="Stage 2 prompt。 省略時は stage1-prompt と同じ")
    ap.add_argument("--stage2-strength", type=float, default=0.45,
                    help="img2img strength (0.3=構図維持、 0.6=大きく変える)")
    ap.add_argument("--ip-scale", type=float, default=0.6)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--resolution", type=str, default=None,
                    help="Stage 2 (IP-Adapter) 用の解像度。 'N' / 'WxH' 可。 "
                         "省略時は panel aspect の SDXL bucket (= ボードと同じ縦横比)。")
    ap.add_argument("--stage1-resolution", type=str, default=None,
                    help="Stage 1 (Plan E) 用の解像度。 'N' / 'WxH' 可。 "
                         "省略時は panel aspect の SDXL bucket。")
    # 2026-05-29 (style-pool-rebalance): Stage 2 (IP-Adapter) skip フラグ。
    # Stage 1 を LoRA 込み preset で強化したので、 Stage 2 不要な場面が増えた。
    ap.add_argument("--skip-stage2", action="store_true",
                    help="Stage 2 (IP-Adapter style 転写) を skip し、 Stage 1 出力を "
                         "そのまま 最終結果に。 Vectorize + render は実行。 "
                         "実行時間 ~64s → ~30s に短縮、 Stage 2 の副作用 (style 過剰 / "
                         "副題材化) も回避可能。")
    args = ap.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)

    # 解像度: 省略時は panel aspect の SDXL bucket (= ボードと同じ縦横比)。
    # Stage 1 / Stage 2 を同一解像度で揃え、 init/final のアスペクト不整合を防ぐ。
    from modules.panel_geometry import parse_resolution, panel_image_resolution
    panel_wh = panel_image_resolution()
    res_w, res_h = parse_resolution(args.resolution) or panel_wh
    s1_w, s1_h = parse_resolution(args.stage1_resolution) or panel_wh
    print(f"[2stage] resolution: stage1={s1_w}x{s1_h}, stage2={res_w}x{res_h}")

    # stage1 preset 解決
    if args.stage1_preset is None:
        args.stage1_preset = CATEGORY_TO_STAGE1_PRESET[args.category]
    print(f"[2stage] category={args.category}, stage1_preset={args.stage1_preset}")

    # --skip-stage2: Stage 2 自体を skip (style_ref を None 扱いに)
    # 後続の "if style_ref is None" 分岐で Stage 2 skip 経路に入る
    if args.skip_stage2:
        print(f"[2stage] --skip-stage2: Stage 1 + Vectorize のみ実行")
        style_ref = None
    else:
        # style ref 解決 (--style-ref 直指定 or --category から ランダム選択)
        style_ref = _resolve_style_ref(args)
    if style_ref is None:
        print(f"[2stage] Stage 2 skip (--skip-stage2 or category 'other')")
    else:
        if not style_ref.exists():
            print(f"[2stage] style ref not found: {style_ref}")
            return 2
        print(f"[2stage] style ref ({args.category}): {style_ref}")

    from PIL import Image
    import torch

    # ============================================================
    # Stage 1: illustrious_v2_inpaint で構図確定 (compare_imagegen_models 経由)
    # ============================================================
    print(f"[2stage] Stage 1: 構図確定 ({args.stage1_preset})")
    import subprocess
    s1_dir = args.output / "stage1"
    s1_dir.mkdir(exist_ok=True)
    res_code = subprocess.run([
        "./venv/bin/python", "-m", "scripts.compare_imagegen_models",
        "--guide", str(args.user_sketch),
        "--prompt", args.stage1_prompt,
        "--presets", args.stage1_preset,
        "--seed", str(args.seed),
        "--resolution", f"{s1_w}x{s1_h}",
        "--out", str(s1_dir),
    ], cwd=str(_ROOT)).returncode
    if res_code != 0:
        print(f"[2stage] Stage 1 failed (exit {res_code})")
        return res_code
    s1_out = s1_dir / f"{args.stage1_preset}.png"
    if not s1_out.exists():
        print(f"[2stage] Stage 1 output not found: {s1_out}")
        return 2
    print(f"[2stage] Stage 1 ok: {s1_out}")

    # ============================================================
    # Stage 2: img2img + IP-Adapter で style 転写 (構図維持)
    # other category なら Stage 2 skip (Plan E のみで clean lineart)
    # ============================================================
    if style_ref is None:
        # Stage 2 skip mode: Stage 1 の出力をそのまま 最終結果に
        from modules.vectorizer import Vectorizer
        from modules.stroke_render import render_strokes_to_image
        final = Image.open(s1_out).convert("RGB").resize((res_w, res_h))
        final.save(args.output / f"20_final_no_ip_adapter.png")
        vec = Vectorizer()
        # object mode は img2img で sketch を stylize するため、 Vectorizer
        # に user_image を渡すと diff で元線が引かれて 0 strokes になる。
        # → user_image=None で 全 strokes を抽出。
        # character/other は inpaint なので user_image diff で「追加された線」
        # だけ抽出するのが妥当 (元 sketch は別途 robot 側で描画想定)。
        if args.category == "object":
            r = vec.vectorize(generated_image=final, user_image=None)
            mode_label = "object (no diff)"
        else:
            user_full = Image.open(args.user_sketch).convert("RGB").resize((res_w, res_h))
            r = vec.vectorize(generated_image=final, user_image=user_full)
            mode_label = "other (diff vs user)"
        rendered = render_strokes_to_image(
            r.strokes, width=r.image_shape[1], height=r.image_shape[0],
            line_width=2)
        rendered.save(args.output / "30_vectorized_strokes.png")
        print(f"[2stage] {mode_label}: {r.n_strokes} strokes, {r.n_points} pts")
        return 0

    print(f"[2stage] Stage 2: IP-Adapter style transfer")
    from diffusers import StableDiffusionXLImg2ImgPipeline

    # variant=fp16 が無い場合のフォールバック
    try:
        pipe = StableDiffusionXLImg2ImgPipeline.from_pretrained(
            "John6666/illustrious-xl-early-release-v0-sdxl",
            torch_dtype=torch.float16,
            variant="fp16",
            use_safetensors=True,
        )
    except (ValueError, OSError) as e:
        print(f"[2stage] variant=fp16 not available, retrying without: {e}")
        pipe = StableDiffusionXLImg2ImgPipeline.from_pretrained(
            "John6666/illustrious-xl-early-release-v0-sdxl",
            torch_dtype=torch.float16,
            use_safetensors=True,
        )
    pipe = pipe.to("cuda")
    pipe.enable_vae_tiling()
    pipe.enable_vae_slicing()
    pipe.load_ip_adapter(
        "h94/IP-Adapter",
        subfolder="sdxl_models",
        weight_name="ip-adapter_sdxl.safetensors",
    )
    pipe.set_ip_adapter_scale(args.ip_scale)

    init = Image.open(s1_out).convert("RGB").resize((res_w, res_h))
    style_ref_img = Image.open(style_ref).convert("RGB").resize((res_w, res_h))
    init.save(args.output / "10_init_from_stage1.png")
    style_ref_img.save(args.output / "11_style_ref.png")

    stage2_prompt = args.stage2_prompt or args.stage1_prompt
    style_hint = (", monochrome, greyscale, lineart, sketch, "
                  "white_background, simple_background")
    full = stage2_prompt + style_hint
    negative = ("color, colored, blue background, cyan, sky, gradient, "
                "hatching, crosshatch, screentone, halftone, dot pattern, "
                "filled background, paper texture, scribble, sketchy, "
                "shading, gray, sepia, "
                "watermark, signature, text, frame, border, "
                "blurry, noise, jpeg artifacts")

    gen = torch.Generator("cuda").manual_seed(args.seed)
    t0 = time.time()
    result = pipe(
        prompt=full,
        negative_prompt=negative,
        image=init,
        ip_adapter_image=style_ref_img,
        strength=args.stage2_strength,
        num_inference_steps=28,
        guidance_scale=6.5,
        generator=gen,
    )
    elapsed = time.time() - t0
    out = args.output / (
        f"20_stage2_str{args.stage2_strength:.2f}"
        f"_ip{args.ip_scale:.2f}.png")
    result.images[0].save(out)
    print(f"[2stage] Stage 2 saved {out} ({elapsed:.1f}s)")

    # Vectorize もまとめて
    from modules.vectorizer import Vectorizer
    from modules.stroke_render import render_strokes_to_image
    vec = Vectorizer()
    user_full = Image.open(args.user_sketch).convert("RGB").resize((res_w, res_h))
    r = vec.vectorize(generated_image=result.images[0], user_image=user_full)
    rendered = render_strokes_to_image(
        r.strokes, width=r.image_shape[1], height=r.image_shape[0],
        line_width=2)
    rendered.save(args.output / "30_vectorized_strokes.png")
    print(f"[2stage] Vectorize: {r.n_strokes} strokes, {r.n_points} pts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
