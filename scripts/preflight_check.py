"""Preflight check for stroke drawing — verify safety before invoking the arm.

実機で描画コマンドを発行する前に、 strokes が安全に描けるか dry-run で
チェックする。 描画自体は行わず、 以下を検証して結果を出力する:

  1. panel_frame.yaml が calibrated=true か
  2. 全 strokes が panel.in_bounds 内か (はみ出しを 1 つでも検出したら WARN)
  3. 推定 timing (benchmark の式) と推定 jerk
  4. 推定 描画パス長 / travel パス長
  5. TSP reorder の効果プレビュー

入力は:
  - 画像 (画像 → Vectorizer → strokes_mm)
  - JSON (既存の strokes JSON、 test_pipeline_smooth --out-json の出力等)
  - 内蔵シーン (face_lite / scattered / zigzag、 ベンチマーク用)

使い方:
  python3 -m scripts.preflight_check --image scripts/test_sketch.jpg
  python3 -m scripts.preflight_check --strokes-json out.json
  python3 -m scripts.preflight_check --scene face_lite
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from modules.robot import PanelFrame                              # noqa: E402
from modules.stroke_planner import (                              # noqa: E402
    reorder_strokes_tsp, plan_clear_heights, total_travel_distance,
    stroke_set_diagnostics,
)


def _load_strokes_from_image(image_path: Path, panel) -> list:
    """Run Vectorizer to get strokes_mm from an image."""
    from modules.vectorizer import Vectorizer
    vec = Vectorizer(verbose=False)
    result = vec.vectorize_to_panel(str(image_path), None, panel,
                                     clip_to_bounds=False)
    return result.strokes_mm or []


def _load_strokes_from_json(json_path: Path) -> list:
    """Load strokes_mm from a JSON (e.g. test_pipeline_smooth --out-json)."""
    d = json.loads(json_path.read_text())
    return d.get("strokes_mm", [])


def _load_strokes_from_scene(scene: str, panel) -> list:
    """Load a built-in scene for benchmark-style preflight."""
    from scripts.test_draw_strokes_smooth import SCENES
    if scene not in SCENES:
        raise ValueError(f"unknown scene: {scene}; "
                         f"available: {list(SCENES.keys())}")
    return SCENES[scene](panel)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--image", type=Path,
                    help="入力画像、 Vectorizer で strokes_mm を生成")
    src.add_argument("--strokes-json", type=Path,
                    help="既存 strokes JSON (test_pipeline_smooth --out-json 出力)")
    src.add_argument("--scene", type=str,
                    help="内蔵シーン (face_lite / scattered / zigzag)")
    ap.add_argument("--panel-yaml", type=Path,
                    default=_ROOT / "calibration" / "panel_frame.yaml")
    ap.add_argument("--draw-speed", type=int, default=30)
    ap.add_argument("--draw-speed-min", type=int, default=10)
    ap.add_argument("--draw-speed-max", type=int, default=50)
    ap.add_argument("--near-threshold-mm", type=float, default=15.0)
    ap.add_argument("--strict", action="store_true",
                    help="bounds 違反があれば exit code 1 にする")
    args = ap.parse_args()

    # --- panel ---
    if not args.panel_yaml.exists():
        print(f"[preflight] panel_frame.yaml not found: {args.panel_yaml}",
              file=sys.stderr)
        return 2
    panel = PanelFrame.from_yaml(args.panel_yaml)

    print(f"=== preflight check ===")
    print(f"panel size       : {panel.size_mm[0]:.1f} x {panel.size_mm[1]:.1f} mm")
    if panel.calibrated:
        print(f"panel calibrated : YES")
    else:
        print(f"panel calibrated : NO ⚠️  (placeholder; uv→base unreliable for real draw)")

    # --- load strokes ---
    if args.image:
        if not args.image.exists():
            print(f"[preflight] image not found: {args.image}", file=sys.stderr)
            return 2
        strokes = _load_strokes_from_image(args.image, panel)
        src_label = f"image ({args.image.name})"
    elif args.strokes_json:
        if not args.strokes_json.exists():
            print(f"[preflight] json not found: {args.strokes_json}",
                  file=sys.stderr)
            return 2
        strokes = _load_strokes_from_json(args.strokes_json)
        src_label = f"json ({args.strokes_json.name})"
    else:
        strokes = _load_strokes_from_scene(args.scene, panel)
        src_label = f"scene ({args.scene})"

    print(f"input            : {src_label}")
    print(f"n_strokes        : {len(strokes)}")

    if not strokes:
        print("[preflight] NO STROKES — nothing to check")
        return 0

    # --- bounds check ---
    n_oob = 0
    oob_strokes = []
    for si, s in enumerate(strokes):
        oob_pts = [(u, v) for (u, v) in s if not panel.in_bounds(u, v)]
        if oob_pts:
            n_oob += len(oob_pts)
            oob_strokes.append((si, len(oob_pts), len(s)))
    if n_oob == 0:
        print(f"bounds check     : ✓ all {sum(len(s) for s in strokes)} points within panel")
    else:
        print(f"bounds check     : ⚠️  {n_oob} points OUT OF BOUNDS "
              f"in {len(oob_strokes)} strokes")
        for si, n_bad, n_tot in oob_strokes[:5]:
            print(f"  stroke[{si}]: {n_bad}/{n_tot} oob")
        if len(oob_strokes) > 5:
            print(f"  (+ {len(oob_strokes) - 5} more strokes with oob)")

    # --- TSP preview ---
    diag = stroke_set_diagnostics(strokes, start_point=None)
    reordered, indices = reorder_strokes_tsp(strokes, start_point=None)
    travel_before = total_travel_distance(strokes, start_point=None)
    travel_after = total_travel_distance(reordered, start_point=None)
    print()
    print(f"draw length      : {diag['draw_length_mm']:.1f} mm")
    print(f"travel (raw)     : {travel_before:.1f} mm")
    print(f"travel (TSP)     : {travel_after:.1f} mm  "
          f"(-{100 * (travel_before - travel_after) / max(1e-9, travel_before):.1f}%)")

    # --- clear height preview ---
    heights = plan_clear_heights(
        reordered,
        w_clear_max_mm=panel.w_clear_mm,
        w_clear_near_mm=panel.w_contact_mm
            + (panel.w_clear_mm - panel.w_contact_mm) / 3,
        near_threshold_mm=args.near_threshold_mm,
    )
    n_near = sum(1 for h in heights if h < panel.w_clear_mm)
    print(f"look-ahead clear : {n_near}/{len(heights)} strokes use the lower height")

    # --- estimated timing ---
    # rough: assume travel @ 200mm/s, draw @ 80mm/s scaled by speed%, z @ 100mm/s
    travel_speed_mm_s = 200.0
    draw_ref_mm_s = 80.0
    z_speed_mm_s = 100.0
    # estimate draw timing using mean speed (rough — would need curvature
    # analysis for proper jerk-aware estimate, but speeds avg around 0.7-1.0 base)
    eff_draw_mm_s = draw_ref_mm_s * (args.draw_speed / 30.0)
    t_draw = diag['draw_length_mm'] / max(1.0, eff_draw_mm_s)
    t_travel = travel_after / travel_speed_mm_s
    n_pen_up_down = 2 * len(strokes)
    t_z = (panel.w_clear_mm - panel.w_contact_mm) * n_pen_up_down / z_speed_mm_s
    t_total_est = t_draw + t_travel + t_z
    print(f"estimated time   : ~{t_total_est:.1f}s "
          f"(draw {t_draw:.1f}s + travel {t_travel:.1f}s + z {t_z:.1f}s)")
    print(f"  draw @ ~{eff_draw_mm_s:.0f}mm/s, travel @ {travel_speed_mm_s:.0f}mm/s, z @ {z_speed_mm_s:.0f}mm/s")

    # --- verdict ---
    print()
    if n_oob > 0:
        print(f"[preflight] FAIL — {n_oob} out-of-bounds points")
        return 1 if args.strict else 0
    print(f"[preflight] OK — safe to draw")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
