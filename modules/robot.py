"""piper_sdk wrapper (robot.py)

Concrete implementation for Step A: simple utilities to move the arm in
the XY plane and emulate pen up/down using Z offsets. The implementation
uses `piper_sdk.C_PiperInterface_V2` when available; otherwise it falls
back to a mock mode so the code can be developed without hardware.

Notes about units (per project docs):
- EndPoseCtrl accepts position in 0.001mm (i.e. mm * 1000)
- Rotation in 0.001deg
- JointCtrl accepts joint angles in 0.001deg

Initialization (firmware S-V1.8-2):
- `Config Init` (ArmParamEnquiryAndConfig 0x01,0x02,0,0,0x02) MUST be sent
  after ConnectPort, otherwise JointCtrl/EndPoseCtrl are silently ignored.
  This reproduces the AgileX GUI "Config Init" button — see
  docs/20260522_1700_piper_jointctrl_solved.md.
- If the arm is in master mode (feedback on 0x3A* not 0x2A*), run the
  MasterSlaveConfig(0xFC,0,0,0) recovery + power cycle first.

This module exposes a small API:
- `connect()` / `disconnect()`
- `goto_xyz(x_mm, y_mm, z_mm, speed_pct=50, move_mode=0x02)`
- `pen_up()` / `pen_down()`
- `draw_stroke(points_mm, z_down_mm, z_up_mm)`
- `goto_ready_pose()`             (joint-space, MOVE J)
- `get_end_pose()` / `get_joints()` (read-back from hardware)
"""

import time
import math
try:
    from piper_sdk import C_PiperInterface_V2
except Exception:
    C_PiperInterface_V2 = None

# backwards-compat alias: callers (e.g. run_draw_test.py) check SDK availability
# via `C_PiperInterface is None`.
C_PiperInterface = C_PiperInterface_V2

try:
    from .piper_feedback import PiperFeedback
except Exception:
    PiperFeedback = None


# Default ready pose in degrees (per HANDOFF doc).
# Avoids shoulder + wrist singularities.
DEFAULT_READY_POSE_DEG = {'j1': -45.0, 'j2': 60.0, 'j3': -60.0, 'j4': 0.0, 'j5': 30.0, 'j6': 0.0}


class Robot:
    def __init__(self, can_port='can0', default_z_mm=10.0, ready_pose=None, mock=None,
                 use_feedback_workaround=False):
        """If mock is None: auto-detect (mock when piper_sdk is unavailable).
        Pass mock=True to force mock even when piper_sdk is installed
        (the safe default for tests). Pass mock=False to require real hardware.

        use_feedback_workaround: start the 0x3A* side listener (only needed if
        the arm is still in master mode; normally feedback is on 0x2A* and the
        SDK reads it natively).
        """
        self.can_port = can_port
        self.default_z_mm = default_z_mm
        self.ready_pose = ready_pose or dict(DEFAULT_READY_POSE_DEG)
        self._conn = None
        self.connected = False
        self.use_feedback_workaround = use_feedback_workaround
        if mock is None:
            self._mock = C_PiperInterface_V2 is None
        else:
            if not mock and C_PiperInterface_V2 is None:
                raise RuntimeError("mock=False requested but piper_sdk is not installed")
            self._mock = bool(mock)
        # optional 0x3A* side listener (master-mode fallback only)
        self._feedback = None
        # cached end-pose orientation (0.001 deg) — used when caller omits rx/ry/rz.
        self._rx_mdeg = 0
        self._ry_mdeg = 0
        self._rz_mdeg = 0

    # ------------------------------------------------------------------
    # connect / disconnect
    # ------------------------------------------------------------------
    def connect(self, enable_motors=True, settle_s=1.5, config_init=True):
        """Connect and initialize the arm.

        config_init: send the Config Init sequence (required for JointCtrl /
        EndPoseCtrl to take effect on firmware S-V1.8-2). Leave True unless you
        have a specific reason to skip it.
        """
        if self._mock:
            reason = 'piper_sdk not installed' if C_PiperInterface_V2 is None else 'mock=True'
            print(f'[robot] MOCK mode ({reason}) — no CAN traffic')
            self.connected = True
            return

        self._conn = C_PiperInterface_V2(self.can_port)
        self._conn.ConnectPort()
        time.sleep(1.0)

        # optional master-mode fallback listener (0x3A* feedback)
        if self.use_feedback_workaround and PiperFeedback is not None:
            try:
                self._feedback = PiperFeedback(self.can_port)
                self._feedback.start()
                if self._feedback.wait_until_ready(timeout=2.0):
                    print('[robot] 0x3A* feedback listener ready (master-mode fallback)')
                else:
                    print('[robot] 0x3A* feedback listener: no frames within 2s')
            except Exception as e:
                print('[robot] feedback listener failed to start:', e)
                self._feedback = None

        # Config Init — resets joint params to defaults; without this the arm
        # silently ignores motion commands on firmware S-V1.8-2.
        if config_init:
            try:
                self._conn.ArmParamEnquiryAndConfig(0x01, 0x02, 0, 0, 0x02)
                time.sleep(0.5)
                self._conn.SearchAllMotorMaxAngleSpd()
                time.sleep(0.3)
                print('[robot] Config Init done')
            except Exception as e:
                print('[robot] Config Init error:', e)

        if enable_motors:
            # high-level enable: loops until all motors are energized
            t0 = time.time()
            try:
                while not self._conn.EnablePiper():
                    time.sleep(0.01)
                    if time.time() - t0 > settle_s + 4.0:
                        print('[robot] EnablePiper timeout')
                        break
                else:
                    print('[robot] EnablePiper OK')
            except Exception as e:
                print('[robot] EnablePiper error:', e)
            time.sleep(0.5)

        # CAN control mode (0x01) + MOVE J (0x01) at safe speed by default
        try:
            self._conn.ModeCtrl(0x01, 0x01, 20, 0x00)
        except Exception as e:
            print('[robot] ModeCtrl error:', e)
        time.sleep(0.3)

        # cache the current end-pose orientation so XY moves preserve the wrist
        self._refresh_orientation_cache()

        self.connected = True

    def disconnect(self):
        if self._mock:
            self.connected = False
            return
        if self._feedback:
            try:
                self._feedback.stop()
            except Exception:
                pass
            self._feedback = None
        if self._conn:
            try:
                self._conn.ClosePort()
            except Exception:
                pass
        self.connected = False

    # ------------------------------------------------------------------
    # state read-back
    # ------------------------------------------------------------------
    def get_end_pose(self):
        """Return (x_mm, y_mm, z_mm, rx_deg, ry_deg, rz_deg) or None in mock mode."""
        if self._mock:
            return None
        if self._feedback:
            ep = self._feedback.get_end_pose()
            if ep is not None:
                return ep
        if not self._conn:
            return None
        ep = self._conn.GetArmEndPoseMsgs().end_pose
        return (
            ep.X_axis / 1000.0,
            ep.Y_axis / 1000.0,
            ep.Z_axis / 1000.0,
            ep.RX_axis / 1000.0,
            ep.RY_axis / 1000.0,
            ep.RZ_axis / 1000.0,
        )

    def get_joints(self):
        """Return 6-tuple of joint angles in degrees, or None in mock mode."""
        if self._mock:
            return None
        if self._feedback:
            js = self._feedback.get_joints()
            if js is not None:
                return js
        if not self._conn:
            return None
        js = self._conn.GetArmJointMsgs().joint_state
        return (
            js.joint_1 / 1000.0,
            js.joint_2 / 1000.0,
            js.joint_3 / 1000.0,
            js.joint_4 / 1000.0,
            js.joint_5 / 1000.0,
            js.joint_6 / 1000.0,
        )

    def _refresh_orientation_cache(self):
        if self._mock:
            return
        ep = self.get_end_pose()
        if ep is None:
            print('[robot] orientation cache: no feedback yet, leaving rx/ry/rz at 0')
            return
        self._rx_mdeg = int(round(ep[3] * 1000.0))
        self._ry_mdeg = int(round(ep[4] * 1000.0))
        self._rz_mdeg = int(round(ep[5] * 1000.0))
        print(f"[robot] cached orientation (deg): RX={ep[3]:.2f}, "
              f"RY={ep[4]:.2f}, RZ={ep[5]:.2f}")

    # ------------------------------------------------------------------
    # ready pose (joint-space)
    # ------------------------------------------------------------------
    def goto_ready_pose(self, speed_pct=15, settle_s=6.0):
        """Move the arm to the configured ready pose via JointCtrl (MOVE J).

        Uses the "hold current first" pattern to avoid sudden jumps when the
        controller comes online.
        """
        if not self.connected:
            raise RuntimeError('Robot not connected')

        target_mdeg = tuple(
            int(round(self.ready_pose[k] * 1000.0))
            for k in ('j1', 'j2', 'j3', 'j4', 'j5', 'j6')
        )

        if self._mock:
            print(f"[MOCK] goto_ready_pose: {self.ready_pose} (speed={speed_pct}%)")
            time.sleep(0.1)
            return True

        try:
            self._conn.ModeCtrl(0x01, 0x01, int(max(1, min(100, speed_pct))), 0x00)
            time.sleep(0.3)
        except Exception as e:
            print('[robot] ModeCtrl error before ready pose:', e)
            return False

        # hold the current joints first, then issue the target
        try:
            js = self._conn.GetArmJointMsgs().joint_state
            self._conn.JointCtrl(js.joint_1, js.joint_2, js.joint_3,
                                 js.joint_4, js.joint_5, js.joint_6)
            time.sleep(1.0)
        except Exception as e:
            print('[robot] hold-current before ready pose failed:', e)

        try:
            self._conn.JointCtrl(*target_mdeg)
        except Exception as e:
            print('[robot] goto_ready_pose error:', e)
            return False
        time.sleep(settle_s)

        # refresh orientation cache now that we are at a known pose
        self._refresh_orientation_cache()
        return True

    # ------------------------------------------------------------------
    # end-pose control (cartesian)
    # ------------------------------------------------------------------
    def _to_piper_pos(self, x_mm, y_mm, z_mm, rx_deg=None, ry_deg=None, rz_deg=None):
        X = int(round(x_mm * 1000.0))
        Y = int(round(y_mm * 1000.0))
        Z = int(round(z_mm * 1000.0))
        # fall back to cached orientation when caller does not pass one
        RX = int(round(rx_deg * 1000.0)) if rx_deg is not None else self._rx_mdeg
        RY = int(round(ry_deg * 1000.0)) if ry_deg is not None else self._ry_mdeg
        RZ = int(round(rz_deg * 1000.0)) if rz_deg is not None else self._rz_mdeg
        return X, Y, Z, RX, RY, RZ

    def goto_xyz(self, x_mm, y_mm, z_mm,
                 rx_deg=None, ry_deg=None, rz_deg=None,
                 speed_pct=50, move_mode=0x02, wait_s=0.0):
        """Move the end-effector to the given pose (mm, deg).

        rx/ry/rz default to the cached current orientation. move_mode:
        0x01=MOVE J, 0x02=MOVE L. wait_s blocks after issuing the command.
        """
        if not self.connected:
            raise RuntimeError('Robot not connected')

        if self._mock:
            print(f"[MOCK] goto_xyz: ({x_mm:.1f},{y_mm:.1f},{z_mm:.1f})mm speed={speed_pct}")
            time.sleep(0.05)
            return True

        X, Y, Z, RX, RY, RZ = self._to_piper_pos(x_mm, y_mm, z_mm, rx_deg, ry_deg, rz_deg)
        try:
            self._conn.ModeCtrl(0x01, move_mode, int(max(1, min(100, speed_pct))), 0x00)
            self._conn.EndPoseCtrl(X, Y, Z, RX, RY, RZ)
        except Exception as e:
            print('[robot] goto_xyz error:', e)
            return False
        if wait_s > 0:
            time.sleep(wait_s)
        return True

    # ------------------------------------------------------------------
    # pen up / down + stroke helpers
    # ------------------------------------------------------------------
    def pen_up(self, x_mm=None, y_mm=None, z_up_mm=30.0, speed_pct=60, wait_s=0.0):
        if x_mm is None or y_mm is None:
            return self.goto_xyz(0.0, 0.0, z_up_mm, speed_pct=speed_pct, move_mode=0x00, wait_s=wait_s)
        return self.goto_xyz(x_mm, y_mm, z_up_mm, speed_pct=speed_pct, move_mode=0x02, wait_s=wait_s)

    def pen_down(self, x_mm=None, y_mm=None, z_down_mm=5.0, speed_pct=30, wait_s=0.0):
        if x_mm is None or y_mm is None:
            return self.goto_xyz(0.0, 0.0, z_down_mm, speed_pct=speed_pct, move_mode=0x02, wait_s=wait_s)
        return self.goto_xyz(x_mm, y_mm, z_down_mm, speed_pct=speed_pct, move_mode=0x02, wait_s=wait_s)

    def draw_stroke(self, points_mm, z_down_mm=5.0, z_up_mm=30.0,
                    travel_speed=80, draw_speed=30,
                    inter_point_delay=0.02, settle_s=2.0):
        """Draw a stroke defined by a list of (x_mm, y_mm) points.

        settle_s is the blocking wait after pen-up / pen-down transitions —
        needed on real hardware because EndPoseCtrl returns immediately.
        inter_point_delay is per-waypoint while drawing.
        """
        if not points_mm:
            return
        x0, y0 = points_mm[0]
        # travel to first point at safe height
        self.goto_xyz(x0, y0, z_up_mm, speed_pct=travel_speed, move_mode=0x02, wait_s=settle_s)
        # pen down
        self.goto_xyz(x0, y0, z_down_mm, speed_pct=draw_speed, move_mode=0x02, wait_s=settle_s)

        for (x, y) in points_mm[1:]:
            self.goto_xyz(x, y, z_down_mm, speed_pct=draw_speed, move_mode=0x02,
                          wait_s=inter_point_delay)

        # pen up at end
        self.goto_xyz(points_mm[-1][0], points_mm[-1][1], z_up_mm,
                      speed_pct=travel_speed, move_mode=0x02, wait_s=settle_s)


if __name__ == '__main__':
    # simple smoke test: draw a 30mm square in mock or real mode
    r = Robot()
    r.connect()
    square = [(0.0, 0.0), (30.0, 0.0), (30.0, 30.0), (0.0, 30.0), (0.0, 0.0)]
    r.draw_stroke(square, z_down_mm=5.0, z_up_mm=30.0)
    r.disconnect()
