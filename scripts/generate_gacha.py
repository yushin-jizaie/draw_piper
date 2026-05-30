#!/usr/bin/env python3
"""1 つの sketch + category から N 個の松本タッチ変形を gacha-ish に生成。

「同じスケッチから 3-5 個 出して 1 番気に入ったものを選ぶ」 UX。 ユーザの「同じ
ポーズしか出ない」 不満への対処として、 seed を毎回振り直して構図とタッチを
分散させる。 style ref も 同カテゴリ内で 試行毎に rotate。

使用:
  ./venv/bin/python -m scripts.generate_gacha \\
      --user-sketch scripts/test_sketch.jpg \\
      --category character \\
      --n 5 \\
      --output logs/gacha_$(date +%Y%m%d_%H%M%S)
"""
from __future__ import annotations

import argparse
import random
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user-sketch", type=Path, required=True)
    ap.add_argument("--category", type=str, default="character",
                    choices=["character", "object", "other"])
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--n", type=int, default=5, help="生成枚数")
    ap.add_argument("--ip-scale", type=float, default=0.6)
    ap.add_argument("--stage2-strength", type=float, default=0.45)
    ap.add_argument("--stage1-resolution", type=int, default=1024)
    ap.add_argument("--resolution", type=int, default=768)
    ap.add_argument("--master-seed", type=int, default=None,
                    help="再現性が欲しい時の master seed。 省略時は system 時刻")
    ap.add_argument("--stage1-prompt", type=str,
                    default="1boy, solo, young boy with full body, "
                            "messy hair, simple t-shirt")
    ap.add_argument("--auto-prompt", action="store_true",
                    help="VLM (Qwen2.5-VL) で sketch を識別して "
                         "category に応じた prompt を自動生成し --stage1-prompt "
                         "を上書きする。 character → CHARACTER_TEMPLATE、 "
                         "object → COMPANION_TEMPLATE (Matsumoto)。")
    ap.add_argument("--confidence-threshold", type=float, default=0.3,
                    help="VLM 信頼度がこの値未満なら fallback prompt を使う。")
    ap.add_argument("--skip-stage2", action="store_true",
                    help="各 variant で test_ip_adapter_two_stage に --skip-stage2 を渡す "
                         "(Stage 2 IP-Adapter を skip、 Stage 1 のみで Vectorize)。 "
                         "object mode で推奨。")
    ap.add_argument("--with-composition", action="store_true",
                    help="Phase 1 (2026-05-30): VLM の predict_composition_refinement "
                         "を呼んで、 stage1_prompt の subject 直後に「構図 phrase」 "
                         "(例: 'looking back over shoulder') を挿入する。 "
                         "modules/vlm.py に method 必要。 --auto-prompt と併用推奨。")
    args = ap.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)

    # --auto-prompt: VLM 推論 1 回 → category に応じた template で
    # stage1_prompt を上書き。 subprocess loop が走る前に VLM を unload
    # しておかないと VRAM を SDXL Turbo と取り合う。
    if args.auto_prompt:
        print(f"[gacha] --auto-prompt → VLM で stage1_prompt を自動生成")
        from PIL import Image as _Image
        from modules.vlm import VLM
        from modules.prompt_builder import (
            build_prompt,
            COMPANION_TEMPLATE, COMPANION_FALLBACK_TEMPLATE,
            CHARACTER_TEMPLATE, CHARACTER_FALLBACK_TEMPLATE,
        )
        sketch_img = _Image.open(args.user_sketch).convert("RGB")
        # Phase 1 (2026-05-30): --with-composition で 構図 refinement も取得
        composition_refinement = ""
        with VLM(verbose=True) as vlm:
            guess = vlm.predict_intent(sketch_img)
            if args.with_composition:
                _fn = getattr(vlm, "predict_composition_refinement", None)
                if callable(_fn):
                    try:
                        composition_refinement = _fn(sketch_img)
                    except Exception as _e:
                        print(f"[gacha] composition skip: {_e}")
                else:
                    print("[gacha] predict_composition_refinement not found "
                          "(skip --with-composition)")
        print(f"[gacha]   guess: {guess.to_text()} "
              f"(conf={guess.confidence:.2f})")
        if args.category == "character":
            base_tpl, fb_tpl = CHARACTER_TEMPLATE, CHARACTER_FALLBACK_TEMPLATE
        elif args.category == "object":
            base_tpl, fb_tpl = COMPANION_TEMPLATE, COMPANION_FALLBACK_TEMPLATE
        else:
            base_tpl, fb_tpl = CHARACTER_TEMPLATE, CHARACTER_FALLBACK_TEMPLATE
        args.stage1_prompt = build_prompt(
            guess,
            confidence_threshold=args.confidence_threshold,
            base_template=base_tpl,
            fallback_template=fb_tpl,
        )
        # 構図 refinement を subject 直後に挿入
        if composition_refinement and "," in args.stage1_prompt:
            head, rest = args.stage1_prompt.split(",", 1)
            args.stage1_prompt = f"{head}, {composition_refinement},{rest}"
        print(f"[gacha]   stage1_prompt: {args.stage1_prompt}")
        (args.output / "00_auto_prompt.txt").write_text(
            f"category={args.category}\n"
            f"subject_ja={guess.subject.ja}\n"
            f"location_ja={guess.location.ja}\n"
            f"action_ja={guess.action.ja}\n"
            f"confidence={guess.confidence:.3f}\n"
            f"composition_refinement={composition_refinement or ''}\n"
            f"stage1_prompt={args.stage1_prompt}\n",
            encoding="utf-8",
        )

    rng = random.Random(args.master_seed)
    # 各 試行用の seed 列
    seeds = [rng.randint(0, 100000) for _ in range(args.n)]
    print(f"[gacha] N={args.n}, seeds={seeds}")
    print(f"[gacha] category={args.category}, sketch={args.user_sketch}")

    results = []
    for i, seed in enumerate(seeds):
        sub = args.output / f"v{i+1}_seed{seed}"
        print(f"\n[gacha] === {i+1}/{args.n}: seed={seed} ===")
        cmd = [
            "./venv/bin/python", "-m", "scripts.test_ip_adapter_two_stage",
            "--user-sketch", str(args.user_sketch),
            "--category", args.category,
            "--output", str(sub),
            "--stage1-resolution", str(args.stage1_resolution),
            "--resolution", str(args.resolution),
            "--stage2-strength", str(args.stage2_strength),
            "--ip-scale", str(args.ip_scale),
            "--stage1-prompt", args.stage1_prompt,
            "--seed", str(seed),
        ]
        if args.skip_stage2:
            cmd.append("--skip-stage2")
        rc = subprocess.run(cmd, cwd=str(_ROOT)).returncode
        if rc != 0:
            print(f"[gacha]   FAILED rc={rc}")
            continue
        strokes_png = sub / "30_vectorized_strokes.png"
        if strokes_png.exists():
            results.append((i + 1, seed, sub))

    # Grid 合成
    if results:
        print(f"\n[gacha] composing grid of {len(results)} results")
        from PIL import Image, ImageDraw
        cell = 384
        cols = min(len(results), 5)
        rows = (len(results) + cols - 1) // cols
        grid = Image.new("RGB", (cols * cell, rows * cell + 30),
                         (240, 240, 240))
        draw = ImageDraw.Draw(grid)
        draw.text((6, 6), f"gacha {args.category}: {len(results)} variants",
                  fill=(40, 40, 40))
        for idx, (n, seed, sub) in enumerate(results):
            r, c = idx // cols, idx % cols
            strokes_img = Image.open(sub / "30_vectorized_strokes.png").convert("RGB")
            thumb = strokes_img.resize((cell, cell), Image.LANCZOS)
            grid.paste(thumb, (c * cell, r * cell + 30))
            draw.text((c * cell + 6, r * cell + 36),
                      f"v{n} seed={seed}", fill=(0, 0, 80))
        grid_path = args.output / "gacha_grid.png"
        grid.save(grid_path)
        print(f"[gacha] grid -> {grid_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
