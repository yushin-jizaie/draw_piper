# ImageGen LoRA 学習 作業引き継ぎ (2026-05-27 23:00 時点)

> Claude Code Web 版 / 別セッションへの引き継ぎ。
> ブランチ: `claude/smooth-curve-rendering-e88Vb` (全 push 済)
> 親プロジェクト: `~/draw_piper` (Piper ロボットアーム × ホワイトボード描画)
> 本作業は画像生成スレッド (M9 from MILESTONES.md の先) の探索中。 完了 (●) にはまだ到達していない。

---

## TL;DR (5 行)

1. **目的**: ユーザのスケッチ → 松本大洋風線画 (ロボット描画可能) を生成する LoRA を作る
2. **進捗**: ImageGenerator に inpaint mode 実装済。 LoRA 学習 v2 (lineart grayscale) が現在進行中 (~23:30 完走予定)
3. **直近の失敗**: lineart v1 (bolden ON) で大面積黒テクスチャ暴走 → bolden 削除して v2 再学習中
4. **次セッションの最優先**: v2 学習の結果を確認 → 推論テスト → ダメなら更なる対処
5. **最重要制約**: 出力はロボット描画 = **線のみ・白背景・ハッチング NG・塗りつぶし NG**。 これを忘れない

---

## プロジェクトの本流 (再確認)

- `draw_piper` = Piper アームでホワイトボードに描く
- パイプライン: VLM → prompt_builder → **ImageGenerator** → Vectorizer → Robot
- 本作業は **ImageGenerator** 部分の品質向上 (M8/M9 完了済の先)
- 出力画像は 後段 Vectorizer で strokes 化 → ロボットがペン 1 本で描画
- → **塗りつぶし・ハッチング・グレー塗り・紙質感 は全て NG** (Vectorizer がノイズと誤認 + ロボットが描けない)

詳細は `MILESTONES.md` 参照。 現在地は M12 (canvas キャリブ v3)。

---

## 作業の経緯 (時系列、 失敗から学んだこと)

### 1. 構図問題の解決 (済)

ユーザの 「小さい oval を そのスケールで保持しつつ周辺に肉付け」 要望に対し、 当初の text2img + ControlNet では SDXL の "subject fills frame" prior が支配的で 顔がフレーム超え。

**解決**: `img2img + ControlNet` モード追加 (`StableDiffusionXLControlNetImg2ImgPipeline`)。 init image にユーザのスケッチを渡して 白背景 prior を保持。 commit `29cff00`。

### 2. ControlNet variant 整合 (済)

MistoLine は fp16 variant のみ提供。 base model (Animagine) が variant=None だと ControlNet 側も None で .bin を探して失敗。 commit `47bc5f0` で base / ControlNet 独立解決。

### 3. peft / diffusers 版整合 (済)

`train_text_to_image_lora_sdxl.py` が peft を直接 import + `check_min_version` が installed diffusers と整合しない問題。 commit `23cb2d1` で `_installed_diffusers_tag()` でインストール済 diffusers 版に合致する git tag で clone。

### 4. matsumoto LoRA v0 学習 (済、 1080 step で中断)

35 枚の漫画パネルで学習。 ターミナル切れで step 1323/1500 で中断したが、 checkpoint-1080 を `matsumoto_taiyo.safetensors` (89MB) として確定。 commit history 参照。

### 5. inpaint mode 実装 (済)

img2img の構造的限界 (init 一律保持) を超えるため、 inpaint pipeline を追加。 ユーザの黒線部分 (mask=0) は exact 保持、 白部分 (mask=255) は LoRA 全力描画。 commit `76e4227`。

### 6. ❌ LoRA v0 (panel 学習) の暴走

inpaint mode で v0 LoRA を使うと、 学習データが漫画パネル全体 (screentone・ハッチング・吹き出し・複数キャラ) だったため、 LoRA は「松本らしさ = ハッチング背景 + 塗り」 として記憶。 出力に **背景ハッチング・グレー塗り** が必ず混入 → ロボット描画 NG。

**問題の根本**: dataset が「線 + 塗り + トーン」 のセット。 LoRA はそれをセットで学習する。

### 7. 線画抽出パイプライン実装 (済)

`scripts/extract_lineart.py` を新規作成。 controlnet_aux の `LineartAnimeDetector` で 36 枚の raw 画像から線画抽出。

- Method A (cv2 adaptive threshold): カラー画で塗りつぶし残る → 不採用
- Method B (LineartAnimeDetector ML): 全画像で クリーンな線抽出 → 採用

36 枚から 7 枚除外 (ロゴ・大文字テキスト多い画像)、 残り 29 枚で学習用 dataset 化。 commit `b827d88`、 `5543bc8`。

### 8. ❌ LoRA v1 (lineart + bolden) の暴走

`--bolden` (threshold 180 + dilate 1 で 2 値化) を適用して学習。 1500 step 完走したが、 推論時に **黒テクスチャで全画面埋め尽くす** 失敗。

**原因**: ML lineart 検出器の出力 grayscale を 2 値化する際、 「弱く検出された線」 (シロの黒髪領域のシルエット境界・服の塗り境界 etc) も全部黒化。 結果として dataset に大面積の黒が残り、 LoRA がそれを学習。

詳細: docs に書いてないけど、 学習データ `training/matsumoto_taiyo/lineart_b_bold/` の中身が「線画」 ではなく「黒い塊 + 線」 になっていた。

### 9. ❌ LoRA v2 (lineart grayscale、 bolden 無し) も失敗

`--bolden` 削除して grayscale 出力で 1500 step 再学習 → 推論結果が **更に悪化**
(黒テクスチャ + RGB カラーノイズ点描の暴走)。

実出力サンプル: `logs/imagegen_comparison_20260527_234800/matsumoto_taiyo_inpaint.png`

**確定診断**: lineart-anime 検出器の grayscale 出力を SDXL 学習データに直接
使うアプローチが **構造的に間違い**。

理由: SDXL は VAE で latent 化して学習する。 grayscale lineart (中間グレー値の
分布) を VAE エンコードすると 「ハッチング的高周波 latent」 として表現される。
LoRA はそれを「松本らしさ」 として獲得 → 推論で全画面ハッチング暴走。

bolden 有無 (v1/v2) に関係なく LoRA は「黒テクスチャ pattern」 を学んでいる。
原因が dataset 表現形式の根本にある。

### 10. → 次セッションの本命: Plan D

v0 (panel)、 v1 (lineart + bolden)、 v2 (lineart grayscale) で 3 連続失敗。
別アプローチが必要。 最有力候補は **dataset を 「線が細く塗り少ない画像」 だけに
精選 → hyperparameter を緩めて再学習** (引き継ぎ §「次の手 D」)。

---

## 走っているプロセス (なし)

v2 学習は完走済 (23:30 頃)。 PID 203679 は消えてる。 推論 1 枚走らせて
結果 (上記 §9) を確認済。

LoRA 退避状況:
- `training/lora/matsumoto_taiyo.safetensors` = v2 (失敗、 黒+カラーノイズ暴走)
- `training/lora/matsumoto_taiyo_bold_v1.safetensors` = v1 (失敗、 黒テクスチャ)
- v0 (panel 学習) は上書き済で残ってない

次セッションで作るなら v3 として `matsumoto_taiyo_v3_*.safetensors` 命名推奨。

---

## 次セッションの最優先タスク

v2 完走 + 推論結果確認済 → **失敗** (黒テクスチャ + RGB カラーノイズ暴走)。
LoRA 3 連続失敗 (v0/v1/v2)。 次の選択肢:

### P0: 路線判断 (まずユーザに確認)

LoRA は 3 連続詰まり中。 ユーザから新提案 **「そもそも漫画を描くようなモデルを
使えばいいのでは?」** あり (2026-05-27 23:50)。 正しい指摘 — Animagine XL 3.1 は
anime/celluloid 寄りで manga (印刷物) 寄りじゃない。 ベースモデル変えれば
LoRA 不要 or 軽い LoRA で済む可能性大。 これを **最有力 (E 路線)** とする:

- **E 路線**: ベースモデルを manga 寄り SDXL に乗り換え (新規、 これを推奨)
  → 後述
- **A 路線**: 既存 Animagine XL 3.1 で dataset 精選 + hyperparam 緩めて v3 → P1
- **B 路線**: LoRA を捨てて Animagine 素のまま test_vlm_to_image 通しテスト → P2

時間 / モチベ / 完成度の優先度次第。 ただ E が構造的にクリーンなので、
時間あれば E → 結果次第で A/B、 が筋良い。

### P_E (新提案、 manga base 乗り換え)

調査 + 試行手順:

1. **manga 寄り SDXL base モデルを探す** (HuggingFace で):
   - `cagliostrolab/animagine-xl-4.0` (もし出てれば、 3.1 の後継)
   - `OnomaAIResearch/Illustrious-xl-early-release-v0` 系 (NoobAI 系)
   - `Laxhar/noobai-XL-1.1` 等の NoobAI XL 系
   - `John6666/manga-style-models-XYZ` (個人配布の SDXL manga 派生)
   - Civitai (HuggingFace 経由 mirror あり) で 「manga」 「lineart」 タグの
     SDXL チェックポイント
   - 確実な ID は Web 版で `huggingface-cli search` or huggingface.co 検索

2. **MODEL_PRESETS に追加** (`modules/image_gen.py`):
   ```python
   "manga_base_mistoline": {
       "base_model_id": "<found-id>",
       "controlnet_id": "TheMistoAI/MistoLine",
       "variant": "fp16" if has_fp16 else None,
       "num_inference_steps": 28,
       "guidance_scale": 6.5,
       "controlnet_conditioning_scale": 0.85,
       "style_hint": "manga style, ink line art, monochrome, white background",
       "guide_dilate_ksize": 5,
       "img2img_strength": 0.85,
       # まず LoRA 無しで試す
       # "lora_path": None,
   },
   ```

3. **LoRA 無しで推論テスト** (manga base の素の出力をまず見る):
   ```bash
   python3 -m scripts.compare_imagegen_models \
       --guide scripts/test_sketch.jpg \
       --prompt "young boy with messy hair, surprised expression" \
       --presets manga_base_mistoline --seed 42
   ```

4. **結果分岐**:
   - 既に十分 manga タッチ + 白背景キープ + 線のみ → 🎉 これで採用、 LoRA 不要、
     P3 (ロボット統合) へ
   - manga タッチ出るが弱い → 既存の `matsumoto_taiyo.safetensors` (v2 黒暴走版)
     を LoRA scale 0.5 程度で軽く乗せる(暴走しない範囲で松本らしさ補強)
   - 全然違う(リアル写真風 etc) → 別 base 探す

5. **既存 ControlNet + LoRA 互換性**: SDXL ファミリ間は互換。 MistoLine も SDXL
   ControlNet なので動く。 LoRA も SDXL LoRA であれば 別 base 上でも乗る。

### P1 (A 路線、 E がダメだった場合のフォールバック): dataset 精選 + hyperparam 緩めて v3

3 連続失敗の真因は **dataset 含有の大面積黒**。 LoRA がそれを獲得 → 推論で
ハッチング暴走。 物理的に排除する。

1. **手動精選**: raw/ から「黒塊が多い画像」 を除外。 残し方の目安
   (preview を見て主観で振り分け):
   - 除外候補 (黒髪・黒服が画面の大半): `IMG_4326` (シロ夜空)、 `IMG_4324`
     (ゴーグル)、 `EdvzOK7VAAAoA9G` (黒シャツ)、 `1.png` (4 コマ黒線多)、
     `2.png` (鉄コン、 影濃い)、 `69d7496` (シロクロ近接、 黒髪)、
     `8f640a63` (シロ青シャツ、 黒髪)
   - 残し候補 (線細め・塗り少なめ): `1090748_300` (海辺、 アニメ調)、
     `IMG_4311` (花男表紙、 線細い)、 `IMG_4321` (ナンバーファイブ表紙)、
     `5e4f3e756cefd41aaf5a88da14f2020c` (バットマン)、 `feccbf...` (Peco)、
     `o0600045013450720343` (5 人並び)、 `f341cbadd...` (3 人正面)
   - 目安: **約 12-15 枚に精選**

2. **lineart 再抽出** (extract_lineart は bolden 無しで OK):
   ```bash
   python3 -m scripts.extract_lineart \
       --input training/matsumoto_taiyo/raw \
       --output training/matsumoto_taiyo \
       --apply b \
       --exclude "IMG_4310,IMG_4316,IMG_4318,desktop-0519+(2),images,IMG_4312,20061104011427,IMG_4326,IMG_4324,EdvzOK7VAAAoA9G,1,2,69d7496db867374debb24b8f46387853,8f640a63f5520f466b5ba1560d2e89dc"
   ```
   (除外を 7 → 14 に増やす、 残り 22 枚 → さらに preview 見て手で絞る)

3. **dataset 再構築 + LoRA 退避**:
   ```bash
   mv training/matsumoto_taiyo/dataset training/matsumoto_taiyo/dataset_v2_failed
   mv training/lora/matsumoto_taiyo.safetensors training/lora/matsumoto_taiyo_v2_failed.safetensors
   python3 -m scripts.prepare_style_dataset \
       --input training/matsumoto_taiyo/lineart_b \
       --output training/matsumoto_taiyo/dataset \
       --trigger mt_taiyo_style --no-caption
   cp training/matsumoto_taiyo/dataset_v2_failed/*.txt training/matsumoto_taiyo/dataset/
   # 除外した画像の .txt は残るので、 dataset/ の .png に対応する .txt のみ残すよう掃除:
   for txt in training/matsumoto_taiyo/dataset/*.txt; do
     png=${txt%.txt}.png
     [ -f "$png" ] || rm "$txt"
   done
   ls training/matsumoto_taiyo/dataset/ | wc -l  # 12-15 * 2 (png+txt) になる
   ```

4. **hyperparameter を緩めて学習** (LoRA が「テクスチャ暗記」 しにくくする):
   ```bash
   nohup python3 -m scripts.train_style_lora \
       --dataset training/matsumoto_taiyo/dataset \
       --name matsumoto_taiyo \
       --base cagliostrolab/animagine-xl-3.1 \
       --rank 16 --steps 800 --lr 5e-5 \
       > training/lora_runs/lineart_v3_$(date +%Y%m%d_%H%M%S).log 2>&1 &
   disown
   ```
   - rank 16 (32 から半減): LoRA の表現容量を絞ってテクスチャ暗記を防ぐ
   - steps 800 (1500 から短縮): overfit 前に止める
   - lr 5e-5 (1e-4 から半減): 浅く学習させる
   - 完走 ~20 分

5. 完走後 推論テスト → 結果次第で更に調整 or B 路線へ転戦

### P2 (B 路線): LoRA 捨てて Animagine + ControlNet でロボット統合へ

過去テストで `animagine_xl_31_mistoline` preset (LoRA 無し) は:
- ✅ 白背景キープ (img2img + ControlNet)
- ✅ 顔 oval スケール保持
- ✅ Clean lineart (Animagine 自体が anime 線画学習済)
- ❌ 「松本タッチ」 はほぼ無し (Animagine の素の絵)

これで M9 の test_vlm_to_image を回す。 「ホワイトボードに線画描く」 という
project 目標は達成可能。 松本タッチは将来課題。

```bash
# 通し試験 (VLM → ImageGen → Vectorizer)
python3 -m scripts.test_vlm_to_image --steps 4 --cycles 1
# cycle_NN/strokes.json と vec_debug/06_strokes.png を確認

# matsumoto 系 preset 指定でなく、 default (animagine_xl_31_mistoline?)
# あるいは スクリプトの内部 preset 指定を確認、 必要なら animagine 指定。
grep -n "ImageGenerator" scripts/test_vlm_to_image.py
```

ロボット描画まで通せば本来の MILESTONE 完成。 M9 から先の正常地点 ● を
MILESTONES.md に追記提案。

---

## 戦略レビュー候補 (ユーザ提案、 2026-05-27 24:00)

ユーザから 「現状まぁまぁ動いてる、 一旦ベンチマークとして別視点で考える」 提案。
画像生成側 (本ドキュメント) とは別の軸で、 ロボット動作の **スムーズさ** に
着目。 次セッションで深掘り候補:

### A. Frida (CMU robotics painting) — https://github.com/cmubig/Frida

物理筆ロボットの参考 project。 取れそうなヒント (工数順):

1. **Bezier stroke 表現**: Vectorizer の polyline を Bezier 曲線 fit、
   アーム側で MOVE_C 系列 or Cartesian spline で実行。 折れ線のカクつき解消
2. **曲率連動の速度プロファイル**: 緩い曲線=速く / 鋭い曲線=遅く、 jerk 削減
3. **Stroke ordering 最適化**: 近い stroke を連続実行で travel 時間削減
   (TSP 近似で greedy nearest-neighbor 程度で十分)
4. **Look-ahead descent height**: 次 stroke が近ければ pen-up を低く
5. **カメラフィードバック loop**: 撮影 → 差分検出 → 補正 stroke 追加

**現状 draw_piper との接続**: 軌道実行は M5/M6/M11/M12 で OK。 ストローク
**表現** が polyline のまま → 上記 1 (Bezier 化) が最大の改善ポイント。

調査タスク (次セッション):
- Frida リポの `src/` 構造を読んで Bezier renderer の実装を把握
- draw_piper の `modules/vectorizer.py` (or 該当) を Bezier 出力に拡張可能か
- `modules/robot.py` の `draw_stroke_panel` を Bezier 入力対応に拡張する設計

### B. MoveIt2 再検討

過去に検討した可能性あり (docs/ で要確認)。 再度の検討論点:

**Pros**:
- jerk-limited time parameterization で物理的にスムーズな速度
- Cartesian path planning (compute_cartesian_path) で平面経路保証
- 衝突回避 (押し付けすぎ防止)

**Cons**:
- 工数大 (ros2_control hardware interface で Piper SDK と接続、 1-2 週間)
- 既存 wall_drawing_gui / drag-teach キャリブの大幅書き直し
- N4 (master mode feedback ダウン) が MoveIt 層でも再発する可能性

調査タスク (次セッション):
- `agilex-robotics/piper_ros` などの MoveIt2 統合状況を確認
- 既存 Piper SDK と ros2_control の薄い hardware interface だけ書く案
  (full ROS2 化せず MoveIt の planning だけ流用) の実現性
- Frida 案 (Bezier + 速度プロファイル) で十分か、 MoveIt が必要か の評価軸を
  決める (= 「スムーズさ」 の定量基準)

### 推奨進め方

時間予算次第:
- **短期** (1-2 日): Frida 案 1 + 2 (Bezier + 速度プロファイル) だけ実装。
  軌道がどれだけ滑らかになるかベンチマーク
- **中期** (1 週): Frida 案 3 + 4 (ordering + look-ahead) 追加
- **長期** (2-4 週): MoveIt2 移行検討、 Piper ROS2 driver 統合

短期だけで体感大きく改善する可能性が高いので、 そっちから始めるのが筋。
MoveIt2 はその後 「Bezier では足りない領域」 が見つかってから判断。

---

### 1. `--bolden` を再有効化しない

v1 で 黒テクスチャ暴走の元凶。 grayscale lineart のままで OK。 もし「線が薄い」 と感じても bolden 復活ではなく、 **学習 step 増 or rank 増 で対処** する。

### 2. 推論を学習中に走らせない

GPU 16GB しか無い。 学習が 10.5GB、 推論も 同程度 必要 → 100% OOM。 `ps -p 203679` が消えるまで推論コマンドは打たない (前回 OOM 14 連発した)。

### 3. ターミナル切れ防止

`nohup ... & disown` のお作法を必ず守る。 これを忘れた回 (1080 step で中断) があった。

### 4. matsumoto preset の値を意図せず変えない

`modules/image_gen.py` の `matsumoto_taiyo_inpaint` preset は現状:
- `lora_scale: 1.4`
- `inpaint_strength: 1.0`
- `inpaint_keep_dilate: 4`
- `style_hint`: trigger + 描く対象だけ (短い)
- `guidance_scale: 7.0`

これらは試行錯誤の結果。 動作試験では CLI overrride (`--lora-scale 1.5` 等) を使う。

### 5. style_hint は 77 token 超えない

CLIP 制限。 超えると末尾が切られて指示が届かない。 commit `18d122d` で短縮済だが、 prompt + style_hint 合計を意識。

---

## 次の手 D (v2 もダメだった場合の対処順)

1. **dataset 精選**: lineart_b/ から「黒い塊が残ってる画像」 を手動 exclude。 候補:
   - `IMG_4326` (シロ黒髪)、 `IMG_4324` (ゴーグルキャラ)、 `EdvzOK7VAAAoA9G` (黒シャツ)
   - 残り 20 枚程度に絞って再学習
2. **学習 hyperparameter 緩める**: rank 32 → 16、 lr 1e-4 → 5e-5、 steps 1500 → 800
   ```bash
   python3 -m scripts.train_style_lora --dataset ... --rank 16 --steps 800 --lr 5e-5
   ```
3. **別の lineart 抽出器**: `LineartDetector` (anime じゃない方) / Canny / DexiNed 等を試す
4. **LoRA 諦めて prompt engineering**: 「松本タッチ」 は LoRA 無しの SDXL + prompt で部分的に再現可能。 完璧主義捨てる選択肢

---

## 重要ファイル早見

| 何 | パス |
|---|---|
| この引き継ぎ | `docs/20260527_2300_imagegen_lineart_lora_handoff.md` |
| 全体方針 | `MILESTONES.md`, `CLAUDE.md` |
| ImageGenerator 本体 | `modules/image_gen.py` |
| 学習スクリプト | `scripts/train_style_lora.py` |
| 線画抽出 | `scripts/extract_lineart.py` |
| 推論比較 | `scripts/compare_imagegen_models.py` |
| dataset 準備 | `scripts/prepare_style_dataset.py` |
| LoRA v2 (本命) | `training/lora/matsumoto_taiyo.safetensors` (学習完走後) |
| LoRA v1 退避 | `training/lora/matsumoto_taiyo_bold_v1.safetensors` (黒テクスチャ版) |
| 学習データ v2 | `training/matsumoto_taiyo/lineart_b/` (29 枚 grayscale) |
| 学習データ v1 退避 | `training/matsumoto_taiyo/lineart_b_bold/` (失敗版) |
| 学習 dataset | `training/matsumoto_taiyo/dataset/` (29 枚 + caption.txt) |
| 学習 raw | `training/matsumoto_taiyo/raw/` (36 枚、 触らない) |
| 学習ログ v2 | `training/lora_runs/lineart_v2_grayscale_*.log` |

---

## 直近の commit history (上から新しい順)

```
b73e0b4  inpaint preset 調整 (lineart LoRA に合わせて LoRA scale UP)
2182e0f  --bolden で B 出力を 2 値化、 --exclude で除外指定
5543bc8  detector 出力 shape を元画像に揃える
b827d88  学習 raw → 線画抽出スクリプト新規作成
4805ce0  inpaint preset を方針反転 (LoRA に自由を与える、 周辺描き足し)
8074e8c  inpaint mode の LoRA 背景埋め暴走を抑制
76e4227  inpaint mode 追加 (黒線 exact 保持 + 白部分を LoRA 全開で再描画)
18d122d  3 preset の style_hint を短縮 (CLIP 77 token 制限対策)
8641888  matsumoto preset を方針 B 値で確定 (白背景 + 顔ディテール)
81d2353  --image_column=file_name → image に修正
23cb2d1  pip 版 diffusers と clone tag を整合
e796bb9  peft を deps に追加 + Animagine の --variant=fp32 削除
47bc5f0  ControlNet の variant を base model と独立に解決
29cff00  img2img + ControlNet モードで構図維持 (顔はみ出し fix)
```

---

## ユーザの目標を ふたたび 明文化

「**スケッチで描いた顔の輪郭はそのまま尊重しつつ、 周辺に松本大洋風の線で体・髪・服を描き足したい。 ロボットアームで描けるよう、 黒い塗りつぶしやハッチングは無し。 純粋な線画のみ。**」

これに合致してるか、 全ての判断の基準。
