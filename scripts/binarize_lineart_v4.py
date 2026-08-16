#!/usr/bin/env python3
"""v4 LoRA 用の dataset 厳格 binarize 前処理。

過去の v0-v3 LoRA 失敗の真因 (grayscale lineart の VAE encode が
高周波ハッチング latent として表現される) への対処として、 入力 lineart を
**完全 2 値** に変換してから dataset 化する。

threshold=50 の根拠:
  - lineart-anime detector の出力では「強く検出された主要な線」 が pixel
    値 < 50 (= 完全黒に近い)
  - 弱い線 / 影 boundary / screentone 残渣は 50-200 のグレー
  - threshold=50 でこれらを完全に白に飛ばす = pure line のみ残る

使用:
  python3 -m scripts.binarize_lineart_v4 \\
      --input  training/matsumoto_taiyo/lineart_b \\
      --output training/matsumoto_taiyo/lineart_v4_binary \\
      --threshold 50 \\
      --keep "8f640a63f5520f466b5ba1560d2e89dc,IMG_4321,..."

明日の朝、 まとめて Bash 承認時にこのスクリプトを動かす予定。
"""
from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

import numpy as np
from PIL import Image

# v3 で curate した 13 ファイル (lost recovery 後の baseline)
DEFAULT_KEEP_STEMS = [
    "8f640a63f5520f466b5ba1560d2e89dc",
    "IMG_4321",
    "5e4f3e756cefd41aaf5a88da14f2020c",
    "feccbf2756d31b496b18e31694969146",
    "o0600045013450720343",
    "f341cbadd1aede96e2fdef7bc84cc3c6",
    "da5069ca33f7ec6605bb5e46455a6fba",
    "IMG_4325",
    "IMG_4327",
    "IMG_4311",
    "IMG_4315",
    "IMG_4314",
    "GLk4xRyaMAAy4wq",
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", type=Path, required=True,
                    help="lineart 画像 (grayscale 出力) フォルダ")
    ap.add_argument("--output", type=Path, required=True,
                    help="binary 化結果の出力フォルダ")
    ap.add_argument("--threshold", type=int, default=50,
                    help="binarize 閾値 (pixel < threshold → 0, else 255)")
    ap.add_argument("--keep", type=str, default=None,
                    help="使用する file stem の comma list。 省略時は DEFAULT_KEEP_STEMS")
    args = ap.parse_args()

    if not args.input.is_dir():
        print(f"[binarize] FATAL: input dir not found: {args.input}", file=sys.stderr)
        return 2

    args.output.mkdir(parents=True, exist_ok=True)
    keep = (args.keep.split(",") if args.keep else DEFAULT_KEEP_STEMS)
    keep = [s.strip() for s in keep if s.strip()]

    n_done = 0
    n_missing = 0
    for stem in keep:
        # 拡張子は元の lineart_b/ の出力 (png) を想定、 ただし jpg/webp も探す
        paths = list(args.input.glob(f"{stem}.png"))
        if not paths:
            paths = list(args.input.glob(f"{stem}.*"))
        if not paths:
            print(f"[binarize] MISSING: {stem}", file=sys.stderr)
            n_missing += 1
            continue
        src = paths[0]

        img = Image.open(src).convert("L")
        arr = np.array(img)
        # 厳格 binarize
        bin_arr = np.where(arr < args.threshold, 0, 255).astype(np.uint8)
        # SDXL 学習用に RGB 3ch にして保存 (binary だが channel 数を合わせる)
        bin_img = Image.fromarray(bin_arr).convert("RGB")
        dst = args.output / f"{stem}.png"
        bin_img.save(dst)
        n_done += 1
        n_black = int((bin_arr == 0).sum())
        n_total = int(bin_arr.size)
        print(f"[binarize] {stem}: {n_black/n_total*100:5.2f}% black pixels -> {dst.name}")

    print(f"\n[binarize] DONE: {n_done} ok, {n_missing} missing")
    return 0 if n_missing == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
