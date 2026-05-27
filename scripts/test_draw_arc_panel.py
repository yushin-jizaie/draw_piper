#!/usr/bin/env python3
"""Real-hardware test: draw_stroke_panel_arcs (MOVE_C 円弧描画) + wait_for_pose

実機テスト用ランナー。 panel_frame.yaml を読んで Robot を connect → ready_pose
→ 正弦波 1 ストロークを MOVE_C 円弧で描く → ready_pose に戻る。

使い方:
    cd ~/draw_piper
    source venv/bin/activate
    python3 -m scripts.test_draw_arc_panel \\
        --inspect              # 接続して end-pose を読むだけ (描画しない)
    python3 -m scripts.test_draw_arc_panel \\
        --shape sine --speed 20 --step-mm 2.0     # 実描画

    # MOVE_L (旧来) と比較したいとき:
    python3 -m scripts.test_draw_arc_panel \\
        --shape sine --linear    # MOVE_L で描く

shape の選択肢:
    sine        — 横方向の sin 波 1 周期
    circle      — 半径 30mm の円 (closed)
    spiral      — 内→外の螺旋 2 周

panel の uv 座標 (mm) で描くので、 panel_frame.yaml が calibrated:true
で正しい原点 / size_mm を持っている必要があります。 size_mm の中央付近で
描くようパスを生成します。
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

# allow running as script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.robot import Robot, PanelFrame, C_PiperInterface       # noqa: E402


def gen_sine(panel: PanelFrame) -> list[tuple[float, float]]:
    """横方向に 100mm、 中央高さ ±15mm の sin 波 1 周期。"""
    width_u, height_v = panel.size_mm[0], panel.size_mm[1]
    cu = width_u * 0.5
    cv = height_v * 0.5
    span = min(100.0, width_u * 0.6)
    amp = min(15.0, height_v * 0.15)
    n = 30
    pts = []
    for i in range(n):
        t = i / (n - 1)
        u = cu - span * 0.5 + span * t
        v = cv + amp * math.sin(t * 2 * math.pi)
        pts.append((u, v))
    return pts


def gen_circle(panel: PanelFrame) -> list[tuple[float, float]]:
    """半径 30mm の閉じた円 (中央)。"""
    width_u, height_v = panel.size_mm[0], panel.size_mm[1]
    cu = width_u * 0.5
    cv = height_v * 0.5
    r = min(30.0, width_u * 0.25, height_v * 0.25)
    n = 36
    pts = []
    for i in range(n + 1):     # +1 で閉じる
        ang = 2 * math.pi * i / n
        pts.append((cu + r * math.cos(ang), cv + r * math.sin(ang)))
    return pts


def gen_spiral(panel: PanelFrame) -> list[tuple[float, float]]:
    """中央から外へ 2 周の螺旋。"""
    width_u, height_v = panel.size_mm[0], panel.size_mm[1]
    cu = width_u * 0.5
    cv = height_v * 0.5
    r_max = min(40.0, width_u * 0.3, height_v * 0.3)
    n = 60
    pts = []
    for i in range(n):
        t = i / (n - 1)
        ang = 2 * 2 * math.pi * t       # 2 周
        r = r_max * t
        pts.append((cu + r * math.cos(ang), cv + r * math.sin(ang)))
    return pts


SHAPES = {"sine": gen_sine, "circle": gen_circle, "spiral": gen_spiral}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inspect", action="store_true",
                    help="connect → end-pose 読むだけ、 描画なし")
    ap.add_argument("--shape", choices=list(SHAPES), default="sine")
    ap.add_argument("--linear", action="store_true",
                    help="MOVE_C ではなく MOVE_L (draw_stroke_panel) で描く")
    ap.add_argument("--speed", type=int, default=20,
                    help="draw_speed (1-100). 円弧モードは控えめが安全")
    ap.add_argument("--step-mm", type=float, default=2.0,
                    help="smooth_polyline の再サンプリング間隔 (MOVE_C 時)")
    ap.add_argument("--rank", "--smooth-rank", dest="smooth_lambda",
                    type=float, default=0.0,
                    help="スプライン平滑度 (0 = exact pass through)")
    ap.add_argument("--no-smooth", action="store_true",
                    help="smooth_polyline を通さず raw 点を MOVE_C 化")
    ap.add_argument("--panel-yaml", type=Path,
                    default=Path.home() / "draw_piper" /
                            "calibration" / "panel_frame.yaml")
    args = ap.parse_args()

    if C_PiperInterface is None:
        print("[test_arc] piper_sdk not installed — abort", file=sys.stderr)
        return 2

    if not args.panel_yaml.exists():
        print(f"[test_arc] panel_frame.yaml が無い: {args.panel_yaml}",
              file=sys.stderr)
        return 2

    panel = PanelFrame.from_yaml(args.panel_yaml)
    if not panel.calibrated:
        print(f"[test_arc] WARN: panel.calibrated=False — placeholder 値で "
              "実機描画は危険。 中断したければ Ctrl+C。")
        time.sleep(2.0)

    print(f"[test_arc] panel size = {panel.size_mm[0]:.1f} x "
          f"{panel.size_mm[1]:.1f} mm")
    print(f"[test_arc] origin = {panel.origin}")

    robot = Robot(mock=False, panel_frame=panel,
                  use_feedback_workaround=True)
    robot.connect(enable_motors=not args.inspect)

    try:
        ep = robot.get_end_pose()
        if ep:
            print(f"[test_arc] current end-pose: X={ep[0]:.1f} Y={ep[1]:.1f} "
                  f"Z={ep[2]:.1f}mm  RX={ep[3]:.1f} RY={ep[4]:.1f} "
                  f"RZ={ep[5]:.1f} deg")
        else:
            print("[test_arc] end-pose: <no feedback>")
        if args.inspect:
            print("[test_arc] inspect done — exiting before any motion")
            return 0

        print("[test_arc] moving to ready pose ...")
        if not robot.goto_ready_pose(speed_pct=15, settle_s=10.0):
            print("[test_arc] goto_ready_pose failed", file=sys.stderr)
            return 3

        pts = SHAPES[args.shape](panel)
        print(f"[test_arc] generated {len(pts)} input points for "
              f"shape={args.shape}")
        print(f"[test_arc]   first  = {pts[0]}")
        print(f"[test_arc]   last   = {pts[-1]}")

        for s in (3, 2, 1):
            print(f"[test_arc] starting in {s} ...")
            time.sleep(1.0)

        t0 = time.time()
        if args.linear:
            print("[test_arc] mode = MOVE_L (draw_stroke_panel)")
            robot.draw_stroke_panel(
                pts, draw_speed=args.speed, travel_speed=40, settle_s=2.0,
                inter_point_delay=0.05)
        else:
            print(f"[test_arc] mode = MOVE_C (draw_stroke_panel_arcs) "
                  f"smooth={not args.no_smooth} step={args.step_mm}mm")
            robot.draw_stroke_panel_arcs(
                pts, draw_speed=args.speed, travel_speed=40, settle_s=2.0,
                smooth=not args.no_smooth, step_mm=args.step_mm,
                smooth_lambda=args.smooth_lambda)
        print(f"[test_arc] done in {time.time() - t0:.2f}s")

        print("[test_arc] moving back to ready pose ...")
        robot.goto_ready_pose(speed_pct=15, settle_s=10.0)
        return 0
    except KeyboardInterrupt:
        print("\n[test_arc] interrupted", file=sys.stderr)
        return 130
    finally:
        robot.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
