#!/usr/bin/env python3
"""SDXL LoRA fine-tune 用データセット準備。

入力: 生画像が並んだフォルダ (例: training/matsumoto_taiyo/raw/)
出力: 学習スクリプトが読める形式 (画像 + 同名 .txt キャプション) を
      training/matsumoto_taiyo/dataset/ に書き出す。

手順:
  1. 各画像を SDXL 解像度 (default 1024px の最長辺) にリサイズ + 中央クロップ
     アスペクト比は保ったままバケット (1024x1024 / 1152x896 / 896x1152) に
     振り分ける(SDXL の resolution buckets と一致させる)
  2. BLIP-2 (Salesforce/blip2-opt-2.7b) で自動キャプション生成
     生成キャプションの先頭に trigger word (例: "mt_taiyo_style") を挿入
  3. <name>.png + <name>.txt を出力先に置く

使用:
  python3 -m scripts.prepare_style_dataset \
      --input  training/matsumoto_taiyo/raw \
      --output training/matsumoto_taiyo/dataset \
      --trigger mt_taiyo_style

著作権注意 (CLAUDE.md にも記載):
  原画は個人 / 研究目的の範囲を超えて使わない。 学習結果 LoRA を公開しない。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Optional

# allow running as a script
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


SDXL_BUCKETS = [   # (w, h) - SDXL 公式 resolution buckets の主要 3 種
    (1024, 1024),
    (1152, 896),
    (896, 1152),
]


def _closest_bucket(w: int, h: int) -> tuple[int, int]:
    """Pick the bucket whose aspect ratio is closest to (w, h)."""
    ar = w / h
    best = SDXL_BUCKETS[0]
    best_diff = abs((best[0] / best[1]) - ar)
    for b in SDXL_BUCKETS[1:]:
        d = abs((b[0] / b[1]) - ar)
        if d < best_diff:
            best = b
            best_diff = d
    return best


def _fit_to_bucket(img, target_w: int, target_h: int):
    """Resize + center-crop to (target_w, target_h)."""
    from PIL import Image
    src_w, src_h = img.size
    src_ar = src_w / src_h
    tgt_ar = target_w / target_h
    if src_ar > tgt_ar:
        # source wider -> resize by height
        new_h = target_h
        new_w = int(round(src_w * new_h / src_h))
    else:
        new_w = target_w
        new_h = int(round(src_h * new_w / src_w))
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))


def _caption_blip2(images, device: str = "cuda"):
    """Generate captions for a list of PIL Images using BLIP-2.

    Returns list[str] of caption strings (no trigger word added yet).
    """
    try:
        import torch
        from transformers import Blip2Processor, Blip2ForConditionalGeneration
    except ImportError as e:
        raise RuntimeError(
            "transformers + torch が必要。 pip install transformers torch") from e

    print(f"[prep] loading BLIP-2 (Salesforce/blip2-opt-2.7b) ...")
    t0 = time.time()
    proc = Blip2Processor.from_pretrained("Salesforce/blip2-opt-2.7b")
    dtype = torch.float16 if device == "cuda" else torch.float32
    model = Blip2ForConditionalGeneration.from_pretrained(
        "Salesforce/blip2-opt-2.7b", torch_dtype=dtype
    ).to(device)
    model.eval()
    print(f"[prep] BLIP-2 ready in {time.time()-t0:.1f}s")

    captions = []
    with torch.no_grad():
        for i, img in enumerate(images):
            inputs = proc(img.convert("RGB"), return_tensors="pt").to(device, dtype)
            ids = model.generate(**inputs, max_new_tokens=40)
            text = proc.decode(ids[0], skip_special_tokens=True).strip()
            captions.append(text)
            if (i + 1) % 5 == 0:
                print(f"[prep]   caption {i+1}/{len(images)}: {text}")
    # free GPU mem
    del model
    del proc
    import gc; gc.collect()
    try:
        import torch as _t
        if _t.cuda.is_available():
            _t.cuda.empty_cache()
    except Exception:
        pass
    return captions


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", type=Path, required=True,
                    help="生画像フォルダ (jpg / png 混在 OK)")
    ap.add_argument("--output", type=Path, required=True,
                    help="出力フォルダ (画像 + .txt キャプションが並ぶ)")
    ap.add_argument("--trigger", type=str, default="mt_taiyo_style",
                    help="caption 先頭に挿入する trigger word")
    ap.add_argument("--no-caption", action="store_true",
                    help="BLIP-2 を使わずキャプションは trigger word のみ")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--max-images", type=int, default=200,
                    help="安全リミット (デフォルト 200 枚で打ち切り)")
    args = ap.parse_args()

    if not args.input.exists():
        print(f"[prep] input dir not found: {args.input}", file=sys.stderr)
        return 2

    raw_paths = sorted(
        p for p in args.input.iterdir()
        if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    )
    if not raw_paths:
        print(f"[prep] no images in {args.input}", file=sys.stderr)
        return 2
    if len(raw_paths) > args.max_images:
        print(f"[prep] truncating to {args.max_images} (found {len(raw_paths)})")
        raw_paths = raw_paths[:args.max_images]
    print(f"[prep] {len(raw_paths)} images")

    args.output.mkdir(parents=True, exist_ok=True)

    # 1. resize + bucket
    from PIL import Image
    processed = []
    bucket_counts = {b: 0 for b in SDXL_BUCKETS}
    for p in raw_paths:
        try:
            img = Image.open(p).convert("RGB")
        except Exception as e:
            print(f"[prep] skip {p.name}: {e}", file=sys.stderr)
            continue
        bucket = _closest_bucket(*img.size)
        fitted = _fit_to_bucket(img, *bucket)
        out_name = p.stem + ".png"
        fitted.save(args.output / out_name)
        processed.append((out_name, fitted))
        bucket_counts[bucket] += 1
    print(f"[prep] bucket distribution:")
    for b, c in bucket_counts.items():
        print(f"[prep]   {b[0]:4d}x{b[1]:4d}: {c}")

    # 2. caption
    if args.no_caption:
        captions = [""] * len(processed)
    else:
        captions = _caption_blip2([im for _, im in processed], device=args.device)

    # 3. write <name>.txt with trigger word prefixed
    trigger = args.trigger.strip()
    for (out_name, _img), caption in zip(processed, captions):
        # de-duplicate trigger if BLIP-2 happens to include it
        cap_clean = caption.replace(trigger, "").strip(" ,.")
        full = trigger if not cap_clean else f"{trigger}, {cap_clean}"
        (args.output / (Path(out_name).stem + ".txt")).write_text(
            full + "\n", encoding="utf-8")
    print(f"[prep] wrote {len(processed)} image+caption pairs -> {args.output}")
    sample = (args.output / (Path(processed[0][0]).stem + ".txt")).read_text()
    print(f"[prep] sample caption: {sample.strip()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
