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

Panel (drawing-surface) layer -- for drawing on an arbitrarily oriented
flat surface, e.g. a vertical acrylic panel. Panel coordinates (u, v, w)
are converted to robot base coordinates via a `PanelFrame` loaded from
`calibration/panel_frame.yaml` (populated by Step B / ArUco calibration):
- `panel_to_base(u, v, w)`
- `goto_panel(u, v, w)` / `pen_up_panel()` / `pen_down_panel()`
- `draw_stroke_panel(points_uv, w_contact, w_clear)`
"""

import os
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

# Default location of the panel-frame calibration file (project_root/calibration).
DEFAULT_PANEL_FRAME_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'calibration', 'panel_frame.yaml')


def _unit(vec):
    """Return vec normalised to a unit-length 3-tuple."""
    v = [float(c) for c in vec]
    n = math.sqrt(sum(c * c for c in v))
    if n == 0.0:
        raise ValueError('panel frame: zero-length axis vector')
    return tuple(c / n for c in v)


class PanelFrame:
    """Drawing-surface coordinate frame, expressed in the robot base_link.

    Panel coordinates (all mm):
      u -- panel horizontal (+u = the drawing's right)
      v -- panel vertical   (+v = up)
      w -- panel normal     (w = 0 at the surface, +w = away from the panel)

    Conversion to robot base coordinates:
      base = origin + u * u_axis + v * v_axis + w * normal
    """

    def __init__(self, origin_mm, u_axis, v_axis, normal, pen_orientation_deg,
                 size_mm, w_contact_mm=0.0, w_clear_mm=25.0,
                 ready_pose_deg=None, calibrated=False):
        self.origin = tuple(float(c) for c in origin_mm)
        self.u_axis = _unit(u_axis)
        self.v_axis = _unit(v_axis)
        self.normal = _unit(normal)
        self.pen_orientation_deg = tuple(float(c) for c in pen_orientation_deg)
        self.size_mm = tuple(float(c) for c in size_mm)
        self.w_contact_mm = float(w_contact_mm)
        self.w_clear_mm = float(w_clear_mm)
        self.ready_pose_deg = ready_pose_deg
        self.calibrated = bool(calibrated)

    def to_base(self, u_mm, v_mm, w_mm):
        """Convert panel (u, v, w) mm to robot base (x, y, z) mm."""
        return tuple(
            self.origin[i]
            + u_mm * self.u_axis[i]
            + v_mm * self.v_axis[i]
            + w_mm * self.normal[i]
            for i in range(3)
        )

    def from_base(self, x_mm, y_mm, z_mm):
        """Convert robot base (x, y, z) mm to panel (u, v, w) mm.

        Inverse of to_base. Assumes (u_axis, v_axis, normal) form an
        orthonormal basis (i.e. they're mutually perpendicular unit vectors,
        which from_yaml normalizes via _unit). Then the inverse is just the
        dot product with each basis vector.

        Used e.g. to ask "where is the pen tip right now in panel coords?"
        for TSP-style trajectory planning that wants to start from the
        current end-effector position.
        """
        dx = x_mm - self.origin[0]
        dy = y_mm - self.origin[1]
        dz = z_mm - self.origin[2]
        u = dx * self.u_axis[0] + dy * self.u_axis[1] + dz * self.u_axis[2]
        v = dx * self.v_axis[0] + dy * self.v_axis[1] + dz * self.v_axis[2]
        w = dx * self.normal[0] + dy * self.normal[1] + dz * self.normal[2]
        return (u, v, w)

    def in_bounds(self, u_mm, v_mm):
        """True if (u, v) lies within the declared drawing area."""
        return 0.0 <= u_mm <= self.size_mm[0] and 0.0 <= v_mm <= self.size_mm[1]

    @classmethod
    def from_yaml(cls, path):
        """Load a PanelFrame from a YAML file (see calibration/panel_frame.yaml)."""
        import yaml  # lazy import: keep robot.py importable without pyyaml
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        p = data.get('panel', data)
        return cls(
            origin_mm=p['origin_mm'],
            u_axis=p['u_axis'],
            v_axis=p['v_axis'],
            normal=p['normal'],
            pen_orientation_deg=p['pen_orientation_deg'],
            size_mm=p['size_mm'],
            w_contact_mm=p.get('w_contact_mm', 0.0),
            w_clear_mm=p.get('w_clear_mm', 25.0),
            ready_pose_deg=p.get('ready_pose_deg'),
            calibrated=p.get('calibrated', False),
        )


class Robot:
    def __init__(self, can_port='can0', default_z_mm=10.0, ready_pose=None, mock=None,
                 use_feedback_workaround=False, panel_frame=None):
        """If mock is None: auto-detect (mock when piper_sdk is unavailable).
        Pass mock=True to force mock even when piper_sdk is installed
        (the safe default for tests). Pass mock=False to require real hardware.

        use_feedback_workaround: start the 0x3A* side listener (only needed if
        the arm is still in master mode; normally feedback is on 0x2A* and the
        SDK reads it natively).

        panel_frame: drawing-surface frame for the panel API. Pass a PanelFrame,
        a path to a YAML file, or None to auto-load calibration/panel_frame.yaml
        when present. The panel_* methods raise if no frame is configured.
        """
        self.can_port = can_port
        self.default_z_mm = default_z_mm
        self.ready_pose = ready_pose or dict(DEFAULT_READY_POSE_DEG)
        self._conn = None
        self.connected = False
        self.use_feedback_workaround = use_feedback_workaround
        # optional panel (drawing-surface) frame for the panel-space API
        self.panel = self._load_panel_frame(panel_frame)
        self._panel_calib_warned = False
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

    @staticmethod
    def _load_panel_frame(panel_frame):
        """Resolve the panel_frame argument into a PanelFrame, or None."""
        if isinstance(panel_frame, PanelFrame):
            return panel_frame
        path = panel_frame or DEFAULT_PANEL_FRAME_PATH
        if not os.path.exists(path):
            return None
        try:
            pf = PanelFrame.from_yaml(path)
            status = 'calibrated' if pf.calibrated else 'PLACEHOLDER values'
            print(f'[robot] panel frame loaded from {path} ({status})')
            return pf
        except Exception as e:
            print(f'[robot] panel frame load failed ({path}): {e}')
            return None

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

    def wait_for_pose(self, x_mm, y_mm, z_mm, *,
                      tol_mm=2.0, timeout_s=10.0, poll_s=0.05,
                      fallback_s=None):
        """Block until the end-effector is within tol_mm of (x_mm,y_mm,z_mm).

        Uses end-pose feedback polling (works through PiperFeedback 0x3A* or
        the SDK 0x2A* path — whichever returns non-stuck values). When
        feedback is unavailable / stuck at zeros, falls back to a fixed sleep
        of `fallback_s` (default = timeout_s / 4, min 1.5s).

        Returns True on arrival or fallback completion, False only on a hard
        timeout while feedback IS working (so the caller knows something is
        wrong).
        """
        if self._mock:
            time.sleep(0.05)
            return True
        if fallback_s is None:
            fallback_s = max(1.5, timeout_s / 4.0)
        deadline = time.time() + timeout_s
        tol_sq = tol_mm * tol_mm
        feedback_seen = False
        stuck_zero = 0
        no_feedback = 0
        last_pose = None
        while time.time() < deadline:
            ep = self.get_end_pose()
            if ep is None:
                no_feedback += 1
                if no_feedback >= 8:    # ~0.4s of total silence
                    time.sleep(fallback_s)
                    return True
                time.sleep(poll_s)
                continue
            x, y, z = ep[0], ep[1], ep[2]
            # detect SDK 0x2A path returning all-zero on firmware S-V1.8-2
            if abs(x) < 0.5 and abs(y) < 0.5 and abs(z) < 0.5:
                stuck_zero += 1
                if stuck_zero >= 8:
                    time.sleep(fallback_s)
                    return True
                time.sleep(poll_s)
                continue
            feedback_seen = True
            dx = x - x_mm; dy = y - y_mm; dz = z - z_mm
            if dx * dx + dy * dy + dz * dz <= tol_sq:
                return True
            last_pose = ep
            time.sleep(poll_s)
        if not feedback_seen:
            time.sleep(fallback_s)
            return True
        print(f"[robot] wait_for_pose timeout {timeout_s:.1f}s: "
              f"target=({x_mm:.1f},{y_mm:.1f},{z_mm:.1f}) "
              f"last=({last_pose[0]:.1f},{last_pose[1]:.1f},{last_pose[2]:.1f})")
        return False

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
    # arc (MOVE_C) primitive
    # ------------------------------------------------------------------
    def goto_arc(self, start_xyz, mid_xyz, end_xyz,
                 rx_deg=None, ry_deg=None, rz_deg=None,
                 speed_pct=25):
        """Move along a circular arc from start through mid to end.

        Uses piper MOVE_C (move_mode=0x03). The arc is fully defined by
        these 3 points; orientation is held at the cached current value
        unless rx/ry/rz are passed. The arm MUST already be at `start_xyz`
        (or very close) — MOVE_C does NOT travel to the start; it interprets
        the three points as defining a circle on which to interpolate from
        start to end via mid.

        Returns True on success. In mock mode just logs.
        """
        if not self.connected:
            raise RuntimeError('Robot not connected')

        if self._mock:
            print(f"[MOCK] goto_arc: start={tuple(round(c,1) for c in start_xyz)} "
                  f"mid={tuple(round(c,1) for c in mid_xyz)} "
                  f"end={tuple(round(c,1) for c in end_xyz)} speed={speed_pct}")
            time.sleep(0.05)
            return True

        spd = int(max(1, min(100, speed_pct)))
        try:
            # switch to MOVE_C mode (CAN ID 0x151)
            self._conn.ModeCtrl(0x01, 0x03, spd, 0x00)
            time.sleep(0.005)

            # start
            X, Y, Z, RX, RY, RZ = self._to_piper_pos(*start_xyz,
                                                       rx_deg=rx_deg,
                                                       ry_deg=ry_deg,
                                                       rz_deg=rz_deg)
            self._conn.EndPoseCtrl(X, Y, Z, RX, RY, RZ)
            self._conn.MoveCAxisUpdateCtrl(0x01)
            time.sleep(0.005)

            # mid
            X, Y, Z, RX, RY, RZ = self._to_piper_pos(*mid_xyz,
                                                       rx_deg=rx_deg,
                                                       ry_deg=ry_deg,
                                                       rz_deg=rz_deg)
            self._conn.EndPoseCtrl(X, Y, Z, RX, RY, RZ)
            self._conn.MoveCAxisUpdateCtrl(0x02)
            time.sleep(0.005)

            # end (triggers execution)
            X, Y, Z, RX, RY, RZ = self._to_piper_pos(*end_xyz,
                                                       rx_deg=rx_deg,
                                                       ry_deg=ry_deg,
                                                       rz_deg=rz_deg)
            self._conn.EndPoseCtrl(X, Y, Z, RX, RY, RZ)
            self._conn.MoveCAxisUpdateCtrl(0x03)
            time.sleep(0.005)
        except Exception as e:
            print('[robot] goto_arc error:', e)
            return False
        return True

    def goto_arc_panel(self, start_uvw, mid_uvw, end_uvw, speed_pct=25):
        """Circular-arc move on the panel. Coordinates are (u_mm, v_mm, w_mm).

        Pen orientation is held at the panel frame's configured pen pose.
        """
        panel = self._require_panel()
        rx, ry, rz = panel.pen_orientation_deg
        return self.goto_arc(panel.to_base(*start_uvw),
                              panel.to_base(*mid_uvw),
                              panel.to_base(*end_uvw),
                              rx_deg=rx, ry_deg=ry, rz_deg=rz,
                              speed_pct=speed_pct)

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
                    inter_point_delay=0.02, settle_s=2.0,
                    arrival_tol_mm=2.0, arrival_timeout_s=15.0):
        """Draw a stroke defined by a list of (x_mm, y_mm) points.

        Waits for end-pose feedback at each pen-up / pen-down transition so
        the descent command doesn't pre-empt the lateral travel (which would
        cause the pen to touch the surface before reaching the target XY).
        Falls back to a fixed sleep of `settle_s` when feedback is broken.
        inter_point_delay is per-waypoint while drawing.
        """
        if not points_mm:
            return
        x0, y0 = points_mm[0]
        x_last, y_last = points_mm[-1]

        # travel to first point at safe height
        self.goto_xyz(x0, y0, z_up_mm, speed_pct=travel_speed,
                      move_mode=0x02, wait_s=0.0)
        self.wait_for_pose(x0, y0, z_up_mm,
                           tol_mm=arrival_tol_mm,
                           timeout_s=arrival_timeout_s,
                           fallback_s=settle_s)
        # pen down
        self.goto_xyz(x0, y0, z_down_mm, speed_pct=draw_speed,
                      move_mode=0x02, wait_s=0.0)
        self.wait_for_pose(x0, y0, z_down_mm,
                           tol_mm=arrival_tol_mm,
                           timeout_s=arrival_timeout_s,
                           fallback_s=settle_s)

        for (x, y) in points_mm[1:]:
            self.goto_xyz(x, y, z_down_mm, speed_pct=draw_speed,
                          move_mode=0x02, wait_s=inter_point_delay)

        # pen up at end
        self.goto_xyz(x_last, y_last, z_up_mm, speed_pct=travel_speed,
                      move_mode=0x02, wait_s=0.0)
        self.wait_for_pose(x_last, y_last, z_up_mm,
                           tol_mm=arrival_tol_mm,
                           timeout_s=arrival_timeout_s,
                           fallback_s=settle_s)

    # ------------------------------------------------------------------
    # panel (drawing-surface) coordinate layer
    # ------------------------------------------------------------------
    def _require_panel(self):
        if self.panel is None:
            raise RuntimeError(
                'panel frame not configured — create calibration/panel_frame.yaml '
                'or pass panel_frame= to Robot()')
        return self.panel

    def panel_to_base(self, u_mm, v_mm, w_mm):
        """Convert panel coordinates (u, v, w) mm to robot base (x, y, z) mm."""
        return self._require_panel().to_base(u_mm, v_mm, w_mm)

    def goto_panel(self, u_mm, v_mm, w_mm,
                   speed_pct=50, move_mode=0x02, wait_s=0.0):
        """Move the pen to panel coordinates (u, v, w).

        The end-effector is held at the panel's pen orientation (pen ⊥ panel).
        w = 0 is the panel surface; +w retracts the pen. move_mode 0x02 = MOVE L.
        """
        panel = self._require_panel()
        if not panel.calibrated and not self._panel_calib_warned:
            print('[robot] WARNING: panel frame uses PLACEHOLDER geometry '
                  '(calibrated: false) — run Step B before real drawing')
            self._panel_calib_warned = True
        if not panel.in_bounds(u_mm, v_mm):
            print(f'[robot] WARNING: panel point ({u_mm:.1f},{v_mm:.1f}) is '
                  f'outside the {panel.size_mm[0]:.0f}x{panel.size_mm[1]:.0f} mm '
                  'drawing area')
        x, y, z = panel.to_base(u_mm, v_mm, w_mm)
        rx, ry, rz = panel.pen_orientation_deg
        if self._mock:
            print(f'[MOCK] goto_panel: u={u_mm:.1f} v={v_mm:.1f} w={w_mm:.1f} '
                  f'-> base ({x:.1f},{y:.1f},{z:.1f})')
        return self.goto_xyz(x, y, z, rx_deg=rx, ry_deg=ry, rz_deg=rz,
                             speed_pct=speed_pct, move_mode=move_mode, wait_s=wait_s)

    def pen_down_panel(self, u_mm, v_mm, speed_pct=25, wait_s=0.0):
        """Bring the pen into contact with the panel at (u, v)."""
        panel = self._require_panel()
        return self.goto_panel(u_mm, v_mm, panel.w_contact_mm,
                               speed_pct=speed_pct, move_mode=0x02, wait_s=wait_s)

    def pen_up_panel(self, u_mm, v_mm, speed_pct=50, wait_s=0.0):
        """Retract the pen clear of the panel above (u, v)."""
        panel = self._require_panel()
        return self.goto_panel(u_mm, v_mm, panel.w_clear_mm,
                               speed_pct=speed_pct, move_mode=0x02, wait_s=wait_s)

    def draw_stroke_panel(self, points_uv, w_contact=None, w_clear=None,
                          travel_speed=60, draw_speed=25,
                          inter_point_delay=0.02, settle_s=2.0,
                          arrival_tol_mm=2.0, arrival_timeout_s=15.0):
        """Draw a stroke on the panel from a list of (u, v) points (mm).

        Panel-space counterpart of draw_stroke(): travel above the first point
        with the pen clear of the panel, pen down, trace the path, pen up.
        w_contact / w_clear default to the panel frame's configured values.

        Critical transitions (travel-to-first-point, descent, final pen-up)
        wait for end-pose feedback to confirm arrival before issuing the next
        EndPoseCtrl. Without this, the descent command can pre-empt the
        lateral travel and the pen contacts the canvas *before* reaching the
        target u,v (because base-frame linear interpolation from a partial
        position is diagonal in panel uvw coords).
        """
        panel = self._require_panel()
        if not points_uv:
            return
        wc = panel.w_contact_mm if w_contact is None else w_contact
        wu = panel.w_clear_mm if w_clear is None else w_clear
        u0, v0 = points_uv[0]
        u_last, v_last = points_uv[-1]

        # 1. lateral travel to (u0, v0) at pen-up offset
        self.goto_panel(u0, v0, wu, speed_pct=travel_speed, move_mode=0x02,
                        wait_s=0.0)
        bx, by, bz = panel.to_base(u0, v0, wu)
        self.wait_for_pose(bx, by, bz,
                           tol_mm=arrival_tol_mm,
                           timeout_s=arrival_timeout_s,
                           fallback_s=settle_s)

        # 2. descend straight down to canvas
        self.goto_panel(u0, v0, wc, speed_pct=draw_speed, move_mode=0x02,
                        wait_s=0.0)
        bx, by, bz = panel.to_base(u0, v0, wc)
        self.wait_for_pose(bx, by, bz,
                           tol_mm=arrival_tol_mm,
                           timeout_s=arrival_timeout_s,
                           fallback_s=settle_s)

        # 3. trace path
        for (u, v) in points_uv[1:]:
            self.goto_panel(u, v, wc, speed_pct=draw_speed, move_mode=0x02,
                            wait_s=inter_point_delay)

        # 4. pen up at end
        self.goto_panel(u_last, v_last, wu,
                        speed_pct=travel_speed, move_mode=0x02, wait_s=0.0)
        bx, by, bz = panel.to_base(u_last, v_last, wu)
        self.wait_for_pose(bx, by, bz,
                           tol_mm=arrival_tol_mm,
                           timeout_s=arrival_timeout_s,
                           fallback_s=settle_s)

    def draw_stroke_panel_arcs(self, points_uv,
                                w_contact=None, w_clear=None,
                                travel_speed=60, draw_speed=25,
                                arc_speed=None,
                                settle_s=2.0,
                                smooth=True, step_mm=2.0, smooth_lambda=0.0,
                                arrival_tol_mm=2.0, arrival_timeout_s=15.0):
        """Draw a stroke as a chain of MOVE_C circular arcs.

        Pipeline:
          input polyline → (optional) spline smoothing + uniform resample
                         → grouped into 3-point arc triplets
                         → MOVE_L travel to first point at pen-up offset
                         → MOVE_L descent straight down to canvas
                         → MOVE_C for each arc (waits for arrival between arcs)
                         → MOVE_L pen-up at end

        smooth, step_mm, smooth_lambda are passed to trajectory.smooth_polyline.
        With smooth=False the input points are used as-is (grouped into triplets);
        useful when the caller has already smoothed.

        Falls back to draw_stroke_panel (MOVE_L only) when the input has <3
        points (no arc can be defined).
        """
        from .trajectory import smooth_polyline, polyline_to_arc_triplets

        panel = self._require_panel()
        if not points_uv or len(points_uv) < 2:
            return
        wc = panel.w_contact_mm if w_contact is None else w_contact
        wu = panel.w_clear_mm if w_clear is None else w_clear
        if arc_speed is None:
            arc_speed = draw_speed

        pts = list(points_uv)
        if smooth:
            pts = smooth_polyline(pts, step_mm=step_mm,
                                   smooth_lambda=smooth_lambda)
        if len(pts) < 3:
            # not enough points for an arc — fall back to MOVE_L
            return self.draw_stroke_panel(
                pts, w_contact=wc, w_clear=wu,
                travel_speed=travel_speed, draw_speed=draw_speed,
                inter_point_delay=0.02, settle_s=settle_s,
                arrival_tol_mm=arrival_tol_mm,
                arrival_timeout_s=arrival_timeout_s)
        triplets = polyline_to_arc_triplets(pts)
        if not triplets:
            return

        u0, v0 = pts[0]
        u_last, v_last = pts[-1]

        # 1. lateral travel to first point at pen-up offset (MOVE_L)
        self.goto_panel(u0, v0, wu, speed_pct=travel_speed, move_mode=0x02,
                        wait_s=0.0)
        bx, by, bz = panel.to_base(u0, v0, wu)
        self.wait_for_pose(bx, by, bz,
                           tol_mm=arrival_tol_mm,
                           timeout_s=arrival_timeout_s,
                           fallback_s=settle_s)

        # 2. descend straight down to canvas (MOVE_L)
        self.goto_panel(u0, v0, wc, speed_pct=draw_speed, move_mode=0x02,
                        wait_s=0.0)
        bx, by, bz = panel.to_base(u0, v0, wc)
        self.wait_for_pose(bx, by, bz,
                           tol_mm=arrival_tol_mm,
                           timeout_s=arrival_timeout_s,
                           fallback_s=settle_s)

        # 3. arc chain (MOVE_C)
        for (a, b, c) in triplets:
            self.goto_arc_panel((a[0], a[1], wc),
                                 (b[0], b[1], wc),
                                 (c[0], c[1], wc),
                                 speed_pct=arc_speed)
            bx, by, bz = panel.to_base(c[0], c[1], wc)
            self.wait_for_pose(bx, by, bz,
                               tol_mm=arrival_tol_mm,
                               timeout_s=arrival_timeout_s,
                               fallback_s=settle_s)

        # 4. pen up at end (MOVE_L)
        self.goto_panel(u_last, v_last, wu,
                        speed_pct=travel_speed, move_mode=0x02, wait_s=0.0)
        bx, by, bz = panel.to_base(u_last, v_last, wu)
        self.wait_for_pose(bx, by, bz,
                           tol_mm=arrival_tol_mm,
                           timeout_s=arrival_timeout_s,
                           fallback_s=settle_s)

    # ==================================================================
    # Frida-inspired multi-stroke smooth drawing
    # ==================================================================
    def draw_strokes_panel_smooth(self, strokes_uv, *,
                                   w_contact=None, w_clear_max=None,
                                   w_clear_near=None,
                                   travel_speed=60,
                                   draw_speed_base=30,
                                   draw_speed_min=10,
                                   draw_speed_max=50,
                                   curvature_break=0.1,
                                   curvature_steep=0.5,
                                   curvature_straight=0.02,
                                   near_threshold_mm=15.0,
                                   step_mm=2.0,
                                   smooth_lambda=0.0,
                                   reorder=True,
                                   speed_smooth_window=3,
                                   settle_s=2.0,
                                   arrival_tol_mm=2.0,
                                   arrival_timeout_s=15.0):
        """Multi-stroke smooth drawing (Frida-inspired).

        Apply 3 multi-stroke optimizations on top of draw_stroke_panel_arcs:

        1. **Stroke ordering** (TSP greedy nearest-neighbor):
           Reorder strokes so adjacent strokes are spatially close, reducing
           pen-up travel between them. Each stroke may be reversed if its
           tail is closer than its head.

        2. **Curvature-coupled speed**:
           Compute each arc triplet's curvature and modulate draw speed
           (lower for sharp turns, higher for gentle curves) — reduces jerk.

        3. **Look-ahead descent height**:
           Use a low pen-up height between near-by strokes (faster travel),
           and the full clear height between distant strokes.

        Parameters
        ----------
        strokes_uv : list of polylines [(u_mm, v_mm), ...]
        w_contact, w_clear_max : float or None
            Panel contact / max clear heights. None → panel frame defaults.
        w_clear_near : float or None
            Pen-up height for short inter-stroke travel. Defaults to
            (w_clear_max - w_contact) / 3 above contact.
        travel_speed : int
            speed% for inter-stroke travel.
        draw_speed_base : int
            speed% for gentle arcs (curvature <= curvature_break).
        draw_speed_min : int
            speed% floor for sharp arcs (curvature >= curvature_steep).
        draw_speed_max : int
            (reserved) ceiling for very gentle / straight arcs.
        curvature_break, curvature_steep : float (1/mm)
            Curvature thresholds for the speed map.
        near_threshold_mm : float
            Inter-stroke gap below which we use w_clear_near.
        step_mm, smooth_lambda : passed to trajectory.smooth_polyline.
        reorder : bool
            Apply TSP ordering. Disable for benchmarking / when caller has
            already ordered.
        speed_smooth_window : int
            Moving-average window for the speed profile (reduces jerk from
            abrupt speed switches).
        settle_s, arrival_tol_mm, arrival_timeout_s :
            passed to wait_for_pose.

        Returns
        -------
        dict with planning diagnostics:
            n_strokes, n_arcs, reorder_indices,
            travel_before_mm, travel_after_mm,
            speed_min/max/mean, clear_heights, ...
        """
        from .trajectory import smooth_polyline, polyline_to_arc_triplets
        from .stroke_planner import (
            reorder_strokes_tsp, plan_clear_heights,
            speed_profile_for_stroke, total_travel_distance,
        )

        panel = self._require_panel()
        if not strokes_uv:
            return {"n_strokes": 0, "n_arcs": 0}
        wc = panel.w_contact_mm if w_contact is None else w_contact
        wu_max = panel.w_clear_mm if w_clear_max is None else w_clear_max
        if w_clear_near is None:
            # default: 1/3 of the way up from contact to clear
            w_clear_near = wc + (wu_max - wc) / 3.0

        # current pen position (if known) for TSP start: use the inverse
        # transform panel.from_base() to get the pen tip in panel uv coords.
        # Only used as a hint to start TSP from the nearest stroke endpoint.
        start_uv = None
        try:
            cur_pose = self.get_end_pose()
            if cur_pose and len(cur_pose) >= 3:
                u_now, v_now, _w_now = panel.from_base(
                    cur_pose[0], cur_pose[1], cur_pose[2])
                start_uv = (u_now, v_now)
                if self.verbose if hasattr(self, "verbose") else False:
                    pass
        except Exception:
            start_uv = None

        strokes_in = [list(s) for s in strokes_uv]
        travel_before = total_travel_distance(strokes_in, start_point=start_uv)

        if reorder:
            strokes_out, indices = reorder_strokes_tsp(
                strokes_in, start_point=start_uv)
        else:
            strokes_out = strokes_in
            indices = list(range(len(strokes_in)))

        travel_after = total_travel_distance(strokes_out, start_point=start_uv)

        # look-ahead clear heights
        clear_heights = plan_clear_heights(
            strokes_out,
            w_clear_max_mm=wu_max,
            w_clear_near_mm=w_clear_near,
            near_threshold_mm=near_threshold_mm,
        )

        # ---- draw each stroke ----
        n_arcs_total = 0
        speeds_all = []
        for si, stroke in enumerate(strokes_out):
            if len(stroke) < 2:
                continue
            # 1) smooth + arc grouping
            pts = smooth_polyline(stroke, step_mm=step_mm,
                                   smooth_lambda=smooth_lambda)
            if len(pts) < 3:
                # too short for arc — fall back to MOVE_L
                self.draw_stroke_panel(
                    pts, w_contact=wc, w_clear=clear_heights[si],
                    travel_speed=travel_speed,
                    draw_speed=draw_speed_base,
                    inter_point_delay=0.02, settle_s=settle_s,
                    arrival_tol_mm=arrival_tol_mm,
                    arrival_timeout_s=arrival_timeout_s)
                continue
            triplets = polyline_to_arc_triplets(pts)
            if not triplets:
                continue

            # 2) per-arc speed profile from curvature
            speeds = speed_profile_for_stroke(
                triplets, base_speed_pct=draw_speed_base,
                min_speed_pct=draw_speed_min,
                max_speed_pct=draw_speed_max,
                curvature_break=curvature_break,
                curvature_steep=curvature_steep,
                curvature_straight=curvature_straight,
                smooth_window=speed_smooth_window,
            )
            speeds_all.extend(speeds)

            u0, v0 = pts[0]
            u_last, v_last = pts[-1]

            # travel to start at clear height (use prev stroke's look-ahead)
            wu_in = clear_heights[si - 1] if si > 0 else wu_max
            self.goto_panel(u0, v0, wu_in, speed_pct=travel_speed,
                             move_mode=0x02, wait_s=0.0)
            bx, by, bz = panel.to_base(u0, v0, wu_in)
            self.wait_for_pose(bx, by, bz,
                                tol_mm=arrival_tol_mm,
                                timeout_s=arrival_timeout_s,
                                fallback_s=settle_s)

            # descent
            self.goto_panel(u0, v0, wc, speed_pct=draw_speed_base,
                             move_mode=0x02, wait_s=0.0)
            bx, by, bz = panel.to_base(u0, v0, wc)
            self.wait_for_pose(bx, by, bz,
                                tol_mm=arrival_tol_mm,
                                timeout_s=arrival_timeout_s,
                                fallback_s=settle_s)

            # arc chain with per-arc speed
            for (a, b, c), spd in zip(triplets, speeds):
                self.goto_arc_panel((a[0], a[1], wc),
                                     (b[0], b[1], wc),
                                     (c[0], c[1], wc),
                                     speed_pct=int(spd))
                bx, by, bz = panel.to_base(c[0], c[1], wc)
                self.wait_for_pose(bx, by, bz,
                                    tol_mm=arrival_tol_mm,
                                    timeout_s=arrival_timeout_s,
                                    fallback_s=settle_s)
                n_arcs_total += 1

            # pen-up to look-ahead height for this stroke
            wu_out = clear_heights[si]
            self.goto_panel(u_last, v_last, wu_out,
                             speed_pct=travel_speed, move_mode=0x02,
                             wait_s=0.0)
            bx, by, bz = panel.to_base(u_last, v_last, wu_out)
            self.wait_for_pose(bx, by, bz,
                                tol_mm=arrival_tol_mm,
                                timeout_s=arrival_timeout_s,
                                fallback_s=settle_s)

        # diagnostics
        if speeds_all:
            spd_min = min(speeds_all)
            spd_max = max(speeds_all)
            spd_mean = sum(speeds_all) / len(speeds_all)
        else:
            spd_min = spd_max = spd_mean = draw_speed_base
        return {
            "n_strokes": len(strokes_out),
            "n_arcs": n_arcs_total,
            "reorder_indices": indices,
            "travel_before_mm": travel_before,
            "travel_after_mm": travel_after,
            "travel_saved_mm": travel_before - travel_after,
            "speed_min_pct": spd_min,
            "speed_max_pct": spd_max,
            "speed_mean_pct": spd_mean,
            "clear_heights_mm": clear_heights,
            "w_contact_mm": wc,
            "w_clear_max_mm": wu_max,
            "w_clear_near_mm": w_clear_near,
        }


if __name__ == '__main__':
    # simple smoke test: draw a 30mm square in mock or real mode
    r = Robot()
    r.connect()
    square = [(0.0, 0.0), (30.0, 0.0), (30.0, 30.0), (0.0, 30.0), (0.0, 0.0)]
    r.draw_stroke(square, z_down_mm=5.0, z_up_mm=30.0)
    r.disconnect()
