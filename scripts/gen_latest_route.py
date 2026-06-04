"""ルート選択対応の生成ディスパッチャ (GUI バックエンド)。

pipeline_test_gui.py と同じ契約:
  入力: --sketch <img> --steps N --cycles 1 --log-dir <dir> [--seed S] [--literal-only]
        [--vstretch V] [--no-split] [--warp-correct] [--one-stroke]
        [--route flux_decorate|sdxl_routed|sdxl_text2img|ip_matsumoto]
        [--category character|object|other] [--style-ref <img>]   (IP用)
  出力: <log-dir>/vlm_to_image_<ts>/cycle_01/ に
        topic_guess.json / prompt.txt / generated.png / strokes.json /
        vec_debug/{02a_user_binary.png,06_strokes.png}

ルート間で違うのは「crop + prompt + seed → 生成画像」の 1 ステップのみ。
入力分割・VLMビジョン・線抽出+vectorize・曲率制約・stroke順・warp・出力は
modules/route_driver が共通で担い、 生成方式は modules/route_backends が差し替える。
"""
import argparse, datetime, sys
from pathlib import Path
import cv2, numpy as np
from PIL import Image
ROOT = Path("/home/jizaiedev2026/draw_piper"); sys.path.insert(0, str(ROOT))
import os; os.chdir(ROOT)

from modules.route_driver import log, split_objects, run_vlm, run_driver
from modules.route_backends import make_backend


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sketch", type=Path, required=True)
    ap.add_argument("--steps", type=int, default=4)        # FLUX schnell は 4 step 固定
    ap.add_argument("--cycles", type=int, default=1)       # 互換のため受けるが 1 のみ
    ap.add_argument("--log-dir", type=Path, default=ROOT / "logs")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--literal-only", action="store_true")  # literal subject を使う
    ap.add_argument("--vstretch", type=float, default=1.0)
    ap.add_argument("--no-split", action="store_true")
    ap.add_argument("--warp-correct", action="store_true")
    ap.add_argument("--one-stroke", action="store_true")
    # ルート選択 (生成バックボーン)。 default は現行 FLUX-decorate。
    ap.add_argument("--route", type=str, default="flux_decorate",
                    choices=["flux_decorate", "sdxl_routed", "sdxl_text2img", "ip_matsumoto"])
    ap.add_argument("--category", type=str, default="character",
                    choices=["character", "object", "other"])  # IP-松本の参照画風プール
    ap.add_argument("--style-ref", type=Path, default=None)     # IP-松本のスタイル参照(任意)
    args = ap.parse_args()

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    cyc = args.log_dir / f"vlm_to_image_{ts}" / "cycle_01"
    (cyc / "vec_debug").mkdir(parents=True, exist_ok=True)
    log("output:", cyc, "route:", args.route)

    backend = make_backend(args.route, args)

    inp = Image.open(args.sketch).convert("RGB")
    if args.no_split or not backend.multi_object:
        W, H = inp.size; objs = [((0, 0, W, H), inp.convert("RGB"))]
        log(f"single object (no-split or backend single): input {W}x{H}")
    else:
        objs, (W, H) = split_objects(inp)
        log(f"split: {len(objs)} object(s), input {W}x{H}")

    # user binary (vec_debug 表示用)
    ug = cv2.cvtColor(np.array(inp), cv2.COLOR_RGB2GRAY)
    Image.fromarray(cv2.threshold(ug, 200, 255, cv2.THRESH_BINARY)[1]).save(
        cyc / "vec_debug" / "02a_user_binary.png")

    if backend.uses_vlm:
        visions = run_vlm(objs, args)
    else:
        visions = [{"scene": "", "vision": ""} for _ in objs]

    backend.load()
    run_driver(objs, backend, args, cyc, visions, W, H)
    backend.teardown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
