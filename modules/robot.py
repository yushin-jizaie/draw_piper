"""piper_sdk wrapper (robot.py)

Concrete implementation for Step A: simple utilities to move the arm in
the XY plane and emulate pen up/down using Z offsets. The implementation
uses `piper_sdk.C_PiperInterface` when available; otherwise it falls back
to a mock mode so the code can be developed without hardware.

Notes about units (per project docs):
- EndPoseCtrl accepts position in 0.001mm (i.e. mm * 1000)
- Rotation in 0.001deg

This module exposes a small API:
- `connect()` / `disconnect()`
- `goto_xyz(x_mm, y_mm, z_mm, speed_pct=50, move_mode=0x02)`
- `pen_up()` / `pen_down()`
- `draw_stroke(points_mm, z_down_mm, z_up_mm)`
"""

import time
import math
try:
    from piper_sdk import C_PiperInterface
except Exception:
    C_PiperInterface = None


class Robot:
    def __init__(self, can_port='can0', default_z_mm=10.0, ready_pose=None):
        self.can_port = can_port
        self.default_z_mm = default_z_mm
        self.ready_pose = ready_pose or {'j1': -45, 'j2': 60, 'j3': -60, 'j4': 0, 'j5': 30, 'j6': 0}
        self._conn = None
        self.connected = False
        self._mock = C_PiperInterface is None

    def connect(self):
        if self._mock:
            print('[robot] piper_sdk not found — running in MOCK mode')
            self.connected = True
            return

        self._conn = C_PiperInterface(self.can_port)
        self._conn.ConnectPort()
        time.sleep(0.2)
        # enable all motors (7 == all)
        try:
            self._conn.EnableArm(7, 0x02)
        except Exception:
            pass
        # switch to CAN control mode (ctrl_mode=0x01), default Move P (0x00)
        try:
            self._conn.ModeCtrl(0x01, 0x00, 50, 0x00)
        except Exception:
            pass
        time.sleep(0.2)
        self.connected = True

    def disconnect(self):
        if self._mock:
            self.connected = False
            return
        if self._conn:
            try:
                self._conn.ClosePort()
            except Exception:
                pass
        self.connected = False

    def _to_piper_pos(self, x_mm, y_mm, z_mm, rx_deg=0.0, ry_deg=0.0, rz_deg=0.0):
        # convert mm -> 0.001mm units, deg -> 0.001deg
        X = int(round(x_mm * 1000.0))
        Y = int(round(y_mm * 1000.0))
        Z = int(round(z_mm * 1000.0))
        RX = int(round(rx_deg * 1000.0))
        RY = int(round(ry_deg * 1000.0))
        RZ = int(round(rz_deg * 1000.0))
        return X, Y, Z, RX, RY, RZ

    def goto_xyz(self, x_mm, y_mm, z_mm, rx_deg=0.0, ry_deg=0.0, rz_deg=0.0, speed_pct=50, move_mode=0x02):
        """Move the end-effector to the given pose (mm, deg). move_mode: 0x01=MOVE J, 0x02=MOVE L."""
        if not self.connected:
            raise RuntimeError('Robot not connected')

        if self._mock:
            print(f"[MOCK] goto_xyz: ({x_mm:.1f},{y_mm:.1f},{z_mm:.1f})mm speed={speed_pct}")
            time.sleep(0.05)
            return True

        X, Y, Z, RX, RY, RZ = self._to_piper_pos(x_mm, y_mm, z_mm, rx_deg, ry_deg, rz_deg)
        try:
            # ensure control mode and move mode are set
            self._conn.ModeCtrl(0x01, move_mode, int(max(1, min(100, speed_pct))), 0x00)
            # EndPoseCtrl expects integers in 0.001mm / 0.001deg
            self._conn.EndPoseCtrl(X, Y, Z, RX, RY, RZ)
        except Exception as e:
            print('[robot] goto_xyz error:', e)
            return False
        # small sleep to allow piper to start motion
        time.sleep(0.01)
        return True

    def pen_up(self, x_mm=None, y_mm=None, z_up_mm=30.0, speed_pct=60):
        """Move to safe Z (pen up). If x_mm/y_mm provided, move there first at pen-up height."""
        if x_mm is None or y_mm is None:
            # stay in place, just lift
            return self.goto_xyz(0.0, 0.0, z_up_mm, speed_pct=speed_pct, move_mode=0x00)
        return self.goto_xyz(x_mm, y_mm, z_up_mm, speed_pct=speed_pct, move_mode=0x02)

    def pen_down(self, x_mm=None, y_mm=None, z_down_mm=5.0, speed_pct=30):
        """Move to drawing Z (pen down)."""
        if x_mm is None or y_mm is None:
            return self.goto_xyz(0.0, 0.0, z_down_mm, speed_pct=speed_pct, move_mode=0x02)
        return self.goto_xyz(x_mm, y_mm, z_down_mm, speed_pct=speed_pct, move_mode=0x02)

    def draw_stroke(self, points_mm, z_down_mm=5.0, z_up_mm=30.0, travel_speed=80, draw_speed=30, inter_point_delay=0.02):
        """Draw a stroke defined by a list of (x_mm, y_mm) points.

        This function will:
        - move to first point at pen-up height
        - pen down (move to z_down)
        - iterate through points with MOVE L at draw_speed
        - pen up at end
        """
        if not points_mm:
            return
        # move to first point at pen-up
        x0, y0 = points_mm[0]
        self.goto_xyz(x0, y0, z_up_mm, speed_pct=travel_speed, move_mode=0x02)
        # pen down
        self.goto_xyz(x0, y0, z_down_mm, speed_pct=draw_speed, move_mode=0x02)

        # draw
        for (x, y) in points_mm[1:]:
            self.goto_xyz(x, y, z_down_mm, speed_pct=draw_speed, move_mode=0x02)
            time.sleep(inter_point_delay)

        # pen up at end
        self.goto_xyz(points_mm[-1][0], points_mm[-1][1], z_up_mm, speed_pct=travel_speed, move_mode=0x02)


if __name__ == '__main__':
    # simple smoke test: draw a 30mm square in mock or real mode
    r = Robot()
    r.connect()
    square = [(0.0, 0.0), (30.0, 0.0), (30.0, 30.0), (0.0, 30.0), (0.0, 0.0)]
    r.draw_stroke(square, z_down_mm=5.0, z_up_mm=30.0)
    r.disconnect()
