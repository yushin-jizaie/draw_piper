#!/usr/bin/env python3
"""テスト用 strokes.json (丸 / 四角) を panel_uv_mm 形式で生成。

panel_uv_mm 形式なら座標が直接パネル mm なので、 画像アスペクトに依存せず
確実に「丸は丸・四角は四角」で描ける (GUI 側でパネルサイズに合わせ aspect
維持 fit される)。 出力は StrokePicker が拾える sketch_variations/test_shapes/
配下。 サムネ PNG も生成。

使い方:
    ~/draw_piper/venv/bin/python scripts/make_test_strokes.py
"""
from __future__ import annotations

import json
import math
import os

from PIL import Image, ImageDraw

OUT_ROOT = os.path.join(os.path.dirname(__file__), "..",
                        "sketch_variations", "test_shapes")
# パネル基準サイズ (mm)。 実機 panel と違っても GUI が aspect 維持で fit する。
PANEL_W_MM = 96.62
PANEL_H_MM = 181.38
MARGIN = 0.85  # パネル短辺に対する figure 占有率


def _write(name: str, strokes_uv, subject: str):
    d = os.path.join(OUT_ROOT, name)
    os.makedirs(os.path.join(d, "vec_debug"), exist_ok=True)
    n_pts = sum(len(s) for s in strokes_uv)
    payload = {
        "coordinate_system": "panel_uv_mm",
        "panel_size_mm": [PANEL_W_MM, PANEL_H_MM],
        "n_strokes": len(strokes_uv),
        "n_points": n_pts,
        "strokes": strokes_uv,
        "meta": {
            "coordinate_system": "panel_uv_mm",
            "panel_size_mm": [PANEL_W_MM, PANEL_H_MM],
            "source": "make_test_strokes.py",
        },
    }
    with open(os.path.join(d, "strokes.json"), "w") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    # topic_guess.json (picker が題材表示に使う)
    with open(os.path.join(d, "topic_guess.json"), "w") as f:
        json.dump({"subject": {"ja": subject, "en": name}}, f,
                  ensure_ascii=False)
    # サムネ (generated.png + vec_debug/06_strokes.png)
    W, H = 220, int(220 * PANEL_H_MM / PANEL_W_MM)
    im = Image.new("RGB", (W, H), "white")
    dr = ImageDraw.Draw(im)
    for s in strokes_uv:
        pp = [((u / PANEL_W_MM) * (W - 4) + 2,
               H - ((v / PANEL_H_MM) * (H - 4) + 2)) for u, v in s]
        if len(pp) > 1:
            dr.line(pp, fill="black", width=2)
    im.save(os.path.join(d, "generated.png"))
    im.save(os.path.join(d, "vec_debug", "06_strokes.png"))
    print(f"  {name}: {len(strokes_uv)} strokes, {n_pts} pts -> {d}")


def make_circle(segments: int = 64):
    cx, cy = PANEL_W_MM / 2.0, PANEL_H_MM / 2.0
    r = (min(PANEL_W_MM, PANEL_H_MM) / 2.0) * MARGIN
    pts = []
    for i in range(segments + 1):     # +1 で閉じる
        t = 2 * math.pi * i / segments
        pts.append([round(cx + r * math.cos(t), 3),
                    round(cy + r * math.sin(t), 3)])
    return [pts]


def make_square(pts_per_edge: int = 12):
    cx, cy = PANEL_W_MM / 2.0, PANEL_H_MM / 2.0
    half = (min(PANEL_W_MM, PANEL_H_MM) / 2.0) * MARGIN
    corners = [(cx - half, cy - half), (cx + half, cy - half),
               (cx + half, cy + half), (cx - half, cy + half),
               (cx - half, cy - half)]   # 閉じる
    pts = []
    for (x0, y0), (x1, y1) in zip(corners[:-1], corners[1:]):
        for k in range(pts_per_edge):
            f = k / pts_per_edge
            pts.append([round(x0 + (x1 - x0) * f, 3),
                        round(y0 + (y1 - y0) * f, 3)])
    pts.append([round(corners[0][0], 3), round(corners[0][1], 3)])
    return [pts]


if __name__ == "__main__":
    os.makedirs(OUT_ROOT, exist_ok=True)
    print("test strokes 生成:")
    _write("circle", make_circle(), "テスト丸")
    _write("square", make_square(), "テスト四角")
    print("完了。 GUI の「選択...」→ 参照元プルダウン sketch_variations/ "
          "→ test_shapes/circle or square を選択して描画。")
