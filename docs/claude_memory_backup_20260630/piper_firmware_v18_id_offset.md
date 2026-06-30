---
name: piper-firmware-v18-id-offset
description: Piper S-V1.8-2 arm bring-up — master-mode feedback offset and the Config Init requirement for JointCtrl; both now solved
metadata: 
  node_type: memory
  type: project
  originSessionId: b3398478-6d70-472e-beb3-4918bb7498de
---

Bringing up the Piper arm (firmware S-V1.8-2, piper_sdk 0.6.1) hit two distinct issues, both now SOLVED:

1. **All-zero state readback** — the arm was in master-arm mode (`MasterSlaveConfig` linkage_config=0xFA), which shifts feedback CAN IDs 0x2A1–0x2A7 → 0x3A1–0x3A7. Fix: `MasterSlaveConfig(0xFC,0,0,0)` + power-cycle. Feedback returns to 0x2A* and piper_sdk reads it natively.

2. **JointCtrl silently ignored** — even with feedback fixed and a healthy bus, motion commands did nothing. The missing piece was the AgileX GUI's **"Config Init"** button: `ArmParamEnquiryAndConfig(0x01,0x02,0,0,0x02)` (CAN 0x477, param_setting=0x02 = reset joint limits/speed/accel to defaults), followed by `SearchAllMotorMaxAngleSpd()`. Without it the arm accepts config commands but executes no motion.

**Why it matters:** Config Init must run every session after `ConnectPort` — it is not persistent. This was traced from `Piper_sdk_ui/piper_ui.py` `run_config_init()` and verified GUI-free on hardware (j1 moved +4.93°).

**How to apply:** `modules/robot.py` `Robot.connect()` now runs Config Init automatically (`config_init=True` default), uses `C_PiperInterface_V2`, and `EnablePiper()`. So `Robot().connect()` fully initializes the arm with no GUI. If the arm is ever in master mode again (feedback on 0x3A*), do the MasterSlaveConfig recovery first. See [[piper-feedback-listener-workaround]], [[piper-can-tx-physical-fault]], and docs/20260522_1700_piper_jointctrl_solved.md.

**★WEB 裏取り (2026-06-30) — 0x3A* シフトは master mode の副作用、回避可能**: AgileX 公式 issue で同一現象が確認できた。**feedback all-zero/no-update の公式原因は「master mode にいるから」**で、公式の修正は **slave mode に戻すこと**（piper_sdk #76 メンテナ RosenYin: `piper_set_slave.py` で slave へ、切替で失電するのでゼロ点で実施 / piper_sdk #33 メンテナ HoranTian: master↔slave トグル + gripper assist level 255→1。firmware S-V1.5-8 でも発生＝バージョン跨ぎの既知挙動）。つまり 0x3A* シフトと workaround listener は **master mode を使っているから必要なだけ**。**drag-teach を master mode でやめ slave+teach ボタンにすれば、示教中も SDK が 0x2A* をネイティブに読め、listener / candump 常時化 / ros2_control は不要になる可能性が高い**（要実機検証: `drag_teach_calibrate.py` の示教経路が `MasterSlaveConfig(0xFA)` を使っているなら置換）。重力補償との両立は [[piper_end_load_gravity_comp]] 参照。Sources: github.com/agilexrobotics/piper_sdk/issues/76, /issues/33, /issues/109(CAN bus-off=N3)。
