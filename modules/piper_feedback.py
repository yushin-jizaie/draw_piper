"""Workaround listener for Piper firmware S-V1.8-x.

Firmware S-V1.8 broadcasts joint / end-pose feedback at CAN IDs 0x3A1-0x3A7
instead of the SDK-expected 0x2A1-0x2A7. piper_sdk 0.6.1 doesn't parse the
new range, so `GetArmJointMsgs()` / `GetArmEndPoseMsgs()` always return 0.

This module opens a second socketcan reader on the same bus and decodes the
0x3A* frames using the documented 0x2A* layout (offset +0x100, payload
identical). Empirically verified against physical pose on 2026-05-21.

References:
- piper_sdk: /piper_msgs/msg_v2/can_id.py — 0x2A* definitions
- AgileX issue tracker: V1.8 feedback ID shift is not documented in
  CHANGELOG.MD as of piper_sdk 0.6.1
"""

from __future__ import annotations

import struct
import threading
import time
from typing import Optional, Tuple

try:
    import can
except Exception:
    can = None


JointTuple = Tuple[float, float, float, float, float, float]
EndPoseTuple = Tuple[float, float, float, float, float, float]


class PiperFeedback:
    """Background listener for 0x3A1-0x3A7 feedback frames.

    Usage:
        fb = PiperFeedback('can0')
        fb.start()
        fb.wait_until_ready(timeout=2.0)   # optional
        ep = fb.get_end_pose()    # (X_mm, Y_mm, Z_mm, RX_deg, RY_deg, RZ_deg)
        js = fb.get_joints()      # (j1, ..., j6) in degrees
        fb.stop()
    """

    def __init__(self, can_port: str = 'can0'):
        if can is None:
            raise RuntimeError("python-can not installed")
        self.can_port = can_port
        self._bus = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        # staging: each frame contributes a slice of the consolidated state
        self._x_mm: Optional[float] = None
        self._y_mm: Optional[float] = None
        self._z_mm: Optional[float] = None
        self._rx_deg: Optional[float] = None
        self._ry_deg: Optional[float] = None
        self._rz_deg: Optional[float] = None
        self._joints: list[Optional[float]] = [None] * 6
        # frame counters (for diagnostics)
        self.frames_seen = 0
        self.last_update_ts: float = 0.0

    def start(self):
        self._bus = can.interface.Bus(channel=self.can_port, interface='socketcan')
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name='PiperFeedback')
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._bus:
            try:
                self._bus.shutdown()
            except Exception:
                pass
            self._bus = None

    def wait_until_ready(self, timeout: float = 2.0) -> bool:
        """Block until both end-pose and joints have at least one valid sample."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.get_end_pose() is not None and self.get_joints() is not None:
                return True
            time.sleep(0.02)
        return False

    def get_end_pose(self) -> Optional[EndPoseTuple]:
        with self._lock:
            parts = (self._x_mm, self._y_mm, self._z_mm,
                     self._rx_deg, self._ry_deg, self._rz_deg)
            if any(p is None for p in parts):
                return None
            return parts  # type: ignore[return-value]

    def get_joints(self) -> Optional[JointTuple]:
        with self._lock:
            if any(j is None for j in self._joints):
                return None
            return tuple(self._joints)  # type: ignore[return-value]

    def _loop(self):
        while not self._stop.is_set():
            try:
                msg = self._bus.recv(timeout=0.2)
            except Exception:
                time.sleep(0.05)
                continue
            if msg is None:
                continue
            self._handle(msg)

    def _handle(self, msg):
        arb = msg.arbitration_id
        if arb < 0x3A2 or arb > 0x3A7:
            return
        data = msg.data
        if len(data) < 8:
            return
        a = struct.unpack('>i', data[0:4])[0] / 1000.0
        b = struct.unpack('>i', data[4:8])[0] / 1000.0
        with self._lock:
            self.frames_seen += 1
            self.last_update_ts = time.time()
            if arb == 0x3A2:        # END_POSE_1: X, Y (mm)
                self._x_mm, self._y_mm = a, b
            elif arb == 0x3A3:      # END_POSE_2: Z (mm), RX (deg)
                self._z_mm, self._rx_deg = a, b
            elif arb == 0x3A4:      # END_POSE_3: RY, RZ (deg)
                self._ry_deg, self._rz_deg = a, b
            elif arb == 0x3A5:      # JOINT_12 (deg)
                self._joints[0], self._joints[1] = a, b
            elif arb == 0x3A6:      # JOINT_34 (deg)
                self._joints[2], self._joints[3] = a, b
            elif arb == 0x3A7:      # JOINT_56 (deg)
                self._joints[4], self._joints[5] = a, b


if __name__ == '__main__':
    fb = PiperFeedback()
    fb.start()
    ok = fb.wait_until_ready(timeout=2.0)
    print(f"ready: {ok}")
    for _ in range(5):
        print('end:', fb.get_end_pose())
        print('joints:', fb.get_joints())
        time.sleep(0.5)
    fb.stop()
