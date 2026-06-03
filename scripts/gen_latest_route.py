"""最新ルート (M19) を GUI バックエンドとして実行する CLI。

pipeline_test_gui.py と同じ契約:
  入力: --sketch <img> --steps N --cycles 1 --log-dir <dir> [--seed S] [--literal-only]
  出力: <log-dir>/vlm_to_image_<ts>/cycle_01/ に
        topic_guess.json / prompt.txt / generated.png / strokes.json /
        vec_debug/{02a_user_binary.png,06_strokes.png}

M19 ルート: 入力を connected components で分割(複数被写体) → 各被写体を
FLUX.1-schnell + ControlNet(Union canny, CN0.2) + winners LoRA@0.6 +
VLM完成形ビジョン(describe_scene→design_instruction complete) + manga default style
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
CN_SCALE = 0.2; MINF = 8
# M19 default style 文
STYLE = ("manga style, clean bold ink lineart, white background, appealing design, multiple")

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
    args = ap.parse_args()

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    cyc = args.log_dir / f"vlm_to_image_{ts}" / "cycle_01"
    (cyc / "vec_debug").mkdir(parents=True, exist_ok=True)
    log("output:", cyc)

    inp = Image.open(args.sketch).convert("RGB")
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
            vision = vlm.design_instruction(crop, scene, mode="complete") or scene
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
    from modules.stroke_order import order_strokes_center_out
    vc = Vectorizer(gen_line_mode="binarize", **load_binarize_config())

    SCALE = min(CW / W, CH / H); XOFF = (CW - W * SCALE) / 2; YOFF = (CH - H * SCALE) / 2
    multi = len(objs) > 1
    obj_lists = []; prompts = []; gen_imgs = []
    for i, (bbox, crop) in enumerate(objs):
        vision = visions[i]["vision"]; prompt = f"{TRIGGER}, {vision} {STYLE}"; prompts.append(prompt)
        ctrl = canny_ctrl(square_pad(crop, SIZE))
        log(f"FLUX generate obj{i} (CN{CN_SCALE})")
        try:
            img = pipe(prompt=prompt, control_image=ctrl, control_mode=0,
                       controlnet_conditioning_scale=CN_SCALE, width=SIZE, height=SIZE,
                       num_inference_steps=4, guidance_scale=0.0,
                       generator=torch.Generator("cpu").manual_seed(args.seed)).images[0]
        except Exception as e:
            log("FAIL gen", i, repr(e)); traceback.print_exc(); continue
        gen_imgs.append((bbox, img))
        log("vectorize / OpenCV line extract obj%d" % i)
        st = vc.vectorize(generated_image=extract_lines(img), user_image=None).strokes
        if multi:
            bx, by, bw, bh = bbox
            obj_lists.append(remap(st, bx * SCALE + XOFF, by * SCALE + YOFF, bw * SCALE, bh * SCALE))
        else:
            obj_lists.append(place_fill(st))

    # 中心→外側・オブジェクト単位の描画順
    combined = order_strokes_center_out(obj_lists, (CW / 2.0, CH / 2.0))
    log(f"stroke order: {len(combined)} strokes, center-out per-object")

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
               "n_objects": len(objs), "route": "M19_latest (FLUX+winnersLoRA+complete+manga+opencv+center-out)",
               "visions": visions}, open(cyc / "topic_guess.json", "w"), ensure_ascii=False, indent=2)
    log("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
