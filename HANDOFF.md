# HANDOFF — 2026-06-02 FLUX 移行期 (新スレッド用・これだけ読めばOK)

このセッションは重くなったので新スレに移行。 まず下の「①最初にやること」を実行。
過去の詳細レシピは **メモリ** (`MEMORY.md` の FLUX 系3件) に集約済み。

## ① 最初にやること: 走っている LoRA 学習の確認
**FLUX style LoRA を 16GB で学習中**（バックグラウンド、nohup）。新スレ開始時に状態確認:

```bash
cd /home/jizaiedev2026/draw_piper
grep -oE "[0-9]+/1500 \[[0-9:]+<[0-9:]+[^]]*" logs/flux_train.log | tail -1   # 進捗
grep -q TRAIN_DONE logs/flux_train.log && echo DONE || echo running
ls models/flux_lora_winners/*.safetensors 2>/dev/null   # 完成LoRA (最終)
ls -d models/flux_lora_winners/checkpoint-*             # 中間 (500/1000/1500)
```
- **2026-06-02 16:35 時点**: 1072/1500 step (71%)、残り ~1h。checkpoint-500/1000 保存済、最終未。
- 完了後の `pytorch_lora_weights.safetensors` が成果物。GPU 専有中はテスト不可（推論と排他）。

## ② 学習完了後にやること: LoRA を載せて検証→webapp
学習が「過去の勝ちパターン全般」(168枚の線画) の画風を学べたか確認する。
1. `scripts/gen_flux_big.py`（FLUX-CN 生成パイプライン）の pipe 構築後に
   `pipe.load_lora_weights("models/flux_lora_winners")` を1行足したコピーを作る。
2. 数枚（samp_IMG_4357 顔 / 4362 椅子 / 4363 犬 等）を生成 → LoRA 有無で比較。
3. 良ければ webapp バッチに出して push（下記④の手順）。
4. LoRA 強度は `pipe.set_adapters(["default"], [0.6~1.0])` で調整。

## 現在の生成パイプライン (FLUX、マーカー描画向け)
- スクリプト: **`scripts/gen_flux_big.py`**（「大きくシンプルな単一被写体」全画像生成→webapp push）。
- モデル: **ungated ミラー `chutesai/FLUX.1-schnell`** + ControlNet `Shakker-Labs/FLUX.1-dev-ControlNet-Union-Pro`(canny, control_mode=0)。nf4量子化+`enable_model_cpu_offload`で**15GB**, ~47s/枚。
- レシピ要点（メモリ [flux_schnell_controlnet_setup] 参照）:
  - schnell は `guidance_scale=0` で **negative無視** → 制約は positive に書く。
  - FLUX の線は薄い → vectorize前に **hardboost(cut250)** で黒線化、**binarize**モードで単一線（二重線回避）。
  - 配置は **fill-board**（canvas92%に拡大、入力小領域に合わせない）。**15px未満の細部は除去**（マーカー2-3mm対応）。
- 直近バッチ: `sketch_variations/disp_2026-06-02-FLUX/`（7画像×3seed の big 版、webapp反映済）。

## 物理制約（重要）
- ホワイトボード描画域 **94×194mm**、`mm_per_px≈0.134`。マーカー **2-3mm = 15-22px**。
- → 細かい文字/装飾は潰れる。**大きくシンプル**が正義。canvas は CW,CH=704,1472。

## LoRA 学習レシピ（メモリ [flux_lora_training_16gb] 参照）
- スクリプト: **`scripts/train_flux_lora_16gb.py`**（diffusers公式+nf4パッチ、全パッチに`# [PATCH]`）。
- データ: `scripts/build_lora_dataset.py` が `sketch_variations/lora_winners_dataset/selections.json`
  (=webapp選別JSON) から 168枚を内容クロップ→768正方化。出力 `sketch_variations/lora_winners_dataset/*.png`。
- 起動: `/tmp/run_train.sh`（中身は下記コマンド）。`PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`必須。
  trigger語 `tklineart`、rank16/batch1/res768/8bit-adam/grad-ckpt/cache_latents、~8.5s/step、validation無し(nf4 .to不可)。

## ④ webapp 反映の定型
```bash
./venv/bin/python -m scripts.build_selection_webapp     # disp_* 自動検出。変種dirは v*_seed* 命名必須
git add sketch_variations/<batch> docs/selection/index.html
git commit -q -m "..."; git push origin claude/style-pool-rebalance-20260529
```
RAW URL: `https://raw.githubusercontent.com/yushin-jizaie/draw_piper/claude/style-pool-rebalance-20260529/<path>`

## 環境メモ
- venv に **pip 無し**（パッケージ追加不可、既存で対応）。bitsandbytes/peft/diffusers0.38/accelerate あり。
- GPU RTX 2000 Ada **16GB**。HFトークン無し→FLUXは ungated ミラー必須。
- ブランチ `claude/style-pool-rebalance-20260529`。チャットに画像を出しても**ユーザーは見られない**(SendUserFile/markdown画像ともNG)→ webapp で確認してもらう。

## これまでの経緯（要約）
SDXL系(matsumoto/lineart/enriched/hybrid)を経て、品質不足で **FLUX+ControlNet に移行**。
マーカー描画制約から「大きくシンプル」方針に。 最後に「勝ちパターン168枚で FLUX style LoRA学習」へ。
未完: LoRA学習(進行中) → 検証 → 採否判断。
