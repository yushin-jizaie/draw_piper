#!/usr/bin/env python3
"""キャンバス矩形の内側 80% を描く四角のテスト strokes.json を生成。

canvas_calibration.yaml の whiteboard_computed.width_mm / height_mm を読み、
panel_size = (W, H) のまま、内側 80% (各辺 10% マージン) の長方形を
panel_uv_mm 形式で書き出す。 中央+サイズ矩形モードでは panel==canvas に
なるので、 描画結果はキャンバス中央を中心とした 80% の長方形になる。

W/H を変えたら再実行すれば追従する。
"""
import json
import os

import yaml

CALIB = "/home/jizaiedev2026/draw_piper/calibration/canvas_calibration.yaml"
OUT_DIR = "/home/jizaiedev2026/draw_piper/sketch_variations/test_shapes/rect80_inner"
INSET = 0.10          # 各辺のマージン (=内側 80%)
EDGE_STEP_MM = 10.0   # 辺を分割する間隔 (描画/IK 用の中間点)


def _interp(p0, p1, step_mm):
    (u0, v0), (u1, v1) = p0, p1
    dist = ((u1 - u0) ** 2 + (v1 - v0) ** 2) ** 0.5
    n = max(1, int(round(dist / step_mm)))
    pts = []
    for i in range(n):
        t = i / n
        pts.append([round(u0 + (u1 - u0) * t, 3),
                    round(v0 + (v1 - v0) * t, 3)])
    return pts


def main():
    d = yaml.safe_load(open(CALIB))
    comp = d.get("whiteboard_computed") or {}
    W = float(comp.get("width_mm"))
    H = float(comp.get("height_mm"))

    u0, u1 = INSET * W, (1.0 - INSET) * W
    v0, v1 = INSET * H, (1.0 - INSET) * H
    # 反時計回り (左下→右下→右上→左上→閉じる)
    corners = [(u0, v0), (u1, v0), (u1, v1), (u0, v1), (u0, v0)]
    stroke = []
    for a, b in zip(corners[:-1], corners[1:]):
        stroke.extend(_interp(a, b, EDGE_STEP_MM))
    stroke.append([round(u0, 3), round(v0, 3)])  # 終点で閉じる

    data = {
        "coordinate_system": "panel_uv_mm",
        "panel_size_mm": [round(W, 2), round(H, 2)],
        "n_strokes": 1,
        "n_points": len(stroke),
        "strokes": [stroke],
        "meta": {
            "coordinate_system": "panel_uv_mm",
            "panel_size_mm": [round(W, 2), round(H, 2)],
            "source": "make_test_rect80.py",
            "note": f"canvas {W:.1f}x{H:.1f}mm の内側 {int((1-2*INSET)*100)}% 長方形",
        },
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, "strokes.json")
    with open(out, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"書き出し: {out}")
    print(f"  canvas W×H = {W:.2f} × {H:.2f} mm")
    print(f"  内側80% 長方形 u[{u0:.1f},{u1:.1f}] v[{v0:.1f},{v1:.1f}]")
    print(f"  {len(stroke)} 点 / 1 ストローク")


if __name__ == "__main__":
    main()
