"""Stroke visualizer — render strokes (panel uv mm or pixel) to an image.

実機で描く前に 「ロボットがどう描くか」 を視覚的に確認するためのツール。
draw_strokes_panel_smooth に渡す strokes (TSP 並べ替え後 / smooth 後) を
PNG にレンダリングする。

主な用途:
- Vectorizer の出力が想定通りかチェック (元画像と並べて見る)
- TSP 並べ替えの travel 経路を視覚化 (stroke 順を色グラデで表現)
- smooth_polyline の resample 結果を見る (元 polyline vs smooth 後)
- benchmark の補助 (実機なしで動作確認)

依存: PIL (numpy 経由) のみ、 cv2 不要。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import List, Optional, Sequence, Tuple, Union

import numpy as np
from PIL import Image, ImageDraw, ImageFont


Point = Tuple[float, float]
Stroke = Sequence[Point]
Color = Tuple[int, int, int]


_DEFAULT_PEN_COLORS_HSV = [
    (0, 70, 90), (24, 70, 90), (48, 70, 90), (72, 70, 90),
    (120, 70, 80), (160, 70, 80), (200, 70, 90), (240, 70, 90),
    (280, 70, 85), (320, 70, 90),
]


def _hsv_to_rgb(h: float, s: float, v: float) -> Color:
    """h: 0-360, s: 0-100, v: 0-100 -> (r, g, b) 0-255."""
    h, s, v = h / 360.0, s / 100.0, v / 100.0
    if s <= 0:
        c = int(v * 255)
        return (c, c, c)
    i = int(h * 6) % 6
    f = h * 6 - int(h * 6)
    p = v * (1 - s)
    q = v * (1 - s * f)
    t = v * (1 - s * (1 - f))
    r, g, b = [(v, t, p), (q, v, p), (p, v, t),
               (p, q, v), (t, p, v), (v, p, q)][i]
    return (int(r * 255), int(g * 255), int(b * 255))


def render_strokes_uv(
    strokes_mm: Sequence[Stroke],
    panel_size_mm: Tuple[float, float],
    *,
    out_size_px: Tuple[int, int] = (1024, 1024),
    pen_color: Color = (20, 20, 20),
    bg_color: Color = (255, 255, 255),
    pen_width_px: int = 3,
    margin_mm: float = 5.0,
    show_travel: bool = False,
    travel_color: Color = (200, 200, 220),
    travel_width_px: int = 1,
    color_by_order: bool = False,
    start_marker: bool = False,
    title: Optional[str] = None,
) -> Image.Image:
    """Render panel-uv strokes (mm) to a PIL image.

    Parameters
    ----------
    strokes_mm : list of polylines [(u_mm, v_mm), ...]
    panel_size_mm : (width_u, height_v)
    out_size_px : output image (W, H)
    pen_color : pen RGB color
    bg_color : background RGB
    pen_width_px : draw stroke width in pixels
    margin_mm : extra margin around the panel in mm (output region)
    show_travel : if True, draws dashed travel lines between strokes
    color_by_order : if True, each stroke gets a distinct hue (debug)
    start_marker : if True, marks each stroke start with a small circle
    title : optional title text (top-left)

    Returns
    -------
    PIL.Image (RGB)
    """
    W, H = out_size_px
    img = Image.new("RGB", (W, H), bg_color)
    draw = ImageDraw.Draw(img)

    panel_w, panel_h = panel_size_mm
    total_w_mm = panel_w + 2 * margin_mm
    total_h_mm = panel_h + 2 * margin_mm
    # fit panel to image with margin, preserve aspect ratio
    scale = min(W / total_w_mm, H / total_h_mm)
    # transform: uv mm (origin bottom-left in panel coord) → image px (origin top-left)
    pad_u = margin_mm + (W / scale - total_w_mm) / 2
    pad_v = margin_mm + (H / scale - total_h_mm) / 2

    def uv_to_px(u_mm: float, v_mm: float) -> Tuple[int, int]:
        x = (u_mm + pad_u) * scale
        y = H - (v_mm + pad_v) * scale     # flip Y
        return (int(x), int(y))

    # draw panel boundary (very light gray)
    p0 = uv_to_px(0, 0)
    p1 = uv_to_px(panel_w, panel_h)
    draw.rectangle([p0[0], p1[1], p1[0], p0[1]], outline=(230, 230, 230),
                   width=1)

    # travel lines (under strokes)
    if show_travel and len(strokes_mm) >= 2:
        for i in range(len(strokes_mm) - 1):
            a = strokes_mm[i][-1]
            b = strokes_mm[i + 1][0]
            ax, ay = uv_to_px(*a)
            bx, by = uv_to_px(*b)
            _draw_dashed_line(draw, (ax, ay), (bx, by),
                              fill=travel_color, width=travel_width_px,
                              dash_len_px=6, gap_len_px=4)

    # strokes
    n = len(strokes_mm)
    for i, s in enumerate(strokes_mm):
        if len(s) < 2:
            continue
        if color_by_order and n > 1:
            hue = 360.0 * i / max(1, n - 1) * 0.85   # cycle but stop short of full wrap
            color = _hsv_to_rgb(hue, 80, 85)
        else:
            color = pen_color
        pts_px = [uv_to_px(u, v) for (u, v) in s]
        draw.line(pts_px, fill=color, width=pen_width_px, joint="curve")
        if start_marker:
            sx, sy = pts_px[0]
            r = max(2, pen_width_px)
            draw.ellipse([sx - r, sy - r, sx + r, sy + r], outline=color,
                         width=1)

    # title
    if title:
        try:
            draw.text((10, 8), title, fill=(60, 60, 60))
        except Exception:
            pass

    return img


def _draw_dashed_line(draw: ImageDraw.ImageDraw, p1, p2, *,
                       fill, width, dash_len_px=6, gap_len_px=4) -> None:
    """Draw a dashed line between p1 and p2."""
    x1, y1 = p1
    x2, y2 = p2
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    if length < 1.0:
        return
    ux, uy = dx / length, dy / length
    step = dash_len_px + gap_len_px
    n = int(length / step) + 1
    for i in range(n):
        s0 = i * step
        s1 = min(length, s0 + dash_len_px)
        a = (int(x1 + ux * s0), int(y1 + uy * s0))
        b = (int(x1 + ux * s1), int(y1 + uy * s1))
        draw.line([a, b], fill=fill, width=width)


def render_comparison_grid(
    strokes_mm: Sequence[Stroke],
    panel_size_mm: Tuple[float, float],
    *,
    reordered_strokes_mm: Optional[Sequence[Stroke]] = None,
    out_size_px: Tuple[int, int] = (1024, 512),
    pen_width_px: int = 2,
) -> Image.Image:
    """Side-by-side: raw order (with travel) vs reordered (with travel).

    Useful to visualize the TSP reordering's effect.
    """
    half_w = out_size_px[0] // 2
    img = Image.new("RGB", out_size_px, (255, 255, 255))
    left = render_strokes_uv(
        strokes_mm, panel_size_mm, out_size_px=(half_w, out_size_px[1]),
        pen_color=(40, 40, 40), pen_width_px=pen_width_px,
        show_travel=True, color_by_order=True, start_marker=True,
        title="raw order (travel dashed, color = stroke index)",
    )
    img.paste(left, (0, 0))
    if reordered_strokes_mm is not None:
        right = render_strokes_uv(
            reordered_strokes_mm, panel_size_mm,
            out_size_px=(out_size_px[0] - half_w, out_size_px[1]),
            pen_color=(40, 40, 40), pen_width_px=pen_width_px,
            show_travel=True, color_by_order=True, start_marker=True,
            title="TSP reordered",
        )
        img.paste(right, (half_w, 0))
    return img


# ============================================================ smoke test

if __name__ == "__main__":
    # Synthetic strokes
    import sys
    from pathlib import Path as _P
    _ROOT = _P(__file__).resolve().parent.parent
    sys.path.insert(0, str(_ROOT))
    from modules.stroke_planner import reorder_strokes_tsp

    panel = (100.0, 100.0)
    strokes = [
        [(10, 10), (30, 10), (30, 30), (10, 30), (10, 10)],   # square
        [(50, 50), (60, 60)],                                  # tiny diag
        [(80, 20), (90, 30)],                                  # corner
        [(40, 80), (60, 80), (60, 90)],                        # L-shape
    ]
    reordered, _ = reorder_strokes_tsp(strokes, start_point=(0, 0))

    out_dir = _P("/tmp/stroke_viz_smoke")
    out_dir.mkdir(exist_ok=True)
    img1 = render_strokes_uv(strokes, panel, color_by_order=True,
                              show_travel=True, start_marker=True,
                              title="raw")
    img1.save(out_dir / "raw.png")
    img2 = render_strokes_uv(reordered, panel, color_by_order=True,
                              show_travel=True, start_marker=True,
                              title="TSP reordered")
    img2.save(out_dir / "tsp.png")
    grid = render_comparison_grid(strokes, panel,
                                   reordered_strokes_mm=reordered)
    grid.save(out_dir / "compare.png")
    print(f"OK — wrote {out_dir}/raw.png, tsp.png, compare.png")
