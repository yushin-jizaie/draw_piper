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


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user-sketch", type=Path, required=True)
    ap.add_argument("--style-ref", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--stage1-preset", type=str, default="illustrious_v2_inpaint",
                    help="Stage 1 (構図確定) で使う preset")
    ap.add_argument("--stage1-prompt", type=str,
                    default="1boy, solo, young boy with full body, "
                            "messy hair, surprised expression, simple t-shirt")
    ap.add_argument("--stage2-prompt", type=str, default=None,
                    help="Stage 2 prompt。 省略時は stage1-prompt と同じ")
    ap.add_argument("--stage2-strength", type=float, default=0.45,
                    help="img2img strength (0.3=構図維持、 0.6=大きく変える)")
    ap.add_argument("--ip-scale", type=float, default=0.6)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--resolution", type=int, default=768,
                    help="Stage 2 (IP-Adapter) 用の解像度")
    ap.add_argument("--stage1-resolution", type=int, default=1024,
                    help="Stage 1 (Plan E) 用の解像度。 1024 が face/body 構図に有利")
    args = ap.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    res = args.resolution

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
        "--resolution", str(args.stage1_resolution),
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
    # ============================================================
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

    init = Image.open(s1_out).convert("RGB").resize((res, res))
    style_ref = Image.open(args.style_ref).convert("RGB").resize((res, res))
    init.save(args.output / "10_init_from_stage1.png")
    style_ref.save(args.output / "11_style_ref.png")

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
        ip_adapter_image=style_ref,
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
    user_full = Image.open(args.user_sketch).convert("RGB").resize((res, res))
    r = vec.vectorize(generated_image=result.images[0], user_image=user_full)
    rendered = render_strokes_to_image(
        r.strokes, width=r.image_shape[1], height=r.image_shape[0],
        line_width=2)
    rendered.save(args.output / "30_vectorized_strokes.png")
    print(f"[2stage] Vectorize: {r.n_strokes} strokes, {r.n_points} pts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
