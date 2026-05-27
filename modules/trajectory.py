"""Stroke smoothing + arc decomposition for MOVE_C drawing.

The Vectorizer emits polylines (lists of (x, y) points) produced by
approxPolyDP, which are jagged piecewise-linear approximations of the
underlying skeleton. To draw them as smooth curves on the panel we:

  1. Smooth each polyline with a parametric cubic spline (scipy.splprep
     when available, otherwise Catmull-Rom on numpy only)
  2. Resample at a uniform arc-length step (default 2 mm)
  3. Group every 2 segments (3 consecutive points) into a MOVE_C arc

The grouped triplets are consumed by Robot.draw_polyline_panel_arcs in
modules/robot.py.

Public API:
  smooth_polyline(points, step_mm=2.0, smooth_lambda=0.0)
  polyline_to_arc_triplets(points)
  smooth_strokes(strokes, step_mm=2.0, smooth_lambda=0.0)
  strokes_to_trajectory(strokes)       # legacy stub kept for callers
"""

from __future__ import annotations

import math
from typing import Iterable, List, Sequence, Tuple

import numpy as np


Point = Tuple[float, float]
Stroke = List[Point]
ArcTriplet = Tuple[Point, Point, Point]   # (start, mid, end)


# ---------------------------------------------------------------- smoothing

def _polyline_arclength(points: np.ndarray) -> np.ndarray:
    """Cumulative arc-length parameter, shape (N,), starts at 0."""
    diffs = np.diff(points, axis=0)
    seg = np.hypot(diffs[:, 0], diffs[:, 1])
    return np.concatenate(([0.0], np.cumsum(seg)))


def _resample_uniform(t: np.ndarray, xy: np.ndarray, step: float
                       ) -> np.ndarray:
    """Linear-interpolate (t, xy) at a uniform step. Returns (M, 2).

    Used as the numpy-only fallback when scipy is missing — combined with
    Catmull-Rom interpolation for actual smoothing.
    """
    total = float(t[-1])
    if total <= 0.0 or step <= 0.0:
        return xy.copy()
    n = max(2, int(math.ceil(total / step)) + 1)
    ts = np.linspace(0.0, total, n)
    x = np.interp(ts, t, xy[:, 0])
    y = np.interp(ts, t, xy[:, 1])
    return np.stack([x, y], axis=1)


def _catmull_rom_densify(points: np.ndarray, factor: int = 8) -> np.ndarray:
    """Insert (factor-1) Catmull-Rom interpolants between each polyline
    segment. The original points are preserved (curve passes through them).
    Returns shape ((N-1)*factor + 1, 2).

    Reference: standard Catmull-Rom centripetal formulation with α=0.5.
    """
    pts = points.astype(np.float64)
    n = len(pts)
    if n < 2:
        return pts.copy()
    # pad ends by reflection so the first/last segment also gets a tangent
    p0 = pts[0] - (pts[1] - pts[0])
    pn = pts[-1] + (pts[-1] - pts[-2])
    extended = np.vstack([p0[None, :], pts, pn[None, :]])

    out: list[np.ndarray] = []
    factor = max(2, int(factor))
    for i in range(n - 1):
        p0, p1, p2, p3 = extended[i:i + 4]
        # centripetal knots
        def _knot(a, b, prev):
            return prev + (np.linalg.norm(b - a) ** 0.5)
        t0 = 0.0
        t1 = _knot(p0, p1, t0)
        t2 = _knot(p1, p2, t1)
        t3 = _knot(p2, p3, t2)
        # guard against duplicate points
        if t1 <= t0:
            t1 = t0 + 1e-6
        if t2 <= t1:
            t2 = t1 + 1e-6
        if t3 <= t2:
            t3 = t2 + 1e-6
        ts = np.linspace(t1, t2, factor + 1)[:-1]   # exclude endpoint
        for t in ts:
            a1 = (t1 - t) / (t1 - t0) * p0 + (t - t0) / (t1 - t0) * p1
            a2 = (t2 - t) / (t2 - t1) * p1 + (t - t1) / (t2 - t1) * p2
            a3 = (t3 - t) / (t3 - t2) * p2 + (t - t2) / (t3 - t2) * p3
            b1 = (t2 - t) / (t2 - t0) * a1 + (t - t0) / (t2 - t0) * a2
            b2 = (t3 - t) / (t3 - t1) * a2 + (t - t1) / (t3 - t1) * a3
            c = (t2 - t) / (t2 - t1) * b1 + (t - t1) / (t2 - t1) * b2
            out.append(c)
    out.append(extended[-2])      # final point (= original last)
    return np.asarray(out)


def smooth_polyline(points: Sequence[Point], *,
                     step_mm: float = 2.0,
                     smooth_lambda: float = 0.0,
                     ) -> List[Point]:
    """Smooth a polyline and resample at uniform arc-length step.

    Parameters
    ----------
    points : list of (x, y)
        Input polyline. Need >=2 points; passes through unchanged when <3.
    step_mm : float
        Target spacing (mm) between output points along arc length.
    smooth_lambda : float
        Spline smoothness parameter (scipy splprep `s`). 0 = exact pass
        through input points; >0 = smooths out noise. Only used when scipy
        is available; ignored in the Catmull-Rom fallback.

    Returns
    -------
    list of (x, y)
        Smoothed and resampled points. Length >= 2.
    """
    if len(points) < 2:
        return list(points)
    arr = np.asarray(points, dtype=np.float64)
    if len(arr) == 2:
        return _list_of_tuples(_resample_uniform(_polyline_arclength(arr),
                                                  arr, step_mm))

    # collapse exactly-duplicate adjacent points (splprep / Catmull-Rom hate them)
    diffs = np.diff(arr, axis=0)
    keep = np.concatenate(([True], np.any(diffs != 0.0, axis=1)))
    arr = arr[keep]
    if len(arr) < 3:
        return _list_of_tuples(_resample_uniform(_polyline_arclength(arr),
                                                  arr, step_mm))

    try:
        from scipy.interpolate import splprep, splev   # type: ignore
        # parametric cubic B-spline; k=3 needs >=4 points, fall to k=2 / k=1
        k = 3 if len(arr) >= 4 else max(1, len(arr) - 1)
        tck, _u = splprep([arr[:, 0], arr[:, 1]],
                           s=float(smooth_lambda), k=k)
        # estimate length for sample count
        u_dense = np.linspace(0.0, 1.0, max(64, 16 * len(arr)))
        x_d, y_d = splev(u_dense, tck)
        seg = np.hypot(np.diff(x_d), np.diff(y_d))
        total = float(np.sum(seg))
        n = max(2, int(math.ceil(total / max(0.01, step_mm))) + 1)
        u = np.linspace(0.0, 1.0, n)
        x, y = splev(u, tck)
        return [(float(xi), float(yi)) for xi, yi in zip(x, y)]
    except ImportError:
        # numpy-only fallback
        dense = _catmull_rom_densify(arr, factor=8)
        t = _polyline_arclength(dense)
        resampled = _resample_uniform(t, dense, step_mm)
        return _list_of_tuples(resampled)


def _list_of_tuples(arr: np.ndarray) -> List[Point]:
    return [(float(p[0]), float(p[1])) for p in arr]


# ---------------------------------------------------------------- arc grouping

def polyline_to_arc_triplets(points: Sequence[Point]) -> List[ArcTriplet]:
    """Group consecutive points into MOVE_C triplets (start, mid, end).

    Each triplet consumes 2 segments. Successive triplets share an endpoint:
    triplet[i].end == triplet[i+1].start, so the resulting arcs chain into a
    continuous path. If the polyline has an even number of points (odd
    segments), the trailing leftover segment becomes a 2-point "degenerate
    arc" — represented as (start, midpoint, end) where mid is the segment's
    midpoint, which the MOVE_C controller will draw as a straight line.

    Examples
    --------
    >>> polyline_to_arc_triplets([(0,0), (1,0), (2,0)])
    [((0,0), (1,0), (2,0))]
    >>> polyline_to_arc_triplets([(0,0), (1,0), (2,0), (3,0)])
    # → [(p0,p1,p2), (p2, midpoint(p2,p3), p3)]
    """
    pts = list(points)
    n = len(pts)
    if n < 3:
        if n == 2:
            mid = ((pts[0][0] + pts[1][0]) * 0.5,
                   (pts[0][1] + pts[1][1]) * 0.5)
            return [(pts[0], mid, pts[1])]
        return []
    triplets: List[ArcTriplet] = []
    i = 0
    while i + 2 < n:
        triplets.append((pts[i], pts[i + 1], pts[i + 2]))
        i += 2
    if i + 1 < n:
        # leftover single segment after the last triplet
        a = pts[i]
        b = pts[i + 1]
        mid = ((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5)
        triplets.append((a, mid, b))
    return triplets


# ---------------------------------------------------------------- stroke-level

def smooth_strokes(strokes: Iterable[Sequence[Point]], *,
                    step_mm: float = 2.0,
                    smooth_lambda: float = 0.0,
                    min_points: int = 2,
                    ) -> List[Stroke]:
    """Apply smooth_polyline to each stroke. Drops strokes with <min_points."""
    out: List[Stroke] = []
    for s in strokes:
        if len(s) < min_points:
            continue
        smoothed = smooth_polyline(s, step_mm=step_mm,
                                    smooth_lambda=smooth_lambda)
        if len(smoothed) >= min_points:
            out.append(smoothed)
    return out


# ---------------------------------------------------------------- legacy stub

def strokes_to_trajectory(strokes):
    """Legacy v0.1 stub kept for callers that import it."""
    return {'strokes': strokes}


# ---------------------------------------------------------------- smoke test

if __name__ == '__main__':
    # zigzag → smoothed → arc triplets
    raw = [(0.0, 0.0), (10.0, 5.0), (20.0, -5.0), (30.0, 5.0), (40.0, 0.0)]
    print(f"raw     : {len(raw)} pts")
    smoothed = smooth_polyline(raw, step_mm=2.0)
    print(f"smoothed: {len(smoothed)} pts, first={smoothed[0]}, last={smoothed[-1]}")
    arcs = polyline_to_arc_triplets(smoothed)
    print(f"arcs    : {len(arcs)} MOVE_C triplets")
    print(f"first arc: {arcs[0]}")
    # check chain continuity
    for i in range(len(arcs) - 1):
        assert arcs[i][-1] == arcs[i + 1][0], \
            f"arc chain discontinuity at {i}: {arcs[i][-1]} != {arcs[i+1][0]}"
    print("chain continuity OK")
