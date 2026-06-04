"""ルート共通ドライバ (2026-06-04)。

生成GUIの「ルート選択」対応で、 4 種の生成バックボーン (FLUX-decorate /
SDXL-routed / SDXL-text2img / IP-松本) が共有する処理をここに集約する。

ルート間で違うのは「crop画像 + prompt + seed → 生成PIL画像」の 1 ステップだけ
(= backend.generate_object_image)。 それ以外 ―― 入力分割・VLMビジョン・
OpenCV線抽出+vectorize・曲率制約・stroke順・warp補正・cycle_01契約出力 ―― は
全ルート共通なのでこの driver が担う。

backend protocol (modules/route_backends.py):
    name: str            ルートID
    route_label: str     topic_guess.json 用の説明
    multi_object: bool   connected-components 分割を使うか (False=全体1枚)
    uses_vlm: bool       VLMビジョンを使うか
    load() -> None
    build_prompt(vision: dict) -> str
    generate_object_image(crop_pil, prompt, seed) -> PIL.Image | None
    teardown() -> None
"""
from __future__ import annotations
import json, traceback
from pathlib import Path
import numpy as np, cv2, torch
from PIL import Image

ROOT = Path("/home/jizaiedev2026/draw_piper")
CW, CH, SIZE = 704, 1472, 1024

import yaml as _yaml_panel


def log(*a):
    print("[driver]", *a, flush=True)


def canny_ctrl(img):
    """FLUX ControlNet 用の canny ガイド (FLUX backend が使用)。"""
    g = np.array(img.convert("L")); e = cv2.dilate(cv2.Canny(g, 80, 160), np.ones((2, 2), np.uint8))
    return Image.fromarray(cv2.cvtColor(e, cv2.COLOR_GRAY2RGB))


def _panel_mm():
    """panel 実寸 (mm)。 曲率制約を mm 空間で評価するため。"""
    try:
        p = _yaml_panel.safe_load(open(ROOT / "calibration" / "panel_frame.yaml")) or {}
        pb = p.get("panel", p); return float(pb["size_mm"][0]), float(pb["size_mm"][1])
    except Exception:
        return 145.31, 264.41


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


def run_vlm(objs, args):
    """各被写体の完成形ビジョン (backend ロード前に VLM を回して解放)。

    design_instruction の mode は args.design_mode (decorate=元線維持+装飾 /
    complete=未来の完成形を積極デザイン / finish=ラフを完成イラスト化)。 既定 decorate。
    """
    mode = getattr(args, "design_mode", "decorate") or "decorate"
    log("VLM load (design_mode=%s)" % mode)
    from modules.vlm import VLM
    vlm = VLM(verbose=True); visions = []
    for i, (bbox, crop) in enumerate(objs):
        if getattr(args, "literal_only", False):
            sub = vlm.describe_literal(crop) or "subject"; vision = sub; scene = sub
        else:
            log("VLM predict_intent /scene")
            scene = vlm.describe_scene(crop) or "subject"
            vision = vlm.design_instruction(crop, scene, mode=mode) or scene
        visions.append({"scene": scene, "vision": vision}); log(f"obj{i} vision:", vision)
    del vlm; import gc; gc.collect(); torch.cuda.empty_cache()
    return visions


def run_driver(objs, backend, args, cyc, visions, W, H):
    """全ルート共通: per-obj 生成 → 線抽出+vectorize → 配置 → 曲率制約 → stroke順
    → warp補正 → cycle_01 契約ファイル出力。 backend.generate_object_image で生成方式が差し替わる。
    """
    from modules.vectorizer import Vectorizer, load_binarize_config
    from modules.gen_line_extract import extract_lines
    from modules.stroke_order import order_strokes_center_out, order_strokes_tsp_joined
    from modules.stroke_render import render_strokes_to_image
    from modules.robot_draw_constraints import enforce_robot_constraints

    V = max(0.1, args.vstretch)
    vc = Vectorizer(gen_line_mode="binarize", **load_binarize_config())
    SCALE = min(CW / W, CH / H); XOFF = (CW - W * SCALE) / 2; YOFF = (CH - H * SCALE) / 2
    multi = len(objs) > 1
    obj_lists = []; prompts = []; gen_imgs = []
    for i, (bbox, crop) in enumerate(objs):
        vision = visions[i] if i < len(visions) else {"scene": "", "vision": ""}
        prompt = backend.build_prompt(vision); prompts.append(prompt)
        log(f"imagegen generate obj{i} via {backend.name}")
        try:
            img = backend.generate_object_image(crop, prompt, args.seed)
        except Exception as e:
            log("FAIL gen", i, repr(e)); traceback.print_exc(); continue
        if img is None:
            log("skip obj", i, "(backend returned no image)"); continue
        gen_imgs.append((bbox, img))
        # 縦伸ばし補正: 生成画像の高さを V 倍にしてからストローク化 (配置はアスペクト
        # 保持なので伸びが維持され、 place_fill/contain が枠内に収め直す→はみ出し無し)。
        vimg = img if V == 1.0 else img.resize((img.width, max(1, round(img.height * V))), Image.LANCZOS)
        if getattr(backend, "diff_vs_user", False):
            # 入力線(顔+首等)は既にボード上 → diff で引き、 生成で加筆された分だけ抽出
            # (raw画像 + user_image diff。 robot_draws_only_additions 方針)。
            user_img = crop.convert("RGB").resize(vimg.size)
            log("vectorize (diff vs user, 加筆分のみ) obj%d (vstretch=%.2f)" % (i, V))
            st = vc.vectorize(generated_image=vimg, user_image=user_img).strokes
        else:
            log("vectorize / OpenCV line extract obj%d (vstretch=%.2f)" % (i, V))
            st = vc.vectorize(generated_image=extract_lines(vimg), user_image=None).strokes
        if multi:
            bx, by, bw, bh = bbox
            obj_lists.append(remap(st, bx * SCALE + XOFF, by * SCALE + YOFF, bw * SCALE, bh * SCALE))
        else:
            obj_lists.append(place_fill(st))

    # ロボット描画制約: 曲率半径>=8mm・微小ディテール/渦巻き/小円を除去 (mm 空間で評価)。
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
        combined = order_strokes_tsp_joined(obj_lists, (CW / 2.0, CH / 2.0), max_connect=80.0)
        log(f"one-stroke (TSP+長渡りペンアップ): {len(combined)} runs, {sum(len(s) for s in combined)} pts")
    else:
        combined = order_strokes_center_out(obj_lists, (CW / 2.0, CH / 2.0))
        log(f"stroke order: {len(combined)} strokes, center-out per-object")

    # 生成側ワープ補正: ストローク点でなく「画像」をワープしてから再ベクトル化 (滑らかで安定)。
    if args.warp_correct:
        from modules.draw_warp_correction import load_correction
        import yaml as _yaml
        corr = load_correction()
        if corr.enabled:
            _p = _yaml.safe_load(open(ROOT / "calibration" / "panel_frame.yaml")) or {}
            _pb = _p.get("panel", _p); pw = float(_pb["size_mm"][0]); ph = float(_pb["size_mm"][1])
            uc, vcen = pw / 2.0, ph / 2.0           # ★ vc(Vectorizer)を上書きしないよう vcen
            def _cmd_px(x, y):                       # desired px → command px (中心基準local)
                u = x * pw / CW; v = (CH - y) * ph / CH
                cl = corr.apply((u - uc, v - vcen)); cu, cv2v = cl[0] + uc, cl[1] + vcen
                return (cu * CW / pw, CH - cv2v * CH / ph)
            line_img = np.array(render_strokes_to_image(
                combined, width=CW, height=CH, line_width=2).convert("L"))
            d3 = np.float32([[CW * 0.3, CH * 0.3], [CW * 0.7, CH * 0.3], [CW * 0.5, CH * 0.7]])
            c3 = np.float32([_cmd_px(*p) for p in d3])
            Mfwd = cv2.getAffineTransform(d3, c3)    # desired→command の前進warp (検証済)
            warped = cv2.warpAffine(line_img, Mfwd, (CW, CH),
                                    flags=cv2.INTER_LINEAR, borderValue=255)
            combined = vc.vectorize(
                generated_image=Image.fromarray(warped).convert("RGB"), user_image=None).strokes
            _mm = [[(x * sx, y * sy) for x, y in st] for st in combined]
            _ce, _ = enforce_robot_constraints(_mm)  # warp 後も曲率制約を再保証
            combined = [[(x / sx, y / sy) for x, y in st] for st in _ce]
            if args.one_stroke:
                combined = order_strokes_tsp_joined([combined], (CW / 2.0, CH / 2.0), max_connect=80.0)
            else:
                combined = order_strokes_center_out([combined], (CW / 2.0, CH / 2.0))
            log(f"warp-correct (画像warp→再vectorize) applied, {len(combined)} strokes ({corr.summary()})")
        else:
            log("warp-correct 要求されたが correction disabled — 無補正")

    # --- 出力 (GUI 契約ファイル) ---
    render_strokes_to_image(combined, width=CW, height=CH, line_width=2).save(cyc / "vec_debug" / "06_strokes.png")
    # generated.png: 各被写体の生成画像を panel レイアウトへ合成
    # ★生成解像度は画像ごとに img.size を使う (FLUX=1024² / SDXL・IP=非正方 でも正しく配置)。
    canvas = Image.new("RGB", (CW, CH), (255, 255, 255))
    for bbox, img in gen_imgs:
        iw, ih = img.width, img.height
        if multi:
            bx, by, bw, bh = bbox; tw = int(bw * SCALE); th = int(bh * SCALE)
            sc = min(tw / iw, th / ih)
            rw, rh = max(1, int(iw * sc)), max(1, int(ih * sc))
            px = int(bx * SCALE + XOFF + (tw - rw) / 2); py = int(by * SCALE + YOFF + (th - rh) / 2)
            canvas.paste(img.resize((rw, rh)), (px, py))
        else:
            sc = min(CW * 0.92 / iw, CH * 0.92 / ih); rw = max(1, int(iw * sc)); rh = max(1, int(ih * sc))
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
               "route": backend.route_label, "visions": visions},
              open(cyc / "topic_guess.json", "w"), ensure_ascii=False, indent=2)
    log("DONE")
