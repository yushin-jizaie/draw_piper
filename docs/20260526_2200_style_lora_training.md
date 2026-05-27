# 画風 LoRA fine-tune (松本大洋風) 手順

> 日時: 2026-05-26 22:00 (JST)
> ステータス: ⏳ **infra 構築完了、 学習画像はユーザ準備待ち**

## 目的

SDXL 系の base モデル(Animagine XL 3.1 を採用) に **松本大洋風の線**を覚えさ
せる LoRA を作る。 既存パイプ (`prompt_builder → ImageGenerator →
vectorizer → robot.draw_stroke_panel_arcs`) はそのまま、 ImageGenerator
の preset を `matsumoto_taiyo_animagine` に切り替えるだけで使える設計。

## 著作権メモ

- 学習画像は **個人 / 研究目的** の範囲内で。 商用利用は不可
- 学習成果 LoRA (`training/lora/matsumoto_taiyo.safetensors`) を
  HuggingFace 等で **公開しない** こと
- リポジトリ上にも commit しないので `.gitignore` に追加(後述)

## 構成

```
draw_piper/
├── training/
│   ├── matsumoto_taiyo/
│   │   ├── raw/           # ユーザが収集する原画像 (gitignore)
│   │   └── dataset/       # prepare_style_dataset.py の出力
│   │                      # (画像 + .txt キャプション + metadata.jsonl)
│   ├── lora_runs/         # 学習中のチェックポイント
│   │                      # (gitignore)
│   └── lora/
│       └── matsumoto_taiyo.safetensors  # 最終 LoRA (gitignore)
```

## 手順

### 1. 学習画像を集める

`training/matsumoto_taiyo/raw/` に **15-50 枚** の松本大洋作品(jpg/png/webp)
を置く。 推奨:

- 顔のクローズアップ ×5-10 枚 (彼の特徴的な線が出る)
- 全身 / 動き ×5-10 枚 (動的な線)
- ベタ塗りや細密描写は混ぜない(LoRA が混乱)
- 解像度はバラバラで OK(scripts 側で 1024 系にリサイズ)

### 2. dataset 作成 (resize + auto-caption)

```bash
python3 -m scripts.prepare_style_dataset \
    --input  training/matsumoto_taiyo/raw \
    --output training/matsumoto_taiyo/dataset \
    --trigger mt_taiyo_style
```

中で BLIP-2 が走って `<filename>.txt` に caption を書く
(例: `mt_taiyo_style, a man with serious face looking forward`)。

VRAM が厳しい / 時間短縮したい場合は `--no-caption` で trigger word のみ。

### 3. 学習

```bash
python3 -m scripts.train_style_lora \
    --dataset training/matsumoto_taiyo/dataset \
    --name matsumoto_taiyo \
    --base cagliostrolab/animagine-xl-3.1 \
    --rank 32 \
    --steps 1500
```

- 初回は diffusers の git clone (shallow) が走る (~30s、 `~/.cache/draw_piper/diffusers`)
- `accelerate config` 未実行なら先に `accelerate config default` を一度
- VRAM ~14GB(RTX 2000 Ada 16GB なら OK)、 所要 1-2 時間
- 完了で `training/lora/matsumoto_taiyo.safetensors` (~80-150 MB)

ハイパラ調整目安:
- 画風が薄い → `--rank 64` か `--steps 2500`
- 過学習 (生成が同じ顔ばかり) → `--rank 16` か `--steps 800`

### 4. 動作確認

```bash
python3 -m scripts.compare_imagegen_models \
    --guide path/to/user_sketch.jpg \
    --prompt "boy with messy hair looking up" \
    --presets matsumoto_taiyo_animagine animagine_xl_31_mistoline
```

`logs/imagegen_comparison_<ts>/grid.png` に 2 枚並びで出る。
LoRA ありなしで線の太さ・粗さが変わっていれば成功。

### 5. パイプラインに組み込む

`MODEL_PRESETS["matsumoto_taiyo_animagine"]` が既に preset 登録されている
ので、 普段使う ImageGenerator の構築を:

```python
# Before
gen = ImageGenerator()                   # SDXL Turbo + MistoLine

# After
gen = ImageGenerator.from_preset("matsumoto_taiyo_animagine")
```

に変えるだけ。

## .gitignore に追加すべきもの

```
training/matsumoto_taiyo/raw/
training/lora_runs/
training/lora/*.safetensors
```

## ハイパラ詳細

`train_text_to_image_lora_sdxl.py` (diffusers 公式) に渡される実際の
コマンドは scripts/train_style_lora.py の `cmd` を読んでください。
主要パラメータ:

- `lr=1e-4 + cosine + warmup_steps=100` — 安定収束
- `mixed_precision=bf16` — RTX 2000 Ada (Ada Lovelace) は bf16 対応、
  fp16 より数値安定
- `gradient_checkpointing` — VRAM 4-5GB 節約・速度 30% 低下
- `center_crop + random_flip` — augmentation 最小限
- `checkpointing_steps=steps/5` — 5 等分でセーブ、 過学習回避用

## トラブルシュート

| 症状 | 原因 | 対処 |
|---|---|---|
| `CUDA out of memory` | rank/batch 大きすぎ | `--rank 16 --batch 1` |
| 学習スクリプトが見つからない | diffusers clone 失敗 | `HF_DIFFUSERS_PATH=/path/to/diffusers` をセット |
| 生成画像が真っ黒 / ノイズ | LoRA scale 高すぎ | preset の `lora_scale` を 0.85 → 0.5 に |
| 画風変わらない | LoRA 弱い | `--steps` 増やすか `--rank` 上げる |

## 関連ファイル

- `scripts/prepare_style_dataset.py` — 画像 + キャプション準備
- `scripts/train_style_lora.py` — 学習 launcher
- `scripts/compare_imagegen_models.py` — 比較ツール
- `modules/image_gen.py` — `MODEL_PRESETS["matsumoto_taiyo_animagine"]`
