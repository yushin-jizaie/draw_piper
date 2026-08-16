#!/usr/bin/env python3
"""IP-Adapter で「松本大洋 画風」 を style transfer する実験スクリプト。

LoRA 学習 (v0-v4) が「dataset の文字 / 紙質感 / ハッチング」 を一緒に学んで
ロボット描画適性を失うのに対し、 IP-Adapter は「参照画像のスタイル」 を
モデル内部の attention layer に注入する手法。 dataset preprocessing なしで
直接 raw 画像を style 参照に使えるのがメリット。

仕組み (h94/IP-Adapter):
  - SDXL の cross-attention に追加 projector を挿入
  - 参照画像を CLIP-ViT で encode し その embedding を condition に
  - LoRA と違って weight 更新せず、 推論時の attention だけ調整

使用:
  ./venv/bin/python -m scripts.test_ip_adapter_style \\
      --base John6666/illustrious-xl-early-release-v0-sdxl \\
      --controlnet TheMistoAI/MistoLine \\
      --user-sketch scripts/test_sketch.jpg \\
      --style-ref training/matsumoto_taiyo/raw/IMG_4321.JPG \\
      --prompt "1boy, solo, young boy with messy hair, surprised, simple shirt" \\
      --ip-scale 0.6 --seed 42 \\
      --output logs/ip_adapter_test_$(date +%Y%m%d_%H%M%S)

複数 style ref + scale sweep は scripts/sweep_matsumoto_experiments.sh から
呼ばれる。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# allow `python -m scripts.test_ip_adapter_style`
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", type=str,
                    default="John6666/illustrious-xl-early-release-v0-sdxl")
    ap.add_argument("--controlnet", type=str, default="TheMistoAI/MistoLine")
    ap.add_argument("--user-sketch", type=Path, required=True,
                    help="ユーザの入力スケッチ (顔の輪郭 + 目)")
    ap.add_argument("--style-ref", type=Path, required=True,
                    help="参照画像 (松本大洋の raw 画像 etc)")
    ap.add_argument("--prompt", type=str, default="1boy, solo, young boy")
    ap.add_argument("--ip-scale", type=float, default=0.6,
                    help="IP-Adapter scale (0.3 弱め - 1.0 強め)")
    ap.add_argument("--cn-scale", type=float, default=0.85,
                    help="ControlNet conditioning scale")
    ap.add_argument("--inpaint-strength", type=float, default=1.0)
    ap.add_argument("--steps", type=int, default=28)
    ap.add_argument("--guidance", type=float, default=6.5)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--resolution", type=int, default=768,
                    help="output resolution (default 768 to fit 16GB GPU)")
    ap.add_argument("--ip-adapter-model", type=str,
                    default="h94/IP-Adapter",
                    help="IP-Adapter repo")
    ap.add_argument("--ip-adapter-weight", type=str,
                    default="ip-adapter_sdxl.safetensors",
                    help="weight filename")
    ap.add_argument("--ip-adapter-subfolder", type=str, default="sdxl_models")
    ap.add_argument("--output", type=Path, required=True,
                    help="出力 dir")
    args = ap.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    print(f"[ip_adapter] output: {args.output}")

    from PIL import Image
    import torch
    from diffusers import (
        StableDiffusionXLControlNetInpaintPipeline,
        ControlNetModel,
    )

    print(f"[ip_adapter] loading controlnet: {args.controlnet}")
    cn = ControlNetModel.from_pretrained(
        args.controlnet, torch_dtype=torch.float16,
        variant="fp16", use_safetensors=True)

    print(f"[ip_adapter] loading base: {args.base}")
    pipe = StableDiffusionXLControlNetInpaintPipeline.from_pretrained(
        args.base, controlnet=cn, torch_dtype=torch.float16,
        use_safetensors=True)
    pipe = pipe.to("cuda")
    pipe.enable_vae_tiling()
    pipe.enable_vae_slicing()

    print(f"[ip_adapter] loading IP-Adapter: {args.ip_adapter_model}/"
          f"{args.ip_adapter_subfolder}/{args.ip_adapter_weight}")
    pipe.load_ip_adapter(
        args.ip_adapter_model,
        subfolder=args.ip_adapter_subfolder,
        weight_name=args.ip_adapter_weight,
    )
    pipe.set_ip_adapter_scale(args.ip_scale)

    # 画像読み込み
    res = args.resolution
    user_sk = Image.open(args.user_sketch).convert("RGB").resize((res, res))
    style_ref = Image.open(args.style_ref).convert("RGB").resize((res, res))
    user_sk.save(args.output / "00_user_sketch.png")
    style_ref.save(args.output / "01_style_ref.png")

    # inpaint mask: 黒線部分は keep (mask=0)、 白部分は再生成 (mask=255)
    import numpy as np
    arr = np.array(user_sk.convert("L"))
    mask_arr = np.where(arr < 200, 0, 255).astype(np.uint8)
    # dilate keep 領域 (黒線) でラフな保護
    import cv2
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    keep_arr = (mask_arr == 0).astype(np.uint8) * 255
    keep_arr = cv2.dilate(keep_arr, kernel, iterations=4)
    mask_arr = np.where(keep_arr > 0, 0, 255).astype(np.uint8)
    mask_img = Image.fromarray(mask_arr).convert("RGB")
    mask_img.save(args.output / "02_inpaint_mask.png")

    style_hint = ("monochrome, greyscale, lineart, sketch, "
                  "white_background, simple_background")
    full_prompt = f"{args.prompt}, {style_hint}"
    negative = ("color, colored, blue background, cyan, sky, gradient, "
                "hatching, crosshatch, screentone, halftone, dot pattern, "
                "filled background, paper texture, scribble, sketchy, "
                "shading, gray, sepia, "
                "watermark, signature, text, frame, border, "
                "blurry, noise, jpeg artifacts")

    print(f"[ip_adapter] generating with ip_scale={args.ip_scale} "
          f"cn_scale={args.cn_scale} seed={args.seed}")
    gen = torch.Generator("cuda").manual_seed(args.seed)
    t0 = time.time()
    result = pipe(
        prompt=full_prompt,
        negative_prompt=negative,
        image=user_sk,
        mask_image=mask_img,
        control_image=user_sk,
        ip_adapter_image=style_ref,
        num_inference_steps=args.steps,
        guidance_scale=args.guidance,
        controlnet_conditioning_scale=args.cn_scale,
        strength=args.inpaint_strength,
        generator=gen,
    )
    elapsed = time.time() - t0
    out_path = args.output / f"03_result_ip{args.ip_scale:.2f}.png"
    result.images[0].save(out_path)
    print(f"[ip_adapter] saved {out_path} ({elapsed:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
