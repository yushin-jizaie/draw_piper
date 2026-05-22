"""Real-hardware integration test: robot.draw_stroke() (Step A).

Exercises the full draw_stroke() orchestration -- travel -> pen-down ->
draw loop -> pen-up -- on real hardware. AERIAL: z_down == z_up, so the
pen never changes height; this isolates the XY path. Traces a 30 mm
square relative to the ready-pose end pose.

    python3 test_draw_stroke.py inspect   # connect (no energize), read state
    python3 test_draw_stroke.py move      # energize -> ready pose -> draw_stroke

goto_xyz() is wrapped so every waypoint draw_stroke() issues is recorded
with its commanded vs settled-actual end pose, giving a per-leg pass/fail.
The settle after each call also serializes the draw loop, so corners are
actually reached (the default streaming cadence is for dense paths).

Requires CAN up (candump can0 shows 0x2A*) and piper_sdk installed.
"""

import argparse
import sys
import time

from modules.robot import Robot, C_PiperInterface

SQUARE_MM = 30.0          # side length of the aerial square
POS_TOL_MM = 5.0          # per-axis pass tolerance for end-pose error
TRAVEL_SPEED = 40
DRAW_SPEED = 25


def read_pose(robot):
    return robot.get_end_pose()


def fmt_pose(ep):
    return (f"X={ep[0]:7.1f} Y={ep[1]:7.1f} Z={ep[2]:7.1f} mm  "
            f"RX={ep[3]:6.1f} RY={ep[4]:6.1f} RZ={ep[5]:6.1f} deg")


def settle_and_read(robot, timeout_s=6.0):
    """Poll the end pose until two consecutive samples agree, then return it."""
    prev = None
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        cur = read_pose(robot)
        if cur is not None and prev is not None:
            if max(abs(a - b) for a, b in zip(cur[:3], prev[:3])) < 0.3:
                return cur
        prev = cur
        time.sleep(0.4)
    return prev


def square_points(ox, oy, side):
    """Closed square as (x, y) pairs, relative origin at (ox, oy)."""
    return [
        (ox,        oy),
        (ox + side, oy),
        (ox + side, oy + side),
        (ox,        oy + side),
        (ox,        oy),
    ]


def cmd_inspect():
    print("=" * 64)
    print("  INSPECT: read-only, motors NOT energized, no motion")
    print("=" * 64)
    robot = Robot(mock=False)
    robot.connect(enable_motors=False)
    try:
        time.sleep(0.5)
        ep = read_pose(robot)
        if ep is None:
            print("  end pose: <no feedback>  -- do NOT run `move`.")
            return 3
        print(f"  end pose: {fmt_pose(ep)}")
        print(f"  planned: draw_stroke() of a {SQUARE_MM:.0f} mm square, "
              f"AERIAL at Z={ep[2]:.1f} mm, origin = this pose")
    finally:
        robot.disconnect()
    print("  inspect done.")
    return 0


def phase_label(i, n):
    if i == 0:
        return "travel  "
    if i == 1:
        return "pen-down"
    if i == n - 1:
        return "pen-up  "
    return "draw    "


def cmd_move(side_mm):
    print("=" * 64)
    print("  MOVE: energize -> ready pose -> draw_stroke() of an aerial square")
    print(f"  side={side_mm:.0f}mm  travel={TRAVEL_SPEED}%  draw={DRAW_SPEED}%  "
          f"AERIAL (Z fixed)")
    print("=" * 64)
    robot = Robot(mock=False)
    robot.connect(enable_motors=True)
    try:
        time.sleep(0.5)
        ep = read_pose(robot)
        if ep is None or (abs(ep[0]) < 1.0 and abs(ep[1]) < 1.0 and abs(ep[2]) < 1.0):
            print("  ABORT: end pose missing or ~0 -- feedback likely dead.",
                  file=sys.stderr)
            return 3

        print("  moving to ready pose for a clean start ...")
        if not robot.goto_ready_pose(speed_pct=15, settle_s=10.0):
            print("  ABORT: goto_ready_pose() failed.", file=sys.stderr)
            return 3

        origin = settle_and_read(robot)
        ox, oy, oz = origin[0], origin[1], origin[2]
        print(f"  origin (ready pose): {fmt_pose(origin)}")
        pts = square_points(ox, oy, side_mm)

        for s in (3, 2, 1):
            print(f"  starting draw_stroke() in {s} ...")
            time.sleep(1.0)

        # Wrap goto_xyz to record every waypoint draw_stroke() issues.
        captured = []
        orig_goto = robot.goto_xyz

        def logging_goto(x_mm, y_mm, z_mm, **kw):
            ok = orig_goto(x_mm, y_mm, z_mm, **kw)
            actual = settle_and_read(robot)
            if actual is None:
                raise RuntimeError("lost end-pose feedback during draw_stroke()")
            captured.append({"cmd": (x_mm, y_mm, z_mm), "actual": actual})
            return ok

        robot.goto_xyz = logging_goto
        try:
            # AERIAL: z_down == z_up == oz, so the pen never descends.
            robot.draw_stroke(pts, z_down_mm=oz, z_up_mm=oz,
                              travel_speed=TRAVEL_SPEED, draw_speed=DRAW_SPEED,
                              inter_point_delay=0.5, settle_s=2.0)
        finally:
            robot.goto_xyz = orig_goto

        n = len(captured)
        print("-" * 64)
        print(f"  draw_stroke() issued {n} waypoints:")
        worst = 0.0
        for i, w in enumerate(captured):
            cx, cy, cz = w["cmd"]
            a = w["actual"]
            err = (a[0] - cx, a[1] - cy, a[2] - cz)
            worst = max(worst, max(abs(e) for e in err))
            flag = "  <-- OFF" if max(abs(e) for e in err) > POS_TOL_MM else ""
            print(f"   [{i}] {phase_label(i, n)}  cmd X={cx:7.1f} Y={cy:7.1f} "
                  f"Z={cz:6.1f}  err dX={err[0]:+5.1f} dY={err[1]:+5.1f} "
                  f"dZ={err[2]:+5.1f} mm{flag}")

        # Closed square: final pen-up should land back on the origin.
        final = captured[-1]["actual"]
        home_err = max(abs(final[0] - ox), abs(final[1] - oy), abs(final[2] - oz))
        print(f"  closed-loop check: final pose is {home_err:.1f} mm from origin")

        print("-" * 64)
        if worst <= POS_TOL_MM and home_err <= POS_TOL_MM:
            print(f"  RESULT: PASS  (worst waypoint error {worst:.1f} mm, "
                  f"return-to-origin {home_err:.1f} mm, both <= {POS_TOL_MM:.0f} mm)")
            return 0
        print(f"  RESULT: CHECK (worst waypoint error {worst:.1f} mm, "
              f"return-to-origin {home_err:.1f} mm)")
        return 4
    except RuntimeError as e:
        print(f"  ABORT: {e}", file=sys.stderr)
        return 3
    finally:
        robot.disconnect()


def main():
    ap = argparse.ArgumentParser(description="draw_stroke() hardware integration test")
    ap.add_argument("stage", choices=["inspect", "move"])
    ap.add_argument("--side", type=float, default=SQUARE_MM,
                    help=f"square side length in mm (default {SQUARE_MM:.0f})")
    args = ap.parse_args()

    if C_PiperInterface is None:
        print("[test_draw_stroke] piper_sdk not installed -- cannot run on hardware.",
              file=sys.stderr)
        return 2

    if args.stage == "inspect":
        return cmd_inspect()
    return cmd_move(args.side)


if __name__ == "__main__":
    raise SystemExit(main())
