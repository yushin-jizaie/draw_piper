#!/usr/bin/env python3
"""占有率ルーティング + 勝ち筋レシピ (lineart_char cn0.5) の統合生成パイプライン。

2026-06-01 検証で確定した方針:
  - 入力の被写体占有率で route を自動選択 (modules.input_router)
  - 共通レシピ: illustrious_v2_lineart_char + controlnet_conditioning_scale=0.5
    (高CN は忠実トレースで退屈、 cn0.5 がポーズ保持しつつキャラ化する転換点)
  - stylize (いっぱいの入力): 縦長のまま単一キャラにスタイル化 → 位置保持
  - scatter (余白の多い入力): 縦長生成は自然にスプライト化するので、 それを
    個々に分割してユーザーの線の周囲 (空白) に散布 → 位置保持 + 空白充填

出力は webapp disp pattern-2 構成。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

CN_SCALE = 0.5
PRESET = "illustrious_v2_lineart_char"
DEFAULT_SEEDS = [123, 7, 555]
# framed は旧 stage1_lora02 で良好だった seed を使う (object_mt が単一被写体を
# 出しやすい当たり seed)。
FRAMED_SEEDS = [59628, 19093, 60231]

# framed (旧 stage1_lora02 再現) の negative。 matsumoto LoRA が誘発する
# テキスト/署名/枠/網点を抑制 (旧ランと同じ)。
FRAMED_NEGATIVE = (
    "color, colored, blue background, cyan, sky, gradient, hatching, crosshatch, "
    "screentone, halftone, dot pattern, filled background, paper texture, scribble, "
    "sketchy, shading, gray, sepia, watermark, signature, text, letters, words, "
    "frame, border, blurry, noise, jpeg artifacts")

# stylize はカテゴリ別テンプレ (入力はキャラとは限らない: 動物・オブジェクトも有り)。
#   person → ポーズ重視 / animal → 躍動重視 / object → 構図・デザイン重視
STYLIZE_TEMPLATES = {
    "person": ("{subj}, manga character, dynamic confident pose, expressive face, "
               "clean bold ink lineart, white background, appealing character design"),
    "animal": ("{subj}, manga style, dynamic lively pose, expressive, "
               "clean bold ink lineart, white background, appealing design"),
    "object": ("stylish {subj}, manga style illustration, appealing bold design, "
               "interesting angle, dynamic composition, clean bold ink lineart, "
               "white background"),
}
# scatter で撒く対象の sprite 生成 prompt (撒く subject を差し込む)。
# 人/動物/物いずれも来るので中立 (pose/expressive 等のキャラ語は入れない)。
SCATTER_PROMPT = ("{subj}, manga style, clean bold ink lineart, white background, "
                  "appealing design, multiple")

# framed (正方形パディング→正方形生成→縦長中央配置): 横長/コンパクト被写体用。
# 旧 align/gacha と同じ 768 正方形 + 被写体を拡大しない (square_pad) で同品質に。
FRAMED_SIZE = 768
# framed は旧 stage1_lora02 (2026-05-29) の良かった単一ステージ設定を再現する:
# _mt プリセット (matsumoto LoRA 0.2) + 「Matsumoto-style」 prompt。 IP-Adapter なし。
# (ユーザー評価: その時の値・プロンプトが良い。 正方形入力なら同結果になるはず)
FRAMED_PRESET = {
    "object": "illustrious_v2_object_mt",   # text2img + CN0.65 + matsumoto LoRA0.2
    "animal": "illustrious_v2_object_mt",
    "person": "illustrious_v2_inpaint_mt",  # inpaint + CN0.85 + matsumoto LoRA0.2
}
FRAMED_PROMPT = {
    "object": ("a detailed Matsumoto-style {subj}, mt_taiyo_style, manga style, "
               "ink lineart, single continuous black line on plain white "
               "background, clean smooth strokes, no shading"),
    "animal": ("a detailed Matsumoto-style {subj}, mt_taiyo_style, manga style, "
               "ink lineart, single continuous black line on plain white "
               "background, clean smooth strokes, no shading"),
    "person": ("a detailed Matsumoto-style {subj}, mt_taiyo_style, manga style, "
               "dynamic pose, ink lineart, single continuous black line on plain "
               "white background, clean smooth strokes, no shading"),
}


def _save_candidate(out_dir, strokes, CW, CH, generated=None, meta=None):
    import numpy as np
    import cv2
    from PIL import Image
    from modules.stroke_render import render_strokes_to_image
    from modules.vectorizer import _skeletonize
    out_dir.mkdir(parents=True, exist_ok=True)
    if meta is not None:
        (out_dir / "00_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2))
    (out_dir / "vec_debug").mkdir(exist_ok=True)
    poly = [[(float(x), float(y)) for x, y in st] for st in strokes]
    render_strokes_to_image(poly, width=CW, height=CH, line_width=2
                            ).save(out_dir / "30_vectorized_strokes.png")
    (out_dir / "strokes.json").write_text(json.dumps({
        "image_shape": [CH, CW], "n_strokes": len(strokes),
        "n_points": sum(len(s) for s in strokes),
        "strokes": [[[float(x), float(y)] for x, y in st] for st in strokes]}))
    if generated is not None:
        generated.save(out_dir / "generated.png")
    arr = np.array(render_strokes_to_image(
        poly, width=CW, height=CH, line_width=2).convert("L"))
    m = (arr < 128).astype(np.uint8)
    canvas = np.full(arr.shape, 255, np.uint8)
    if m.sum() > 0:
        canvas[cv2.dilate(_skeletonize(m).astype(np.uint8),
                          np.ones((2, 2), np.uint8)) > 0] = 0
    Image.fromarray(canvas).save(out_dir / "vec_debug" / "06_strokes.png")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--sid", type=str, required=True)
    ap.add_argument("--subject", type=str, default=None,
                    help="prompt に差し込む被写体語 (例: cat, 1boy)。 "
                         "省略時は VLM describe_literal で自動推論。")
    ap.add_argument("--category", choices=["person", "animal", "object"],
                    default=None,
                    help="stylize テンプレ選択。 省略時は VLM classify_category。")
    ap.add_argument("--scatter-mode", choices=["same", "assoc"], default="same",
                    help="scatter で撒く対象: same=入力と同じ被写体 / "
                         "assoc=VLM 連想の別の関連物 (猫→魚 等)")
    ap.add_argument("--output-base", type=Path, required=True,
                    help="disp dir base (この下に <sid>/vN_seedS/)")
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--resolution", type=str, default="704x1472")
    ap.add_argument("--force-route", choices=["stylize", "framed", "companion"], default=None)
    args = ap.parse_args()

    import numpy as np
    from PIL import Image
    from modules.image_gen import ImageGenerator
    from modules.vectorizer import Vectorizer, load_binarize_config
    from modules.input_router import decide_route
    from modules.stroke_scatter import scatter_companions
    from modules.blob_detect import detect_blobs, union_bbox
    from modules.panel_geometry import parse_resolution

    CW, CH = parse_resolution(args.resolution) or (704, 1472)
    cfg = load_binarize_config()
    inp = Image.open(args.input).convert("RGB")
    route = args.force_route or decide_route(inp).route

    # --- VLM 推論 (subject / category / 連想 companion) を必要時のみ ---
    subject, category = args.subject, args.category
    assoc_subject = None
    need_vlm = (subject is None
                or (route in ("stylize", "framed") and category is None)
                or (route == "companion" and args.scatter_mode == "assoc"))
    if need_vlm:
        from modules.vlm import VLM
        vlm = VLM(verbose=True)
        if subject is None:
            subject = vlm.describe_literal(inp) or "subject"
        if route in ("stylize", "framed") and category is None:
            category = vlm.classify_category(inp)
        if route == "companion" and args.scatter_mode == "assoc":
            assoc_subject = vlm.predict_companion_subject(inp) or subject
        del vlm  # VRAM 解放 (SDXL ロード前に)
        import torch, gc
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    category = category or "object"
    print(f"[routed] {args.sid}: route={route} subj='{subject}' "
          f"cat={category} scatter_mode={args.scatter_mode} assoc='{assoc_subject}'")
    seeds = DEFAULT_SEEDS[:args.n]

    if route == "framed":
        # 正方形パディング → 旧 stage1_lora02 (2026-05-29、 ユーザー評価良) と同じ
        # 単一ステージ生成: _mt プリセット (matsumoto LoRA 0.2) + Matsumoto-style
        # prompt。 IP-Adapter なし。 strokes を縦長中央配置。 正方形入力なので
        # 旧ランと同条件 → 同等の結果になるはず。
        from modules.input_prep import square_pad, place_strokes_centered
        from modules.image_gen import MODEL_PRESETS
        from modules.stroke_transform import compute_strokes_bbox, transform_strokes
        preset = FRAMED_PRESET.get(category, "illustrious_v2_object_mt")
        base_prompt = FRAMED_PROMPT[category].format(subj=subject)
        sh = MODEL_PRESETS.get(preset, {}).get("style_hint")
        prompt = f"{base_prompt}, {sh}" if sh else base_prompt
        sq = square_pad(inp, FRAMED_SIZE)
        # 入力の被写体位置を contain-fit (アスペクト維持) で縦長キャンバスへ写して
        # bbox を取得 → そこを 1.4 倍に拡大した領域に生成結果を合成する。
        #  - contain: 正方形入力を縦長に引き伸ばさない (webapp も contain 表示)
        #  - ×1.4: 生成画像が入力より小さく見える問題への対処 (大きめに置く)
        from modules.input_prep import content_bbox
        _bb = content_bbox(inp)
        if _bb:
            _W, _H = inp.size
            _s = min(CW / _W, CH / _H)
            _ox, _oy = (CW - _W * _s) / 2.0, (CH - _H * _s) / 2.0
            bx, by = _bb[0] * _s + _ox, _bb[1] * _s + _oy
            bw, bh = (_bb[2] - _bb[0]) * _s, (_bb[3] - _bb[1]) * _s
            f = 1.4
            cx, cy = bx + bw / 2.0, by + bh / 2.0
            nw, nh = min(bw * f, CW), min(bh * f, CH)
            nx = max(0.0, min(cx - nw / 2.0, CW - nw))
            ny = max(0.0, min(cy - nh / 2.0, CH - nh))
            ib = (int(nx), int(ny), int(nw), int(nh))
            use_bbox = ib[2] > 0 and ib[3] > 0
        else:
            ib, use_bbox = None, False
        gen = ImageGenerator.from_preset(
            preset, resolution=(FRAMED_SIZE, FRAMED_SIZE), verbose=False)
        gen.load()
        vec = Vectorizer(gen_line_mode="canny_centerline", **cfg)
        framed_seeds = FRAMED_SEEDS[:args.n]
        print(f"[routed]   framed (旧lora02再現) preset={preset} "
              f"input_bbox={ib if use_bbox else 'なし→中央'} prompt: {prompt}")
        for i, seed in enumerate(framed_seeds):
            raster = gen.generate(prompt, sq, seed=seed,
                                  negative_prompt=FRAMED_NEGATIVE)
            r = vec.vectorize(generated_image=raster, user_image=None)
            if use_bbox:
                gb = compute_strokes_bbox(r.strokes)
                placed = transform_strokes(r.strokes, gb, ib, fit="contain")
            else:
                placed = place_strokes_centered(r.strokes, (CW, CH), fill=0.9)
            d = args.output_base / args.sid / f"v{i+1}_seed{seed}"
            meta = {"sid": args.sid, "route": "framed", "subject": subject,
                    "category": category, "preset": preset, "cn": None,
                    "seed": seed, "prompt": prompt, "placed_at": "input_bbox",
                    "input_fit": "contain"}
            _save_candidate(d, placed, CW, CH, generated=raster, meta=meta)
            print(f"[routed]   framed v{i+1} seed{seed}: {len(placed)} strokes -> {d}")
    elif route == "stylize":
        prompt = STYLIZE_TEMPLATES[category].format(subj=subject)
        print(f"[routed]   stylize prompt: {prompt}")
        guide = inp.resize((CW, CH))
        gen = ImageGenerator.from_preset(PRESET, resolution=(CW, CH), verbose=False)
        gen.load()
        vec = Vectorizer(gen_line_mode="canny_centerline", **cfg)
        for i, seed in enumerate(seeds):
            raster = gen.generate(prompt, guide,
                                  controlnet_conditioning_scale=CN_SCALE, seed=seed)
            r = vec.vectorize(generated_image=raster, user_image=None)
            d = args.output_base / args.sid / f"v{i+1}_seed{seed}"
            meta = {"sid": args.sid, "route": "stylize", "subject": subject,
                    "category": category, "preset": PRESET, "cn": CN_SCALE,
                    "seed": seed, "prompt": prompt}
            _save_candidate(d, r.strokes, CW, CH, generated=raster, meta=meta)
            print(f"[routed]   stylize v{i+1} seed{seed}: {r.n_strokes} strokes -> {d}")
    else:  # companion → scatter (v1=グリッド / v2,v3=ランダム)
        scatter_subj = assoc_subject if args.scatter_mode == "assoc" else subject
        prompt = SCATTER_PROMPT.format(subj=scatter_subj)
        print(f"[routed]   scatter ({args.scatter_mode}) subj='{scatter_subj}' "
              f"prompt: {prompt}")
        guide = inp.resize((CW, CH))
        gen = ImageGenerator.from_preset(PRESET, resolution=(CW, CH), verbose=False)
        gen.load()
        vec_bin = Vectorizer(**cfg)
        vec_canny = Vectorizer(gen_line_mode="canny_centerline", **cfg)
        input_strokes = vec_bin.vectorize(generated_image=guide, user_image=None).strokes
        bbox = union_bbox(detect_blobs(Image.fromarray(np.array(guide.convert("L")))))
        for i, seed in enumerate(seeds):
            jitter = 0.0 if i == 0 else 1.0   # v1=グリッド、 以降=ランダム
            pat = "grid" if jitter == 0.0 else "random"
            sheet = gen.generate(prompt, guide,
                                 controlnet_conditioning_scale=CN_SCALE, seed=seed)
            combined, n_placed, n_found, n_cells = scatter_companions(
                sheet, input_strokes, bbox, (CW, CH), seed=seed,
                jitter=jitter, vectorizer=vec_canny)
            d = args.output_base / args.sid / f"v{i+1}_seed{seed}_{pat}"
            meta = {"sid": args.sid, "route": "scatter", "subject": scatter_subj,
                    "category": category, "scatter_mode": args.scatter_mode,
                    "pattern": pat, "preset": PRESET, "cn": CN_SCALE,
                    "seed": seed, "prompt": prompt}
            _save_candidate(d, combined, CW, CH, generated=sheet, meta=meta)
            # direct バリアント: scatter せず生成画像 (sheet) を全体ストローク化。
            # 生成画像が良い構図のときはこちらがそのまま使える (ユーザー案)。
            rd = vec_canny.vectorize(generated_image=sheet, user_image=None)
            dd = args.output_base / args.sid / f"v{i+1}_seed{seed}_direct"
            dmeta = dict(meta); dmeta.update(route="direct", pattern="direct")
            _save_candidate(dd, rd.strokes, CW, CH, generated=sheet, meta=dmeta)
            print(f"[routed]   direct v{i+1} seed{seed}: {rd.n_strokes} strokes -> {dd}")
            print(f"[routed]   scatter v{i+1} ({pat}) seed{seed}: "
                  f"{n_placed}/{n_found} chars, {len(combined)} strokes -> {d}")
    print(f"[routed] {args.sid} done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
