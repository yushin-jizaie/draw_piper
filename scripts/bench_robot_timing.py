"""Real-arm timing benchmark: naive vs arcs vs smooth.

同じ scene を 3 strategy で 連続描画し、 各 strategy の壁時計時間を計測する。
mock モードだと wait_for_pose が固定 sleep なので 純関数 simulator
(benchmark_stroke_smoothness.py) と大差ない。 real モードのみが本来の用途。

各 strategy ごとに記録:
  - 壁時計時間 (時計)
  - 描画パス長 / travel パス長
  - 戻り値 diagnostics (smooth のみ)

複数 scene を 1 実行で回せる。 ペン交換が要らない範囲 (例 5 stroke の face_lite)
での連続実行を想定。 長い scene (100 stroke の scattered) は 1 strategy 約 2 分
かかるので 全部走らせると 6 分超え。 user 都合で --strategies を絞れる。

⚠️ 実機運用上の注意:
  - インクが減るので、 用紙を変えながら走らせる or 同じ場所に重ね書きされる
  - 連続 描画で アームの温度上昇 / accuracy ドリフトに注意
  - --countdown で各 strategy 開始前に user が紙交換できる

使い方:
  # mock (sandbox)
  python3 -m scripts.bench_robot_timing --scene face_lite

  # real arm
  python3 -m scripts.bench_robot_timing --scene face_lite --real \\
      --countdown 5         # 紙を交換する時間

  # 結果は logs/robot_timing_<ts>/ に JSON で保存
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from modules.robot import Robot, PanelFrame                       # noqa: E402
from modules.stroke_planner import (                              # noqa: E402
    stroke_set_diagnostics, total_travel_distance,
)


def run_naive(robot, strokes, draw_speed, travel_speed):
    """naive: draw_stroke_panel per stroke, raw order."""
    t0 = time.time()
    for s in strokes:
        if len(s) < 2:
            continue
        robot.draw_stroke_panel(
            s,
            draw_speed=draw_speed, travel_speed=travel_speed,
            inter_point_delay=0.02, settle_s=1.0,
            arrival_tol_mm=2.0, arrival_timeout_s=15.0,
        )
    return time.time() - t0


def run_arcs(robot, strokes, draw_speed, travel_speed, step_mm):
    """arcs: draw_stroke_panel_arcs per stroke, raw order."""
    t0 = time.time()
    for s in strokes:
        if len(s) < 2:
            continue
        robot.draw_stroke_panel_arcs(
            s,
            draw_speed=draw_speed, travel_speed=travel_speed,
            settle_s=1.0, smooth=True, step_mm=step_mm,
            arrival_tol_mm=2.0, arrival_timeout_s=15.0,
        )
    return time.time() - t0


def run_smooth(robot, strokes, draw_speed, draw_speed_min, draw_speed_max,
                travel_speed, near_threshold_mm, step_mm, reorder,
                merge_threshold_mm=0.0):
    """smooth: Frida-inspired single-call draw_strokes_panel_smooth."""
    t0 = time.time()
    diag = robot.draw_strokes_panel_smooth(
        strokes,
        travel_speed=travel_speed,
        draw_speed_base=draw_speed,
        draw_speed_min=draw_speed_min,
        draw_speed_max=draw_speed_max,
        near_threshold_mm=near_threshold_mm,
        step_mm=step_mm,
        reorder=reorder,
        merge_threshold_mm=merge_threshold_mm,
        settle_s=1.0,
        arrival_tol_mm=2.0, arrival_timeout_s=15.0,
    )
    return time.time() - t0, diag


STRATEGIES = ["naive", "arcs", "smooth"]


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scene", default="face_lite",
                    help="Scene name from test_draw_strokes_smooth.SCENES")
    ap.add_argument("--strategies", nargs="+", default=STRATEGIES,
                    choices=STRATEGIES,
                    help="どの strategy を走らせるか (subset 可)")
    ap.add_argument("--real", action="store_true",
                    help="mock=False で実機実行 (default は mock)")
    ap.add_argument("--panel-yaml", type=Path,
                    default=_ROOT / "calibration" / "panel_frame.yaml")
    ap.add_argument("--draw-speed", type=int, default=30)
    ap.add_argument("--draw-speed-min", type=int, default=10)
    ap.add_argument("--draw-speed-max", type=int, default=50)
    ap.add_argument("--travel-speed", type=int, default=60)
    ap.add_argument("--near-threshold-mm", type=float, default=15.0)
    ap.add_argument("--step-mm", type=float, default=2.0)
    ap.add_argument("--no-reorder", action="store_true",
                    help="smooth で TSP を OFF (raw 順、 純粋に curve/speed 比較)")
    ap.add_argument("--merge-mm", type=float, default=0.0,
                    help="smooth で 近接 stroke 連続化を有効化 (mm threshold、 "
                         "default 0=OFF、 副作用で接続線描かれる)")
    ap.add_argument("--countdown", type=int, default=3,
                    help="各 strategy 開始前のカウントダウン (用紙交換)")
    ap.add_argument("--out-dir", type=Path, default=None,
                    help="結果 JSON の保存先 (default: logs/robot_timing_<ts>/)")
    args = ap.parse_args()

    if not args.panel_yaml.exists():
        print(f"[bench] panel_frame.yaml not found: {args.panel_yaml}",
              file=sys.stderr)
        return 2
    panel = PanelFrame.from_yaml(args.panel_yaml)

    # --- load scene ---
    from scripts.test_draw_strokes_smooth import SCENES
    if args.scene not in SCENES:
        print(f"[bench] unknown scene: {args.scene}; available: "
              f"{list(SCENES.keys())}", file=sys.stderr)
        return 2
    strokes = SCENES[args.scene](panel)
    diag_in = stroke_set_diagnostics(strokes, start_point=None)
    print(f"=== bench_robot_timing: scene={args.scene} ===")
    print(f"  n_strokes        : {diag_in['n_strokes']}")
    print(f"  draw_length_mm   : {diag_in['draw_length_mm']:.1f}")
    print(f"  travel_raw_mm    : {diag_in['travel_length_mm']:.1f}")
    print(f"  mode             : {'REAL' if args.real else 'MOCK'}")
    print(f"  strategies       : {args.strategies}")
    print()

    use_mock = not args.real
    robot = Robot(mock=use_mock, panel_frame=panel,
                  use_feedback_workaround=not use_mock)
    robot.connect(enable_motors=True)
    if use_mock:
        # speed up: skip the 50ms sleep in wait_for_pose
        robot.wait_for_pose = lambda *a, **kw: True   # type: ignore

    out_dir = args.out_dir or (
        _ROOT / "logs" / f"robot_timing_{time.strftime('%Y%m%d_%H%M%S')}")
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"  out_dir          : {out_dir}")
    print()

    results = {
        "scene": args.scene,
        "mode": "real" if args.real else "mock",
        "panel_calibrated": panel.calibrated,
        "scene_diag": diag_in,
        "params": {
            "draw_speed": args.draw_speed,
            "draw_speed_min": args.draw_speed_min,
            "draw_speed_max": args.draw_speed_max,
            "travel_speed": args.travel_speed,
            "near_threshold_mm": args.near_threshold_mm,
            "step_mm": args.step_mm,
            "reorder": not args.no_reorder,
        },
        "strategies": {},
    }

    try:
        if args.real:
            print("[bench] moving to ready pose ...")
            robot.goto_ready_pose(speed_pct=15, settle_s=10.0)

        for strat in args.strategies:
            if args.real and args.countdown > 0:
                for s in range(args.countdown, 0, -1):
                    print(f"[bench] {strat}: 開始まで {s}s (紙交換時間)")
                    time.sleep(1.0)

            print(f"[bench] running {strat} ...")
            try:
                if strat == "naive":
                    elapsed = run_naive(robot, strokes,
                                         args.draw_speed, args.travel_speed)
                    strat_diag = None
                elif strat == "arcs":
                    elapsed = run_arcs(robot, strokes,
                                        args.draw_speed, args.travel_speed,
                                        args.step_mm)
                    strat_diag = None
                else:  # smooth
                    elapsed, strat_diag = run_smooth(
                        robot, strokes,
                        args.draw_speed, args.draw_speed_min,
                        args.draw_speed_max, args.travel_speed,
                        args.near_threshold_mm, args.step_mm,
                        not args.no_reorder,
                        merge_threshold_mm=args.merge_mm)
                print(f"[bench] {strat}: {elapsed:.2f}s")
                results["strategies"][strat] = {
                    "elapsed_s": elapsed,
                    "diag": strat_diag if isinstance(strat_diag, dict) else None,
                }
            except Exception as e:
                print(f"[bench] {strat}: FAILED — {e}", file=sys.stderr)
                results["strategies"][strat] = {"error": str(e)}

        # --- summary ---
        print()
        print("=== summary ===")
        print(f"{'strategy':<12} {'elapsed_s':>12} {'rel_speedup':>14}")
        print("-" * 42)
        baseline = results["strategies"].get("naive", {}).get("elapsed_s")
        for strat in args.strategies:
            r = results["strategies"].get(strat, {})
            t = r.get("elapsed_s")
            if t is None:
                print(f"{strat:<12} {'(failed)':>12}")
                continue
            spdup = (baseline / t) if (baseline and t > 0.01) else None
            spd_str = f"{spdup:.2f}x" if spdup else "-"
            print(f"{strat:<12} {t:>12.2f} {spd_str:>14}")

        # save JSON
        json_path = out_dir / f"timing_{args.scene}.json"
        json_path.write_text(json.dumps(results, indent=2, default=str))
        print()
        print(f"[bench] wrote {json_path}")

        if args.real:
            print("[bench] returning to ready pose ...")
            robot.goto_ready_pose(speed_pct=15, settle_s=6.0)

        return 0
    finally:
        robot.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
