#!/usr/bin/env python3
"""calib_simple_prototype.py -- 4隅だけからパネルフレームを作る試作 (方式B).

目的:
  現行のキャリブは「位置=隅3点アンカー」と「接触面=128点の最小二乗」で
  平面が2つあり食い違う。本試作は「4隅だけ」から
    1) 4点に平面を最小二乗フィット (4点のみ、128点ではない)
    2) 4隅をその平面へ射影
    3) 射影点から直交フレーム (origin/u/v/normal) を構築
  という単一定義のフレームを作り、旧2方式と数値比較する。
  実機不要 -- 既存の canvas_calibration.yaml の4隅を読むだけ。

使い方:
  python3 scripts/calib_simple_prototype.py [canvas_calibration.yaml]
"""

import sys
import numpy as np
import yaml

CORNER_KEYS = ("tl", "tr", "br", "bl")


def load_compare_normals(canvas_path):
    """比較用 normal を「現在のファイル自身」から読む (ハードコードしない)."""
    out = {}
    cd = yaml.safe_load(open(canvas_path))
    pf = (cd.get("plane_fit") or {}).get("normal")
    if pf:
        out["stored plane_fit"] = (np.array(pf, float),
                                   f"n={cd['plane_fit'].get('n_points')}")
    import os
    panel_path = os.path.join(os.path.dirname(canvas_path) or ".",
                              "panel_frame.yaml")
    if os.path.exists(panel_path):
        pn = (yaml.safe_load(open(panel_path)).get("panel") or {}).get("normal")
        if pn:
            out["panel_frame (位置/隅3点)"] = (np.array(pn, float), "")
    return out


def load_corners_xyz(path):
    d = yaml.safe_load(open(path))
    raw = d.get("whiteboard_corners_mm") or {}
    out = {}
    for k in CORNER_KEYS:
        rec = raw.get(k)
        if not isinstance(rec, dict):
            raise SystemExit(f"corner {k} missing/empty in {path}")
        ep = rec["end_pose_mm_deg"]
        out[k] = np.array([float(ep[0]), float(ep[1]), float(ep[2])])
    return out


def fit_plane(points):
    """4点に平面を最小二乗フィット -> (normal, centroid, residuals)."""
    pts = np.asarray(points, dtype=float)
    centroid = pts.mean(axis=0)
    centered = pts - centroid
    _, _, vh = np.linalg.svd(centered, full_matrices=False)
    normal = vh[-1] / np.linalg.norm(vh[-1])
    if normal[0] > 0:            # ボードは +X を向く -> 法線は -X 側 (アーム向き)
        normal = -normal
    residuals = centered @ normal     # 各点の面からの符号付き距離 mm
    return normal, centroid, residuals


def build_frame(corners_xyz):
    """方式B: 4点フィット -> 射影 -> 直交フレーム."""
    pts = np.array([corners_xyz[k] for k in CORNER_KEYS])  # tl,tr,br,bl 順
    normal, centroid, resid = fit_plane(pts)

    def project(p):
        return p - np.dot(p - centroid, normal) * normal

    bl = project(corners_xyz["bl"])
    br = project(corners_xyz["br"])
    tl = project(corners_xyz["tl"])
    tr = project(corners_xyz["tr"])

    u_axis = br - bl
    u_axis /= np.linalg.norm(u_axis)
    v_raw = tl - bl
    v_axis = v_raw - np.dot(v_raw, u_axis) * u_axis   # Gram-Schmidt
    v_axis /= np.linalg.norm(v_axis)
    fr_normal = np.cross(u_axis, v_axis)
    fr_normal /= np.linalg.norm(fr_normal)
    if fr_normal[0] > 0:
        fr_normal = -fr_normal

    width = float(np.dot(br - bl, u_axis))
    height = float(np.dot(tl - bl, v_axis))
    # tr が矩形の (width, height) にどれだけ近いか = 矩形性チェック
    tr_u = float(np.dot(tr - bl, u_axis))
    tr_v = float(np.dot(tr - bl, v_axis))
    return {
        "origin": bl, "u_axis": u_axis, "v_axis": v_axis,
        "normal": fr_normal, "width": width, "height": height,
        "fit_normal": normal, "centroid": centroid,
        "corner_residuals": dict(zip(CORNER_KEYS, resid)),
        "tr_uv": (tr_u, tr_v),
    }


def angle_deg(a, b):
    a = a / np.linalg.norm(a)
    b = b / np.linalg.norm(b)
    return float(np.degrees(np.arccos(np.clip(abs(np.dot(a, b)), -1, 1))))


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else \
        "calibration/canvas_calibration.yaml"
    corners = load_corners_xyz(path)
    fr = build_frame(corners)

    print(f"=== 入力 4隅 (base_link XYZ mm) [{path}] ===")
    for k in CORNER_KEYS:
        print(f"  {k}: {np.round(corners[k], 2).tolist()}")

    print("\n=== 方式B: 4点ベストフィット平面 ===")
    print(f"  centroid : {np.round(fr['centroid'], 2).tolist()}")
    print(f"  normal   : {np.round(fr['fit_normal'], 4).tolist()}")
    print("  各隅の面からの距離 (平坦性/sag 指標):")
    for k in CORNER_KEYS:
        print(f"    {k}: {fr['corner_residuals'][k]:+.2f} mm")
    rms = float(np.sqrt(np.mean(
        [r ** 2 for r in fr['corner_residuals'].values()])))
    print(f"    rms = {rms:.3f} mm   (旧128点fit rms = 8.044 mm)")

    print("\n=== 構築した直交フレーム ===")
    print(f"  origin (BL) : {np.round(fr['origin'], 2).tolist()}")
    print(f"  u_axis      : {np.round(fr['u_axis'], 4).tolist()}")
    print(f"  v_axis      : {np.round(fr['v_axis'], 4).tolist()}")
    print(f"  normal      : {np.round(fr['normal'], 4).tolist()}")
    print(f"  size_mm     : [{fr['width']:.2f}, {fr['height']:.2f}]")
    print(f"  u_axis . v_axis = {np.dot(fr['u_axis'], fr['v_axis']):+.2e} "
          "(0 = 完全直交)")
    print(f"  TR の (u,v) = ({fr['tr_uv'][0]:.2f}, {fr['tr_uv'][1]:.2f}) "
          f"  矩形なら ({fr['width']:.2f}, {fr['height']:.2f}) に一致すべき")
    tr_err = np.hypot(fr['tr_uv'][0] - fr['width'],
                      fr['tr_uv'][1] - fr['height'])
    print(f"  -> TR 矩形ズレ = {tr_err:.2f} mm (4隅の非矩形性)")

    print("\n=== 法線の比較 (現在のファイル自身と) ===")
    n_new = fr['normal']
    print(f"  新 方式B (4点fit)     : {np.round(n_new, 4).tolist()}")
    others = load_compare_normals(path)
    for name, (vec, note) in others.items():
        print(f"  {name:22s}: {np.round(vec, 4).tolist():}  {note}"
              f"   角度差 {angle_deg(n_new, vec):.2f}°")
    if "panel_frame (位置/隅3点)" in others and "stored plane_fit" in others:
        a = others["panel_frame (位置/隅3点)"][0]
        b = others["stored plane_fit"][0]
        print(f"  -> 位置フレーム vs plane_fit の食い違い = "
              f"{angle_deg(a, b):.2f}°  (★これが残っている2平面問題)")


if __name__ == "__main__":
    main()
