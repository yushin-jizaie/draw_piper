"""Real-hardware test: storage pose -> ready pose via robot.goto_ready_pose().

Two-stage so the operator can verify joint feedback BEFORE any motion:

    python3 test_ready_pose.py inspect   # connect (no energize), read state only
    python3 test_ready_pose.py move      # connect + energize, then goto_ready_pose()

Run `inspect` first and confirm the printed joints match the physical storage
pose. The `move` stage refuses to issue motion if joints read all-near-zero
(a sign that feedback is dead) -- in that case goto_ready_pose()'s
"hold current" step would otherwise command every joint to 0 deg.

Requires CAN up (`candump can0` shows 0x2A* frames) and piper_sdk installed.
"""

import argparse
import sys
import time

from modules.robot import Robot, C_PiperInterface, DEFAULT_READY_POSE_DEG

JOINT_KEYS = ("j1", "j2", "j3", "j4", "j5", "j6")


def print_state(robot, label):
    js = robot.get_joints()
    ep = robot.get_end_pose()
    print(f"  [{label}] joints (deg):")
    if js is None:
        print("    <no joint feedback>")
    else:
        for i, v in enumerate(js, start=1):
            print(f"    j{i} = {v:8.3f}")
    if ep is None:
        print(f"  [{label}] end pose: <no feedback>")
    else:
        print(f"  [{label}] end pose: X={ep[0]:.1f} Y={ep[1]:.1f} Z={ep[2]:.1f} mm  "
              f"RX={ep[3]:.1f} RY={ep[4]:.1f} RZ={ep[5]:.1f} deg")
    return js, ep


def cmd_inspect():
    print("=" * 60)
    print("  INSPECT: read-only, motors NOT energized, no motion")
    print("=" * 60)
    robot = Robot(mock=False)
    robot.connect(enable_motors=False)
    try:
        time.sleep(0.5)
        js, ep = print_state(robot, "current")
        target = DEFAULT_READY_POSE_DEG
        print("  ready-pose target (deg):")
        for k in JOINT_KEYS:
            print(f"    {k} = {target[k]:8.3f}")
        if js is not None:
            print("  delta needed (target - current), deg:")
            for i, k in enumerate(JOINT_KEYS):
                print(f"    {k}: {target[k] - js[i]:+8.3f}")
        else:
            print("  WARNING: no joint feedback -- do NOT run `move` until fixed.")
    finally:
        robot.disconnect()
    print("  inspect done. If joints match the physical storage pose, run `move`.")
    return 0


def cmd_move(speed_pct, settle_s):
    print("=" * 60)
    print("  MOVE: energize + goto_ready_pose()  (PHYSICAL ARM WILL MOVE)")
    print(f"  speed={speed_pct}%   settle={settle_s}s")
    print("=" * 60)
    robot = Robot(mock=False)
    robot.connect(enable_motors=True)
    try:
        time.sleep(0.5)
        js, ep = print_state(robot, "before")

        # Safety gate: goto_ready_pose() holds the *current* joints first; if
        # feedback is dead, GetArmJointMsgs/GetArmEndPoseMsgs both return 0 and
        # the hold step would slam every joint to 0 deg. Proof of live feedback
        # is a non-zero end pose -- a real arm's end effector is never at the
        # base origin. (Joints alone can legitimately read ~0 when the arm is
        # parked at its zero pose, so they are not a reliable liveness check.)
        if js is None or ep is None:
            print("  ABORT: no feedback -- refusing to move.", file=sys.stderr)
            return 3
        if abs(ep[0]) < 1.0 and abs(ep[1]) < 1.0 and abs(ep[2]) < 1.0:
            print("  ABORT: end pose reads ~0 -- feedback is likely dead. "
                  "Refusing to move.", file=sys.stderr)
            return 3

        for s in (3, 2, 1):
            print(f"  moving in {s} ...")
            time.sleep(1.0)

        t0 = time.time()
        ok = robot.goto_ready_pose(speed_pct=speed_pct, settle_s=settle_s)
        if not ok:
            print("  goto_ready_pose() returned False -- move failed.", file=sys.stderr)
            return 3
        print(f"  goto_ready_pose() returned in {time.time() - t0:.1f}s")

        # Watch joints settle: poll until two consecutive samples agree.
        prev = None
        for _ in range(20):
            cur = robot.get_joints()
            if cur is not None and prev is not None:
                if max(abs(a - b) for a, b in zip(cur, prev)) < 0.1:
                    break
            prev = cur
            time.sleep(0.5)

        js_after, _ = print_state(robot, "after")
        target = DEFAULT_READY_POSE_DEG
        print("  per-joint error (actual - target), deg:")
        worst = 0.0
        for i, k in enumerate(JOINT_KEYS):
            err = js_after[i] - target[k]
            worst = max(worst, abs(err))
            flag = "  <-- OFF" if abs(err) > 2.0 else ""
            print(f"    {k}: {err:+8.3f}{flag}")
        if worst <= 2.0:
            print(f"  RESULT: PASS  (worst joint error {worst:.3f} deg <= 2.0)")
            return 0
        print(f"  RESULT: CHECK (worst joint error {worst:.3f} deg > 2.0)")
        return 4
    finally:
        robot.disconnect()


def main():
    ap = argparse.ArgumentParser(description="storage -> ready pose hardware test")
    ap.add_argument("stage", choices=["inspect", "move"],
                    help="inspect = read-only; move = energize + goto_ready_pose")
    ap.add_argument("--speed", type=int, default=15,
                    help="JointCtrl speed pct for the move stage (default 15)")
    ap.add_argument("--settle", type=float, default=10.0,
                    help="seconds to wait inside goto_ready_pose (default 10)")
    args = ap.parse_args()

    if C_PiperInterface is None:
        print("[test_ready_pose] piper_sdk not installed -- cannot run on hardware.",
              file=sys.stderr)
        return 2

    if args.stage == "inspect":
        return cmd_inspect()
    return cmd_move(args.speed, args.settle)


if __name__ == "__main__":
    raise SystemExit(main())
