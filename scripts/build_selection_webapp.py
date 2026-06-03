#!/usr/bin/env python3
"""GitHub Pages 用の選定 WEB アプリ (docs/selection/index.html) を生成。

v2: 複数選択対応 + gacha 3 variants を候補に追加。

各 sketch につき 4 + 3 = 7 候補 (align noS2 + shift v1/v2/v3 + gacha v1/v2/v3)。
ブラウザでクリックで複数選択可能 (緑ハイライト) → JSON ダウンロード。
"""
from __future__ import annotations

import glob
import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
REPO = "yushin-jizaie/draw_piper"
BRANCH = "claude/style-pool-rebalance-20260529"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"
# 2026-05-30: skeleton 化された strokes (vec_debug/06_strokes.png) は
# robot-input-set ブランチ logs/ にある。 webapp 側で並列表示する。
BRANCH_ROBOT = "claude/robot-input-set-20260529"
RAW_BASE_ROBOT = f"https://raw.githubusercontent.com/{REPO}/{BRANCH_ROBOT}"
ROBOT_INPUT_SET_DIR = "logs/robot_input_set_v2_20260530_174341"
# 2026-05-30: shift mode を large subject prompt で再生成した 8 件
# (object × {shift v3, composition shift}) は別 logs dir。
ROBOT_INPUT_SET_DIR_SHIFTFIX = "logs/robot_input_set_shift_fix_20260530_225557"

ALIGN_BASE = "sketch_variations/align_noS2_all8_20260529_131555"
SHIFT_BASE = "sketch_variations/shift_v3_compare_20260529_100542"
# gacha / composition は timestamp 付きなので auto-detect
GACHA_GLOB = "sketch_variations/gacha_char_auto_all8_*"
COMPOSITION_GLOB = "sketch_variations/composition_all8_*"

# --local: GitHub raw URL ではなくリポジトリ root 相対パス (/sketch_variations/...) を
# 出力し、 push せずに `python -m http.server` (repo root) で確認できるようにする。
# main() で --local 指定時に RAW_BASE="" / LOCAL=True に書き換える。
LOCAL = False


def _sanitize_route(route: str) -> str:
    """build_robot_input_set.py の sanitize_route と同じロジック。"""
    return (route.replace("(", "")
                  .replace(")", "")
                  .replace(":", "_")
                  .replace("/", "_")
                  .replace(" ", "_")
                  .replace("__", "_")
                  .replace("__", "_")
                  .lower())


def skeleton_png_url(sid: str, route: str, gacha_seed: str | None = None) -> str:
    """robot-input-set ブランチの vec_debug/06_strokes.png URL を組み立てる。

    shift_fix 系 (disp:shift_fix_*) は別 logs dir に出力されている。
    route の "/ " 以降の subroute (例: "shift v3", "composition shift")
    から build_robot_input_set の key を再構築して URL を返す。

    --local 時は別ブランチ (robot-input-set) の skeleton は手元に無いので "" を返す。
    """
    if LOCAL:
        return ""
    if "shift_fix" in route:
        if " / " in route:
            sub_route = route.split(" / ", 1)[1].strip()
        else:
            sub_route = route
        key = f"{sid}_{_sanitize_route(sub_route)}"
        return (f"{RAW_BASE_ROBOT}/{ROBOT_INPUT_SET_DIR_SHIFTFIX}/"
                f"{key}/vec_debug/06_strokes.png")
    key = f"{sid}_{_sanitize_route(route)}"
    if gacha_seed:
        key += f"_s{gacha_seed}"
    return f"{RAW_BASE_ROBOT}/{ROBOT_INPUT_SET_DIR}/{key}/vec_debug/06_strokes.png"


_DATE_RE = re.compile(r"(20\d{6})")
_DATE_DASH_RE = re.compile(r"(20\d{2})-(\d{2})-(\d{2})")
# disp_2026-06-01-B のような「日付+レター」 バッチ名を抽出 (生成日フィルタ用)。
_BATCH_RE = re.compile(r"disp_(20\d\d-\d\d-\d\d-[A-Za-z0-9]+)")


def date_for_rel_path(rel_path: str) -> str:
    """rel_path から YYYYMMDD を抽出 (見つからなければ "").

    disp_2026-06-01-B のようなダッシュ区切り日付 (名前付きバッチ) も対応。
    """
    m = _DATE_RE.search(rel_path)
    if m:
        return m.group(1)
    md = _DATE_DASH_RE.search(rel_path)
    if md:
        return md.group(1) + md.group(2) + md.group(3)
    return ""


def _build_key(sid: str, route: str, gacha_seed: str | None = None) -> str:
    key = f"{sid}_{_sanitize_route(route)}"
    if gacha_seed:
        key += f"_s{gacha_seed}"
    return key


def frida_info(sid: str, route: str, gacha_seed: str | None = None) -> dict:
    """robot-input-set ブランチ logs/ から strokes.json を読み、
    Frida 適合度を判定して dict を返す (n_strokes, avg_pts, warns)。

    logs/ が無い場合は {} (webapp 側で safe fallback)。
    """
    try:
        from scripts.check_frida_friendly import frida_friendly  # type: ignore
    except ImportError:
        return {}
    key = _build_key(sid, route, gacha_seed)
    json_p = (_ROOT / ROBOT_INPUT_SET_DIR / key / "strokes.json")
    if not json_p.exists():
        # shift_fix 系 fallback (別 logs dir、 別 key 計算)
        if "shift_fix" in route:
            sub_route = (route.split(" / ", 1)[1].strip()
                         if " / " in route else route)
            alt_key = f"{sid}_{_sanitize_route(sub_route)}"
            json_p = (_ROOT / ROBOT_INPUT_SET_DIR_SHIFTFIX
                      / alt_key / "strokes.json")
        if not json_p.exists():
            return {}
    try:
        data = json.loads(json_p.read_text())
        strokes = data.get("strokes", [])
        shape = data.get("image_shape", [768, 768])
        canvas_w = shape[1] if len(shape) > 1 else 768
        warns = frida_friendly(strokes, canvas_w=canvas_w)
        n = len(strokes)
        pts = sum(len(s) for s in strokes)
        return {
            "n_strokes": n,
            "n_points": pts,
            "avg_pts": round(pts / max(n, 1), 1),
            "warns": warns,
        }
    except Exception:
        return {}

INPUTS = [
    ("B_round_smiley",   "sketch_variations/_inputs/B_round_smiley.png",   "character"),
    ("C_face_with_neck", "sketch_variations/_inputs/C_face_with_neck.png", "character"),
    ("D_stick_figure",   "sketch_variations/_inputs/D_stick_figure.png",   "character"),
    ("F_angry_face",     "sketch_variations/_inputs/F_angry_face.png",     "character"),
    ("house",            "sketch_variations/_inputs/house.png",            "object"),
    ("tree",             "sketch_variations/_inputs/tree.png",             "object"),
    ("cat",              "sketch_variations/_inputs/cat.png",              "object"),
    ("car",              "sketch_variations/_inputs/car.png",              "object"),
    # 2026-05-31: literal-only / shift 経路の単純図形テスト (円)。
    # align/shift v1-3/gacha は無いので、 disp_* auto-detect だけが候補になる。
    ("circle",           "sketch_variations/_inputs/circle.png",           "object"),
    # 2026-05-31: 縦長(704x1472)入力での生成テスト (balanced prompt)。
    ("portrait_person",  "sketch_variations/_inputs/portrait_person.png",  "character"),
    # 2026-06-01: 「キャラを撒く」 デモ (lineartLoRA スプライト→分割→空白散布)。
    ("scatter",          "sketch_variations/_inputs/scatter_input.png",    "character"),
    # 2026-06-01: 大きく描かれたオブジェクトのテスト (縦長→stylize / 横長→scatter)。
    ("tree_big",         "sketch_variations/_inputs/tree_big.png",         "object"),
    ("cat_big",          "sketch_variations/_inputs/cat_big.png",          "object"),
    ("house_big",        "sketch_variations/_inputs/house_big.png",        "object"),
    ("car_big",          "sketch_variations/_inputs/car_big.png",          "object"),
    # 2026-06-01: 実写サンプル (image_sample/ の HEIC → photo_to_sketch で線画化)。
    ("samp_IMG_4357",    "sketch_variations/_inputs/samp_IMG_4357.png",    "character"),
    ("samp_IMG_4358",    "sketch_variations/_inputs/samp_IMG_4358.png",    "character"),
    ("samp_IMG_4359",    "sketch_variations/_inputs/samp_IMG_4359.png",    "object"),
    ("samp_IMG_4360",    "sketch_variations/_inputs/samp_IMG_4360.png",    "character"),
    ("samp_IMG_4361",    "sketch_variations/_inputs/samp_IMG_4361.png",    "object"),
    ("samp_IMG_4362",    "sketch_variations/_inputs/samp_IMG_4362.png",    "object"),
    ("samp_IMG_4363",    "sketch_variations/_inputs/samp_IMG_4363.png",    "character"),
    # 2026-06-03: 新規手描き3枚 (assets/IMG_4368 を分割: 木2 + 車)。
    ("new_tree_a",       "sketch_variations/_inputs/new_tree_a.png",       "object"),
    ("new_tree_b",       "sketch_variations/_inputs/new_tree_b.png",       "object"),
    ("new_car",          "sketch_variations/_inputs/new_car.png",          "object"),
    # 2026-06-03: 3生成を元レイアウト(IMG_4368)へ戻した合成 (象+花+トラック)。
    ("new_combo",        "sketch_variations/_inputs/new_combo.png",        "object"),
    # 2026-06-03: B ルート(scatter/direct manga)で個別生成→元レイアウトへ合成。
    ("new_combo_b",      "sketch_variations/_inputs/new_combo.png",        "object"),
    # 2026-06-03: 6/3最新ルート+mangaデフォルトスタイルで個別生成→合成。
    ("new_combo_m",      "sketch_variations/_inputs/new_combo.png",        "object"),
    # 2026-06-04: align(S2 OFF)×現在プロンプトで分割せず1枚生成。
    ("new_combo_align",  "sketch_variations/_inputs/new_combo_align.png",  "object"),
    # 2026-06-04: direct ルート(scatter/companion→direct manga)で分割せず1枚生成。
    ("new_combo_direct", "sketch_variations/_inputs/new_combo_direct.png", "object"),
]

# 2026-06-01: GUI (pipeline_test_gui) からアップロードされた候補の入力定義。
# upload_to_webapp.py が sketch_variations/disp_gui_uploads/_inputs.json に
# {"sid","input","kind"} を追記する。 ここで INPUTS にマージして列を生やす。
_GUI_EXTRA = _ROOT / "sketch_variations" / "disp_gui_uploads" / "_inputs.json"
if _GUI_EXTRA.exists():
    try:
        for _e in json.loads(_GUI_EXTRA.read_text()):
            INPUTS.append((_e["sid"], _e["input"], _e.get("kind", "object")))
    except Exception as _ex:   # noqa: BLE001
        print(f"[webapp] GUI upload inputs 読み込み失敗 (skip): {_ex}")


def read_companion(meta_p: Path) -> str:
    if not meta_p.exists():
        return ""
    for ln in meta_p.read_text().splitlines():
        if ln.startswith("companion_subject="):
            return ln.split("=", 1)[1]
    return ""


def find_dispatcher_variants(sketch_id: str) -> list:
    """Phase 3: 新 dispatcher dir を auto-detect。

    対象パターン (sketch_variations/ 配下):
      - disp_*/<sid>_<route>/30_companion_strokes.png  (shift / composition shift)
      - disp_*/<sid>_<route>/30_vectorized_strokes.png (align / composition align / gacha)
      - disp_*/<sid>/v*_seed*/30_vectorized_strokes.png (gacha-style)

    各 dir の 00_auto_prompt.txt から composition_refinement / companion_subject
    を読み取って label に表示。
    """
    out = []
    for base in sorted(_ROOT.glob("sketch_variations/disp_*")):
        # パターン 1: <sid>_<route>/ サブディレクトリ
        for sub in sorted(base.glob(f"{sketch_id}_*")):
            if not sub.is_dir():
                continue
            route = sub.name[len(sketch_id) + 1:].replace("_", " ")
            png = (sub / "30_companion_strokes.png"
                    if "shift" in route
                    else sub / "30_vectorized_strokes.png")
            if not png.exists():
                continue
            meta = sub / "00_auto_prompt.txt"
            comp = read_companion(meta)
            comp_phrase = ""
            if meta.exists():
                for ln in meta.read_text().splitlines():
                    if ln.startswith("composition_refinement="):
                        comp_phrase = ln.split("=", 1)[1]
            label_extra = f": {comp_phrase}" if comp_phrase else (
                f": {comp}" if comp else "")
            r = f"disp:{base.name[5:]} / {route}"
            # skeleton: 同 dir に vec_debug/06_strokes.png があればこのブランチの
            # それを使う (robot-input-set ブランチに無い circle 等でも表示可能)。
            skel_local = sub / "vec_debug" / "06_strokes.png"
            skel_png = (f"{RAW_BASE}/{skel_local.relative_to(_ROOT)}"
                        if skel_local.exists()
                        else skeleton_png_url(sketch_id, r))
            out.append({
                "route": r,
                "label": f"disp {route}{label_extra}",
                "strokes_png": f"{RAW_BASE}/{png.relative_to(_ROOT)}",
                "skeleton_png": skel_png,
                "rel_path": str(sub.relative_to(_ROOT)),
                "companion": comp or comp_phrase,
                "frida": frida_info(sketch_id, r),
                "date": date_for_rel_path(str(sub.relative_to(_ROOT))),
            })
        # パターン 2: <sid>/v*_seed*/ サブディレクトリ
        sid_dir = base / sketch_id
        if sid_dir.is_dir():
            for seed_dir in sorted(sid_dir.glob("v*_seed*")):
                png = seed_dir / "30_vectorized_strokes.png"
                if not png.exists():
                    continue
                seed = seed_dir.name.split("_seed")[1]
                meta = base / "00_auto_prompt.txt"
                comp_phrase = ""
                if meta.exists():
                    for ln in meta.read_text().splitlines():
                        if ln.startswith("composition_refinement="):
                            comp_phrase = ln.split("=", 1)[1]
                label_extra = f": {comp_phrase}" if comp_phrase else ""
                r = f"disp:{base.name[5:]} / {seed_dir.name}"
                out.append({
                    "route": r,
                    # disp dir 名 (preset 等) も含めて、 どの生成設定かを明示。
                    "label": f"{sketch_id}: {base.name[5:]} / {seed_dir.name}{label_extra}",
                    "strokes_png": f"{RAW_BASE}/{png.relative_to(_ROOT)}",
                    "skeleton_png": skeleton_png_url(sketch_id, r, seed),
                    "rel_path": str(seed_dir.relative_to(_ROOT)),
                    "companion": comp_phrase,
                    "gacha_seed": seed,
                    "frida": frida_info(sketch_id, r, seed),
                    "date": date_for_rel_path(str(seed_dir.relative_to(_ROOT))),
                })
    return out


def find_composition_variants(sketch_id: str) -> list:
    """composition_all8_* base から shift/align の 2 候補を見つける。

    出力ファイルは 30_companion_strokes.png (shift) / 30_vectorized_strokes.png (align)。
    """
    matches = sorted((_ROOT).glob(COMPOSITION_GLOB))
    if not matches:
        return []
    base_dir = matches[-1]   # 最新の timestamp
    out = []
    for mode, fn in [("shift", "30_companion_strokes.png"),
                       ("align", "30_vectorized_strokes.png")]:
        mode_dir = base_dir / f"{sketch_id}_{mode}"
        png = mode_dir / fn
        if not png.exists():
            continue
        # composition phrase を 00_auto_prompt.txt から読む
        meta = mode_dir / "00_auto_prompt.txt"
        comp = ""
        if meta.exists():
            for ln in meta.read_text().splitlines():
                if ln.startswith("composition_refinement="):
                    comp = ln.split("=", 1)[1]
        r = f"composition {mode}"
        out.append({
            "route": r,
            "label": f"comp {mode}: {comp or '(none)'}",
            "strokes_png": f"{RAW_BASE}/{png.relative_to(_ROOT)}",
            "skeleton_png": skeleton_png_url(sketch_id, r),
            "rel_path": str(mode_dir.relative_to(_ROOT)),
            "companion": comp,
            "frida": frida_info(sketch_id, r),
            "date": date_for_rel_path(str(mode_dir.relative_to(_ROOT))),
        })
    return out


def find_gacha_variants(sketch_id: str) -> list:
    """gacha base から v1/v2/v3 seed dir を見つける。"""
    matches = sorted((_ROOT).glob(f"{GACHA_GLOB}/{sketch_id}"))
    if not matches:
        return []
    sketch_dir = matches[-1]   # 最新の timestamp
    gacha_base_rel = sketch_dir.parent.relative_to(_ROOT)
    out = []
    for v in ("v1", "v2", "v3"):
        seed_dirs = sorted(sketch_dir.glob(f"{v}_seed*"))
        if seed_dirs:
            seed_dir = seed_dirs[0]
            seed = seed_dir.name.split("_seed")[1]
            png_rel = (
                seed_dir.relative_to(_ROOT) / "30_vectorized_strokes.png"
            )
            r = f"gacha {v}"
            out.append({
                "route": r,
                "label": f"gacha {v} (seed={seed})",
                "strokes_png": f"{RAW_BASE}/{png_rel}",
                "skeleton_png": skeleton_png_url(sketch_id, r, seed),
                "rel_path": str(seed_dir.relative_to(_ROOT)),
                "gacha_seed": seed,
                "frida": frida_info(sketch_id, r, seed),
                "date": date_for_rel_path(str(seed_dir.relative_to(_ROOT))),
            })
    return out


def build_entries():
    entries = []
    for sid, ip, stype in INPUTS:
        cands = []
        # 1) align (S2 OFF) — strokes png が無ければ skip (circle 等、 未実行の経路)。
        # ローカルに無いものは push もされず online でも 404 なので、 常に存在確認。
        align_rel = f"{ALIGN_BASE}/{sid}/30_vectorized_strokes.png"
        if (_ROOT / align_rel).exists():
            cands.append({
                "route": "align (S2 OFF)",
                "label": "align (S2 OFF)",
                "strokes_png": f"{RAW_BASE}/{align_rel}",
                "skeleton_png": skeleton_png_url(sid, "align (S2 OFF)"),
                "rel_path": f"{ALIGN_BASE}/{sid}",
                "frida": frida_info(sid, "align (S2 OFF)"),
                "date": date_for_rel_path(ALIGN_BASE),
            })
        # 2-4) shift v1/v2/v3 — 同上、 ローカルに無ければ skip
        for v in ("v1", "v2", "v3"):
            shift_rel = f"{SHIFT_BASE}/{v}/{sid}/30_companion_strokes.png"
            if not (_ROOT / shift_rel).exists():
                continue
            comp = read_companion(
                _ROOT / SHIFT_BASE / v / sid / "00_auto_prompt.txt")
            cands.append({
                "route": f"shift {v}",
                "label": f"shift {v}: {comp}",
                "strokes_png": f"{RAW_BASE}/{shift_rel}",
                "skeleton_png": skeleton_png_url(sid, f"shift {v}"),
                "rel_path": f"{SHIFT_BASE}/{v}/{sid}",
                "companion": comp,
                "frida": frida_info(sid, f"shift {v}"),
                "date": date_for_rel_path(SHIFT_BASE),
            })
        # 5-7) gacha v1/v2/v3
        cands += find_gacha_variants(sid)
        # 8-9) composition shift/align (VLM 構図 refinement 入り)
        cands += find_composition_variants(sid)
        # 10+) Phase 3 (2026-05-30): 新 dispatcher dir を auto-detect
        # sketch_variations/disp_*/<sid>_<route>/ or
        # sketch_variations/disp_*/<sid>/v*_seed*/ をスキャン
        cands += find_dispatcher_variants(sid)
        entries.append({
            "sketch_id": sid,
            "type": stype,
            "input_png": f"{RAW_BASE}/{ip}",
            "candidates": cands,
        })
    return entries


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>draw_piper — robot input selection v2 (multi)</title>
<style>
  :root {
    --bg: #1e1e1e; --fg: #ddd; --panel: #2a2a2a; --accent: #ff8c1a;
    --border: #444; --selected: #2c7;
  }
  * { box-sizing: border-box; }
  body { background: var(--bg); color: var(--fg); margin: 0;
         font-family: -apple-system, system-ui, sans-serif; }
  header { background: var(--panel); padding: 12px 24px;
           border-bottom: 1px solid var(--border);
           position: sticky; top: 0; z-index: 100;
           display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
  header h1 { font-size: 18px; margin: 0; }
  header .status { font-size: 14px; opacity: 0.8; }
  header button { background: var(--accent); color: #000; border: 0;
                   padding: 8px 16px; border-radius: 4px; font-weight: 600;
                   cursor: pointer; font-size: 14px; }
  header button:disabled { opacity: 0.4; cursor: not-allowed; }
  header button.secondary { background: var(--panel); color: var(--fg);
                            border: 1px solid var(--border); }
  header .hint { font-size: 12px; opacity: 0.6; }
  main { padding: 16px 24px; }
  .row { background: var(--panel); border-radius: 8px; padding: 16px;
         margin-bottom: 16px; border: 1px solid var(--border); }
  .row h2 { margin: 0 0 12px 0; font-size: 16px; color: var(--accent); }
  .row h2 .type { font-size: 12px; opacity: 0.6; margin-left: 8px; }
  .row h2 .count { font-size: 12px; opacity: 0.7; margin-left: 12px;
                    color: var(--selected); font-weight: normal; }
  /* 2026-05-30: 6 列 wrap (max 6 個/行、 7 個目から自動で次行へ段下げ) */
  .candidates { display: grid;
                grid-template-columns: repeat(6, 1fr); gap: 8px; }
  .panel { background: #1a1a1a; border: 2px solid transparent;
           border-radius: 6px; padding: 6px; cursor: pointer;
           transition: border-color 0.15s, box-shadow 0.15s; position: relative; }
  .panel:hover { border-color: #888; }
  .panel.input { cursor: default; opacity: 0.85; border-color: #333; }
  /* 2026-05-30 v3.2: hover で画面中央に元解像度 floating preview (Canvas
     合成済 256px ではなく、 raw strokes_png をそのまま表示) */
  .hires-preview {
    display: none;
    position: fixed;
    top: 50%; left: 50%;
    transform: translate(-50%, -50%);
    z-index: 1000;
    width: min(80vh, 80vw);
    height: min(80vh, 80vw);
    background: rgba(20, 20, 20, 0.96);
    border: 4px solid var(--accent);
    border-radius: 12px;
    padding: 16px;
    box-shadow: 0 24px 64px rgba(0, 0, 0, 0.8);
    pointer-events: none;
  }
  .hires-preview img {
    width: 100%; height: calc(100% - 24px);
    object-fit: contain; background: white; border-radius: 6px;
  }
  .hires-preview .label {
    color: var(--accent); font-size: 14px; font-weight: 600;
    text-align: center; margin-bottom: 8px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  /* 2026-06-01: hover 拡大を廃止し、 🔍 クリックで modal 大表示に変更 */
  .zoom-btn { display:block; width:100%; margin-top:4px;
    background:#2a2a2a; color:#fff; border:none; border-radius:4px;
    font-size:11px; padding:3px 6px; cursor:pointer; line-height:1.3; }
  .zoom-btn:hover { background:var(--accent); }
  .panel .img-stack { cursor:pointer; }
  .panel .panel-label .sel-chk { margin-top:2px; width:15px; height:15px;
    flex:0 0 auto; cursor:pointer; }
  .modal-back { display:none; position:fixed; inset:0; z-index:2000;
    background:rgba(0,0,0,0.88); }
  .modal-back.open { display:flex; align-items:center; justify-content:center; }
  .modal { background:#181818; border:2px solid var(--accent); border-radius:12px;
    padding:18px; max-width:96vw; max-height:96vh; overflow:auto;
    display:flex; gap:18px; }
  .modal .imgcol { text-align:center; color:#bbb; font-size:12px; }
  .modal canvas.big, .modal img.big { height:80vh; max-width:38vw; width:auto;
    background:#fff; border-radius:8px; object-fit:contain; display:block; }
  .modal .meta { min-width:300px; max-width:360px; color:#ddd; font-size:13px; }
  .modal .meta h3 { color:var(--accent); margin:0 0 6px; font-size:16px; }
  .modal .meta dt { color:#8ad7ff; font-size:11px; margin-top:9px;
    text-transform:uppercase; letter-spacing:.4px; }
  .modal .meta dd { margin:2px 0 0; word-break:break-word; }
  .modal .meta .prompt { background:#0e0e0e; padding:9px; border-radius:6px;
    margin-top:6px; line-height:1.45; white-space:pre-wrap; color:#cfe; }
  .modal .close { position:fixed; top:14px; right:26px; font-size:30px;
    color:#fff; cursor:pointer; z-index:2001; }
  /* 2026-05-30 v3.2: 新規 disp_* (新 dispatcher) 候補に NEW バッジ */
  .panel.is-new::before {
    content: "NEW";
    position: absolute;
    top: 4px; right: 4px;
    background: #ff4081; color: white;
    font-size: 9px; font-weight: 700;
    padding: 2px 6px; border-radius: 3px;
    z-index: 10;
    letter-spacing: 0.5px;
    box-shadow: 0 2px 6px rgba(255, 64, 129, 0.5);
  }
  .panel.selected { border-color: var(--selected);
                    box-shadow: 0 0 0 3px rgba(44, 204, 119, 0.25); }
  .panel .panel-label { font-size: 10px; opacity: 0.9;
                        display: flex; align-items: flex-start; gap: 4px;
                        text-align: left; margin-bottom: 4px; cursor: pointer;
                        white-space: normal; overflow-wrap: anywhere;
                        word-break: break-word; line-height: 1.25;
                        min-height: 2.4em; }
  .panel img, .panel canvas { width: 100%; height: auto; display: block;
                background: #fff; border-radius: 4px; aspect-ratio: 1; }
  .panel.input img { aspect-ratio: 1; }
  /* 2026-05-30 v3.3: panel 内に 「元 PNG canvas + skeleton img」 を縦 2 段 */
  .panel .img-stack { display: flex; flex-direction: column; gap: 3px; }
  .panel .img-stack > div { width: 100%; aspect-ratio: 1; position: relative;
                            background: #fff; border-radius: 3px;
                            overflow: hidden; }
  .panel .img-stack canvas,
  .panel .img-stack img { width: 100% !important; height: 100% !important;
                          aspect-ratio: auto !important; display: block;
                          background: #fff; border-radius: 3px; }
  .panel .img-stack .stack-tag {
    position: absolute; font-size: 10px; padding: 2px 6px;
    background: rgba(0,0,0,0.75); color: #fff; border-radius: 3px;
    pointer-events: none; font-weight: 600; letter-spacing: 0.3px;
    z-index: 5;
  }
  /* 2026-05-30 v3.4: Frida 適合度バッジ (panel 右下、 strokes 数 + warn count) */
  .frida-badge {
    position: absolute; bottom: 4px; right: 4px;
    font-size: 9px; padding: 2px 5px; border-radius: 3px;
    font-weight: 700; letter-spacing: 0.3px;
    z-index: 8;
    cursor: help;
  }
  .frida-badge.ok { background: rgba(44, 204, 119, 0.85); color: #fff; }
  .frida-badge.warn { background: rgba(255, 165, 0, 0.9); color: #000; }
  .frida-badge.bad { background: rgba(220, 60, 60, 0.9); color: #fff; }
  .panel.selected .panel-label::after { content: " ✓"; color: var(--selected);
                                         font-weight: 700; }
  .summary { background: var(--panel); border-radius: 8px; padding: 16px;
             margin-top: 16px; border: 1px solid var(--border); }
  .summary pre { background: #1a1a1a; padding: 12px; border-radius: 4px;
                  overflow-x: auto; font-size: 12px; margin: 8px 0 0 0; }
</style>
</head>
<body>
<header>
  <h1>🎨 draw_piper — input selection v2</h1>
  <span class="status" id="status">0 件選択</span>
  <span class="hint">クリックで複数選択 / 解除可</span>
  <label style="font-size:13px;display:flex;align-items:center;gap:6px">
    生成日:
    <select id="date-filter" style="background:#1a1a1a;color:#ddd;border:1px solid #555;border-radius:4px;padding:4px 8px;font-size:13px">
      <option value="all">すべて</option>
    </select>
  </label>
  <div style="flex: 1"></div>
  <button id="download-btn" disabled>選択結果を JSON でダウンロード</button>
  <button class="secondary" id="reset-btn">リセット</button>
</header>
<main id="main"></main>
<script>
const ENTRIES = __ENTRIES__;
const STORAGE_KEY = "draw_piper_selections_v2";

// 構造: { sketch_id: [route1, route2, ...] }
let selections = {};
try {
  const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
  // 旧 v1 (string) からの migration
  for (const k in raw) {
    selections[k] = Array.isArray(raw[k]) ? raw[k] : [raw[k]];
  }
} catch (e) { selections = {}; }

const main = document.getElementById("main");
const statusEl = document.getElementById("status");
const dlBtn = document.getElementById("download-btn");
const resetBtn = document.getElementById("reset-btn");
const dateFilterEl = document.getElementById("date-filter");
const DATE_FILTER_KEY = "draw_piper_date_filter";

// プルダウンに 日付選択肢を populate (entries 内の全 cand.date を unique → 新しい順)
(function populateDateFilter() {
  const dates = new Set(), batches = new Set();
  for (const e of ENTRIES) {
    for (const c of e.candidates) {
      if (c.date) dates.add(c.date);
      if (c.batch) batches.add(c.batch);   // 例 2026-06-01-B
    }
  }
  const sortedDates = Array.from(dates).sort().reverse();
  for (const d of sortedDates) {
    const opt = document.createElement("option");
    opt.value = d;
    opt.textContent = d.length === 8
      ? `${d.slice(0,4)}-${d.slice(4,6)}-${d.slice(6,8)}` : d;
    dateFilterEl.appendChild(opt);
  }
  // 「日付+レター」 バッチ選択肢 (区切りラベルの後に列挙)
  const sortedBatches = Array.from(batches).sort();
  if (sortedBatches.length) {
    const sep = document.createElement("option");
    sep.disabled = true; sep.textContent = "── バッチ ──";
    dateFilterEl.appendChild(sep);
    for (const b of sortedBatches) {
      const opt = document.createElement("option");
      opt.value = b; opt.textContent = b;
      dateFilterEl.appendChild(opt);
    }
  }
  const all = sortedDates.concat(sortedBatches);
  const latest = sortedDates[0] || "all";
  const saved = localStorage.getItem(DATE_FILTER_KEY);
  dateFilterEl.value = (saved && all.includes(saved)) ? saved : latest;
})();

function totalSelected() {
  let n = 0;
  for (const k in selections) n += (selections[k] || []).length;
  return n;
}

function updateStatus() {
  const total = totalSelected();
  const inputs = Object.keys(selections).filter(k => (selections[k] || []).length).length;
  statusEl.textContent = `${total} 件選択 (${inputs}/${ENTRIES.length} 入力)`;
  dlBtn.disabled = total === 0;
}

function isSelected(sid, route) {
  return (selections[sid] || []).includes(route);
}

function toggleSelection(sid, route) {
  if (!selections[sid]) selections[sid] = [];
  const idx = selections[sid].indexOf(route);
  if (idx >= 0) {
    selections[sid].splice(idx, 1);
    if (selections[sid].length === 0) delete selections[sid];
  } else {
    selections[sid].push(route);
  }
  localStorage.setItem(STORAGE_KEY, JSON.stringify(selections));
  render();
}

// チェックボックス選択 (full render せず軽量更新)
function setSelection(sid, route, on) {
  if (!selections[sid]) selections[sid] = [];
  const i = selections[sid].indexOf(route);
  if (on && i < 0) selections[sid].push(route);
  else if (!on && i >= 0) {
    selections[sid].splice(i, 1);
    if (selections[sid].length === 0) delete selections[sid];
  }
  localStorage.setItem(STORAGE_KEY, JSON.stringify(selections));
  updateStatus();
  refreshSummary();
}
function refreshSummary() {
  const pre = document.getElementById("selSummary");
  if (pre) pre.textContent = JSON.stringify(buildOutput(), null, 2);
}

function loadImage(url) {
  return new Promise((res, rej) => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => res(img);
    img.onerror = rej;
    img.src = url;
  });
}

async function makeOverlay(canvas, inputUrl, candUrl, W, H, inputFit) {
  // input + candidate を Canvas に合成 (input=青、 候補=黒、 背景=白)
  // W,H 指定で任意サイズ。 inputFit="contain" で入力をアスペクト維持で
  // レターボックス表示 (正方形入力を縦長に引き伸ばさない = framed 用)。
  W = W || 256; H = H || 256;
  canvas.width = W; canvas.height = H;
  const ctx = canvas.getContext("2d");
  // 白で初期化 (どちらかが load 失敗しても見える)
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, W, H);
  try {
    const [imgInp, imgCand] = await Promise.all([
      loadImage(inputUrl), loadImage(candUrl)
    ]);
    // 候補を tmp canvas に
    const c1 = document.createElement("canvas");
    c1.width = W; c1.height = H;
    const ctx1 = c1.getContext("2d");
    ctx1.fillStyle = "#fff"; ctx1.fillRect(0, 0, W, H);
    ctx1.drawImage(imgCand, 0, 0, W, H);
    const candData = ctx1.getImageData(0, 0, W, H);
    // input を tmp canvas に (contain なら入力をアスペクト維持でレターボックス)
    const c2 = document.createElement("canvas");
    c2.width = W; c2.height = H;
    const ctx2 = c2.getContext("2d");
    ctx2.fillStyle = "#fff"; ctx2.fillRect(0, 0, W, H);
    if (inputFit === "contain") {
      const s = Math.min(W / imgInp.width, H / imgInp.height);
      const dw = imgInp.width * s, dh = imgInp.height * s;
      ctx2.drawImage(imgInp, (W - dw) / 2, (H - dh) / 2, dw, dh);
    } else {
      ctx2.drawImage(imgInp, 0, 0, W, H);
    }
    const inpData = ctx2.getImageData(0, 0, W, H);
    const out = ctx.createImageData(W, H);
    for (let i = 0; i < W * H; i++) {
      const j = i * 4;
      const cp = candData.data[j];
      const ip = inpData.data[j];
      if (cp < 128) {
        // 候補黒線 → 黒で上書き優先
        out.data[j] = 0; out.data[j+1] = 0; out.data[j+2] = 0;
      } else if (ip < 128) {
        // input 黒線 → 青
        out.data[j] = 100; out.data[j+1] = 150; out.data[j+2] = 255;
      } else {
        out.data[j] = 255; out.data[j+1] = 255; out.data[j+2] = 255;
      }
      out.data[j+3] = 255;
    }
    ctx.putImageData(out, 0, 0);
  } catch (e) {
    // load 失敗時は cand 画像をそのまま描画
    try {
      const cand = await loadImage(candUrl);
      ctx.drawImage(cand, 0, 0, W, H);
    } catch (e2) {}
  }
}

// ---- クリック拡大 modal (合成 + strokes + メタ + 生成プロンプト) ----
function esc(s){ return String(s==null?"":s)
  .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }
function fmtMeta(m, entry, cand){
  const rows = [
    ["route", m.route], ["subject", m.subject], ["category", m.category],
    ["preset", m.preset], ["cn (ControlNet)", m.cn], ["seed", m.seed],
    ["scatter_mode", m.scatter_mode], ["pattern", m.pattern],
  ].filter(([k,v]) => v!=null && v!=="");
  let h = rows.map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");
  h += `<dt>sketch_id</dt><dd>${esc(entry.sketch_id)}</dd>`;
  if(cand.frida && cand.frida.n_strokes!=null)
    h += `<dt>Frida</dt><dd>n=${cand.frida.n_strokes} avg=${cand.frida.avg_pts}pts `
       + `warns=${(cand.frida.warns||[]).length}</dd>`;
  h += `<dt>パス</dt><dd style="font-size:11px;opacity:.7">${esc(cand.rel_path||"")}</dd>`;
  if(m.prompt) h += `<dt>生成プロンプト</dt><dd class="prompt">${esc(m.prompt)}</dd>`;
  else h += `<dt>生成プロンプト</dt><dd style="opacity:.6">(メタ未保存の旧候補)</dd>`;
  return h;
}
function openModal(cand, entry){
  let back = document.getElementById("modalBack");
  if(!back){
    back = document.createElement("div");
    back.id = "modalBack"; back.className = "modal-back";
    document.body.appendChild(back);
    back.addEventListener("click", (e)=>{ if(e.target===back) closeModal(); });
    document.addEventListener("keydown", (e)=>{ if(e.key==="Escape") closeModal(); });
  }
  const m = cand.meta || {};
  const genCol = cand.generated_png
    ? `<div class="imgcol"><div>生成画像 (絵)</div>
         <img class="big" src="${cand.generated_png}" alt="generated"></div>`
    : "";
  back.innerHTML = `<span class="close" onclick="closeModal()">×</span>
    <div class="modal">
      ${genCol}
      <div class="imgcol"><div>元画像 + 候補 合成</div><canvas class="big" id="mComp"></canvas></div>
      <div class="imgcol"><div>候補 (ロボット描画 strokes)</div>
        <img class="big" src="${cand.strokes_png}" alt="strokes"></div>
      <div class="meta"><h3>${esc(cand.label||"")}</h3><dl>${fmtMeta(m, entry, cand)}</dl></div>
    </div>`;
  back.classList.add("open");
  // portrait 比率 (704:1472) で合成。 入力は常に contain 表示 (正方形入力の縦伸び防止)。
  makeOverlay(document.getElementById("mComp"), entry.input_png, cand.strokes_png,
              460, 962, "contain");
}
function closeModal(){
  const back = document.getElementById("modalBack");
  if(back) back.classList.remove("open");
}

function render() {
  main.innerHTML = "";
  const dateFilter = dateFilterEl.value;
  for (const entry of ENTRIES) {
    // 日付 filter 適用 (all なら全部表示)
    const matchFilter = (c) => dateFilter === "all"
      || c.date === dateFilter || c.batch === dateFilter;
    const filtered = entry.candidates.filter(matchFilter);
    if (filtered.length === 0 && dateFilter !== "all") continue;
    const row = document.createElement("div");
    row.className = "row";
    const ncands = filtered.length;
    const nsel = (selections[entry.sketch_id] || []).filter(
      r => entry.candidates.find(c => c.route === r && matchFilter(c))
    ).length;
    row.innerHTML = `
      <h2>${entry.sketch_id} <span class="type">${entry.type}</span>${nsel > 0 ? `<span class="count">★ ${nsel} 件選択</span>` : ""}<span class="type" style="margin-left:8px">(${ncands} 候補)</span></h2>
      <div class="candidates"></div>
    `;
    const grid = row.querySelector(".candidates");
    // INPUT パネル
    const inp = document.createElement("div");
    inp.className = "panel input";
    inp.innerHTML = `<div class="panel-label">INPUT</div>
                     <img src="${entry.input_png}" loading="lazy">`;
    grid.appendChild(inp);
    // 表示順: 日付 (YYYYMMDD) 降順 = 最新順。 同日付は元の順序を維持。
    const ordered = filtered
      .map((c, i) => [c, i])
      .sort((a, b) => ((b[0].date || "").localeCompare(a[0].date || "")) || (a[1] - b[1]))
      .map(([c]) => c);
    // 候補 N つ (Canvas で input overlay 合成)
    ordered.forEach((cand) => {
      const p = document.createElement("div");
      p.className = "panel candidate";
      // 2026-05-30 v3.2: 新規 disp_* (新 dispatcher) 候補に NEW バッジ
      if (cand.route && cand.route.startsWith("disp:")) {
        p.classList.add("is-new");
      }
      const sel = isSelected(entry.sketch_id, cand.route);
      if (sel) p.classList.add("selected");
      const safeLabel = String(cand.label).replace(/"/g, "&quot;");
      const skelUrl = cand.skeleton_png || "";
      // Frida 適合度バッジ: n_strokes + warns 件数。 warn 0 = ok / 1 = warn / 2+ = bad
      const fr = cand.frida || {};
      let fridaHtml = "";
      if (fr.n_strokes != null) {
        const nw = (fr.warns || []).length;
        const cls = nw === 0 ? "ok" : (nw === 1 ? "warn" : "bad");
        const icon = nw === 0 ? "✓" : "⚠";
        const tip = `Frida: n=${fr.n_strokes} avg=${fr.avg_pts}pts` +
                    (nw ? " | " + (fr.warns || []).join(" | ") : "  (Frida OK)");
        const safeTip = String(tip).replace(/"/g, "&quot;");
        fridaHtml = `<span class="frida-badge ${cls}" title="${safeTip}">Frida ${icon} ${fr.n_strokes}</span>`;
      }
      p.innerHTML = `<label class="panel-label" title="${safeLabel}">
                       <input type="checkbox" class="sel-chk" ${sel ? "checked" : ""}>
                       <span>${cand.label}</span>
                     </label>
                     <div class="img-stack" title="クリックで拡大表示">
                       <div style="position:relative">
                         <span class="stack-tag tag-orig" style="top:2px;left:2px">元</span>
                         <canvas></canvas>
                       </div>
                       <div style="position:relative">
                         <span class="stack-tag tag-skel" style="top:2px;left:2px;background:rgba(255,140,26,0.85)">skel</span>
                         <img class="skeleton-img" src="${skelUrl}" alt="skeleton" loading="lazy"
                              onerror="this.style.opacity=0.2;this.alt='(no skeleton)'">
                       </div>
                     </div>
                     ${fridaHtml}
                     <button class="zoom-btn" title="クリックで拡大表示">🔍 拡大</button>`;
      const cv = p.querySelector("canvas");
      // 非同期で overlay 合成 (通常表示用)。 入力は常に contain 表示 (縦伸び防止)。
      makeOverlay(cv, entry.input_png, cand.strokes_png, 256, 256, "contain");
      // 選択 = チェックボックス (full render しない = 軽量)
      const chk = p.querySelector(".sel-chk");
      chk.addEventListener("change", () => {
        setSelection(entry.sketch_id, cand.route, chk.checked);
        p.classList.toggle("selected", chk.checked);
      });
      // 画像 / 🔍 クリック = modal で拡大表示
      const openIt = (ev) => { ev.stopPropagation(); openModal(cand, entry); };
      p.querySelector(".img-stack").addEventListener("click", openIt);
      p.querySelector(".zoom-btn").addEventListener("click", openIt);
      grid.appendChild(p);
    });
    main.appendChild(row);
  }
  // summary
  const summary = document.createElement("div");
  summary.className = "summary";
  const out = buildOutput();
  summary.innerHTML = `<h2 style="color:var(--accent);margin:0;font-size:14px">現在の選択 (localStorage 自動保存、 複数可)</h2><pre id="selSummary">${JSON.stringify(out, null, 2)}</pre>`;
  main.appendChild(summary);
  updateStatus();
}

function buildOutput() {
  const out = {};
  for (const e of ENTRIES) {
    const sels = selections[e.sketch_id] || [];
    if (sels.length === 0) continue;
    out[e.sketch_id] = {
      type: e.type,
      selections: sels.map(route => {
        const cand = e.candidates.find(c => c.route === route);
        const isShift = route.startsWith("shift");
        const fn = isShift ? "30_companion_strokes.png" : "30_vectorized_strokes.png";
        return {
          route: route,
          strokes_png_rel: (cand.rel_path || "") + "/" + fn,
          companion: cand.companion || null,
          gacha_seed: cand.gacha_seed || null,
        };
      }),
    };
  }
  return out;
}

dlBtn.addEventListener("click", () => {
  const out = buildOutput();
  const blob = new Blob([JSON.stringify(out, null, 2)], {type: "application/json"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "draw_piper_selections_v2.json";
  a.click();
});

resetBtn.addEventListener("click", () => {
  if (confirm("すべての選択をリセットしますか?")) {
    selections = {};
    localStorage.removeItem(STORAGE_KEY);
    render();
  }
});

dateFilterEl.addEventListener("change", () => {
  localStorage.setItem(DATE_FILTER_KEY, dateFilterEl.value);
  render();
});

render();
</script>
</body>
</html>
"""


def _apply_local_skeletons(entries: list) -> int:
    """strokes_png と同じ dir に vec_debug/06_strokes.png があれば、 candidate の
    skeleton_png をそのローカル (同ブランチ) URL に差し替える。

    robot-input-set ブランチに skeleton が無いルート (disp gacha 等) でも、
    strokes 描画から生成した skeleton をこのブランチに置けば表示できる。
    返り値は差し替えた候補数。
    """
    n = 0
    for e in entries:
        for c in e.get("candidates", []):
            sp = c.get("strokes_png", "")
            if RAW_BASE and sp.startswith(RAW_BASE + "/"):
                rel = sp[len(RAW_BASE) + 1:]
            elif sp.startswith("/"):
                rel = sp.lstrip("/")
            else:
                continue
            skel_rel = Path(rel).parent / "vec_debug" / "06_strokes.png"
            skel_abs = _ROOT / skel_rel
            # local skeleton が無い候補 (overnight gacha 等) は strokes_png raster
            # から生成 (robot ブランチ URL の 404 を防ぐ)。
            if not skel_abs.exists():
                _gen_skeleton_from_strokes_png(_ROOT / rel, skel_abs)
            if skel_abs.exists():
                c["skeleton_png"] = f"{RAW_BASE}/{skel_rel}"
                n += 1
            elif "robot-input-set" in c.get("skeleton_png", ""):
                # 生成も差し替えもできない → 404 URL を空にして 404 を出さない
                c["skeleton_png"] = ""
    return n


def _gen_skeleton_from_strokes_png(strokes_png: Path, skel_png: Path) -> bool:
    """strokes render (白背景・黒線) を skeletonize して skel_png に保存。"""
    if not strokes_png.exists():
        return False
    try:
        import cv2
        import numpy as np
        from modules.vectorizer import _skeletonize
        arr = cv2.imread(str(strokes_png), cv2.IMREAD_GRAYSCALE)
        if arr is None:
            return False
        m = (arr < 128).astype(np.uint8)
        canvas = np.full(arr.shape, 255, np.uint8)
        if m.sum() > 0:
            sk = _skeletonize(m).astype(np.uint8)
            canvas[cv2.dilate(sk, np.ones((2, 2), np.uint8)) > 0] = 0
        skel_png.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(skel_png), canvas)
        return True
    except Exception as ex:  # noqa: BLE001
        print(f"[webapp] skeleton 生成失敗 ({strokes_png.name}): {ex}")
        return False


def _apply_local_frida(entries: list) -> int:
    """strokes_png と同じ dir に strokes.json があれば、 そこから Frida 適合度を
    計算して candidate['frida'] を埋める (robot-input-set ブランチ非依存)。

    これで robot-branch に entry が無い候補 (circle, disp gacha 等) でも
    Frida バッジが表示される。 返り値は埋めた候補数。
    """
    try:
        from scripts.check_frida_friendly import frida_friendly  # type: ignore
    except ImportError:
        return 0
    n = 0
    for e in entries:
        for c in e.get("candidates", []):
            sp = c.get("strokes_png", "")
            if RAW_BASE and sp.startswith(RAW_BASE + "/"):
                rel = sp[len(RAW_BASE) + 1:]
            elif sp.startswith("/"):
                rel = sp.lstrip("/")
            else:
                continue
            json_p = _ROOT / Path(rel).parent / "strokes.json"
            if not json_p.exists():
                continue
            try:
                data = json.loads(json_p.read_text())
                strokes = data.get("strokes", [])
                shape = data.get("image_shape", [768, 768])
                canvas_w = shape[1] if len(shape) > 1 else 768
                warns = frida_friendly(strokes, canvas_w=canvas_w)
                pts = sum(len(s) for s in strokes)
                c["frida"] = {
                    "n_strokes": len(strokes),
                    "n_points": pts,
                    "avg_pts": round(pts / max(len(strokes), 1), 1),
                    "warns": warns,
                }
                n += 1
            except Exception:
                continue
    return n


def _apply_local_meta(entries: list) -> int:
    """strokes_png と同じ dir に 00_meta.json があれば candidate['meta'] に載せる
    (route / subject / category / prompt / seed 等を webapp の拡大窓で表示)。"""
    n = 0
    for e in entries:
        for c in e.get("candidates", []):
            sp = c.get("strokes_png", "")
            if RAW_BASE and sp.startswith(RAW_BASE + "/"):
                rel = sp[len(RAW_BASE) + 1:]
            elif sp.startswith("/"):
                rel = sp.lstrip("/")
            else:
                continue
            mp = _ROOT / Path(rel).parent / "00_meta.json"
            if mp.exists():
                try:
                    c["meta"] = json.loads(mp.read_text())
                    n += 1
                except Exception:
                    pass
            # 生成画像 (raster) も拡大窓に出せるよう URL を載せる (絵そのものの確認用)。
            gp = _ROOT / Path(rel).parent / "generated.png"
            if gp.exists():
                c["generated_png"] = f"{RAW_BASE}/{Path(rel).parent}/generated.png"
    return n


def _apply_batch_labels(entries: list) -> int:
    """disp_2026-06-01-B 等のバッチ名を candidate['batch'] に載せる
    (生成日プルダウンに「日付+レター」 の選択肢を出すため)。"""
    n = 0
    for e in entries:
        for c in e.get("candidates", []):
            src = c.get("strokes_png", "") or c.get("rel_path", "")
            m = _BATCH_RE.search(src)
            if m:
                c["batch"] = m.group(1)
                n += 1
    return n


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--local", action="store_true",
        help="GitHub raw URL ではなく repo root 相対パスを出力し、 push せずに "
             "`python -m http.server` (repo root) でローカル確認できるようにする。")
    args = ap.parse_args()

    global RAW_BASE, LOCAL
    if args.local:
        LOCAL = True
        RAW_BASE = ""   # f"{RAW_BASE}/{rel}" → "/{rel}" (root 相対)

    entries = build_entries()
    n_skel = _apply_local_skeletons(entries)
    print(f"local skeleton 差し替え: {n_skel} 候補")
    n_frida = _apply_local_frida(entries)
    print(f"local frida 計算: {n_frida} 候補")
    n_meta = _apply_local_meta(entries)
    print(f"local meta 載せ: {n_meta} 候補")
    n_batch = _apply_batch_labels(entries)
    print(f"batch ラベル: {n_batch} 候補")
    html = HTML_TEMPLATE.replace(
        "__ENTRIES__", json.dumps(entries, ensure_ascii=False))
    out_dir = _ROOT / "docs" / "selection"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "index.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"saved {out_path}  (local={LOCAL})")
    # 各 sketch の候補数を報告
    for e in entries:
        print(f"  {e['sketch_id']}: {len(e['candidates'])} candidates")
    if LOCAL:
        print("\nローカル確認:\n"
              "  cd ~/draw_piper && python3 -m http.server 8000\n"
              "  → http://localhost:8000/docs/selection/index.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
