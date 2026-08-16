---
name: piper-feedback-listener-workaround
description: "modules/piper_feedback.py is a python-can side reader that decodes V1.8 firmware's 0x3A* frames; Robot wires it in alongside piper_sdk"
metadata: 
  node_type: memory
  type: project
  originSessionId: b3398478-6d70-472e-beb3-4918bb7498de
---

`modules/piper_feedback.py` (added 2026-05-21) is a thin python-can listener that opens a **second** socketcan reader on can0, parses 0x3A2–0x3A7 in a background thread (struct '>i' int32, 0.001 deg/mm units, same as 0x2A* layout), and exposes `get_end_pose()` / `get_joints()` returning None until the first sample arrives.

**Why:** Needed because [[piper-firmware-v18-id-offset]] makes piper_sdk's state readback unusable. The split design (sdk for commands, custom listener for state) keeps the code revertible — when AgileX patches the SDK we can delete the workaround in one diff.

**How to apply:** `Robot.connect()` starts the listener after `ConnectPort` and calls `wait_until_ready(timeout=2.0)`. If it fails silently (no frames in 2s), motion commands still work but state stays None — handle the None case in callers. `Robot.disconnect()` stops the thread before closing the SDK port. Don't try to share piper_sdk's bus — Linux socketcan allows multiple PF_CAN sockets per device for receive, so the two readers coexist safely. If you need to extend parsing for other 0x3A* IDs (e.g., 0x3A0, 0x3A1 status), follow the same pattern: add elif branches in `_handle()` and a lock-guarded getter.

Critical safety note for tests: `Robot.__init__` now takes an explicit `mock` parameter; `run_draw_test.py` passes `mock=(not args.real)`. Before this fix, having piper_sdk installed in the venv caused "mock" CLI runs to silently send real CAN commands.
