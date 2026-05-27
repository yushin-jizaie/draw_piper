#!/usr/bin/env python3
"""Real-hardware test: Robot.draw_strokes_panel_smooth (Frida-inspired)

複数 stroke を TSP 並べ替え + 曲率連動速度 + look-ahead descent で描画する
新メソッドの実機テスト。 既存 test_draw_arc_panel.py が 1 stroke 用なのに対し、
こちらは複数 stroke set 用。

使い方:
    cd ~/draw_piper
    source venv/bin/activate
    python3 -m scripts.test_draw_strokes_smooth \\
        --inspect                # 接続して end-pose を読むだけ (描画しない)

    # 描画モード:
    python3 -m scripts.test_draw_strokes_smooth --scene face_lite
    python3 -m scripts.test_draw_strokes_smooth --scene scattered \\
        --reorder/--no-reorder    # TSP の効果を実機で確認

scene の選択肢:
    face_lite   — oval + 目 2 つ + 口 + 体線 (5 stroke、 ~30 秒)
    scattered   — 8x8 散らばった短 stroke (TSP 効果が大、 ~2 分)
    zigzag      — 1 stroke の zigzag (curvature speed の効果が見える)

panel_frame.yaml が calibrated:true (M10/M11 のキャリブ済み) であること。

設計: docs/20260528_0030_frida_smoothness_design.md
引き継ぎ: docs/20260528_frida_smoothness_handoff.md
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

# allow running as script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.robot import Robot, PanelFrame, C_PiperInterface       # noqa: E402


def gen_face_lite(panel: PanelFrame):
    """Small face sketch in the panel center. 5 strokes."""
    width_u, height_v = panel.size_mm[0], panel.size_mm[1]
    cu = width_u * 0.5
    cv = height_v * 0.5
    r = min(20.0, width_u * 0.15, height_v * 0.15)
    # oval
    oval = []
    n = 24
    for i in range(n):
        t = 2 * math.pi * i / n
        oval.append((cu + r * math.cos(t),
                     cv + r * 1.25 * math.sin(t)))
    oval.append(oval[0])
    # eyes
    eye_l = [(cu - r * 0.4, cv - r * 0.2), (cu - r * 0.2, cv - r * 0.2)]
    eye_r = [(cu + r * 0.2, cv - r * 0.2), (cu + r * 0.4, cv - r * 0.2)]
    # mouth (slight curve)
    mouth = []
    for i in range(8):
        t = i / 7
        u = cu - r * 0.3 + r * 0.6 * t
        v = cv + r * 0.4 - 5.0 * math.sin(t * math.pi) * 0.4
        mouth.append((u, v))
    # body line (far below face)
    body = [(cu - r * 0.6, cv + r * 1.6), (cu + r * 0.6, cv + r * 1.6)]
    # scrambled order to demonstrate TSP
    return [oval, mouth, body, eye_r, eye_l]


def gen_scattered(panel: PanelFrame):
    """8x8 grid of tiny diagonal strokes, scrambled order."""
    import random
    rnd = random.Random(42)
    width_u, height_v = panel.size_mm[0], panel.size_mm[1]
    # grid covers central ~60% of panel
    pad_u = width_u * 0.2
    pad_v = height_v * 0.2
    sx = (width_u - 2 * pad_u) / 7
    sy = (height_v - 2 * pad_v) / 7
    grid = [(pad_u + i * sx, pad_v + j * sy)
            for i in range(8) for j in range(8)]
    rnd.shuffle(grid)
    # each = tiny 3mm diagonal
    return [[(x, y), (x + 3.0, y + 3.0)] for x, y in grid]


def gen_zigzag(panel: PanelFrame):
    """Single multi-curvature zigzag (1 stroke). Tests speed profile."""
    width_u, height_v = panel.size_mm[0], panel.size_mm[1]
    cu = width_u * 0.5
    cv = height_v * 0.5
    span = min(80.0, width_u * 0.6)
    pts = []
    n = 40
    for i in range(n):
        x = cu - span * 0.5 + span * (i / (n - 1))
        amp = 2.0 + (i % 5)
        y = cv + amp * (1 if i % 2 == 0 else -1)
        pts.append((x, y))
    return [pts]


SCENES = {
    "face_lite": gen_face_lite,
    "scattered": gen_scattered,
    "zigzag": gen_zigzag,
}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inspect", action="store_true",
                    help="connect → end-pose 読むだけ、 描画なし")
    ap.add_argument("--scene", choices=list(SCENES), default="face_lite")
    ap.add_argument("--reorder", dest="reorder", action="store_true",
                    default=True)
    ap.add_argument("--no-reorder", dest="reorder", action="store_false",
                    help="TSP 並べ替えを OFF (旧 raw order で実行、 比較用)")
    ap.add_argument("--draw-speed", type=int, default=30,
                    help="base draw speed percent (1-100)")
    ap.add_argument("--draw-speed-min", type=int, default=10,
                    help="鋭い曲線での最低 speed percent")
    ap.add_argument("--travel-speed", type=int, default=60,
                    help="stroke 間 travel speed percent")
    ap.add_argument("--near-threshold-mm", type=float, default=15.0,
                    help="この距離以下なら pen-up を浅く (look-ahead descent)")
    ap.add_argument("--step-mm", type=float, default=2.0)
    ap.add_argument("--merge-mm", type=float, default=0.0,
                    help="近接 stroke を pen-up せず連続化 (default 0=OFF、 "
                         "副作用で接続線が描かれる)")
    ap.add_argument("--panel-yaml", type=Path,
                    default=Path.home() / "draw_piper" /
                            "calibration" / "panel_frame.yaml")
    ap.add_argument("--countdown", type=int, default=3)
    args = ap.parse_args()

    if C_PiperInterface is None:
        print("[test_smooth] piper_sdk not installed — abort", file=sys.stderr)
        return 2

    if not args.panel_yaml.exists():
        print(f"[test_smooth] panel_frame.yaml が無い: {args.panel_yaml}",
              file=sys.stderr)
        return 2

    panel = PanelFrame.from_yaml(args.panel_yaml)
    if not panel.calibrated:
        print(f"[test_smooth] WARN: panel.calibrated=False — placeholder で "
              "実機描画は危険。 中断は Ctrl+C。")
        time.sleep(2.0)

    print(f"[test_smooth] panel size = {panel.size_mm[0]:.1f} x "
          f"{panel.size_mm[1]:.1f} mm")

    robot = Robot(mock=False, panel_frame=panel,
                  use_feedback_workaround=True)
    robot.connect(enable_motors=not args.inspect)

    try:
        ep = robot.get_end_pose()
        if ep:
            print(f"[test_smooth] current end-pose: X={ep[0]:.1f} Y={ep[1]:.1f} "
                  f"Z={ep[2]:.1f}mm  RX={ep[3]:.1f} RY={ep[4]:.1f} "
                  f"RZ={ep[5]:.1f} deg")
        if args.inspect:
            print("[test_smooth] inspect done — exiting before any motion")
            return 0

        print("[test_smooth] moving to ready pose ...")
        if not robot.goto_ready_pose(speed_pct=15, settle_s=10.0):
            print("[test_smooth] goto_ready_pose failed", file=sys.stderr)
            return 3

        strokes = SCENES[args.scene](panel)
        print(f"[test_smooth] scene={args.scene}, {len(strokes)} strokes "
              f"(reorder={args.reorder})")

        for s in range(args.countdown, 0, -1):
            print(f"[test_smooth] starting in {s} ...")
            time.sleep(1.0)

        t0 = time.time()
        diag = robot.draw_strokes_panel_smooth(
            strokes,
            travel_speed=args.travel_speed,
            draw_speed_base=args.draw_speed,
            draw_speed_min=args.draw_speed_min,
            near_threshold_mm=args.near_threshold_mm,
            step_mm=args.step_mm,
            reorder=args.reorder,
            merge_threshold_mm=args.merge_mm,
        )
        elapsed = time.time() - t0
        print(f"[test_smooth] done in {elapsed:.2f}s (n_arcs={diag.get('n_arcs')})")
        print()
        print("=== diagnostics ===")
        for k, v in diag.items():
            if isinstance(v, list) and len(v) > 6:
                print(f"  {k:<22} {v[:5]}... (len={len(v)})")
            elif isinstance(v, float):
                print(f"  {k:<22} {v:.2f}")
            else:
                print(f"  {k:<22} {v}")

        # save diagnostics for benchmark comparison
        log_dir = Path("logs") / "frida_smoothness"
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        log_path = log_dir / f"diag_{args.scene}_{ts}.json"
        # filter non-JSON keys
        diag_json = {k: v for k, v in diag.items()
                     if isinstance(v, (int, float, str, list, dict))}
        diag_json["scene"] = args.scene
        diag_json["reorder"] = args.reorder
        diag_json["elapsed_s"] = elapsed
        diag_json["draw_speed_base"] = args.draw_speed
        log_path.write_text(json.dumps(diag_json, indent=2))
        print(f"[test_smooth] diagnostics saved -> {log_path}")

        print("[test_smooth] returning to ready pose ...")
        robot.goto_ready_pose(speed_pct=15, settle_s=6.0)
        return 0
    finally:
        robot.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
