"""Benchmark Frida-inspired stroke smoothness improvements.

Compares 3 drawing strategies on synthetic stroke sets WITHOUT requiring
the real arm or even a mock Robot — uses pure function simulation via
trajectory + stroke_planner modules.

Strategies:
  naive  : MOVE_L per polyline segment, raw stroke order
  arcs   : MOVE_C arcs after spline smoothing, raw stroke order
  smooth : MOVE_C + TSP reorder + curvature-coupled speed +
           look-ahead clear height (Frida-inspired, new)

Output: side-by-side table of metrics + JSON summary.

Usage:
  python3 -m scripts.benchmark_stroke_smoothness
  python3 -m scripts.benchmark_stroke_smoothness --scene face_sketch
  python3 -m scripts.benchmark_stroke_smoothness --json out.json

Designed for mock / dev use; for real-arm timing measurements use
test_draw_stroke / test_draw_arc_panel on the actual hardware.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

# allow running as a script
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from modules.trajectory import smooth_polyline, polyline_to_arc_triplets
from modules.stroke_planner import (
    reorder_strokes_tsp,
    plan_clear_heights,
    speed_profile_for_stroke,
    compute_arc_curvature,
    compute_arc_length,
    merge_near_strokes,
    total_travel_distance,
    stroke_set_diagnostics,
)


Point = Tuple[float, float]
Stroke = List[Point]


# ============================================================ scenes

def scene_face_sketch() -> List[Stroke]:
    """A face-like sketch: oval + eyes + mouth + body lines."""
    # face oval
    oval = []
    for i in range(40):
        t = 2 * math.pi * i / 40
        oval.append((30.0 + 20.0 * math.cos(t),
                     40.0 + 25.0 * math.sin(t)))
    oval.append(oval[0])
    # left eye (small filled-look polyline)
    eye_l = [(22.0, 35.0), (24.0, 33.0), (26.0, 35.0), (24.0, 37.0), (22.0, 35.0)]
    eye_r = [(34.0, 35.0), (36.0, 33.0), (38.0, 35.0), (36.0, 37.0), (34.0, 35.0)]
    # mouth
    mouth = [(25.0, 50.0), (28.0, 53.0), (32.0, 53.0), (35.0, 50.0)]
    # body lines (far from face)
    neck = [(25.0, 65.0), (35.0, 65.0)]
    shoulder = [(15.0, 75.0), (25.0, 70.0), (35.0, 70.0), (45.0, 75.0)]
    # hair (multiple short strokes)
    hair = [
        [(15.0, 25.0), (18.0, 20.0), (22.0, 15.0)],
        [(28.0, 12.0), (30.0, 8.0)],
        [(35.0, 12.0), (38.0, 18.0), (42.0, 22.0)],
    ]
    return [oval, eye_l, eye_r, mouth, neck, shoulder] + hair


def scene_scattered_dots() -> List[Stroke]:
    """100 short strokes scattered in a grid — heavily tests stroke ordering."""
    import random
    rnd = random.Random(42)
    strokes = []
    # 10x10 grid points, then scrambled
    grid = [(20 + i * 8, 20 + j * 8) for i in range(10) for j in range(10)]
    rnd.shuffle(grid)
    for (x, y) in grid:
        # each is a tiny diagonal stroke
        strokes.append([(x, y), (x + 3, y + 3)])
    return strokes


def scene_zigzag_signature() -> List[Stroke]:
    """Single long zigzag (single stroke) — tests curvature speed profile."""
    pts = []
    for i in range(50):
        x = 10.0 + i * 1.5
        # alternating ±5 with amplitude depending on i
        amp = 2.0 + (i % 7)
        y = 40.0 + amp * (1 if i % 2 == 0 else -1)
        pts.append((x, y))
    return [pts]


SCENES = {
    "face_sketch": scene_face_sketch,
    "scattered_dots": scene_scattered_dots,
    "zigzag_signature": scene_zigzag_signature,
}


# ============================================================ simulation

def simulate_strategy_naive(
    strokes: Sequence[Stroke],
    *,
    w_contact: float = 0.0,
    w_clear: float = 30.0,
    speed_draw: int = 25,
    speed_travel: int = 60,
) -> Dict:
    """MOVE_L per segment, raw stroke order (= draw_stroke_panel sequence)."""
    n_strokes = len(strokes)
    n_segments = 0
    draw_path = 0.0
    travel_path = 0.0
    speeds: List[int] = []
    z_movements: List[float] = []      # per pen-up / pen-down vertical move

    cur = strokes[0][0] if strokes else (0.0, 0.0)
    for s in strokes:
        if len(s) < 2:
            continue
        # travel to first at clear
        travel_path += _dist(cur, s[0])
        # descent (vertical)
        z_movements.append(w_clear - w_contact)
        # draw segments
        for i in range(len(s) - 1):
            draw_path += _dist(s[i], s[i + 1])
            n_segments += 1
            speeds.append(speed_draw)
        # pen-up vertical at end
        z_movements.append(w_clear - w_contact)
        cur = s[-1]

    return {
        "strategy": "naive",
        "n_strokes": n_strokes,
        "n_segments": n_segments,
        "draw_path_mm": draw_path,
        "travel_path_mm": travel_path,
        "z_movement_mm": sum(z_movements),
        "n_z_moves": len(z_movements),
        "speed_min_pct": min(speeds) if speeds else speed_draw,
        "speed_max_pct": max(speeds) if speeds else speed_draw,
        "speed_mean_pct": sum(speeds) / len(speeds) if speeds else speed_draw,
        "n_arcs": 0,
        "jerk": jerk_proxy(speeds),
    }


def simulate_strategy_arcs(
    strokes: Sequence[Stroke],
    *,
    w_contact: float = 0.0,
    w_clear: float = 30.0,
    speed_draw: int = 25,
    step_mm: float = 2.0,
    smooth_lambda: float = 0.0,
) -> Dict:
    """MOVE_C arcs after smoothing, raw stroke order (= draw_stroke_panel_arcs)."""
    n_strokes = len(strokes)
    n_arcs = 0
    draw_path = 0.0
    travel_path = 0.0
    speeds: List[int] = []
    z_movements: List[float] = []

    cur = strokes[0][0] if strokes else (0.0, 0.0)
    for s in strokes:
        if len(s) < 2:
            continue
        pts = smooth_polyline(s, step_mm=step_mm, smooth_lambda=smooth_lambda)
        # travel to first
        travel_path += _dist(cur, pts[0])
        z_movements.append(w_clear - w_contact)
        # draw via arcs
        if len(pts) < 3:
            for i in range(len(pts) - 1):
                draw_path += _dist(pts[i], pts[i + 1])
                speeds.append(speed_draw)
        else:
            triplets = polyline_to_arc_triplets(pts)
            for tri in triplets:
                draw_path += compute_arc_length(tri)   # true arc length
                speeds.append(speed_draw)
                n_arcs += 1
        # pen-up
        z_movements.append(w_clear - w_contact)
        cur = pts[-1]

    return {
        "strategy": "arcs",
        "n_strokes": n_strokes,
        "n_segments": n_arcs * 2,    # 2 segments per triplet
        "n_arcs": n_arcs,
        "draw_path_mm": draw_path,
        "travel_path_mm": travel_path,
        "z_movement_mm": sum(z_movements),
        "n_z_moves": len(z_movements),
        "speed_min_pct": min(speeds) if speeds else speed_draw,
        "speed_max_pct": max(speeds) if speeds else speed_draw,
        "speed_mean_pct": sum(speeds) / len(speeds) if speeds else speed_draw,
        "jerk": jerk_proxy(speeds),
    }


def simulate_strategy_smooth(
    strokes: Sequence[Stroke],
    *,
    w_contact: float = 0.0,
    w_clear_max: float = 30.0,
    w_clear_near: float = 10.0,
    near_threshold_mm: float = 15.0,
    speed_base: int = 30,
    speed_min: int = 10,
    speed_max: int = 50,
    curvature_break: float = 0.1,
    curvature_steep: float = 0.5,
    curvature_straight: float = 0.02,
    step_mm: float = 2.0,
    smooth_lambda: float = 0.0,
    reorder: bool = True,
    two_opt: bool = True,
    speed_smooth_window: int = 3,
    max_jerk_per_step=None,
    merge_threshold_mm: float = 0.0,
    merge_pen_lift_mm: float = 0.0,
) -> Dict:
    """Frida-inspired: arcs + TSP reorder + curvature speed + look-ahead descent."""
    if not strokes:
        return {"strategy": "smooth", "n_strokes": 0, "n_arcs": 0}

    # 1. reorder
    if reorder:
        strokes_o, indices = reorder_strokes_tsp(
            list(strokes), start_point=None, two_opt=two_opt)
    else:
        strokes_o = list(strokes)
        indices = list(range(len(strokes)))

    # (merge は plan_clear_heights が clear height 経由で表現するので
    # ここで merge_near_strokes を呼ばない。 strokes_o は そのまま使う。
    # lift=0 で接続線描画、 lift>0 で接続線軽量化が clear_heights で制御される。)

    # 2. clear heights (3-region: merge / near / far)
    clear_heights = plan_clear_heights(
        strokes_o,
        w_clear_max_mm=w_clear_max,
        w_clear_near_mm=w_clear_near,
        near_threshold_mm=near_threshold_mm,
        w_contact_mm=w_contact,
        merge_threshold_mm=merge_threshold_mm,
        merge_pen_lift_mm=merge_pen_lift_mm,
    )

    n_arcs = 0
    draw_path = 0.0
    travel_path = 0.0
    speeds: List[int] = []
    z_movements: List[float] = []
    triplets_per_stroke: List[List[tuple]] = []   # for curvature histogram

    cur = strokes_o[0][0] if strokes_o else (0.0, 0.0)
    for si, s in enumerate(strokes_o):
        if len(s) < 2:
            continue
        pts = smooth_polyline(s, step_mm=step_mm, smooth_lambda=smooth_lambda)
        # travel to first at incoming clear height
        wu_in = clear_heights[si - 1] if si > 0 else w_clear_max
        travel_path += _dist(cur, pts[0])
        z_movements.append(wu_in - w_contact)
        # draw
        if len(pts) < 3:
            for i in range(len(pts) - 1):
                draw_path += _dist(pts[i], pts[i + 1])
                speeds.append(speed_base)
        else:
            triplets = polyline_to_arc_triplets(pts)
            triplets_per_stroke.append(triplets)
            per_arc_speeds = speed_profile_for_stroke(
                triplets, base_speed_pct=speed_base,
                min_speed_pct=speed_min, max_speed_pct=speed_max,
                curvature_break=curvature_break,
                curvature_steep=curvature_steep,
                curvature_straight=curvature_straight,
                smooth_window=speed_smooth_window,
                max_jerk_per_step=max_jerk_per_step,
            )
            for tri, spd in zip(triplets, per_arc_speeds):
                draw_path += compute_arc_length(tri)   # true arc length
                speeds.append(spd)
                n_arcs += 1
        # pen-up to look-ahead height
        wu_out = clear_heights[si]
        z_movements.append(wu_out - w_contact)
        cur = pts[-1]

    jerk = jerk_proxy(speeds)
    hist = curvature_histogram(triplets_per_stroke)

    return {
        "strategy": "smooth",
        "n_strokes": len(strokes_o),
        "n_segments": n_arcs * 2,
        "n_arcs": n_arcs,
        "draw_path_mm": draw_path,
        "travel_path_mm": travel_path,
        "z_movement_mm": sum(z_movements),
        "n_z_moves": len(z_movements),
        "speed_min_pct": min(speeds) if speeds else speed_base,
        "speed_max_pct": max(speeds) if speeds else speed_base,
        "speed_mean_pct": sum(speeds) / len(speeds) if speeds else speed_base,
        "reorder_indices": indices,
        "clear_heights_mm": clear_heights,
        "jerk": jerk,
        "curvature_histogram": hist,
    }


def _dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


# ============================================================ timing estimate

def estimate_time_seconds(metrics: Dict, *,
                          travel_speed_mm_s: float = 200.0,
                          draw_speed_ref_mm_s: float = 80.0,
                          z_speed_mm_s: float = 100.0,
                          ref_speed_pct: int = 30,
                          ) -> float:
    """Rough estimate of total drawing time.

    Maps speed% linearly to mm/s assuming `ref_speed_pct` corresponds to
    `draw_speed_ref_mm_s`. Travel and Z moves use their own constants.

    NOTE: pure approximation for cross-strategy comparison, not a real timing
    estimate (real arm has accel/decel, MOVE_C overhead, etc.).
    """
    travel = metrics.get("travel_path_mm", 0.0)
    draw = metrics.get("draw_path_mm", 0.0)
    z = metrics.get("z_movement_mm", 0.0)
    spd_mean = metrics.get("speed_mean_pct", 30.0)
    # effective draw speed mm/s scales linearly with speed%
    eff_draw_mm_s = draw_speed_ref_mm_s * (spd_mean / ref_speed_pct)
    eff_draw_mm_s = max(5.0, eff_draw_mm_s)
    t_draw = draw / eff_draw_mm_s
    t_travel = travel / travel_speed_mm_s
    t_z = z / z_speed_mm_s
    return t_draw + t_travel + t_z


# ============================================================ jerk estimate

def jerk_proxy(speed_profile: List[int]) -> dict:
    """Speed-change proxy for jerk: stats of |Δspeed| over the profile.

    Lower jerk_sum = smoother (fewer abrupt speed changes).

    Returns
    -------
    dict with keys:
        jerk_sum     : Σ |speed[i+1] - speed[i]|
        jerk_mean    : average per transition
        jerk_max     : worst single transition
        n_transitions: len(speed_profile) - 1
    """
    if len(speed_profile) < 2:
        return {"jerk_sum": 0.0, "jerk_mean": 0.0, "jerk_max": 0.0,
                "n_transitions": 0}
    deltas = [abs(speed_profile[i + 1] - speed_profile[i])
              for i in range(len(speed_profile) - 1)]
    return {
        "jerk_sum": float(sum(deltas)),
        "jerk_mean": float(sum(deltas)) / len(deltas),
        "jerk_max": float(max(deltas)),
        "n_transitions": len(deltas),
    }


def curvature_histogram(triplets_list: List[List[tuple]],
                         bins: List[float] = None) -> dict:
    """Histogram of arc curvatures across all triplets.

    Bins default: [0, 0.02, 0.1, 0.5, ∞] which match the speed-mapping
    regions (straight / gentle / mid / sharp) so the output tells you how
    many arcs fall in each speed-control bucket.

    Parameters
    ----------
    triplets_list : list of [(triplet, triplet, ...)] per stroke
    bins : list of upper-bound edges (mm^-1). Default 4-bucket.

    Returns
    -------
    dict: bin_edges, counts (per bin), labels (descriptive)
    """
    if bins is None:
        bins = [0.02, 0.1, 0.5, float("inf")]
    labels = ["straight (R≥50mm)", "gentle (R 10-50mm)",
              "mid (R 2-10mm)", "sharp (R<2mm)"]
    counts = [0] * len(bins)
    for triplets in triplets_list:
        for tri in triplets:
            k = compute_arc_curvature(tri)
            for bi, ub in enumerate(bins):
                if k <= ub:
                    counts[bi] += 1
                    break
    total = sum(counts)
    pct = [100.0 * c / max(1, total) for c in counts]
    return {
        "bin_upper_edges": bins,
        "labels": labels,
        "counts": counts,
        "percentages": pct,
        "total": total,
    }


# ============================================================ main

def run_benchmark(scene_name: str, verbose: bool = True) -> Dict:
    if scene_name not in SCENES:
        raise ValueError(f"unknown scene: {scene_name}; "
                         f"available: {list(SCENES.keys())}")
    strokes = SCENES[scene_name]()
    if verbose:
        diag = stroke_set_diagnostics(strokes, start_point=(0.0, 0.0))
        print(f"=== scene: {scene_name} ===")
        print(f"  n_strokes        : {diag['n_strokes']}")
        print(f"  n_points         : {diag['n_points']}")
        print(f"  draw_length_mm   : {diag['draw_length_mm']:.1f}")
        print(f"  travel_length_mm : {diag['travel_length_mm']:.1f} (raw order)")
        print()

    m_naive = simulate_strategy_naive(strokes)
    m_arcs = simulate_strategy_arcs(strokes)
    m_smooth = simulate_strategy_smooth(strokes)

    for m in (m_naive, m_arcs, m_smooth):
        m["est_time_s"] = estimate_time_seconds(m)

    if verbose:
        print(f"{'metric':<22} {'naive':>12} {'arcs':>12} {'smooth':>12}")
        print("-" * 60)
        rows = [
            ("n_arcs",            "n_arcs",            "{:.0f}"),
            ("draw_path_mm",      "draw_path_mm",      "{:.1f}"),
            ("travel_path_mm",    "travel_path_mm",    "{:.1f}"),
            ("z_movement_mm",     "z_movement_mm",     "{:.1f}"),
            ("speed_mean_pct",    "speed_mean_pct",    "{:.1f}"),
            ("speed_min_pct",     "speed_min_pct",     "{:.0f}"),
            ("speed_max_pct",     "speed_max_pct",     "{:.0f}"),
            ("est_time_s",        "est_time_s",        "{:.1f}"),
        ]
        for label, key, fmt in rows:
            v_naive = m_naive.get(key, 0)
            v_arcs = m_arcs.get(key, 0)
            v_smooth = m_smooth.get(key, 0)
            print(f"{label:<22} "
                  f"{fmt.format(v_naive):>12} "
                  f"{fmt.format(v_arcs):>12} "
                  f"{fmt.format(v_smooth):>12}")
        print()
        # improvements
        if m_naive["travel_path_mm"] > 0:
            saved_pct = (1.0 - m_smooth["travel_path_mm"]
                         / m_naive["travel_path_mm"]) * 100
            print(f"travel saved (smooth vs naive): {saved_pct:.1f}%")
        if m_naive["est_time_s"] > 0:
            speedup = m_naive["est_time_s"] / max(0.01, m_smooth["est_time_s"])
            print(f"estimated speedup (smooth vs naive): {speedup:.2f}x")
        # jerk
        j_n = m_naive.get("jerk", {})
        j_a = m_arcs.get("jerk", {})
        j_s = m_smooth.get("jerk", {})
        print()
        print(f"{'jerk metric':<22} {'naive':>12} {'arcs':>12} {'smooth':>12}")
        print("-" * 60)
        for key, fmt in [("jerk_sum", "{:.0f}"),
                          ("jerk_mean", "{:.2f}"),
                          ("jerk_max", "{:.0f}"),
                          ("n_transitions", "{:.0f}")]:
            print(f"{key:<22} "
                  f"{fmt.format(j_n.get(key, 0)):>12} "
                  f"{fmt.format(j_a.get(key, 0)):>12} "
                  f"{fmt.format(j_s.get(key, 0)):>12}")
        # curvature histogram for smooth (most informative)
        hist = m_smooth.get("curvature_histogram", {})
        if hist and hist.get("total"):
            print()
            print("curvature histogram (smooth strategy):")
            for lab, cnt, pct in zip(hist["labels"],
                                       hist["counts"], hist["percentages"]):
                bar = "#" * int(pct / 2)
                print(f"  {lab:<22} {cnt:>4} ({pct:5.1f}%) {bar}")
        print()

    return {
        "scene": scene_name,
        "naive": m_naive,
        "arcs": m_arcs,
        "smooth": m_smooth,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--scene", choices=list(SCENES.keys()) + ["all"],
                    default="all")
    ap.add_argument("--json", type=Path, default=None,
                    help="Write full results as JSON to this path")
    args = ap.parse_args()

    targets = list(SCENES.keys()) if args.scene == "all" else [args.scene]
    results = {}
    for scene in targets:
        results[scene] = run_benchmark(scene, verbose=True)

    if args.json:
        # strip non-JSON-serializable bits (reorder_indices is fine, clear_heights is fine)
        args.json.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"[benchmark] wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
