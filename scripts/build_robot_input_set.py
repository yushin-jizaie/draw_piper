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


def source_png_for_route(route: str, sketch_id: str, src_dir: Path) -> tuple:
    """選択 route から Vectorize 元の PNG path と、 user_image (diff 用) を返す。

    Returns
    -------
    (vec_source_png, user_image_or_none, generated_preview_png)
    """
    if route.startswith("align"):
        # align (S2 OFF): Stage 1 inpaint 出力 (illustrious_v2_inpaint.png)
        stage1_png = src_dir / "stage1" / "illustrious_v2_inpaint.png"
        if not stage1_png.exists():
            stage1_png = src_dir / "20_final_no_ip_adapter.png"
        # diff 用 user_image は元入力 sketch (test_ip_adapter_two_stage の
        # category=character かつ skip_stage2 経路では user_image diff を使う)
        return (stage1_png, _ROOT / INPUT_PATHS[sketch_id], stage1_png)
    elif route.startswith("shift"):
        # shift: 最終合成済 PNG (30_companion_strokes.png) を再 Vectorize
        # cv2 blob で配置加工された 「input + transformed gen」 が黒線で描画されている
        comp_png = src_dir / "30_companion_strokes.png"
        # 合成 PNG は user_image=None で全 strokes を抽出
        return (comp_png, None, comp_png)
    else:
        raise ValueError(f"unknown route: {route}")


def vectorize_and_save(sel_json_path: Path, out_root: Path) -> dict:
    """選定 JSON を読んで全 sketch_id を処理。"""
    from PIL import Image
    from modules.vectorizer import Vectorizer

    selections = json.loads(sel_json_path.read_text())
    vec = Vectorizer()
    summary = {}

    for sid, entry in selections.items():
        route = entry["route"]
        strokes_rel = entry["strokes_png_rel"]
        companion = entry.get("companion")
        type_ = entry.get("type", "?")
        print(f"\n[{sid}] route={route} (type={type_})")
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
        gen_img = Image.open(vec_src).convert("RGB")
        if user_img_path is not None and user_img_path.exists():
            user_img = Image.open(user_img_path).convert("RGB")
            user_img = user_img.resize(gen_img.size)
        else:
            user_img = None
        result = vec.vectorize(generated_image=gen_img, user_image=user_img)
        print(f"  strokes={result.n_strokes}, points={result.n_points}")
        # 出力 dir
        out_dir = out_root / sid
        out_dir.mkdir(parents=True, exist_ok=True)
        # strokes.json (test_vlm_to_image.py と同じ形式)
        strokes_payload = {
            "image_shape": list(result.image_shape),
            "n_strokes": result.n_strokes,
            "n_points": result.n_points,
            "total_length_px": result.total_length_px,
            "strokes": [
                [[float(x), float(y)] for (x, y) in stroke]
                for stroke in result.strokes
            ],
            "_meta": {
                "route": route,
                "type": type_,
                "companion": companion,
                "source_png": str(vec_src.relative_to(_ROOT)),
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
        summary[sid] = {
            "route": route,
            "n_strokes": result.n_strokes,
            "n_points": result.n_points,
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
