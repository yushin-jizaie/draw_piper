"""Stroke planner — Frida-inspired multi-stroke optimization helpers.

純関数ライブラリ。 Robot 依存無しでテスト可能。 提供する 3 つの最適化:

1. reorder_strokes_tsp() — TSP 近似 (greedy nearest-neighbor) で stroke 順を
   並べ替え、 stroke 間 travel を削減。 各 stroke は両端どちらから始めても良いので
   reverse 候補も含めて検討。
2. speed_from_curvature() — 各 arc の曲率に応じて描画速度を可変。 緩い曲線で速く、
   鋭い曲線で遅く → jerk 削減。
3. plan_clear_heights() — 次 stroke までの距離で pen-up 高さを可変。 短距離なら
   浅く (look-ahead descent) して travel 高速化、 遠ければ深く保持。

設計: docs/20260528_0030_frida_smoothness_design.md
参考: https://github.com/cmubig/Frida
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

import numpy as np


Point = Tuple[float, float]
Stroke = Sequence[Point]


# ============================================================ ordering (TSP)

def _stroke_endpoints(stroke: Stroke) -> Tuple[Point, Point]:
    """Return (first, last) point of a stroke."""
    return stroke[0], stroke[-1]


def _dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def reorder_strokes_tsp(
    strokes: Sequence[Stroke],
    start_point: Optional[Point] = None,
) -> Tuple[List[Stroke], List[int]]:
    """Greedy nearest-neighbor TSP approximation で stroke 順を並べ替え。

    各 stroke は 両端どちらから始めても等価 (反転可能) として扱う。

    Parameters
    ----------
    strokes : list of polylines
    start_point : (x, y) or None
        ロボットの初期位置 (panel uv 系 mm)。 None なら strokes[0] 先頭から開始。

    Returns
    -------
    (reordered_strokes, original_indices)
        reordered_strokes : 並べ替え後の strokes。 必要に応じて反転済。
        original_indices : reordered[i] = strokes[indices[i]] (or reversed) を
                           示す int リスト。 デバッグ・検証用。
    """
    if len(strokes) <= 1:
        return list(strokes), list(range(len(strokes)))

    n = len(strokes)
    used = [False] * n
    reordered: List[Stroke] = []
    indices: List[int] = []
    # current = ロボット先端の現在位置
    if start_point is None:
        current: Point = _stroke_endpoints(strokes[0])[0]
    else:
        current = start_point

    for _ in range(n):
        best_i = -1
        best_dist = float('inf')
        best_reverse = False
        for i, s in enumerate(strokes):
            if used[i]:
                continue
            head, tail = _stroke_endpoints(s)
            d_head = _dist(current, head)
            d_tail = _dist(current, tail)
            if d_head < best_dist:
                best_dist = d_head
                best_i = i
                best_reverse = False
            if d_tail < best_dist:
                best_dist = d_tail
                best_i = i
                best_reverse = True
        s = strokes[best_i]
        if best_reverse:
            s = list(reversed(s))
        reordered.append(s)
        indices.append(best_i)
        used[best_i] = True
        current = _stroke_endpoints(s)[1]

    return reordered, indices


def total_travel_distance(
    strokes: Sequence[Stroke],
    start_point: Optional[Point] = None,
) -> float:
    """Compute total pen-up travel distance (mm) between strokes.

    Excludes the drawing length inside each stroke; only counts the gap from
    one stroke's end to the next stroke's start. Useful as a metric for the
    ordering quality.
    """
    if len(strokes) == 0:
        return 0.0
    total = 0.0
    if start_point is not None:
        total += _dist(start_point, strokes[0][0])
    for i in range(len(strokes) - 1):
        total += _dist(strokes[i][-1], strokes[i + 1][0])
    return total


# ============================================================ curvature → speed

def compute_arc_length(triplet: Tuple[Point, Point, Point]) -> float:
    """3 点 (start, mid, end) の弧長 [mm] を計算する。

    曲率が 0 (collinear) の時は |start-end| を返す (直線扱い)。
    曲率が有限の時は R * angle で 真の弧長を計算 (chord-sum より正確)。

    Notes
    -----
    ベンチマーク script の draw_path_mm 集計に使う。 元の chord-sum
    (|a-b| + |b-c|) は 直線 chord の和で 真の弧長より短い。
    """
    a, b, c = triplet
    k = compute_arc_curvature(triplet)
    chord_ac = math.hypot(c[0] - a[0], c[1] - a[1])
    if k < 1e-9:
        return chord_ac
    R = 1.0 / k
    # central angle from chord: chord = 2 * R * sin(angle / 2)
    # → angle = 2 * arcsin(chord / (2R)), with clip for numerical safety
    arg = max(-1.0, min(1.0, chord_ac / (2.0 * R)))
    angle = 2.0 * math.asin(arg)
    return R * angle


def compute_arc_curvature(triplet: Tuple[Point, Point, Point]) -> float:
    """Compute curvature 1/R (1/mm) for a 3-point arc.

    Uses the formula: kappa = 4 * area / (|AB| * |BC| * |CA|)
    where area is the signed triangle area (we take abs).

    Returns 0.0 if the three points are collinear or coincident
    (= infinite radius = straight line).
    """
    a, b, c = triplet
    ax, ay = a
    bx, by = b
    cx, cy = c
    # Triangle area (twice)
    cross = abs((bx - ax) * (cy - ay) - (by - ay) * (cx - ax))
    if cross < 1e-9:
        return 0.0
    ab = math.hypot(bx - ax, by - ay)
    bc = math.hypot(cx - bx, cy - by)
    ca = math.hypot(ax - cx, ay - cy)
    denom = ab * bc * ca
    if denom < 1e-9:
        return 0.0
    return 2.0 * cross / denom


def speed_from_curvature(
    curvature: float,
    base_speed_pct: float,
    *,
    min_speed_pct: float = 10.0,
    max_speed_pct: float = 50.0,
    curvature_break: float = 0.1,
    curvature_steep: float = 0.5,
    curvature_straight: float = 0.02,
) -> int:
    """曲率 → 速度% マッピング (3 region 線形補間)。

    - curvature <= straight (= ほぼ直線、 半径 >= 50mm 程度) → max_speed_pct
    - straight < curvature <= break (= 緩い曲線、 R 10-50mm) → base 〜 max を線形
    - break < curvature < steep (= 中曲線、 R 2-10mm) → base 〜 min を線形
    - curvature >= steep (= 鋭い曲線、 R <= 2mm) → min_speed_pct

    実機の速度% は EndPoseCtrl の speed パラメータ (推奨は 10-50)。
    曲率は 1/mm 単位 (半径 R[mm] の逆数)。

    Parameters
    ----------
    curvature : float (1/mm)
    base_speed_pct : float — 緩い曲線の基準 (例 30)
    min_speed_pct  : float — 鋭い曲線の下限 (例 10)
    max_speed_pct  : float — 直線部での上限 (例 50)
    curvature_straight : float — この値以下は直線扱いで max 速度
    curvature_break    : float — この値以下は緩い扱い (default 0.1 = R 10mm)
    curvature_steep    : float — この値以上は鋭い扱い (default 0.5 = R 2mm)

    Returns
    -------
    int : 1-100 にクリップした speed%
    """
    if max_speed_pct < base_speed_pct:
        # ガード: 入力が逆転してたら base を上限扱い
        max_speed_pct = base_speed_pct
    if curvature <= curvature_straight:
        spd = max_speed_pct
    elif curvature <= curvature_break:
        # straight → break 区間: max → base に線形
        t = (curvature - curvature_straight) / max(
            1e-12, curvature_break - curvature_straight)
        spd = max_speed_pct + t * (base_speed_pct - max_speed_pct)
    elif curvature >= curvature_steep:
        spd = min_speed_pct
    else:
        # break → steep 区間: base → min に線形
        t = (curvature - curvature_break) / max(
            1e-12, curvature_steep - curvature_break)
        spd = base_speed_pct + t * (min_speed_pct - base_speed_pct)
    return int(round(max(1.0, min(100.0, spd))))


def speed_profile_for_stroke(
    triplets: Sequence[Tuple[Point, Point, Point]],
    base_speed_pct: float,
    *,
    min_speed_pct: float = 10.0,
    max_speed_pct: float = 50.0,
    curvature_break: float = 0.1,
    curvature_steep: float = 0.5,
    curvature_straight: float = 0.02,
    smooth_window: int = 3,
) -> List[int]:
    """各 arc の speed% を計算し、 移動平均で滑らかにして返す。

    急な speed 切替は jerk の原因なので、 隣接 arc と平均する。

    Returns
    -------
    list of int (len == len(triplets))
    """
    if not triplets:
        return []
    raw = [
        speed_from_curvature(
            compute_arc_curvature(t),
            base_speed_pct,
            min_speed_pct=min_speed_pct,
            max_speed_pct=max_speed_pct,
            curvature_break=curvature_break,
            curvature_steep=curvature_steep,
            curvature_straight=curvature_straight,
        )
        for t in triplets
    ]
    if smooth_window <= 1 or len(raw) <= 1:
        return raw
    # 移動平均 (端は片側のみ参照)
    out = []
    half = smooth_window // 2
    for i in range(len(raw)):
        lo = max(0, i - half)
        hi = min(len(raw), i + half + 1)
        out.append(int(round(sum(raw[lo:hi]) / (hi - lo))))
    return out


# ============================================================ look-ahead descent

def plan_clear_heights(
    strokes: Sequence[Stroke],
    *,
    w_clear_max_mm: float = 30.0,
    w_clear_near_mm: float = 10.0,
    near_threshold_mm: float = 15.0,
) -> List[float]:
    """各 stroke 終了後の pen-up 高さを 次 stroke までの距離で可変にする。

    短距離 travel なら 浅く (= 描画キャンバスに近い高さ) でも干渉せず travel 短縮、
    遠距離なら 深く (= 安全高さ) を維持。

    Parameters
    ----------
    strokes : ordered list of polylines (TSP 後を想定)
    w_clear_max_mm : 通常の pen-up 高さ (= panel.w_clear_mm)
    w_clear_near_mm : 短距離 travel 時の pen-up 高さ (浅め)
    near_threshold_mm : この距離以下なら "near" 扱い

    Returns
    -------
    list of float (len == len(strokes))
        clear_heights[i] = stroke i の終端で取る pen-up 高さ (mm)。
        最後の stroke は安全のため w_clear_max を返す。
    """
    n = len(strokes)
    if n == 0:
        return []
    heights = [w_clear_max_mm] * n
    for i in range(n - 1):
        gap = _dist(strokes[i][-1], strokes[i + 1][0])
        if gap <= near_threshold_mm:
            heights[i] = w_clear_near_mm
        # else 既に w_clear_max
    # 最後は常に max (安全に終わる)
    heights[-1] = w_clear_max_mm
    return heights


# ============================================================ debug / diagnostics

def stroke_set_diagnostics(
    strokes: Sequence[Stroke],
    start_point: Optional[Point] = None,
) -> dict:
    """Stroke set の メトリクスを返す。 ベンチマーク用。

    Returns
    -------
    dict with keys:
        n_strokes, n_points, draw_length_mm, travel_length_mm,
        total_length_mm, mean_stroke_length_mm
    """
    n_strokes = len(strokes)
    n_points = sum(len(s) for s in strokes)
    draw_len = 0.0
    for s in strokes:
        for i in range(len(s) - 1):
            draw_len += _dist(s[i], s[i + 1])
    travel_len = total_travel_distance(strokes, start_point=start_point)
    return {
        "n_strokes": n_strokes,
        "n_points": n_points,
        "draw_length_mm": draw_len,
        "travel_length_mm": travel_len,
        "total_length_mm": draw_len + travel_len,
        "mean_stroke_length_mm": draw_len / max(1, n_strokes),
    }


# ============================================================ smoke test

if __name__ == "__main__":
    # Synthetic 4 strokes: 2 grids (one near, one far)
    s1 = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]   # near origin
    s2 = [(100.0, 100.0), (110.0, 100.0)]           # far
    s3 = [(12.0, 10.0), (20.0, 10.0)]               # near s1 end
    s4 = [(110.0, 110.0), (100.0, 110.0)]           # near s2 end
    strokes = [s1, s2, s3, s4]

    print("=== reorder_strokes_tsp ===")
    before = stroke_set_diagnostics(strokes, start_point=(0.0, 0.0))
    reordered, indices = reorder_strokes_tsp(strokes, start_point=(0.0, 0.0))
    after = stroke_set_diagnostics(reordered, start_point=(0.0, 0.0))
    print(f"indices: {indices}")
    print(f"travel before: {before['travel_length_mm']:.1f}mm")
    print(f"travel after : {after['travel_length_mm']:.1f}mm")
    assert after['travel_length_mm'] <= before['travel_length_mm'] + 1e-6

    print()
    print("=== compute_arc_curvature ===")
    print(f"straight  : {compute_arc_curvature(((0,0),(1,0),(2,0))):.4f}/mm")
    print(f"R=1 arc   : {compute_arc_curvature(((1,0),(0,1),(-1,0))):.4f}/mm")
    print(f"R=10 arc  : {compute_arc_curvature(((10,0),(0,10),(-10,0))):.4f}/mm")

    print()
    print("=== speed_from_curvature ===")
    for k in [0.0, 0.05, 0.1, 0.2, 0.5, 1.0]:
        spd = speed_from_curvature(k, base_speed_pct=30,
                                    min_speed_pct=10, max_speed_pct=50)
        print(f"  curvature={k:.2f}/mm  speed={spd}%")

    print()
    print("=== plan_clear_heights ===")
    h = plan_clear_heights(reordered,
                            w_clear_max_mm=30.0, w_clear_near_mm=10.0,
                            near_threshold_mm=15.0)
    for i, hi in enumerate(h):
        print(f"  stroke {i}: clear height = {hi}mm")

    print()
    print("OK — stroke_planner.py smoke test passed.")
