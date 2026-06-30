---
name: piper-can-tx-physical-fault
description: "Piper CAN bus can silently lose TX (frames sent but never ACKed) — check error-pass counter, fix by replugging the USB-CAN adapter"
metadata: 
  node_type: memory
  type: project
  originSessionId: b3398478-6d70-472e-beb3-4918bb7498de
---

On 2026-05-22 the Piper CAN link developed a TX-only fault: RX was perfect (millions of frames) but every transmitted frame went un-ACKed, driving `can0` into ERROR-PASSIVE — `error-pass` counter jumped to tens of thousands within seconds and `SendCanMessage` returned `SEND_MESSAGE_FAILED (100017)`. Likely triggered by connector/termination degradation during repeated USB/CAN replugging.

**Why this matters:** `candump` showing your own TX frames does NOT prove delivery — socketcan echoes local TX regardless of bus ACK. The only reliable TX health check is `ip -details -statistics link show can0` → the `error-pass` / `bus-off` counters. A healthy 2-node bus keeps these at 0.

**How to apply:** If motion/config commands silently fail, run `ip -details -statistics link show can0 | grep -A1 re-started`. If `error-pass` is climbing or state is ERROR-PASSIVE: physically replug the USB-CAN adapter, reseat CAN connectors, check 120Ω termination, then `sudo ip link set can0 up type can bitrate 1000000`. Re-verify with a few-second continuous send — `error-pass` must stay 0. Note this fault is separate from the motion-execution block in [[piper-firmware-v18-id-offset]]; fixing the bus did not make JointCtrl work.
