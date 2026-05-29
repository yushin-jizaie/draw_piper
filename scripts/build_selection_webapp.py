#!/usr/bin/env python3
"""GitHub Pages 用の選定 WEB アプリ (docs/selection/index.html) を生成。

v2: 複数選択対応 + gacha 3 variants を候補に追加。

各 sketch につき 4 + 3 = 7 候補 (align noS2 + shift v1/v2/v3 + gacha v1/v2/v3)。
ブラウザでクリックで複数選択可能 (緑ハイライト) → JSON ダウンロード。
"""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
REPO = "yushin-jizaie/draw_piper"
BRANCH = "claude/style-pool-rebalance-20260529"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"

ALIGN_BASE = "sketch_variations/align_noS2_all8_20260529_131555"
SHIFT_BASE = "sketch_variations/shift_v3_compare_20260529_100542"
# gacha は timestamp 付きなので auto-detect
GACHA_GLOB = "sketch_variations/gacha_char_auto_all8_*"

INPUTS = [
    ("B_round_smiley",   "logs/sketch_variations_20260528_084706/inputs/sketch_B_round_smiley.png",   "character"),
    ("C_face_with_neck", "logs/sketch_variations_20260528_084706/inputs/sketch_C_face_with_neck.png", "character"),
    ("D_stick_figure",   "logs/sketch_variations_20260528_084706/inputs/sketch_D_stick_figure.png",   "character"),
    ("F_angry_face",     "logs/sketch_variations_20260528_084706/inputs/sketch_F_angry_face.png",     "character"),
    ("house",            "logs/sketches_objects_20260528_183943/sketch_house.png",                    "object"),
    ("tree",             "logs/sketches_objects_20260528_183943/sketch_tree.png",                     "object"),
    ("cat",              "logs/sketches_objects_20260528_183943/sketch_cat.png",                      "object"),
    ("car",              "logs/sketches_objects_20260528_183943/sketch_car.png",                      "object"),
]


def read_companion(meta_p: Path) -> str:
    if not meta_p.exists():
        return ""
    for ln in meta_p.read_text().splitlines():
        if ln.startswith("companion_subject="):
            return ln.split("=", 1)[1]
    return ""


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
            out.append({
                "route": f"gacha {v}",
                "label": f"gacha {v} (seed={seed})",
                "strokes_png": f"{RAW_BASE}/{png_rel}",
                "rel_path": str(seed_dir.relative_to(_ROOT)),
                "gacha_seed": seed,
            })
    return out


def build_entries():
    entries = []
    for sid, ip, stype in INPUTS:
        cands = []
        # 1) align (S2 OFF)
        cands.append({
            "route": "align (S2 OFF)",
            "label": "align (S2 OFF)",
            "strokes_png": f"{RAW_BASE}/{ALIGN_BASE}/{sid}/30_vectorized_strokes.png",
            "rel_path": f"{ALIGN_BASE}/{sid}",
        })
        # 2-4) shift v1/v2/v3
        for v in ("v1", "v2", "v3"):
            comp = read_companion(
                _ROOT / SHIFT_BASE / v / sid / "00_auto_prompt.txt")
            cands.append({
                "route": f"shift {v}",
                "label": f"shift {v}: {comp}",
                "strokes_png": f"{RAW_BASE}/{SHIFT_BASE}/{v}/{sid}/30_companion_strokes.png",
                "rel_path": f"{SHIFT_BASE}/{v}/{sid}",
                "companion": comp,
            })
        # 5-7) gacha v1/v2/v3
        cands += find_gacha_variants(sid)
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
  .candidates { display: grid;
                grid-template-columns: 1fr 1fr 1fr 1fr 1fr 1fr 1fr 1fr; gap: 8px; }
  .panel { background: #1a1a1a; border: 2px solid transparent;
           border-radius: 6px; padding: 6px; cursor: pointer;
           transition: border-color 0.15s, transform 0.1s; position: relative; }
  .panel:hover { transform: translateY(-2px); border-color: #666; }
  .panel.input { cursor: default; opacity: 0.85; border-color: #333; }
  .panel.input:hover { transform: none; border-color: #333; }
  .panel.selected { border-color: var(--selected);
                    box-shadow: 0 0 0 3px rgba(44, 204, 119, 0.25); }
  .panel .panel-label { font-size: 10px; opacity: 0.8;
                        text-align: center; margin-bottom: 4px;
                        white-space: nowrap; overflow: hidden;
                        text-overflow: ellipsis; line-height: 1.2; }
  .panel img { width: 100%; height: auto; display: block;
                background: #fff; border-radius: 4px; aspect-ratio: 1; }
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

function render() {
  main.innerHTML = "";
  for (const entry of ENTRIES) {
    const row = document.createElement("div");
    row.className = "row";
    const ncands = entry.candidates.length;
    const nsel = (selections[entry.sketch_id] || []).length;
    row.innerHTML = `
      <h2>${entry.sketch_id} <span class="type">${entry.type}</span>${nsel > 0 ? `<span class="count">★ ${nsel} 件選択</span>` : ""}</h2>
      <div class="candidates" style="grid-template-columns: repeat(${ncands + 1}, 1fr)"></div>
    `;
    const grid = row.querySelector(".candidates");
    // INPUT パネル
    const inp = document.createElement("div");
    inp.className = "panel input";
    inp.innerHTML = `<div class="panel-label">INPUT</div>
                     <img src="${entry.input_png}" loading="lazy">`;
    grid.appendChild(inp);
    // 候補 N つ
    entry.candidates.forEach((cand) => {
      const p = document.createElement("div");
      p.className = "panel candidate";
      const sel = isSelected(entry.sketch_id, cand.route);
      if (sel) p.classList.add("selected");
      p.innerHTML = `<div class="panel-label" title="${cand.label}">${cand.label}</div>
                     <img src="${cand.strokes_png}" loading="lazy">`;
      p.addEventListener("click", () => toggleSelection(entry.sketch_id, cand.route));
      grid.appendChild(p);
    });
    main.appendChild(row);
  }
  // summary
  const summary = document.createElement("div");
  summary.className = "summary";
  const out = buildOutput();
  summary.innerHTML = `<h2 style="color:var(--accent);margin:0;font-size:14px">現在の選択 (localStorage 自動保存、 複数可)</h2><pre>${JSON.stringify(out, null, 2)}</pre>`;
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

render();
</script>
</body>
</html>
"""


def main() -> int:
    entries = build_entries()
    html = HTML_TEMPLATE.replace(
        "__ENTRIES__", json.dumps(entries, ensure_ascii=False))
    out_dir = _ROOT / "docs" / "selection"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "index.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"saved {out_path}")
    # 各 sketch の候補数を報告
    for e in entries:
        print(f"  {e['sketch_id']}: {len(e['candidates'])} candidates")
    return 0


if __name__ == "__main__":
    sys.exit(main())
