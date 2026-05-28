#!/usr/bin/env python3
"""scripts/check_panel_geometry.py

「カメラ warp / 画像生成 / ロボット認識平面」 の 3 つが同じ panel 物理
寸法で整合しているかを確認する CLI ツール。

実行:
    python3 scripts/check_panel_geometry.py
    python3 scripts/check_panel_geometry.py --sync   # canvas → panel_frame に書き戻す

確認項目:
    1. canvas_calibration.yaml の whiteboard_computed 実測値
    2. panel_frame.yaml の panel.size_mm (ロボット側 PanelFrame が読む値)
    3. panel_frame.yaml の phase_a_calibration.{panel_size_mm, panel_image_size}
       (PanelCropper が読む値)
    4. imagegen_config.yaml の resolution / auto_from_panel
    5. select_sdxl_bucket() が選ぶ bucket
    6. vectorize_to_panel が暗黙に仮定する panel size と aspect 一致

不整合があれば diff を表示。 --sync で canvas → panel_frame.panel.size_mm を上書き。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import yaml  # noqa: E402

from modules.panel_geometry import (  # noqa: E402
    SDXL_BUCKETS,
    DEFAULT_CANVAS_YAML,
    DEFAULT_PANEL_FRAME_YAML,
    load_panel_geometry,
    select_sdxl_bucket,
    write_geometry_to_panel_frame,
)


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open() as f:
        return yaml.safe_load(f) or {}


def _fmt_size_mm(sz) -> str:
    if not sz or not isinstance(sz, (list, tuple)) or len(sz) != 2:
        return "(missing)"
    return f"{sz[0]:.2f} × {sz[1]:.2f} mm"


def _fmt_size_px(sz) -> str:
    if not sz or not isinstance(sz, (list, tuple)) or len(sz) != 2:
        return "(missing)"
    return f"{int(sz[0])} × {int(sz[1])} px"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--canvas",
        default=str(DEFAULT_CANVAS_YAML),
        help=f"canvas calibration yaml (default: {DEFAULT_CANVAS_YAML})")
    ap.add_argument("--panel-frame",
        default=str(DEFAULT_PANEL_FRAME_YAML),
        help=f"panel_frame yaml (default: {DEFAULT_PANEL_FRAME_YAML})")
    ap.add_argument("--imagegen",
        default=str(_ROOT / "calibration" / "imagegen_config.yaml"),
        help="imagegen_config yaml")
    ap.add_argument("--sync", action="store_true",
        help="canvas calibration の panel 寸法を panel_frame.panel.size_mm に書き戻す")
    args = ap.parse_args()

    canvas_path = Path(args.canvas)
    panel_path = Path(args.panel_frame)
    imagegen_path = Path(args.imagegen)

    canvas = _load(canvas_path)
    panel = _load(panel_path)
    imagegen = _load(imagegen_path)

    print("=" * 70)
    print("draw_piper Panel Geometry — Source 整合チェック")
    print("=" * 70)

    # ---- canvas
    wb = canvas.get("whiteboard_computed") or {}
    canvas_mm = (wb.get("width_mm"), wb.get("height_mm"))
    print(f"\n[1] canvas_calibration.yaml  ({canvas_path.name})")
    print(f"    whiteboard_computed.width/height_mm : "
          f"{_fmt_size_mm(canvas_mm if canvas_mm[0] else None)}")

    # ---- panel_frame.panel
    pn = panel.get("panel") or {}
    panel_mm = pn.get("size_mm")
    print(f"\n[2] panel_frame.yaml  ({panel_path.name})")
    print(f"    panel.size_mm                       : "
          f"{_fmt_size_mm(panel_mm)}")
    print(f"    panel.calibrated                    : "
          f"{pn.get('calibrated', '(missing)')}")

    # ---- phase_a_calibration
    pa = panel.get("phase_a_calibration") or {}
    pa_mm = pa.get("panel_size_mm")
    pa_px = pa.get("panel_image_size")
    print(f"\n[3] phase_a_calibration (PanelCropper が読む)")
    print(f"    panel_size_mm                       : {_fmt_size_mm(pa_mm)}")
    print(f"    panel_image_size                    : {_fmt_size_px(pa_px)}")

    # ---- imagegen_config
    ig = imagegen.get("imagegen") or {}
    res = ig.get("resolution")
    auto = ig.get("auto_from_panel", False)
    print(f"\n[4] imagegen_config.yaml")
    print(f"    auto_from_panel                     : {auto}")
    print(f"    resolution (override)               : "
          f"{_fmt_size_px(res) if res else '(unset — auto / default 1024×1024)'}")

    # ---- 推奨 bucket (canvas 由来)
    try:
        geom = load_panel_geometry(
            canvas_path=canvas_path,
            panel_frame_path=panel_path,
        )
        print(f"\n[5] 推奨 PanelGeometry  (source={geom.source})")
        print(f"    panel_size_mm                       : "
              f"{_fmt_size_mm(geom.panel_size_mm)}")
        print(f"    panel_image_size (SDXL bucket)      : "
              f"{_fmt_size_px(geom.panel_image_size)}")
        print(f"    aspect (W/H)                        : "
              f"{geom.aspect:.4f}")
        print(f"    bucket aspect err                   : "
              f"{geom.bucket_aspect_err * 100:.2f}%")
        print(f"    mm_per_px  (W, H)                   : "
              f"({geom.mm_per_px[0]:.4f}, {geom.mm_per_px[1]:.4f})")
    except Exception as e:
        print(f"\n[5] 推奨 PanelGeometry — ERROR: {e}")
        geom = None

    # ---- 不整合 diff
    print(f"\n[6] 整合 diff")
    issues: list[str] = []

    def _close(a, b, tol=0.5) -> bool:
        if a is None or b is None:
            return False
        if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
            if len(a) != len(b):
                return False
            return all(abs(float(x) - float(y)) < tol for x, y in zip(a, b))
        return abs(float(a) - float(b)) < tol

    if canvas_mm[0] and panel_mm:
        if not _close(canvas_mm, panel_mm):
            issues.append(
                f"❌ canvas={_fmt_size_mm(canvas_mm)} ↔ "
                f"panel_frame.panel.size_mm={_fmt_size_mm(panel_mm)}  "
                f"(--sync で揃える)"
            )
        else:
            print(f"    ✅ canvas ↔ panel_frame.panel.size_mm 一致")

    if canvas_mm[0] and pa_mm:
        if not _close(canvas_mm, pa_mm):
            issues.append(
                f"❌ canvas={_fmt_size_mm(canvas_mm)} ↔ "
                f"phase_a_calibration.panel_size_mm={_fmt_size_mm(pa_mm)}  "
                f"(scripts/calibrate_panel.py で 4 点クリックし直す必要あり)"
            )
        else:
            print(f"    ✅ canvas ↔ phase_a.panel_size_mm 一致")

    if geom and pa_px:
        if not _close(geom.panel_image_size, pa_px, tol=0):
            issues.append(
                f"⚠️  推奨 bucket={_fmt_size_px(geom.panel_image_size)} ↔ "
                f"phase_a.panel_image_size={_fmt_size_px(pa_px)}  "
                f"(--sync で phase_a.panel_image_size を bucket に更新)"
            )
        else:
            print(f"    ✅ phase_a.panel_image_size = 推奨 bucket")

    if geom and res:
        if not _close(geom.panel_image_size, res, tol=0):
            issues.append(
                f"⚠️  推奨 bucket={_fmt_size_px(geom.panel_image_size)} ↔ "
                f"imagegen.resolution={_fmt_size_px(res)}  "
                f"(意図的に override しているなら問題なし)"
            )
        else:
            print(f"    ✅ imagegen.resolution = 推奨 bucket")
    elif geom and not res and auto:
        print(f"    ✅ imagegen.auto_from_panel=true で bucket 自動選択 "
              f"({_fmt_size_px(geom.panel_image_size)})")

    if issues:
        print()
        for msg in issues:
            print(f"    {msg}")
    elif geom is not None:
        print(f"\n  ✅ 全 source が同じ panel 寸法・解像度で整合しています")

    # ---- sync
    if args.sync and geom is not None:
        print(f"\n[7] --sync: panel_frame.yaml に書き戻し中 ...")
        out = write_geometry_to_panel_frame(geom, panel_frame_path=panel_path)
        print(f"    saved: {out}")
        print(f"    panel.size_mm        ← {list(geom.panel_size_mm)}")
        print(f"    phase_a.panel_image_size ← {list(geom.panel_image_size)}")
        print(f"    (phase_a.panel_size_mm は correspondences との整合のため "
              f"既存値が canvas と一致しない場合は変更しません)")

    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
