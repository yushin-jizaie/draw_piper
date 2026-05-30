#!/usr/bin/env python3
"""選定 JSON (draw_piper_selections.json) を読んで、 各選択について Vectorizer を
回し strokes.json + 元画像コピーを logs/robot_input_set_<ts>/<sketch_id>/ に配置。

壁面描画 GUI の StrokePicker が logs/**/strokes*.json を一括 scan するので、
そのまま実機描画選択候補として表示される。
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


# 入力 sketch path (sketch_id → 入力 png path)
INPUT_PATHS = {
    "B_round_smiley":   "logs/sketch_variations_20260528_084706/inputs/sketch_B_round_smiley.png",
    "C_face_with_neck": "logs/sketch_variations_20260528_084706/inputs/sketch_C_face_with_neck.png",
    "D_stick_figure":   "logs/sketch_variations_20260528_084706/inputs/sketch_D_stick_figure.png",
    "F_angry_face":     "logs/sketch_variations_20260528_084706/inputs/sketch_F_angry_face.png",
    "house":            "logs/sketches_objects_20260528_183943/sketch_house.png",
    "tree":             "logs/sketches_objects_20260528_183943/sketch_tree.png",
    "cat":              "logs/sketches_objects_20260528_183943/sketch_cat.png",
    "car":              "logs/sketches_objects_20260528_183943/sketch_car.png",
}


def vectorize_rendered_png(png_path: Path,
                            min_pixels: int = 40,
                            min_length: float = 15.0,
                            approx_epsilon: float = 2.0) -> dict:
    """rendered stroke PNG (太い黒線描画) を skeletonize → 中心線 polyline 化。

    通常の Vectorizer (Canny 経路) は線の両側エッジを抽出するので、
    既に描画済の rendered PNG (shift / composition shift の
    30_companion_strokes.png 等) を入力すると 1 本の線が二重アウトライン
    として残り、 robot が線を 2 度描いてしまう。

    本関数は 二値化 + skeletonize + 輪郭抽出で線の中心 (1px 細線) を
    直接取得する。 shift / composition shift 経路で使用。
    """
    import cv2
    import numpy as np
    from skimage.morphology import skeletonize as sk_skel

    img = cv2.imread(str(png_path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise RuntimeError(f"failed to read: {png_path}")
    h, w = img.shape
    _, mask = cv2.threshold(img, 200, 255, cv2.THRESH_BINARY_INV)
    # 並走する 2 本線 (元 input の Canny 両側エッジを shift mode が描画したもの)
    # を 1 本にまとめる: 中間 kernel の closing で近傍ペア線間を埋める
    kernel_pair = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel_pair)
    # 線の内部に残った小穴を hierarchy ベースで埋める (img の 0.5% 未満を hole 扱い)
    hole_limit = int(h * w * 0.005)
    contours, hierarchy = cv2.findContours(
        mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    if hierarchy is not None:
        for i, c in enumerate(contours):
            if hierarchy[0][i][3] >= 0:  # 内側 contour = 穴
                if cv2.contourArea(c) < hole_limit:
                    cv2.drawContours(mask, [c], -1, 255, -1)
    # 小連結成分除外
    num, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    for i in range(1, num):
        if stats[i, cv2.CC_STAT_AREA] < min_pixels:
            mask[labels == i] = 0
    skel = sk_skel(mask > 0).astype(np.uint8) * 255
    contours, _ = cv2.findContours(skel, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    strokes = []
    total_pts = 0
    total_len = 0.0
    for c in contours:
        L = float(cv2.arcLength(c, False))
        if L < min_length:
            continue
        approx = cv2.approxPolyDP(c, approx_epsilon, False)
        pts = [(float(p[0][0]), float(p[0][1])) for p in approx]
        if len(pts) >= 2:
            strokes.append(pts)
            total_pts += len(pts)
            total_len += L
    return {
        "strokes": strokes,
        "image_shape": (h, w),
        "n_strokes": len(strokes),
        "n_points": total_pts,
        "total_length_px": total_len,
        "vis_png": 255 - skel,
    }


def source_png_for_route(route: str, sketch_id: str, src_dir: Path) -> tuple:
    """選択 route から Vectorize 元の PNG path と、 user_image (diff 用) を返す。

    Returns
    -------
    (vec_source_png, user_image_or_none, generated_preview_png)
    """
    # "composition shift" → cv2 blob 後の最終合成 PNG (shift mode と同じ扱い)
    # "composition align" → Stage 1 inpaint 出力 (align mode と同じ扱い)
    if route == "composition shift" or "shift" in route and "composition" in route:
        comp_png = src_dir / "30_companion_strokes.png"
        return (comp_png, None, comp_png)
    if route == "composition align" or "align" in route and "composition" in route:
        stage1_png = src_dir / "stage1" / "illustrious_v2_inpaint.png"
        if not stage1_png.exists():
            stage1_png = src_dir / "20_final_no_ip_adapter.png"
        return (stage1_png, _ROOT / INPUT_PATHS[sketch_id], stage1_png)
    if route.startswith("align") or route.startswith("disp:"):
        # disp:* は dispatcher 経由の align mode (Stage 1 inpaint 出力)
        stage1_png = src_dir / "stage1" / "illustrious_v2_inpaint.png"
        if not stage1_png.exists():
            stage1_png = src_dir / "20_final_no_ip_adapter.png"
        if not stage1_png.exists():
            # fallback: 30_vectorized_strokes.png の親に 20_stage2_*.png があれば
            stage2_pngs = sorted(src_dir.glob("20_stage2_*.png"))
            if stage2_pngs:
                stage1_png = stage2_pngs[0]
        return (stage1_png, _ROOT / INPUT_PATHS[sketch_id], stage1_png)
    elif route.startswith("shift"):
        comp_png = src_dir / "30_companion_strokes.png"
        return (comp_png, None, comp_png)
    elif route.startswith("gacha"):
        # gacha: Stage 2 後 PNG (20_stage2_*.png) を Vectorize、 user_image diff
        stage2_pngs = sorted(src_dir.glob("20_stage2_*.png"))
        if stage2_pngs:
            return (stage2_pngs[0],
                    _ROOT / INPUT_PATHS[sketch_id],
                    stage2_pngs[0])
        # fallback
        png = src_dir / "30_vectorized_strokes.png"
        return (png, None, png)
    else:
        raise ValueError(f"unknown route: {route}")


def sanitize_route(route: str) -> str:
    """route 名を dir 名向けに正規化。"""
    return (route.replace("(", "")
                  .replace(")", "")
                  .replace(":", "_")
                  .replace("/", "_")
                  .replace(" ", "_")
                  .replace("__", "_")
                  .replace("__", "_")
                  .lower())


def _expand_selections(raw: dict) -> list:
    """v1 (single) / v2 (array) 両形式を list of (sid, entry) に展開。"""
    items = []
    for sid, entry in raw.items():
        if "selections" in entry and isinstance(entry["selections"], list):
            for sel in entry["selections"]:
                items.append((sid, {**sel, "type": entry.get("type", "?")}))
        else:
            items.append((sid, entry))
    return items


def vectorize_and_save(sel_json_path: Path, out_root: Path) -> dict:
    """選定 JSON を読んで全 sketch_id を処理。"""
    from PIL import Image
    from modules.vectorizer import Vectorizer

    selections_raw = json.loads(sel_json_path.read_text())
    items = _expand_selections(selections_raw)
    print(f"[build] {len(items)} selections to process")
    # robot 描画向けに細かいストロークを抑制 (style-pool ブランチで確認した値)
    vec = Vectorizer(min_pixels=40, min_length=15, approx_epsilon=2.0)
    summary = {}

    for sid, entry in items:
        route = entry["route"]
        strokes_rel = entry["strokes_png_rel"]
        companion = entry.get("companion")
        type_ = entry.get("type", "?")
        gacha_seed = entry.get("gacha_seed")
        key = f"{sid}_{sanitize_route(route)}"
        if gacha_seed:
            key += f"_s{gacha_seed}"
        print(f"\n[{key}] route={route} (type={type_})")
        # 選定された 30_*.png から src_dir を逆算
        strokes_full = _ROOT / strokes_rel
        src_dir = strokes_full.parent
        if not src_dir.exists():
            print(f"  SKIP: src dir not found: {src_dir}")
            continue
        vec_src, user_img_path, gen_preview = source_png_for_route(route, sid, src_dir)
        if not vec_src.exists():
            print(f"  SKIP: vec source not found: {vec_src}")
            continue
        print(f"  vec source: {vec_src.relative_to(_ROOT)}")
        # shift / composition shift は cv2 blob 配置済の rendered PNG なので
        # Canny 経路 (両側エッジ) ではなく skeletonize 経路 (中心線) を使う。
        is_rendered = "shift" in route
        if is_rendered:
            res = vectorize_rendered_png(vec_src)
            n_strokes = res["n_strokes"]
            n_points = res["n_points"]
            image_shape = res["image_shape"]
            strokes_list = res["strokes"]
            total_length_px = res["total_length_px"]
            vis_png_arr = res["vis_png"]
            print(f"  [skeleton] strokes={n_strokes}, points={n_points}")
        else:
            gen_img = Image.open(vec_src).convert("RGB")
            if user_img_path is not None and user_img_path.exists():
                user_img = Image.open(user_img_path).convert("RGB")
                user_img = user_img.resize(gen_img.size)
            else:
                user_img = None
            result = vec.vectorize(generated_image=gen_img, user_image=user_img)
            n_strokes = result.n_strokes
            n_points = result.n_points
            image_shape = result.image_shape
            strokes_list = result.strokes
            total_length_px = result.total_length_px
            vis_png_arr = None
            print(f"  [canny] strokes={n_strokes}, points={n_points}")
        # 出力 dir (v2 array: key = <sid>_<route>[_s<seed>])
        out_dir = out_root / key
        out_dir.mkdir(parents=True, exist_ok=True)
        # strokes.json (test_vlm_to_image.py と同じ形式)
        strokes_payload = {
            "image_shape": list(image_shape),
            "n_strokes": n_strokes,
            "n_points": n_points,
            "total_length_px": total_length_px,
            "strokes": [
                [[float(x), float(y)] for (x, y) in stroke]
                for stroke in strokes_list
            ],
            "_meta": {
                "route": route,
                "type": type_,
                "companion": companion,
                "source_png": str(vec_src.relative_to(_ROOT)),
                "vectorize_method": "skeleton" if is_rendered else "canny",
                "selection_source": "draw_piper_selections.json",
            },
        }
        (out_dir / "strokes.json").write_text(
            json.dumps(strokes_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # generated.png (Stage 1 出力、 StrokePicker の左サムネ)
        gen_dest = out_dir / "generated.png"
        shutil.copy2(gen_preview, gen_dest)
        # vec_debug/06_strokes.png (StrokePicker の右サムネ)
        vec_debug = out_dir / "vec_debug"
        vec_debug.mkdir(exist_ok=True)
        if is_rendered and vis_png_arr is not None:
            # skeleton 中心線画像を保存 (元 PNG はアウトラインなので不可)
            import cv2 as _cv2
            _cv2.imwrite(str(vec_debug / "06_strokes.png"), vis_png_arr)
        else:
            strokes_png_src = _ROOT / strokes_rel
            if strokes_png_src.exists():
                shutil.copy2(strokes_png_src, vec_debug / "06_strokes.png")
        # input_sketch.jpg (StrokePicker のオプション表示)
        if sid in INPUT_PATHS:
            inp_src = _ROOT / INPUT_PATHS[sid]
            if inp_src.exists():
                shutil.copy2(inp_src, out_dir / "input_sketch.png")
        # topic_guess.json (StrokePicker の subject 表示用、 簡易)
        topic = {
            "subject": {"ja": sid, "en": companion or sid},
            "_route": route,
        }
        (out_dir / "topic_guess.json").write_text(
            json.dumps(topic, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        summary[key] = {
            "sketch_id": sid,
            "route": route,
            "gacha_seed": gacha_seed,
            "n_strokes": n_strokes,
            "n_points": n_points,
            "method": "skeleton" if is_rendered else "canny",
            "out_dir": str(out_dir.relative_to(_ROOT)),
        }
        print(f"  saved {out_dir.relative_to(_ROOT)}/")

    return summary


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--selection-json", type=Path, required=True,
                    help="draw_piper_selections.json (web app からダウンロード)")
    ap.add_argument("--out-root", type=Path, default=None,
                    help="logs/robot_input_set_<ts>/ default")
    args = ap.parse_args()

    if args.out_root is None:
        ts = time.strftime("%Y%m%d_%H%M%S")
        args.out_root = _ROOT / "logs" / f"robot_input_set_{ts}"
    else:
        args.out_root = Path(args.out_root).resolve()
    args.out_root.mkdir(parents=True, exist_ok=True)

    summary = vectorize_and_save(args.selection_json, args.out_root)
    (args.out_root / "_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n出力先: {args.out_root.relative_to(_ROOT)}")
    print(f"内訳: {len(summary)} 件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
