"""Real-hardware test: cartesian control via robot.goto_xyz() (Step A).

First hardware check of goto_xyz() / EndPoseCtrl (MOVE L) on firmware
S-V1.8-2. All moves are AERIAL (Z held constant -- pen never descends) and
small (<= 30 mm), tracing a square relative to the ready-pose end pose.

    python3 test_cartesian.py inspect   # connect (no energize), read state
    python3 test_cartesian.py move      # energize -> ready pose -> trace square

Each leg is verified by reading the end pose back and comparing to the
commanded pose. If the first leg shows ~0 displacement, EndPoseCtrl is
being ignored and the run aborts before tracing the rest.

Requires CAN up (candump can0 shows 0x2A*) and piper_sdk installed.
"""

import argparse
import sys
import time

from modules.robot import Robot, C_PiperInterface

SQUARE_MM = 30.0          # side length of the aerial square
POS_TOL_MM = 5.0          # per-axis pass tolerance for end-pose error
MOVED_MIN_MM = 3.0        # min displacement that proves a leg actually moved
SPEED_PCT = 30
SETTLE_S = 3.0


def read_pose(robot):
    ep = robot.get_end_pose()
    if ep is None:
        return None
    return ep


def fmt_pose(ep):
    return (f"X={ep[0]:7.1f} Y={ep[1]:7.1f} Z={ep[2]:7.1f} mm  "
            f"RX={ep[3]:6.1f} RY={ep[4]:6.1f} RZ={ep[5]:6.1f} deg")


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
        print(f"  planned aerial square: {SQUARE_MM:.0f} mm side, Z held at "
              f"{ep[2]:.1f} mm, origin = this pose")
    finally:
        robot.disconnect()
    print("  inspect done.")
    return 0


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


def cmd_move(side_mm, speed_pct):
    print("=" * 64)
    print("  MOVE: energize -> ready pose -> trace aerial square")
    print(f"  side={side_mm:.0f}mm  speed={speed_pct}%  AERIAL (Z fixed)")
    print("=" * 64)
    robot = Robot(mock=False)
    robot.connect(enable_motors=True)
    try:
        time.sleep(0.5)
        ep = read_pose(robot)
        if ep is None:
            print("  ABORT: no end-pose feedback -- refusing to move.", file=sys.stderr)
            return 3
        if abs(ep[0]) < 1.0 and abs(ep[1]) < 1.0 and abs(ep[2]) < 1.0:
            print("  ABORT: end pose reads ~0 -- feedback likely dead.", file=sys.stderr)
            return 3

        print("  moving to ready pose for a clean start ...")
        if not robot.goto_ready_pose(speed_pct=15, settle_s=10.0):
            print("  ABORT: goto_ready_pose() failed.", file=sys.stderr)
            return 3

        origin = settle_and_read(robot)
        ox, oy, oz = origin[0], origin[1], origin[2]
        print(f"  origin (ready pose): {fmt_pose(origin)}")
        print(f"  Z will be held at {oz:.1f} mm for every leg.")

        for s in (3, 2, 1):
            print(f"  starting square in {s} ...")
            time.sleep(1.0)

        # square corners, relative to origin, Z held constant
        legs = [
            ("+X", ox + side_mm, oy,            oz),
            ("+Y", ox + side_mm, oy + side_mm,  oz),
            ("-X", ox,           oy + side_mm,  oz),
            ("-Y", ox,           oy,            oz),
        ]

        prev_actual = origin
        worst = 0.0
        for i, (name, tx, ty, tz) in enumerate(legs, start=1):
            print(f"  --- leg {i}/4 {name}: commanded "
                  f"X={tx:.1f} Y={ty:.1f} Z={tz:.1f} ---")
            ok = robot.goto_xyz(tx, ty, tz, speed_pct=speed_pct,
                                move_mode=0x02, wait_s=SETTLE_S)
            if not ok:
                print(f"  ABORT: goto_xyz() returned False on leg {i}.", file=sys.stderr)
                return 3
            actual = settle_and_read(robot)
            if actual is None:
                print(f"  ABORT: lost feedback on leg {i}.", file=sys.stderr)
                return 3

            err = (actual[0] - tx, actual[1] - ty, actual[2] - tz)
            disp = max(abs(actual[j] - prev_actual[j]) for j in range(3))
            print(f"      actual : {fmt_pose(actual)}")
            print(f"      error  : dX={err[0]:+.1f} dY={err[1]:+.1f} "
                  f"dZ={err[2]:+.1f} mm   (moved {disp:.1f} mm)")

            if i == 1 and disp < MOVED_MIN_MM:
                print("  ABORT: first leg barely moved -- EndPoseCtrl is being "
                      "ignored. Stopping before tracing the rest.", file=sys.stderr)
                return 4

            worst = max(worst, max(abs(e) for e in err))
            prev_actual = actual

        print("-" * 64)
        if worst <= POS_TOL_MM:
            print(f"  RESULT: PASS  (worst per-axis error {worst:.1f} mm "
                  f"<= {POS_TOL_MM:.0f} mm)")
            return 0
        print(f"  RESULT: CHECK (worst per-axis error {worst:.1f} mm "
              f"> {POS_TOL_MM:.0f} mm)")
        return 4
    finally:
        robot.disconnect()


def main():
    ap = argparse.ArgumentParser(description="cartesian (goto_xyz) hardware test")
    ap.add_argument("stage", choices=["inspect", "move"])
    ap.add_argument("--side", type=float, default=SQUARE_MM,
                    help=f"square side length in mm (default {SQUARE_MM:.0f})")
    ap.add_argument("--speed", type=int, default=SPEED_PCT,
                    help=f"goto_xyz speed pct (default {SPEED_PCT})")
    args = ap.parse_args()

    if C_PiperInterface is None:
        print("[test_cartesian] piper_sdk not installed -- cannot run on hardware.",
              file=sys.stderr)
        return 2

    if args.stage == "inspect":
        return cmd_inspect()
    return cmd_move(args.side, args.speed)


if __name__ == "__main__":
    raise SystemExit(main())
