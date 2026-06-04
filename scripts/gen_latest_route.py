"""最新ルート (M19) を GUI バックエンドとして実行する CLI。

pipeline_test_gui.py と同じ契約:
  入力: --sketch <img> --steps N --cycles 1 --log-dir <dir> [--seed S] [--literal-only]
  出力: <log-dir>/vlm_to_image_<ts>/cycle_01/ に
        topic_guess.json / prompt.txt / generated.png / strokes.json /
        vec_debug/{02a_user_binary.png,06_strokes.png}

M19 ルート: 入力を connected components で分割(複数被写体) → 各被写体を
FLUX.1-schnell + ControlNet(Union canny, CN0.55=DECORATEルート) + winners LoRA@0.6 +
VLM装飾ビジョン(describe_scene→design_instruction decorate) + manga default style
+ OpenCV線抽出 で生成 → 元レイアウトの各 bbox へ contain 配置(複数時) or panel fill
(単一時) → 中心→外側・オブジェクト単位の描画順に並べ替え。
"""
import argparse, datetime, json, sys, time, traceback
from pathlib import Path
import numpy as np, cv2, torch
from PIL import Image
ROOT = Path("/home/jizaiedev2026/draw_piper"); sys.path.insert(0, str(ROOT))
import os; os.chdir(ROOT)

CW, CH, SIZE = 704, 1472, 1024
LORA_DIR = "models/flux_lora_winners"; LORA_STR = 0.6; TRIGGER = "tklineart"
REPO = "chutesai/FLUX.1-schnell"; CN = "Shakker-Labs/FLUX.1-dev-ControlNet-Union-Pro"
# 2026-06-04: DECORATE ルートに戻す (complete+CN0.2 は被写体を作り替えすぎ=飛躍しすぎ)。
# decorate モード + CN0.55 で元線を保ちつつ装飾を足す。
CN_SCALE = 0.55; MINF = 8
# ロボットが物理的に描ける形に誘導: 太く大胆・滑らかな大曲線・微小ディテール/渦巻き/小円なし。
STYLE = ("manga style, clean bold black ink lineart on white background, "
         "thick smooth confident strokes, large gentle curves, simple bold shapes, "
         "no tiny details, no fine hatching, no spirals, no small concentric circles, "
         "no intricate texture")
# panel 実寸 (mm)。 曲率制約を mm 空間で評価するため。
import yaml as _yaml_panel
def _panel_mm():
    try:
        p = _yaml_panel.safe_load(open(ROOT / "calibration" / "panel_frame.yaml")) or {}
        pb = p.get("panel", p); return float(pb["size_mm"][0]), float(pb["size_mm"][1])
    except Exception:
        return 145.31, 264.41

def log(*a): print("[latest]", *a, flush=True)

def canny_ctrl(img):
    g = np.array(img.convert("L")); e = cv2.dilate(cv2.Canny(g, 80, 160), np.ones((2, 2), np.uint8))
    return Image.fromarray(cv2.cvtColor(e, cv2.COLOR_GRAY2RGB))

def split_objects(pil, pad=40, dil=35, min_area=5000):
    """入力を connected components で被写体ごとに分割。 [(bbox=(x,y,w,h), crop_pil), ...]
    を読み順(上→下,左→右)で返す。 1 つしか無ければ 1 要素。"""
    g = cv2.cvtColor(np.array(pil.convert("RGB")), cv2.COLOR_RGB2GRAY)
    H, W = g.shape
    binv = cv2.threshold(g, 200, 255, cv2.THRESH_BINARY_INV)[1]
    d = cv2.dilate(binv, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dil, dil)), iterations=2)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(d, 8)
    comps = [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] > min_area]
    comps.sort(key=lambda i: (stats[i, cv2.CC_STAT_TOP] // 200, stats[i, cv2.CC_STAT_LEFT]))
    out = []
    for i in comps:
        x, y, w, h = stats[i, cv2.CC_STAT_LEFT], stats[i, cv2.CC_STAT_TOP], stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]
        x0, y0 = max(0, x - pad), max(0, y - pad); x1, y1 = min(W, x + w + pad), min(H, y + h + pad)
        crop = Image.fromarray(255 - binv[y0:y1, x0:x1]).convert("RGB")
        out.append(((x, y, w, h), crop))
    if not out:                                  # fallback: 全体を 1 被写体
        out = [((0, 0, W, H), pil.convert("RGB"))]
    return out, (W, H)

def remap(strokes, bx, by, bw, bh):
    pts = [p for st in strokes for p in st]
    if not pts: return []
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    sx0, sy0, sx1, sy1 = min(xs), min(ys), max(xs), max(ys)
    sw, sh = max(sx1 - sx0, 1), max(sy1 - sy0, 1)
    s = min(bw / sw, bh / sh); ox = bx + (bw - sw * s) / 2 - sx0 * s; oy = by + (bh - sh * s) / 2 - sy0 * s
    return [[(x * s + ox, y * s + oy) for x, y in st] for st in strokes]

def place_fill(strokes, margin=0.92):
    pts = [p for st in strokes for p in st]
    if not pts: return []
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys); bw, bh = max(x1 - x0, 1), max(y1 - y0, 1)
    s = min(CW * margin / bw, CH * margin / bh); ox = (CW - bw * s) / 2 - x0 * s; oy = (CH - bh * s) / 2 - y0 * s
    return [[(x * s + ox, y * s + oy) for x, y in st] for st in strokes]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sketch", type=Path, required=True)
    ap.add_argument("--steps", type=int, default=4)        # FLUX schnell は 4 step 固定
    ap.add_argument("--cycles", type=int, default=1)       # 互換のため受けるが 1 のみ
    ap.add_argument("--log-dir", type=Path, default=ROOT / "logs")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--literal-only", action="store_true")  # 互換: literal subject を使う
    # 縦伸ばし比率: ロボット側の縦潰れ/横伸びの応急補正。 生成画像を縦に V 倍に
    # 引き伸ばしてからストローク化する (1.0=補正なし)。
    ap.add_argument("--vstretch", type=float, default=1.0)
    # --no-split: 複数被写体に分割せず、 入力全体を 1 枚絵として生成 (1オブジェクト扱い)。
    ap.add_argument("--no-split", action="store_true")
    # --warp-correct: ロボット歪みの事前補正を「生成側」で適用 (アーム側補正が効かない場合)。
    # calibration/draw_warp_correction.yaml の affine(desired->command)を最終ストロークに適用。
    ap.add_argument("--warp-correct", action="store_true")
    # --one-stroke: 全ストロークを 1 本に連結 (一筆書き、 ペンを上げない連続描画)。
    # ストローク間は直線コネクタで繋がる。
    ap.add_argument("--one-stroke", action="store_true")
    args = ap.parse_args()
    V = max(0.1, args.vstretch)

    # SDXL/プロンプト設定ダイアログ (imagegen_config.yaml) のうち、 FLUX schnell で
    # 実際に効く項目だけ反映する: controlnet_conditioning_scale と num_inference_steps。
    # preset/guidance/negative/prompt_template は schnell では無効なので読まない
    # (guidance は 0 固定、 negative は無視、 prompt は VLM 完成形ビジョン路線を維持)。
    cn_scale = CN_SCALE; steps = max(1, int(args.steps))
    try:
        from modules.image_gen import load_imagegen_config
        _ig = load_imagegen_config()
        if _ig.get("controlnet_conditioning_scale") is not None:
            cn_scale = float(_ig["controlnet_conditioning_scale"])
        if _ig.get("num_inference_steps"):
            steps = max(1, min(50, int(_ig["num_inference_steps"])))
        log(f"imagegen_config 反映: CN_scale={cn_scale:.2f} steps={steps} "
            f"(FLUXで有効な項目のみ; preset/guidance/negative/promptテンプレは無効)")
    except Exception as e:
        log(f"imagegen_config 読込スキップ ({e}) — CN_scale={cn_scale:.2f} steps={steps} 既定")

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    cyc = args.log_dir / f"vlm_to_image_{ts}" / "cycle_01"
    (cyc / "vec_debug").mkdir(parents=True, exist_ok=True)
    log("output:", cyc)

    inp = Image.open(args.sketch).convert("RGB")
    if args.no_split:
        W, H = inp.size; objs = [((0, 0, W, H), inp.convert("RGB"))]
        log(f"no-split: 入力全体を1枚絵として生成, input {W}x{H}")
    else:
        objs, (W, H) = split_objects(inp)
        log(f"split: {len(objs)} object(s), input {W}x{H}")
    # user binary (vec_debug 表示用)
    ug = cv2.cvtColor(np.array(inp), cv2.COLOR_RGB2GRAY)
    Image.fromarray(cv2.threshold(ug, 200, 255, cv2.THRESH_BINARY)[1]).save(cyc / "vec_debug" / "02a_user_binary.png")

    # --- VLM: 各被写体の完成形ビジョン (FLUX ロード前に) ---
    log("VLM load")
    from modules.vlm import VLM
    vlm = VLM(verbose=True); visions = []
    for i, (bbox, crop) in enumerate(objs):
        if args.literal_only:
            sub = vlm.describe_literal(crop) or "subject"; vision = sub; scene = sub
        else:
            log("VLM predict_intent /scene")
            scene = vlm.describe_scene(crop) or "subject"
            vision = vlm.design_instruction(crop, scene, mode="decorate") or scene
        visions.append({"scene": scene, "vision": vision}); log(f"obj{i} vision:", vision)
    del vlm; import gc; gc.collect(); torch.cuda.empty_cache()

    # --- FLUX + ControlNet + winners LoRA ---
    log("FLUX/imagegen load")
    from diffusers import (FluxControlNetModel, FluxControlNetPipeline,
                           FluxTransformer2DModel, BitsAndBytesConfig as DBNB)
    from transformers import T5EncoderModel, BitsAndBytesConfig as TBNB
    dnf4 = DBNB(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.bfloat16)
    tnf4 = TBNB(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.bfloat16)
    tr = FluxTransformer2DModel.from_pretrained(REPO, subfolder="transformer", quantization_config=dnf4, torch_dtype=torch.bfloat16)
    te2 = T5EncoderModel.from_pretrained(REPO, subfolder="text_encoder_2", quantization_config=tnf4, torch_dtype=torch.bfloat16)
    cnet = FluxControlNetModel.from_pretrained(CN, torch_dtype=torch.bfloat16)
    pipe = FluxControlNetPipeline.from_pretrained(REPO, transformer=tr, text_encoder_2=te2, controlnet=cnet, torch_dtype=torch.bfloat16)
    pipe.load_lora_weights(LORA_DIR, adapter_name="winners"); pipe.set_adapters(["winners"], [LORA_STR])
    pipe.enable_model_cpu_offload(); log("FLUX ready")
    from modules.input_prep import square_pad
    from modules.vectorizer import Vectorizer, load_binarize_config
    from modules.gen_line_extract import extract_lines
    from modules.stroke_order import order_strokes_center_out, order_strokes_tsp_joined
    vc = Vectorizer(gen_line_mode="binarize", **load_binarize_config())

    SCALE = min(CW / W, CH / H); XOFF = (CW - W * SCALE) / 2; YOFF = (CH - H * SCALE) / 2
    multi = len(objs) > 1
    obj_lists = []; prompts = []; gen_imgs = []
    for i, (bbox, crop) in enumerate(objs):
        vision = visions[i]["vision"]; prompt = f"{TRIGGER}, {vision} {STYLE}"; prompts.append(prompt)
        ctrl = canny_ctrl(square_pad(crop, SIZE))
        log(f"FLUX generate obj{i} (CN{cn_scale:.2f}, steps{steps})")
        try:
            img = pipe(prompt=prompt, control_image=ctrl, control_mode=0,
                       controlnet_conditioning_scale=cn_scale, width=SIZE, height=SIZE,
                       num_inference_steps=steps, guidance_scale=0.0,
                       generator=torch.Generator("cpu").manual_seed(args.seed)).images[0]
        except Exception as e:
            log("FAIL gen", i, repr(e)); traceback.print_exc(); continue
        gen_imgs.append((bbox, img))
        # 縦伸ばし補正: 生成画像の高さを V 倍にしてからストローク化 (配置はアスペクト
        # 保持なので伸びが維持され、 place_fill/contain が枠内に収め直す→はみ出し無し)。
        vimg = img if V == 1.0 else img.resize((img.width, max(1, round(img.height * V))), Image.LANCZOS)
        log("vectorize / OpenCV line extract obj%d (vstretch=%.2f)" % (i, V))
        st = vc.vectorize(generated_image=extract_lines(vimg), user_image=None).strokes
        if multi:
            bx, by, bw, bh = bbox
            obj_lists.append(remap(st, bx * SCALE + XOFF, by * SCALE + YOFF, bw * SCALE, bh * SCALE))
        else:
            obj_lists.append(place_fill(st))

    # ロボット描画制約: 曲率半径>=8mm・微小ディテール/渦巻き/小円を除去 (mm 空間で評価)。
    # 並べ替え/一筆書き連結の前に、 オブジェクト単位で清掃する。
    from modules.robot_draw_constraints import enforce_robot_constraints
    PW, PH = _panel_mm(); sx = PW / CW; sy = PH / CH
    cleaned = []; tot = {"dropped_tiny": 0, "dropped_loop": 0, "dropped_spiral": 0, "dropped_kinky": 0}
    for ol in obj_lists:
        mm = [[(x * sx, y * sy) for x, y in st] for st in ol]
        ce, info = enforce_robot_constraints(mm)
        for k in tot: tot[k] += info.get(k, 0)
        cleaned.append([[(x / sx, y / sy) for x, y in st] for st in ce])
    obj_lists = [o for o in cleaned if o]
    log(f"robot constraints: kept {sum(len(o) for o in obj_lists)} strokes, "
        f"dropped tiny={tot['dropped_tiny']} loop={tot['dropped_loop']} "
        f"spiral={tot['dropped_spiral']} kinky={tot['dropped_kinky']} (>=8mm radius)")

    # 中心→外側・オブジェクト単位の描画順
    if args.one_stroke:
        # ほぼ一筆書き: TSP で渡り最小化 → 短い渡りは連結・長い渡りはペンアップ
        combined = order_strokes_tsp_joined(obj_lists, (CW / 2.0, CH / 2.0), max_connect=80.0)
        log(f"one-stroke (TSP+長渡りペンアップ): {len(combined)} runs, {sum(len(s) for s in combined)} pts")
    else:
        combined = order_strokes_center_out(obj_lists, (CW / 2.0, CH / 2.0))
        log(f"stroke order: {len(combined)} strokes, center-out per-object")

    # 生成側ワープ補正: ★ストローク点でなく「画像」をワープしてから再ベクトル化する
    # (点warpは不連続になりやすい→画像warp+再vectorizeの方が滑らかで安定, ユーザー指定)。
    # combined を 704x1472 線画にレンダ → affine(desired->command)で画像warp → 再vectorize。
    if args.warp_correct:
        from modules.draw_warp_correction import load_correction
        import yaml as _yaml
        corr = load_correction()
        if corr.enabled:
            _p = _yaml.safe_load(open(ROOT / "calibration" / "panel_frame.yaml")) or {}
            _pb = _p.get("panel", _p); pw = float(_pb["size_mm"][0]); ph = float(_pb["size_mm"][1])
            uc, vc = pw / 2.0, ph / 2.0
            def _cmd_px(x, y):                                   # desired px → command px (中心基準local)
                u = x * pw / CW; v = (CH - y) * ph / CH
                cl = corr.apply((u - uc, v - vc)); cu, cv = cl[0] + uc, cl[1] + vc
                return (cu * CW / pw, CH - cv * CH / ph)
            line_img = np.array(render_strokes_to_image(
                combined, width=CW, height=CH, line_width=2).convert("L"))
            d3 = np.float32([[CW * 0.3, CH * 0.3], [CW * 0.7, CH * 0.3], [CW * 0.5, CH * 0.7]])
            c3 = np.float32([_cmd_px(*p) for p in d3])
            Mfwd = cv2.getAffineTransform(d3, c3)                # desired→command の前進warp (検証済)
            warped = cv2.warpAffine(line_img, Mfwd, (CW, CH),
                                    flags=cv2.INTER_LINEAR, borderValue=255)
            combined = vc.vectorize(
                generated_image=Image.fromarray(warped).convert("RGB"), user_image=None).strokes
            _mm = [[(x * sx, y * sy) for x, y in st] for st in combined]
            _ce, _ = enforce_robot_constraints(_mm)            # warp 後も曲率制約を再保証
            combined = [[(x / sx, y / sy) for x, y in st] for st in _ce]
            if args.one_stroke:
                combined = order_strokes_tsp_joined([combined], (CW / 2.0, CH / 2.0), max_connect=80.0)
            else:
                combined = order_strokes_center_out([combined], (CW / 2.0, CH / 2.0))
            log(f"warp-correct (画像warp→再vectorize) applied, {len(combined)} strokes ({corr.summary()})")
        else:
            log("warp-correct 要求されたが correction disabled — 無補正")

    # --- 出力 (GUI 契約ファイル) ---
    from modules.stroke_render import render_strokes_to_image
    render_strokes_to_image(combined, width=CW, height=CH, line_width=2).save(cyc / "vec_debug" / "06_strokes.png")
    # generated.png: 各被写体の生成画像を panel レイアウトへ合成
    canvas = Image.new("RGB", (CW, CH), (255, 255, 255))
    for bbox, img in gen_imgs:
        if multi:
            bx, by, bw, bh = bbox; tw = int(bw * SCALE); th = int(bh * SCALE)
            sc = min(tw / SIZE, th / SIZE)
            rw, rh = max(1, int(SIZE * sc)), max(1, int(SIZE * sc))
            px = int(bx * SCALE + XOFF + (tw - rw) / 2); py = int(by * SCALE + YOFF + (th - rh) / 2)
            canvas.paste(img.resize((rw, rh)), (px, py))
        else:
            sc = min(CW * 0.92 / SIZE, CH * 0.92 / SIZE); rw = int(SIZE * sc); rh = int(SIZE * sc)
            canvas.paste(img.resize((rw, rh)), ((CW - rw) // 2, (CH - rh) // 2))
    canvas.save(cyc / "generated.png")
    # 壁面描画 GUI のローダ互換: image_shape=[H,W]=[CH,CW] が必須
    json.dump({"image_shape": [CH, CW], "n_strokes": len(combined),
               "n_points": sum(len(s) for s in combined), "strokes": combined},
              open(cyc / "strokes.json", "w"))
    (cyc / "prompt.txt").write_text("\n\n".join(prompts), encoding="utf-8")
    sc0 = visions[0]["scene"] if visions else "?"
    json.dump({"subject": {"ja": sc0, "en": sc0}, "location": {"ja": ""},
               "action": {"ja": ""}, "confidence": 1.0,
               "n_objects": len(objs), "vstretch": V, "warp_correct": bool(args.warp_correct),
               "route": "DECORATE (FLUX+winnersLoRA+decorate+CN0.55+manga+opencv+center-out)",
               "visions": visions}, open(cyc / "topic_guess.json", "w"), ensure_ascii=False, indent=2)
    log("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
