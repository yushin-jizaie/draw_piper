"""Smoke test for Robot.draw_strokes_panel_smooth() with a mock Robot.

Verifies the API works end-to-end:
  - PanelFrame loaded from calibration/panel_frame.yaml (or placeholder)
  - strokes -> reorder -> smooth -> arcs -> mock goto_panel / goto_arc_panel
  - returned diagnostics dict has expected keys / values

Designed to run quickly in mock mode (~ a few seconds at most). For real-arm
testing, use test_draw_arc_panel on the actual hardware.

Usage:
  python3 -m scripts.test_frida_smoothness
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from modules.robot import Robot


def make_test_strokes():
    """Small set of strokes for the smoke test (face_sketch lite)."""
    import math
    # tiny oval (8 segments) on panel uv plane
    oval = []
    for i in range(8):
        t = 2 * math.pi * i / 8
        oval.append((30.0 + 15.0 * math.cos(t),
                     30.0 + 15.0 * math.sin(t)))
    oval.append(oval[0])

    # mouth
    mouth = [(25.0, 40.0), (28.0, 42.0), (32.0, 42.0), (35.0, 40.0)]
    # eye_l (near mouth) — short stroke
    eye_l = [(22.0, 28.0), (26.0, 28.0)]
    # eye_r (far from eye_l) — short stroke
    eye_r = [(34.0, 28.0), (38.0, 28.0)]
    # body far away — tests TSP ordering
    body = [(15.0, 60.0), (45.0, 60.0)]
    return [oval, mouth, body, eye_l, eye_r]   # intentionally scrambled order


def main() -> int:
    print("=== Frida smoothness smoke test (mock Robot) ===")
    t0 = time.time()
    # mock=True で SDK 不要、 panel_frame は デフォルト calibration/panel_frame.yaml
    r = Robot(mock=True)
    if r.panel is None:
        print("[test] panel frame not loaded; cannot run panel test")
        print("[test] (this is fine if calibration/panel_frame.yaml is missing)")
        return 1

    r.connect()
    print(f"[test] connected (mock), panel = "
          f"{'calibrated' if r.panel.calibrated else 'placeholder'}")

    # speed up the mock: monkey-patch wait_for_pose to avoid the 50ms sleep
    r.wait_for_pose = lambda *a, **kw: True   # type: ignore

    strokes = make_test_strokes()
    print(f"[test] {len(strokes)} input strokes "
          f"(raw order: oval, mouth, body, eye_l, eye_r)")

    t_start = time.time()
    diag = r.draw_strokes_panel_smooth(
        strokes,
        # use defaults; override z to small values so mock prints reasonable
        w_contact=0.0, w_clear_max=30.0, w_clear_near=10.0,
        draw_speed_base=30, draw_speed_min=10, draw_speed_max=50,
        near_threshold_mm=15.0,
        step_mm=2.0,
        reorder=True,
    )
    t_end = time.time()
    print(f"[test] draw completed in {t_end - t_start:.2f}s (mock)")
    print()
    print("=== diagnostics ===")
    for k, v in diag.items():
        if isinstance(v, float):
            print(f"  {k:<22} {v:.2f}")
        elif isinstance(v, list) and len(v) > 6:
            print(f"  {k:<22} {v[:5]}... (len={len(v)})")
        else:
            print(f"  {k:<22} {v}")

    # sanity asserts
    assert diag["n_strokes"] == len(strokes), "n_strokes mismatch"
    assert diag["n_arcs"] > 0, "expected at least some arcs"
    assert diag["travel_after_mm"] <= diag["travel_before_mm"] + 1e-6, \
        "TSP should not increase travel"
    assert len(diag["reorder_indices"]) == len(strokes), \
        "reorder_indices length mismatch"
    assert len(diag["clear_heights_mm"]) == len(strokes), \
        "clear_heights length mismatch"
    assert diag["speed_min_pct"] >= 1
    assert diag["speed_max_pct"] <= 100

    # travel should be saved by reordering (expect oval -> eye_l -> eye_r ...)
    print()
    print(f"travel saved: {diag['travel_saved_mm']:.1f} mm "
          f"({100 * diag['travel_saved_mm'] / max(1e-9, diag['travel_before_mm']):.1f}%)")
    if diag["travel_saved_mm"] > 1.0:
        print("✓ TSP reordering reduced travel")
    else:
        print("(travel essentially unchanged — strokes were close enough)")

    print()
    print(f"=== total wall time: {time.time() - t0:.2f}s ===")
    print("OK — smoke test passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
