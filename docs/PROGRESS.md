# draw_piper v0.4 — Progress & Next Steps

> Session: 2026-05-21 18:00+
> Context: VS Code workspace moved from `~/piper_ws` to `~/draw_piper/`

## Completed (Step A)

### ✅ robot.py (modules/robot.py)

Implemented `Robot` class with:
- **piper_sdk integration** (with mock fallback when SDK unavailable)
- **Unit conversion**: mm ↔ 0.001mm, deg ↔ 0.001deg
- **Core API**:
  - `connect()` / `disconnect()` with auto-enable motors
  - `goto_xyz(x_mm, y_mm, z_mm, ...)` for end-effector movement
  - `pen_up()` / `pen_down()` using Z-axis offsets
  - `draw_stroke(points_mm, z_down_mm, z_up_mm, ...)` for drawing multi-point paths
- **Move modes**: MOVE L (0x02) for drawing, MOVE J (0x01) for fast travel
- **Smoke test**: Running `python3 modules/robot.py` draws a 30mm square (mock mode if no piper_sdk)

**Status**: Ready to integrate into draw pipeline.

---

## Next Steps (Prioritized)

### Step E (High Priority) — Pipeline Integration Test

Create `run_draw_test.py`:
- Input: 2-3 simple strokes (square, circle, line) as hardcoded point lists
- Flow: `robot.connect()` → `draw_stroke()` for each → visualize in mock log
- Output: Verify that points flow through without errors (both mock and real hardware paths)

**Why first**: Validates the entire drawing loop before adding complex modules (VLM, image gen).

---

### Step C (Parallel) — Image Generation VRAM Measurement

Create comprehensive `modules/image_gen.py`:
1. **Qwen2.5-VL-7B INT4** initialization + memory profiling
2. **SDXL Turbo** + Lineart ControlNet initialization + profiling
3. **Test scenarios**:
   - VLM alone
   - Image generation alone
   - Both simultaneously (the critical scenario)
4. **Output**: GPU memory usage CSV/JSON and recommendation (e.g. "CPU offload ControlNet" if > 16GB)

**Why parallel**: VRAM is a hard blocker; results determine which models can run together.

---

### Step D (Parallel) — Vectorizer Skeleton

Create minimal `modules/vectorizer.py`:
- Input: lineart image (PNG/JPG)
- Output: list of strokes (each stroke = list of (x_mm, y_mm))
- Method: OpenCV skeletonize → findContours → approxPolyDP
- **No need to integrate yet**; just validate the algorithm on test images.

---

### Step B (Deferred) — ArUco Calibration

Requires USBカメラ hardware. Once available:
- `calibration/aruco_calibrate.py`: camera → board → robot base_link transforms
- Output: `calibration/camera_to_robot.yaml` (used by all modules)

---

## Environment Setup

```bash
# Activate venv
source ~/draw_piper/venv/bin/activate

# Install base dependencies
pip install -r requirements.txt

# (For Step C only) Install ML models after venv is active:
# pip install transformers diffusers accelerate torch torchvision torchaudio
```

---

## Current Directory Structure

```
~/draw_piper/
├── venv/                    # Python virtual environment
├── modules/
│   ├── robot.py             # ✅ IMPLEMENTED (Step A)
│   ├── image_gen.py         # TODO (Step C)
│   ├── vectorizer.py        # TODO (Step D)
│   ├── camera.py            # Skeleton (median capture)
│   ├── vlm.py               # Skeleton
│   ├── trigger.py           # Skeleton
│   ├── prompt_builder.py    # Skeleton
│   ├── start_point.py       # Skeleton
│   ├── trajectory.py        # Skeleton
│   └── __init__.py
├── calibration/
│   ├── aruco_calibrate.py   # Skeleton
│   └── camera_to_robot.yaml # Placeholder
├── logs/                    # Cycle logs (auto-generated at runtime)
├── orchestrator.py          # Main loop (placeholder)
├── requirements.txt         # Minimal deps (+ comments for optional)
├── README.md
└── PROGRESS.md              # ← THIS FILE

```

---

## Important Notes

### piper_sdk Import

- `robot.py` gracefully handles missing `piper_sdk` → mock mode
- When real hardware is used, ensure:
  ```bash
  sudo ip link set can0 type can bitrate 1000000
  sudo ip link set can0 up
  ```
  before running

### Ready Pose

- Default: j1=-45°, j2=60°, j3=-60°, j4=0°, j5=30°, j6=0°
- Avoids both shoulder and wrist singularities
- Used for safe initialization (not yet auto-applied)

### Unit Conversions

| Item | Input | Internal | Output |
|---|---|---|---|
| Position | mm | 0.001mm | piper_sdk |
| Rotation | deg | 0.001deg | piper_sdk |

All `robot.py` public methods work in **mm** and **deg** for convenience.

---

## Handoff Notes

When resuming in new VS Code session:
1. Check `PROGRESS.md` (this file) for what's done
2. Review "Next Steps" prioritization
3. Confirm `venv` is active: `which python3` should show `~/draw_piper/venv/bin/python3`
4. Reference `docs/20260521_1757_drawing_system_v04_design.md` for full system design
5. Reference `docs/HANDOFF_TO_CLAUDE_CODE(1).md` for hardware/environment details
