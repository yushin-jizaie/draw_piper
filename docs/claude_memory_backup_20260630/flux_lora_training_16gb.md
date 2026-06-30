---
name: flux_lora_training_16gb
description: FLUX LoRA を 16GB VRAM で学習する動作済みレシピ (diffusers公式+nf4パッチ)
metadata: 
  node_type: memory
  type: project
  originSessionId: 66b6007c-50c0-4f19-8c75-54a94658fcb5
---

FLUX.1-schnell の LoRA を **RTX 2000 Ada 16GB で学習できることを実証済**
(2026-06-02、 「勝ちパターン画像で FLUX をファインチューニング」 要望)。

**スクリプト**: `scripts/train_flux_lora_16gb.py`
= diffusers v0.38.0 公式 `examples/dreambooth/train_dreambooth_lora_flux.py` + 16GB 向け nf4 パッチ。
パッチ箇所(全て `# [PATCH]` コメント付き):
1. transformer を nf4 量子化ロード (BitsAndBytesConfig)。
2. T5(text_encoder_2) も nf4 量子化 (埋め込み計算 peak 対策。 計算後解放される)。
3. nf4 モデルは `.to(device, dtype)` 不可 → `is_loaded_in_4bit` で `.to()` をスキップ
   (3箇所: device移動 / 学習後 / 保存前)。
※ `prepare_model_for_kbit_training` は LLM 用で FLUX には不可 (`get_input_embeddings` 無し) → 使わない。

**動作実績**: nf4 transformer + nf4 T5 + `--cache_latents` + `--gradient_checkpointing`
+ `--use_8bit_adam` + rank16 + batch1 + res768 で **~8.3秒/step**、 LoRA 89MB を保存成功。
目安: 500step≒1.2h / 1000step≒2.3h / 1500step≒3.5h。
起動例(`PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` 推奨):
`venv/bin/python scripts/train_flux_lora_16gb.py --pretrained_model_name_or_path chutesai/FLUX.1-schnell --instance_data_dir <dir> --instance_prompt "..." --output_dir <out> --mixed_precision bf16 --resolution 768 --train_batch_size 1 --gradient_checkpointing --use_8bit_adam --rank 16 --cache_latents --max_train_steps N --guidance_scale 1`

**style LoRA の作り方**: 画像フォルダ + 単一 `--instance_prompt`(トリガー語入り) が簡単で省メモリ
(text encoder を埋め込み計算後に解放できる)。 学習 LoRA は標準形式なので
[[flux_schnell_controlnet_setup]] の FluxControlNetPipeline に `pipe.load_lora_weights()` で読める。
ベースは ungated `chutesai/FLUX.1-schnell` を使用。
