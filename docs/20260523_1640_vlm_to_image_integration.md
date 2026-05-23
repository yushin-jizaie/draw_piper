# VLM と画像生成のつなぎこみ (段階的スワップ実証)

> 日時: 2026-05-23 16:40 (JST)
> 関連既存ファイル: `20260521_1757_drawing_system_v04_design.md`, `20260522_2250_vlm_vram_measurement.md`, `20260522_2330_drawing_system_v05_design.md`, `20260523_1530_step_c_completion.md`

## 実施したこと

Step C 完了 (2026-05-23 15:30) を受けて、Qwen2.5-VL-7B (VLM) と SDXL Turbo + MistoLine (ImageGenerator) を**直列のパイプラインとして結合**する作業を実施した。

`modules/image_gen.py` は当時 placeholder だったため、`scripts/measure_sdxl_vram.py` の実機検証済みロジックを基にクラス化。`modules/vlm.py` (v0.5 で TopicGuess 出力済み) と同じ流儀の `load() / unload() / context manager / generate()` API に揃えた。

合わせて、両モジュールを直列に動かす統合テスト `scripts/test_vlm_to_image.py` を新規作成。median 合成キャプチャを模した入力スケッチから、`TopicGuess` → `build_prompt` → `ImageGenerator.generate` までを 1 サイクルとして実行し、各ステージ前後の VRAM スナップショットを取る。

### 設計の選択: 同時常駐ではなく段階的スワップ

設計 v0.4 の VRAM 配分計画は「Qwen2.5-VL-7B INT4 + SDXL Turbo + Lineart ControlNet を同時に GPU 常駐」「OOM が出たら ControlNet を CPU offload」を想定していたが、Step C で実測 (VLM peak 5.98GB / SDXL peak 12.93GB / 合計 18.92GB) の結果、16GB 予算を 2.92GB 超過することが確定。

ターン制 v0.4/v0.5 では VLM 推論と SDXL 推論が並列ではなく直列に走るため、**同時に常駐させる必要がない**と判断:

```
[Prep Thread]
  1. median capture
  2. VLM.load()       ← ここから GPU に乗る
  3. VLM.predict_intent
  4. VLM.unload()     ← GPU から降ろす (allocated=0.01GB に戻る)
  5. ImageGenerator.load()  ← ここから別モデルが GPU に乗る
  6. ImageGenerator.generate
  7. ImageGenerator.unload()
```

`load/unload` のオーバヘッドが許容できれば、CPU offload や量子化のさらなる強化は不要になる。本作業はこの仮説を実機で検証することも兼ねた。

### 追加した資産

- `modules/image_gen.py` (placeholder → 本実装)
  - `ImageGenerator` クラス: `load() / unload() / warmup() / generate(prompt, guide_image, ...)`
  - デフォルトパラメータは Step C 完了時の確定値 (steps=4, guidance=0.0, cn_scale=0.8, MistoLine)
  - 内部実装は `scripts/measure_sdxl_vram.py` の動作確認済みコードを継承
- `scripts/test_vlm_to_image.py` (新規)
  - VLM → prompt_builder → ImageGenerator の直列パイプライン
  - 各ステージ前後で `gpu_mem_snapshot` を取る (measure_*.py と同フォーマット)
  - `--cycles N` で同パイプラインを N 回繰り返し、安定性を見られる
  - サイクルごとに `cycle_NN/` 配下に中間ファイル (`vlm_raw.txt`, `topic_guess.json`, `prompt.txt`, `generated.png`, `timing.json`) を残す

## 結果

### 1 サイクル動作確認 (`--steps 4`)

| ステージ | 時間 | allocated | peak (累積) | 備考 |
|---|---|---|---|---|
| baseline | — | 0.00GB | 0.00GB | |
| VLM load | 10.4s | 5.51GB | 5.55GB | 初回 HF メタ問い合わせ込み |
| VLM predict | 4.0s | 5.51GB | 6.02GB | 63 tok / 15.9 tok/s |
| **VLM unload** | 0.5s | **0.01GB** | 6.02GB | **解放成功** |
| ImageGen load | 3.1s | 8.96GB | 8.96GB | controlnet + pipeline + to(cuda) |
| ImageGen warmup | 1.6s | 8.96GB | 12.93GB | cuda allocator が拡張 |
| ImageGen generate (4-step) | 2.6s | 8.97GB | 12.93GB | |
| **ImageGen unload** | 0.7s | **0.01GB** | 12.93GB | **解放成功** |

VLM unload 直後の allocated が 0.01GB に戻ることを実機で確認。同時最大は **12.93GB << 15.57GB 予算** に収まる。

### 3 サイクル安定性確認 (`--steps 4 --cycles 3`)

| ステージ | Cycle 1 | Cycle 2 | Cycle 3 |
|---|---|---|---|
| vlm_load_s | 10.50 | 7.60 | 7.58 |
| vlm_predict_s | 4.11 | 3.68 | 3.60 |
| vlm_unload_s | 0.50 | 0.57 | 0.57 |
| image_gen_load_s | 3.08 | 2.66 | 2.67 |
| image_gen_warmup_s | 1.61 | 1.52 | 1.48 |
| image_gen_generate_s | 2.70 | 2.73 | 2.65 |
| image_gen_unload_s | 0.74 | 0.75 | 0.75 |
| **cycle total** | **23.2s** | **19.5s** | **19.3s** |

- 2 回目以降は初回 HF メタ問い合わせ分 (~3s) が消えて定常化
- 3 サイクル通して allocated は確実に 0.01GB へ戻る (リーク無し)
- 定常サイクル時間は **約 20 秒**。2 分サイクル予算に対して圧倒的余裕

### VLM の出力 (品質確認)

入力スケッチ (黒い円+目2つ) に対する v0.5 カタログ分類:

```json
{
  "subject_ja": "ロボット",
  "location_ja": "不明",
  "action_ja": "不明",
  "missing_elements": ["目以外の部分", "口", "手足"],
  "confidence": 0.70
}
```

- 単体計測時 (2026-05-22 v0.4 プロンプト) は「人間の顔」と解釈したが、v0.5 の選択肢付きプロンプトでは「ロボット」を選択
- 選択肢を絞ることで判断空間が安定する v0.5 設計意図どおりの挙動
- location / action は手がかり不足で "不明" → これも v0.5 で意図したフォールバック

### SDXL の出力 (生成画像)

prompt: `"robot , line art, black ink on white, simple, clean lines, minimal detail, no shading, white background"`

- 出力は **ロボットの全身画**。VLM の subject 推定が下流まで正しく伝わっている
- ただし Step C 完了時に予告されていた v0.4 設計の "穴" 3点がそのまま現れる:
  1. **写実画問題**: `line art` 指示を Turbo が無視、鉛筆スケッチ風で出力
  2. **ユーザの絵が残る**: 入力の円+目2つが画像中央 (ロボットのお腹) に楕円として残存
  3. **線が多すぎ**: 機械パーツの細線が大量

これら3点は Step C 完了時に `[Canny strong_blur → 差分検出 → ベクトル化]` の後処理パイプラインで解決済みと確認されている。本作業は **生成画像をそのまま使うのではなく**、後続のベクトル化前段までを通す確認として完了とみなす。

## つまずいた点

### 1. プロンプトに残るカンマ前スペース

`prompt_builder.build_prompt(guess)` が `"robot , line art, ..."` のように、`location_en` と `action_en` が空文字のとき**カンマ直前にスペース**を残す。

```python
# UNKNOWN_LOCATION.en == "" のとき
"{subject_en} {action_en} {location_en}, ..." .format(subject_en="robot", action_en="", location_en="")
# → "robot  , line art, ..."
# _normalize_spaces で連続スペースは縮むが、","直前の空白は維持される
```

SDXL Turbo はプロンプト追従が弱いので生成への影響は軽微 (今回も問題なくロボットが生成された)。が、整形しておくのが筋。修正は別件として扱う。

### 2. ファイル受け渡しでの混乱

私 (Claude チャット) → Claude Code への手渡しで、当初「ブラウザからのダウンロード → cp」を案内したが、ユーザ側のダウンロードフォルダにファイルが落ちず混乱。最終的に `cat > path << 'EOF' ... EOF` の heredoc 貼り付けで解決。今後のファイル渡しは heredoc を第一選択にする。

## 学んだこと

### 段階的ロード/アンロードは想定以上に軽い

設計 v0.4 で「load/unload オーバヘッドは無視できるはず」と仮説を立てていたが、実測:

- VLM load: 初回 10s / 定常 7.6s
- VLM unload: 0.5s
- ImageGen load: 初回 3.1s / 定常 2.7s
- ImageGen unload: 0.7s

これは **HF キャッシュにモデルが既にある前提**。1 サイクル内の load+unload 合計が 11s 程度で、生成時間 (2.6s) と合わせても 20秒未満。`Robot Thread` が描画している 60〜120秒間に余裕で 1〜2 サイクル分の prep が回せる。

### `force_gc_and_empty_cache()` を 3 回呼ぶ意義

unload 直後に `gc.collect() + torch.cuda.empty_cache()` を 1 回だけ呼ぶと、python 側 finalizer 残りで allocated が 0 まで落ちきらないことがある (実装時の懸念)。本実機計測では 1 回でも 0.01GB に落ちたが、念のため 3 回ループで叩く実装にした。コストは µs 単位なので保険として残す。

### v0.5 の選択肢付きプロンプトは効果がはっきり出る

同じ入力スケッチに対して、v0.4 自由作文プロンプトでは「人間の顔」、v0.5 選択肢付きでは「ロボット」と異なる解釈に。これは VLM が**選択肢に最も近いものを能動的にマッチ**しているためで、想定どおり。選択肢を増やすと判別が難しくなる側面はあるが、5x5x5 の初期プールでは安定した。

### VRAM 設計の総括: CPU offload も量子化強化も不要

段階的スワップを採用したことで、設計 v0.4 で記載した「OOM が出たら ControlNet を CPU offload」「SD1.5 化」「VLM 3B 化」のフォールバック階段は **すべて発動の必要なし**。今後 ControlNet を追加する余裕も生まれた (例: 後段で別 ControlNet を使う等)。

## 次にやること

### 短期 (次セッションで着手)

- 🔲 `prompt_builder.py` の整形修正 — `_normalize_spaces` が `"robot , line art"` のカンマ前空白を除去できるよう微修正 (`re.sub(r'\s+,', ',', ...)` で十分)
- 🔲 `modules/vectorizer.py` の本実装 — `scripts/experiment_canny.py` + `experiment_diff.py` + `experiment_vectorize.py` の3本をクラス化し、Step C 確定パラメータ (Canny strong_blur σ=3.0 / dilate k=21 / min_pixels=50 / min_length=10〜15) を実装デフォルトに
- 🔲 `test_vlm_to_image.py` を拡張して **vectorize ステージまで含めたフルパス検証** — `cycle_NN/strokes.json` まで出力
- 🔲 `image_gen.py` を `requirements-imagegen.txt` と一緒に `requirements.txt` 統合可否を判断

### 中期 (Step D 後半 / Step F 着手準備)

- 🔲 本物の median 合成キャプチャ画像 (実カメラ入力) でフルパス検証
- 🔲 ストローク列を `trajectory.py` で軌道に変換、`robot.py` に流す結合テスト (実機描画)
- 🔲 Enter トリガーと組み合わせて Cycle 0 から Cycle N までの繰り返し動作確認

### Claude Code との同期

- 🔲 本ファイルと `image_gen.py` / `test_vlm_to_image.py` の commit を Claude Code 側 (Step E の `run_draw_test.py` 担当) と共有
- 🔲 `MILESTONES.md` に「✅ つなぎこみ完了 (段階的スワップ実証)」を `●` として追記提案

### 参照

- 統合テストアーティファクト:
  - 1 サイクル目: `~/draw_piper/logs/vlm_to_image_20260523_160920/cycle_01/`
  - 3 サイクル安定性: `~/draw_piper/logs/vlm_to_image_20260523_161405/cycle_{01,02,03}/`
- 統合テストログ: `~/draw_piper/logs/vlm_to_image_20260523_*.log`
- 関連スクリプト: `scripts/test_vlm_to_image.py`
- 関連モジュール: `modules/image_gen.py` (新規実装)
