#!/usr/bin/env python3
"""指定 base_dir 配下の 30_vectorized_strokes.png / 30_companion_strokes.png を
順次 Vectorize して、 stroke 数 / points / 平均 pts/stroke を評価。

robot 描画向けの「細かい strokes」 判定:
- ❌ 0 strokes      : 生成失敗
- 🟢 < 20 strokes   : シンプル、 robot 描画◎
- 🟡 20-50 strokes  : 適度、 detail と描画時間のバランス
- 🔴 > 50 strokes   : 細かすぎ、 描画時間が長すぎる傾向

Usage:
  ./venv/bin/python -m scripts.eval_stroke_set <base_dir> [--watch N]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


def evaluate(base_dir: Path) -> int:
    from modules.vectorizer import Vectorizer
    from PIL import Image
    pngs = sorted(set(list(base_dir.rglob("30_vectorized_strokes.png")) +
                       list(base_dir.rglob("30_companion_strokes.png"))))
    if not pngs:
        print(f"[eval] no strokes png in {base_dir}")
        return 0
    print(f"[eval] {len(pngs)} variants in {base_dir.relative_to(_ROOT)}")
    print()
    print(f"{'key':<55} {'strk':>5} {'pts':>6} {'pts/s':>6}  rating")
    print("-" * 95)
    vec = Vectorizer()
    rated_summary = {"clean": 0, "ok": 0, "fine": 0, "failed": 0}
    for p in pngs:
        rel = p.relative_to(base_dir)
        key = "/".join(rel.parts[:-1])
        img = Image.open(p).convert("RGB")
        r = vec.vectorize(generated_image=img, user_image=None)
        avg = (r.n_points / r.n_strokes) if r.n_strokes else 0
        if r.n_strokes == 0:
            rating = "❌ failed"
            rated_summary["failed"] += 1
        elif r.n_strokes < 20:
            rating = "🟢 clean (robot ◎)"
            rated_summary["clean"] += 1
        elif r.n_strokes <= 50:
            rating = "🟡 ok"
            rated_summary["ok"] += 1
        else:
            rating = "🔴 too fine (細かすぎ)"
            rated_summary["fine"] += 1
        print(f"{key:<55} {r.n_strokes:>5} {r.n_points:>6} {avg:>6.1f}  {rating}")
    print()
    print(f"[summary] {len(pngs)} variants  |  "
          f"🟢 clean: {rated_summary['clean']}  |  "
          f"🟡 ok: {rated_summary['ok']}  |  "
          f"🔴 too fine: {rated_summary['fine']}  |  "
          f"❌ failed: {rated_summary['failed']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base_dir", type=Path,
                    help="評価対象の base dir (例: sketch_variations/disp_align_<ts>)")
    ap.add_argument("--watch", type=int, default=0,
                    help="N 秒毎に再評価する watch mode (0 = 一回のみ、 default)")
    args = ap.parse_args()
    args.base_dir = args.base_dir.resolve()
    if not args.base_dir.exists():
        print(f"[eval] not found: {args.base_dir}", file=sys.stderr)
        return 1
    if args.watch > 0:
        try:
            while True:
                print(f"\n=== {time.strftime('%H:%M:%S')} ===")
                evaluate(args.base_dir)
                time.sleep(args.watch)
        except KeyboardInterrupt:
            print("\n[eval] interrupted")
            return 0
    return evaluate(args.base_dir)


if __name__ == "__main__":
    sys.exit(main())
