"""Pipeline integration smoke test (Step E).

Drives modules.robot.Robot through a small set of hardcoded strokes
(line / square / circle) end-to-end so we can verify the drawing loop
before wiring in VLM, image-gen, and vectorizer.

Default is MOCK mode (no hardware needed). Pass --real to run against
the actual Piper arm via piper_sdk — confirm CAN is up first:

    sudo ip link set can0 type can bitrate 1000000
    sudo ip link set can0 up

Usage:
    python3 run_draw_test.py                   # mock, all strokes
    python3 run_draw_test.py --strokes line    # mock, only the line
    python3 run_draw_test.py --plot            # also save a PNG of the path
    python3 run_draw_test.py --real            # REAL hardware (asks for confirm)
"""

import argparse
import csv
import math
import sys
import time
from pathlib import Path

from modules.robot import Robot, C_PiperInterface


ROOT = Path(__file__).resolve().parent
LOGS_DIR = ROOT / "logs"


def stroke_line(length_mm: float = 20.0, x0: float = 0.0, y0: float = 0.0):
    return [(x0, y0), (x0 + length_mm, y0)]


def stroke_square(side_mm: float = 30.0, x0: float = 0.0, y0: float = 0.0):
    return [
        (x0, y0),
        (x0 + side_mm, y0),
        (x0 + side_mm, y0 + side_mm),
        (x0, y0 + side_mm),
        (x0, y0),
    ]


def stroke_circle(radius_mm: float = 15.0, cx: float = 0.0, cy: float = 0.0, samples: int = 36):
    pts = []
    for i in range(samples + 1):
        theta = 2.0 * math.pi * i / samples
        pts.append((cx + radius_mm * math.cos(theta), cy + radius_mm * math.sin(theta)))
    return pts


STROKE_FACTORY = {
    "line": stroke_line,
    "square": stroke_square,
    "circle": stroke_circle,
}


def write_csv_log(run_id: str, strokes: dict, z_down: float, z_up: float) -> Path:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    path = LOGS_DIR / f"run_draw_test_{run_id}.csv"
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["stroke", "idx", "x_mm", "y_mm", "z_mm", "phase"])
        for name, pts in strokes.items():
            if not pts:
                continue
            x0, y0 = pts[0]
            w.writerow([name, 0, x0, y0, z_up, "travel"])
            w.writerow([name, 0, x0, y0, z_down, "pen_down"])
            for i, (x, y) in enumerate(pts[1:], start=1):
                w.writerow([name, i, x, y, z_down, "draw"])
            xe, ye = pts[-1]
            w.writerow([name, len(pts) - 1, xe, ye, z_up, "pen_up"])
    return path


def maybe_plot(strokes: dict, run_id: str) -> Path | None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:
        print(f"[plot] matplotlib unavailable, skipping plot ({e})")
        return None

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(6, 6))
    for name, pts in strokes.items():
        if not pts:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        ax.plot(xs, ys, marker="o", markersize=2, label=name)
    ax.set_aspect("equal")
    ax.set_xlabel("X (mm)")
    ax.set_ylabel("Y (mm)")
    ax.set_title(f"draw_test {run_id}")
    ax.legend()
    ax.grid(True, alpha=0.3)
    out = LOGS_DIR / f"run_draw_test_{run_id}.png"
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return out


def confirm_real_run() -> bool:
    print()
    print("=" * 60)
    print("  WARNING: --real selected. This will move the physical arm.")
    print("=" * 60)
    print("  Pre-flight checklist:")
    print("    1. CAN is up: `candump can0` shows frames")
    print("    2. Arm is at a safe pose (preferably ready_pose)")
    print("    3. Workspace is clear, e-stop within reach")
    print("    4. Pen is mounted, paper is in place")
    print()
    try:
        ans = input("Type 'GO' to proceed, anything else aborts: ").strip()
    except EOFError:
        ans = ""
    return ans == "GO"


def main():
    ap = argparse.ArgumentParser(description="draw_piper pipeline smoke test")
    ap.add_argument(
        "--strokes",
        nargs="+",
        default=["line", "square", "circle"],
        choices=list(STROKE_FACTORY.keys()),
        help="Which strokes to run (default: all)",
    )
    ap.add_argument("--real", action="store_true", help="Run on real hardware (default: mock)")
    ap.add_argument("--plot", action="store_true", help="Save PNG of the path to logs/")
    ap.add_argument("--z-down", type=float, default=5.0, help="Pen-down Z (mm)")
    ap.add_argument("--z-up", type=float, default=30.0, help="Pen-up Z (mm)")
    ap.add_argument("--origin-x", type=float, default=250.0,
                    help="Absolute origin X (mm). Ignored if --current-pose is set.")
    ap.add_argument("--origin-y", type=float, default=0.0,
                    help="Absolute origin Y (mm). Ignored if --current-pose is set.")
    ap.add_argument("--origin-z", type=float, default=None,
                    help="Absolute paper Z (mm). If unset, falls back to --z-down for paper height.")
    ap.add_argument("--current-pose", action="store_true",
                    help="Real-hw only: use the arm's current end pose as the origin "
                         "(safer than absolute coordinates).")
    ap.add_argument("--ready-pose", action="store_true",
                    help="Real-hw only: move the arm to ready pose before starting.")
    ap.add_argument("--aerial", action="store_true",
                    help="Safety override: set z_down := z_up so the pen never descends.")
    ap.add_argument("--draw-speed", type=int, default=30)
    ap.add_argument("--travel-speed", type=int, default=80)
    ap.add_argument("--settle-s", type=float, default=2.0,
                    help="Real-hw wait after pen up/down transitions (s).")
    ap.add_argument("--inter-point-delay", type=float, default=0.05,
                    help="Wait between successive draw-points (s).")
    args = ap.parse_args()

    if args.real and C_PiperInterface is None:
        print("[run_draw_test] --real requested but piper_sdk is not installed.", file=sys.stderr)
        return 2
    if (args.current_pose or args.ready_pose) and not args.real:
        print("[run_draw_test] --current-pose / --ready-pose require --real.", file=sys.stderr)
        return 2
    if args.real and not confirm_real_run():
        print("[run_draw_test] Aborted by user.")
        return 1

    z_down = args.z_up if args.aerial else args.z_down
    z_up = args.z_up
    if args.aerial:
        print(f"[run_draw_test] AERIAL mode: pen never descends (z_down := z_up = {z_up}mm)")

    # explicit mock gating: piper_sdk is installed but we must NOT touch the
    # arm unless --real was passed.
    robot = Robot(mock=(not args.real))
    robot.connect()
    try:
        if args.ready_pose:
            print("[run_draw_test] moving to ready pose...")
            ok = robot.goto_ready_pose()
            if not ok:
                print("[run_draw_test] ready pose move failed, aborting.", file=sys.stderr)
                return 3

        # determine origin
        if args.current_pose:
            pose = robot.get_end_pose()
            if pose is None:
                print("[run_draw_test] could not read current pose, aborting.", file=sys.stderr)
                return 3
            ox, oy, oz = pose[0], pose[1], pose[2]
            print(f"[run_draw_test] using current end pose as origin: "
                  f"X={ox:.1f} Y={oy:.1f} Z={oz:.1f}")
            # for safety in aerial mode we keep the same Z as start
            if args.aerial:
                z_down = oz
                z_up = oz
        else:
            ox, oy = args.origin_x, args.origin_y
            if args.origin_z is not None:
                z_down = args.origin_z

        strokes: dict[str, list[tuple[float, float]]] = {}
        for name in args.strokes:
            raw = STROKE_FACTORY[name]()
            strokes[name] = [(x + ox, y + oy) for (x, y) in raw]

        run_id = time.strftime("%Y%m%d_%H%M%S")
        csv_path = write_csv_log(run_id, strokes, z_down, z_up)
        print(f"[run_draw_test] wrote {csv_path}")

        if args.plot:
            png = maybe_plot(strokes, run_id)
            if png:
                print(f"[run_draw_test] wrote {png}")

        mode = "REAL" if args.real else "MOCK"
        print(f"[run_draw_test] mode={mode} strokes={list(strokes.keys())} "
              f"z_down={z_down:.1f} z_up={z_up:.1f}")

        for name, pts in strokes.items():
            print(f"[run_draw_test] --- {name} ({len(pts)} pts) ---")
            t0 = time.time()
            robot.draw_stroke(
                pts,
                z_down_mm=z_down,
                z_up_mm=z_up,
                travel_speed=args.travel_speed,
                draw_speed=args.draw_speed,
                inter_point_delay=args.inter_point_delay,
                settle_s=args.settle_s,
            )
            print(f"[run_draw_test] {name} done in {time.time() - t0:.2f}s")
    finally:
        robot.disconnect()

    print("[run_draw_test] all strokes complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
