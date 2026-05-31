# 2026-06-01 scatter mode + 透明ボード線抽出 + webapp アップロード

本セッションで実装した 4 機能の設計メモ。

---

## 1. scatter placement (キャラを撒く)

### 狙い
companion 生成 (lineartLoRA char preset) のスプライトシート出力
(複数の小さなダイナミックポーズ) を活かし、 **入力の絵に被らないよう
周囲の空白に小さなキャラを散布**する演出。

shift placement が「1 体を最大空白へ移動」 なのに対し、 scatter は
「複数体を周囲のグリッド空白へ撒く」。

### パイプライン (`modules/stroke_scatter.py`)
1. `split_characters` — 塗りシルエットのシートを連結成分で個々のキャラ
   crop に分割 (dilate で手足の隙間を 1 成分に統合、 面積でフィルタ)
2. `vectorize_characters` — 各 crop を **canny (輪郭) mode** で線画化
   (塗り → 輪郭線。 binarize だと塗り潰しがメッシュになる)
3. `free_cells` — 入力 bbox と重ならない grid セルを列挙 (既定 3×7)
4. `scatter_strokes` — 各キャラを空きセルに縮小配置 (アスペクト維持、
   セル占有率 `fill`=0.8)
→ `input_strokes + 散布キャラ` の合成 strokes (shift と同形式)

### 本番 CLI (`scripts/scatter_companions.py`)
```
./venv/bin/python -m scripts.scatter_companions \
    --input  sketch_variations/_inputs/scatter_input.png \
    --sheet  assets/scatter_sheet_lineart_char.png \   # 既存シート(GPU不要)
    --output sketch_variations/disp_scatter_demo/scatter/v1_seed555 \
    --seed 555 --resolution 704x1472
```
`--sheet` 省略時は preset (`illustrious_v2_lineart_char`) で新規生成 (GPU)。
出力は webapp の disp pattern-2 がそのまま拾う構成
(`30_vectorized_strokes.png` / `strokes.json` / `vec_debug/06_strokes.png`)。

再現用スプライト: `assets/scatter_sheet_lineart_char.png`

---

## 2. 透明ホワイトボード 線抽出 (背景差分 + 特定色)

### 仕様 (2026-06-01 ユーザー確認、 ここで初めて文書化)
カメラ (1280×720) は **透明** ホワイトボードに描かれた線を撮る。 1 枚に:
- **手前** (カメラ側): 自分が描いた線
- **奥** (反対側): もう 1 人の人間が描いた線
- さらに奥: その人間の体 + 現実空間の背景の映り込み

が重なる。 線の色は統一。 背景・人体・映り込みを除いて「線だけ」 が欲しい。

### 手法 (`modules/line_extract.py`)
2 つの手がかりを **AND**:
1. **背景差分** — 空ボードを基準 (`background`) に撮り、
   `|frame - background|` が閾値超 = 新規。 静的な映り込み・室内背景を除去。
2. **色フィルタ** — 線色 (HSV 範囲 or 「暗い線」=低 V) に一致する画素のみ。
   人体 (肌・服)・色付き背景を除去。

→ 線 = 「新規」 AND 「線の色」。 人体は色で、 静的背景は差分で落ちる
(合成スモークテスト `python modules/line_extract.py --smoke` で検証済)。

色プリセット: `dark`/`black` (低V), `blue`, `red` (hue 巻き 2 範囲), `green`。

### 未解決 (明日の判断待ち、 §HANDOFF 参照)
- 線の実際の色 (黒/青/赤?) → 今は GUI で選択可、 既定 `dark`
- **near/far の分離は単一カメラでは困難** → 現状は両側まとめて抽出

### GUI 統合 (`scripts/pipeline_test_gui.py`)
入力ソース欄に「透明ボード線抽出」 フレーム:
- 「背景キャプチャ (空ボード)」 → `self.background_bgr`
- 線の色 / 差分閾値 / 暗線V上限
- 「線抽出 → 入力に設定」 → 抽出結果を白背景黒線 PNG にして
  `selected_sketch_path` に差し替え (そのまま生成パイプラインへ)

---

## 3. webapp アップロード (`scripts/upload_to_webapp.py`)
GUI で生成した cycle_dir を選定 webapp の候補列として取り込む。
- 各アップロード = 独立 sketch_id 列 (`disp_gui_uploads/<sid>/v1_seed/`)
- `disp_gui_uploads/_inputs.json` に入力定義を追記 →
  `build_selection_webapp` がマージして列を生やす
- `--local` (push なし) / `--push` (online + git push)

GUI: 結果ボタン行の「⬆ webapp にアップロード」。 表示名入力 + push 確認。

---

## 4. ローカル webapp 起動 (`scripts/webapp_local`)
```
~/draw_piper/scripts/webapp_local [PORT]   # 既定 8765
```
`build_selection_webapp --local` で repo-root 相対パス再ビルド →
`http.server` 配信 → `http://localhost:8765/docs/selection/index.html`。
push 不要でローカル生成物を確認できる。
