"""End-to-end pipeline: image → Vectorizer → Robot.draw_strokes_panel_smooth.

M9 (test_vlm_to_image.py) では VLM → ImageGen → Vectorizer まで通っていたが、
Vectorizer 出力 → Robot 描画 の連携は未実装だった。 本スクリプトでその欠けを埋める。

入力: 既存の生成画像 (PNG/JPG) — VLM/ImageGen は走らせず、 既存出力を使う
出力: Vectorizer の strokes_mm → Robot.draw_strokes_panel_smooth で描画コマンド発行
      (mock または real Robot)

mock モード (デフォルト) は piper_sdk 無し環境でも動く。 real モードは Piper
実機 + can0 が必要。

使い方:
  # Mock pipeline (sandbox / dev)
  python3 -m scripts.test_pipeline_smooth \\
      --image scripts/test_sketch.jpg

  # 既存の生成画像で
  python3 -m scripts.test_pipeline_smooth \\
      --image logs/imagegen_comparison_20260527_234800/matsumoto_taiyo_inpaint.png

  # 実機 (Piper + can0)
  python3 -m scripts.test_pipeline_smooth \\
      --image logs/imagegen_comparison_*/illustrious_v2_inpaint.png \\
      --real

  # Vectorizer + diagnostics 表示のみ (描画コマンド発行せず)
  python3 -m scripts.test_pipeline_smooth --image ... --vectorize-only

引き継ぎ: docs/20260528_frida_smoothness_handoff.md (P2)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# allow running as script
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from modules.robot import Robot, PanelFrame                           # noqa: E402
from modules.vectorizer import Vectorizer                             # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", type=Path, required=True,
                    help="入力画像 (PNG/JPG)、 VLM/ImageGen の出力 or sketch")
    ap.add_argument("--user-image", type=Path, default=None,
                    help="(任意) median 合成キャプチャ。 差分検出に使う")
    ap.add_argument("--panel-yaml", type=Path,
                    default=_ROOT / "calibration" / "panel_frame.yaml",
                    help="panel_frame.yaml path (default: repo calibration/)")
    ap.add_argument("--real", action="store_true",
                    help="mock=False で実機描画 (Piper SDK + can0 必須)")
    ap.add_argument("--vectorize-only", action="store_true",
                    help="Vectorizer + 連携 stats を表示するだけで描画しない")
    ap.add_argument("--no-reorder", action="store_true",
                    help="TSP 並べ替えを OFF (旧 raw 順で描画)")
    ap.add_argument("--draw-speed", type=int, default=30)
    ap.add_argument("--draw-speed-min", type=int, default=10)
    ap.add_argument("--travel-speed", type=int, default=60)
    ap.add_argument("--near-threshold-mm", type=float, default=15.0)
    ap.add_argument("--step-mm", type=float, default=2.0)
    ap.add_argument("--debug-dir", type=Path, default=None,
                    help="Vectorizer の中間 PNG を保存するディレクトリ")
    ap.add_argument("--out-json", type=Path, default=None,
                    help="strokes + diagnostics の JSON 保存先")
    ap.add_argument("--countdown", type=int, default=3,
                    help="real 時の描画前カウントダウン秒数")
    args = ap.parse_args()

    if not args.image.exists():
        print(f"[pipeline] input image not found: {args.image}", file=sys.stderr)
        return 2

    # --- panel frame ---
    if not args.panel_yaml.exists():
        print(f"[pipeline] panel_frame.yaml not found: {args.panel_yaml}",
              file=sys.stderr)
        return 2
    panel = PanelFrame.from_yaml(args.panel_yaml)
    if not panel.calibrated:
        print(f"[pipeline] WARN: panel.calibrated=False (placeholder values). "
              "uv→base mapping is unreliable for real-arm draw.")
    print(f"[pipeline] panel size = {panel.size_mm[0]:.1f} x "
          f"{panel.size_mm[1]:.1f} mm")

    # --- vectorize ---
    print(f"[pipeline] vectorize: {args.image.name}")
    t0 = time.time()
    vec = Vectorizer(verbose=True)
    result = vec.vectorize_to_panel(
        str(args.image),
        str(args.user_image) if args.user_image else None,
        panel,
        debug_dir=args.debug_dir,
        clip_to_bounds=True,
    )
    t_vec = time.time() - t0
    print(f"[pipeline] vectorize done in {t_vec:.2f}s")
    print(f"[pipeline]   image shape : {result.image_shape}")
    print(f"[pipeline]   n_strokes   : {result.n_strokes}")
    print(f"[pipeline]   n_points    : {result.n_points}")
    print(f"[pipeline]   total length: {result.total_length_px:.1f} px")

    strokes_mm = result.strokes_mm or []
    print(f"[pipeline]   strokes_mm  : {len(strokes_mm)}")

    if not strokes_mm:
        print("[pipeline] no strokes after mm conversion — exiting")
        return 3

    # --- save stroke JSON ---
    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "image": str(args.image),
            "panel_size_mm": list(panel.size_mm),
            "panel_calibrated": panel.calibrated,
            "n_strokes": len(strokes_mm),
            "vectorize_diagnostics": result.diagnostics,
            "strokes_mm": [
                [[round(u, 3), round(v, 3)] for (u, v) in s]
                for s in strokes_mm
            ],
        }
        args.out_json.write_text(json.dumps(payload, indent=2,
                                             default=str),
                                  encoding="utf-8")
        print(f"[pipeline] wrote {args.out_json}")

    if args.vectorize_only:
        print("[pipeline] --vectorize-only specified, skipping robot draw")
        # show benchmark even without drawing
        _print_planning_only(strokes_mm, args)
        return 0

    # --- robot ---
    use_mock = not args.real
    print(f"[pipeline] robot mode: {'MOCK' if use_mock else 'REAL'}")
    robot = Robot(mock=use_mock, panel_frame=panel,
                  use_feedback_workaround=not use_mock)
    robot.connect()

    if use_mock:
        # speed up mock by skipping the 50ms wait_for_pose sleep
        robot.wait_for_pose = lambda *a, **kw: True   # type: ignore

    try:
        if not use_mock:
            print("[pipeline] moving to ready pose ...")
            robot.goto_ready_pose(speed_pct=15, settle_s=10.0)
            for s in range(args.countdown, 0, -1):
                print(f"[pipeline] starting draw in {s} ...")
                time.sleep(1.0)

        print(f"[pipeline] drawing {len(strokes_mm)} strokes via "
              f"draw_strokes_panel_smooth (reorder={not args.no_reorder})")
        t0 = time.time()
        diag = robot.draw_strokes_panel_smooth(
            strokes_mm,
            travel_speed=args.travel_speed,
            draw_speed_base=args.draw_speed,
            draw_speed_min=args.draw_speed_min,
            near_threshold_mm=args.near_threshold_mm,
            step_mm=args.step_mm,
            reorder=not args.no_reorder,
        )
        elapsed = time.time() - t0
        print(f"[pipeline] draw completed in {elapsed:.2f}s")
        print()
        print("=== robot diagnostics ===")
        for k, v in diag.items():
            if isinstance(v, list) and len(v) > 6:
                print(f"  {k:<22} {v[:5]}... (len={len(v)})")
            elif isinstance(v, float):
                print(f"  {k:<22} {v:.2f}")
            else:
                print(f"  {k:<22} {v}")

        # save combined diagnostics
        if args.out_json:
            combined = json.loads(args.out_json.read_text())
            combined["robot_diagnostics"] = {
                k: v for k, v in diag.items()
                if isinstance(v, (int, float, str, list, dict))
            }
            combined["draw_elapsed_s"] = elapsed
            combined["robot_mock"] = use_mock
            args.out_json.write_text(json.dumps(combined, indent=2,
                                                 default=str),
                                      encoding="utf-8")
            print(f"[pipeline] updated {args.out_json}")

        if not use_mock:
            print("[pipeline] returning to ready pose ...")
            robot.goto_ready_pose(speed_pct=15, settle_s=6.0)
        return 0
    finally:
        robot.disconnect()


def _print_planning_only(strokes_mm, args):
    """When --vectorize-only, compute planning metrics without invoking Robot."""
    from modules.stroke_planner import (
        reorder_strokes_tsp, plan_clear_heights, total_travel_distance,
    )
    print()
    print("=== planning preview (no draw) ===")
    travel_before = total_travel_distance(strokes_mm, start_point=None)
    if not args.no_reorder:
        reordered, indices = reorder_strokes_tsp(strokes_mm, start_point=None)
        travel_after = total_travel_distance(reordered, start_point=None)
        saved = travel_before - travel_after
        pct = 100 * saved / max(1e-9, travel_before)
        print(f"  TSP indices[:8]    : {indices[:8]}{'...' if len(indices) > 8 else ''}")
        print(f"  travel before (mm) : {travel_before:.1f}")
        print(f"  travel after  (mm) : {travel_after:.1f}")
        print(f"  travel saved       : {saved:.1f} mm ({pct:.1f}%)")
    else:
        print(f"  travel (raw order) : {travel_before:.1f} mm (no reorder)")
    heights = plan_clear_heights(strokes_mm,
                                  w_clear_max_mm=30.0, w_clear_near_mm=10.0,
                                  near_threshold_mm=args.near_threshold_mm)
    n_near = sum(1 for h in heights if h < 30.0)
    print(f"  clear height < max : {n_near}/{len(heights)} strokes")


if __name__ == "__main__":
    raise SystemExit(main())
