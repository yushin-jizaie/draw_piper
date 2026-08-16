#!/usr/bin/env python3
"""GUI で生成した結果 (cycle_dir) を選定 webapp の候補としてアップロードする。

pipeline_test_gui の「webapp にアップロード」 ボタンから呼ばれる想定だが、
CLI 単体でも使える。 各アップロードは独立した sketch_id 列になる
(disp_gui_uploads/<sid>/<vN_ts>/)。

処理:
  1. cycle_dir から generated.png / strokes.json を取得
  2. disp_gui_uploads/<sid>/v1_<ts>/ に配置:
       30_vectorized_strokes.png (strokes を描画)
       strokes.json / generated.png / 00_auto_prompt.txt
       vec_debug/06_strokes.png (skeleton)
  3. 入力画像を _inputs/<sid>.png に保存し、 disp_gui_uploads/_inputs.json に
     {"sid","input","kind"} を追記 (build_selection_webapp がマージ)
  4. webapp を再ビルド:
       --local  → docs/selection/index.html をローカル相対パスで (push なし)
       --push   → online (RAW URL) で再ビルド + git add/commit/push

使い方:
  ./venv/bin/python -m scripts.upload_to_webapp \\
      --cycle logs/vlm_to_image_XXXX/cycle_YYYY --label camera1 --local
  ./venv/bin/python -m scripts.upload_to_webapp --cycle ... --label camera1 --push
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

UPLOAD_BASE = _ROOT / "sketch_variations" / "disp_gui_uploads"
INPUTS_JSON = UPLOAD_BASE / "_inputs.json"
INPUTS_DIR = _ROOT / "sketch_variations" / "_inputs"


def _slug(s: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in s).strip("_").lower() or "upload"


def _append_input(sid: str, input_rel: str, kind: str) -> None:
    data = []
    if INPUTS_JSON.exists():
        try:
            data = json.loads(INPUTS_JSON.read_text())
        except Exception:
            data = []
    data = [e for e in data if e.get("sid") != sid]   # 同 sid は置換
    data.append({"sid": sid, "input": input_rel, "kind": kind})
    INPUTS_JSON.parent.mkdir(parents=True, exist_ok=True)
    INPUTS_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def upload(cycle_dir: Path, label: str, kind: str, timestamp: str,
           input_image: Path | None = None) -> Path:
    """cycle_dir を disp_gui_uploads に取り込み、 disp dir path を返す。"""
    import numpy as np
    import cv2
    from PIL import Image
    from modules.stroke_render import render_strokes_to_image
    from modules.vectorizer import _skeletonize

    strokes_json = cycle_dir / "strokes.json"
    if not strokes_json.exists():
        raise FileNotFoundError(f"strokes.json が無い: {strokes_json}")
    data = json.loads(strokes_json.read_text())
    strokes = data.get("strokes", [])
    shape = data.get("image_shape", [1472, 704])
    CH = shape[0] if len(shape) > 0 else 1472
    CW = shape[1] if len(shape) > 1 else 704

    sid = f"up_{_slug(label)}_{timestamp}"
    out = UPLOAD_BASE / sid / "v1_seed"
    (out / "vec_debug").mkdir(parents=True, exist_ok=True)

    poly = [[(float(x), float(y)) for x, y in st] for st in strokes]
    render_strokes_to_image(poly, width=CW, height=CH, line_width=2
                            ).save(out / "30_vectorized_strokes.png")
    (out / "strokes.json").write_text(json.dumps(data, ensure_ascii=False))
    gen = cycle_dir / "generated.png"
    if gen.exists():
        Image.open(gen).convert("RGB").save(out / "generated.png")
    # skeleton
    arr = np.array(render_strokes_to_image(
        poly, width=CW, height=CH, line_width=2).convert("L"))
    m = (arr < 128).astype(np.uint8)
    canvas = np.full(arr.shape, 255, np.uint8)
    if m.sum() > 0:
        canvas[cv2.dilate(_skeletonize(m).astype(np.uint8),
                          np.ones((2, 2), np.uint8)) > 0] = 0
    Image.fromarray(canvas).save(out / "vec_debug" / "06_strokes.png")
    (out / "00_auto_prompt.txt").write_text(
        f"source=gui_upload\nlabel={label}\ncycle={cycle_dir}\n")

    # 入力画像を _inputs/<sid>.png に保存 (webapp の 元 overlay 参照用)
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    in_dst = INPUTS_DIR / f"{sid}.png"
    src = input_image or (cycle_dir / "vec_debug" / "00_user_input.png")
    if src and Path(src).exists():
        Image.open(src).convert("RGB").save(in_dst)
    else:   # fallback: 生成画像を入力代わりに
        Image.open(out / "30_vectorized_strokes.png").save(in_dst)
    _append_input(sid, f"sketch_variations/_inputs/{sid}.png", kind)
    print(f"[upload] {sid} → {out}")
    return out


def _rebuild(local: bool) -> None:
    cmd = [sys.executable, "-m", "scripts.build_selection_webapp"]
    if local:
        cmd.append("--local")
    subprocess.run(cmd, cwd=_ROOT, check=True)


def _git_push(sid: str) -> None:
    paths = ["docs/selection/index.html",
             "sketch_variations/disp_gui_uploads/",
             "sketch_variations/_inputs/"]
    subprocess.run(["git", "add", *paths], cwd=_ROOT, check=True)
    subprocess.run(["git", "commit", "-q", "-m",
                    f"webapp: GUI 生成をアップロード ({sid})"], cwd=_ROOT, check=True)
    subprocess.run(["git", "push"], cwd=_ROOT, check=True)
    print("[upload] pushed (online webapp 反映まで ~1 分)")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cycle", type=Path, required=True, help="cycle_dir")
    ap.add_argument("--label", type=str, default="upload",
                    help="候補の表示名 (sketch_id に使う)")
    ap.add_argument("--kind", type=str, default="object",
                    choices=["object", "character"])
    ap.add_argument("--input", type=Path, default=None,
                    help="入力画像 (省略時 cycle の 00_user_input.png)")
    ap.add_argument("--timestamp", type=str, default=None,
                    help="再現用に固定 (省略時 cycle 名から、 無ければ now)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--local", action="store_true",
                   help="ローカル相対パスで再ビルド (push なし)")
    g.add_argument("--push", action="store_true",
                   help="online で再ビルド + git push")
    args = ap.parse_args()

    if not args.cycle.exists():
        ap.error(f"cycle_dir が無い: {args.cycle}")
    ts = args.timestamp or args.cycle.name.replace("cycle_", "") or "000000"
    out = upload(args.cycle, args.label, args.kind, ts, args.input)
    _rebuild(local=not args.push)
    if args.push:
        _git_push(out.parent.name)
    else:
        print("[upload] ローカル再ビルド完了。 "
              "`scripts/webapp_local` で確認できます。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
