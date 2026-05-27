#!/usr/bin/env python3
"""Side-by-side image-gen model comparison.

Iterates through a list of preset names (defined in modules.image_gen
MODEL_PRESETS), loads each one in turn, runs the same prompt + guide image
through it, and writes:

  logs/imagegen_comparison_<ts>/
      <preset_name>.png        # raw model output
      grid.png                 # all outputs side-by-side with labels
      summary.json             # prompts, params, elapsed time per preset

VRAM-friendly: each preset is unloaded before the next is loaded (so peak
VRAM is one model at a time, not all together).

Usage:
    python3 -m scripts.compare_imagegen_models \\
        --guide path/to/user_sketch.jpg \\
        --prompt "dog running in park" \\
        --presets sdxl_turbo_mistoline animagine_xl_31_mistoline

    # use defaults (test_sketch + simple prompt + first 2 presets):
    python3 -m scripts.compare_imagegen_models
"""

from __future__ import annotations

import argparse
import gc
import json
import sys
import time
from pathlib import Path
from typing import Optional

# allow `python scripts/compare_imagegen_models.py` from anywhere
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from modules.image_gen import (   # noqa: E402
    ImageGenerator,
    MODEL_PRESETS,
    DEFAULT_NEGATIVE_PROMPT,
)


def _load_guide_image(path: Optional[Path]):
    """Return a PIL Image of the guide. Uses test_sketch.jpg or a blank
    canvas as fallback."""
    from PIL import Image

    if path is None:
        candidate = _ROOT / "scripts" / "test_sketch.jpg"
        if candidate.exists():
            path = candidate
    if path and path.exists():
        return Image.open(path).convert("RGB")
    # fall back to a neutral grey
    print(f"[compare] no guide image found, using a blank canvas")
    return Image.new("RGB", (1024, 1024), (245, 245, 245))


def _build_grid(images: dict, out_path: Path) -> None:
    """Build a labeled horizontal grid from {label: PIL.Image}."""
    from PIL import Image, ImageDraw, ImageFont

    if not images:
        return
    n = len(images)
    w, h = next(iter(images.values())).size
    pad = 16
    label_h = 32
    grid_w = w * n + pad * (n + 1)
    grid_h = h + label_h + pad * 2
    grid = Image.new("RGB", (grid_w, grid_h), (255, 255, 255))
    draw = ImageDraw.Draw(grid)
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
    except OSError:
        font = ImageFont.load_default()
    for i, (label, im) in enumerate(images.items()):
        x = pad + i * (w + pad)
        y = pad + label_h
        grid.paste(im, (x, y))
        draw.text((x + 4, pad), label, fill=(0, 0, 0), font=font)
    grid.save(out_path)
    print(f"[compare] grid saved -> {out_path}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--guide", type=Path, default=None,
                    help="Guide image (user's sketch). Default: scripts/test_sketch.jpg")
    ap.add_argument("--prompt", type=str,
                    default="dog running in a park",
                    help="Base prompt (style_hint from each preset is appended)")
    ap.add_argument("--negative", type=str, default=DEFAULT_NEGATIVE_PROMPT,
                    help="Negative prompt (shared across presets)")
    ap.add_argument("--presets", nargs="+",
                    default=list(MODEL_PRESETS.keys())[:2],
                    help=f"Preset names to compare. Available: "
                         f"{list(MODEL_PRESETS.keys())}")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, default=None,
                    help="Output dir. Default: logs/imagegen_comparison_<ts>")
    ap.add_argument("--resolution", type=int, default=1024)
    ap.add_argument("--strength", type=float, default=None,
                    help="img2img strength を全 preset で上書き (0.5-0.9 推奨)")
    ap.add_argument("--lora-scale", type=float, default=None,
                    help="LoRA scale を全 preset で上書き (0.6-1.2)")
    ap.add_argument("--dilate", type=int, default=None,
                    help="guide_dilate_ksize を全 preset で上書き (0/3/5/7)")
    args = ap.parse_args()

    # validate preset names
    bad = [n for n in args.presets if n not in MODEL_PRESETS]
    if bad:
        print(f"[compare] unknown presets: {bad}", file=sys.stderr)
        print(f"[compare] available: {list(MODEL_PRESETS)}", file=sys.stderr)
        return 2

    ts = time.strftime("%Y%m%d_%H%M%S")
    out_dir = args.out or (_ROOT / "logs" / f"imagegen_comparison_{ts}")
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[compare] output dir: {out_dir}")

    guide = _load_guide_image(args.guide)
    if guide.size != (args.resolution, args.resolution):
        from PIL import Image
        guide = guide.resize((args.resolution, args.resolution), Image.LANCZOS)
    guide.save(out_dir / "00_guide.png")

    results = {
        "prompt": args.prompt,
        "negative_prompt": args.negative,
        "seed": args.seed,
        "guide_image": str(args.guide) if args.guide else "default",
        "presets": [],
    }
    images = {}

    for name in args.presets:
        cfg = MODEL_PRESETS[name]
        print(f"\n[compare] --- preset: {name} ---")
        print(f"[compare]   base   : {cfg['base_model_id']}")
        print(f"[compare]   cnet   : {cfg['controlnet_id']}")
        print(f"[compare]   steps  : {cfg['num_inference_steps']}")
        print(f"[compare]   cfg    : {cfg['guidance_scale']}")
        print(f"[compare]   cn_scl : {cfg['controlnet_conditioning_scale']}")
        full_prompt = args.prompt
        if cfg.get("style_hint"):
            full_prompt = f"{args.prompt}, {cfg['style_hint']}"
        print(f"[compare]   prompt : {full_prompt}")

        gen_overrides = {
            "negative_prompt": args.negative,
            "resolution": args.resolution,
            "verbose": True,
        }
        if args.strength is not None:
            gen_overrides["img2img_strength"] = args.strength
        if args.lora_scale is not None:
            gen_overrides["lora_scale"] = args.lora_scale
        if args.dilate is not None:
            gen_overrides["guide_dilate_ksize"] = args.dilate
        gen = ImageGenerator.from_preset(name, **gen_overrides)
        t0 = time.time()
        try:
            gen.load()
            t_load = time.time() - t0
            t0 = time.time()
            out_img = gen.generate(full_prompt, guide, seed=args.seed)
            t_gen = time.time() - t0
            out_path = out_dir / f"{name}.png"
            out_img.save(out_path)
            print(f"[compare] saved {out_path}  (load={t_load:.1f}s, gen={t_gen:.1f}s)")
            images[name] = out_img
            results["presets"].append({
                "name": name,
                "config": {k: v for k, v in cfg.items() if k != "style_hint"},
                "style_hint": cfg.get("style_hint", ""),
                "elapsed_load_s": round(t_load, 2),
                "elapsed_gen_s": round(t_gen, 2),
                "output_png": str(out_path.name),
                "status": "ok",
            })
        except Exception as e:
            print(f"[compare] FAILED {name}: {e}", file=sys.stderr)
            results["presets"].append({
                "name": name,
                "config": cfg,
                "status": "failed",
                "error": str(e),
            })
        finally:
            gen.unload()
            gc.collect()
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass

    if images:
        _build_grid(images, out_dir / "grid.png")

    (out_dir / "summary.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[compare] summary written -> {out_dir / 'summary.json'}")
    print(f"[compare] open {out_dir / 'grid.png'} to compare side-by-side")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
