---
name: flux_schnell_controlnet_setup
description: FLUX.1-schnell + ControlNet を 16GB VRAM で動かすレシピ (ungatedミラー/nf4/掠れ対策)
metadata: 
  node_type: memory
  type: project
  originSessionId: 66b6007c-50c0-4f19-8c75-54a94658fcb5
---

FLUX.1-schnell + ControlNet で高品質な線画生成 (2026-06-02、 SDXL系が「イマイチ」だったため移行)。
RTX 2000 Ada **16GB** で動作確認・全画像プロトタイプを `disp_2026-06-02-FLUX` に生成済。

**モデル入手 (重要)**: 公式 `black-forest-labs/FLUX.1-schnell` は**ゲート化**(schnell も dev も要HFトークン)。
トークン無しなら **ungated ミラー `chutesai/FLUX.1-schnell`**(フル diffusers 形式・vae 同梱)を使う。
ControlNet は `Shakker-Labs/FLUX.1-dev-ControlNet-Union-Pro`(ungated)、 **canny = control_mode=0**。

**16GB に載せる**: transformer と T5 を **nf4 量子化**(diffusers/transformers の BitsAndBytesConfig)
+ `pipe.enable_model_cpu_offload()`。 ピーク **15.0GB**、 ロード ~96s、 生成 **~47s/枚**(4step)。
control 画像 = 入力(square_pad 1024)の **cv2.Canny → dilate**。

**落とし穴**:
- schnell は `guidance_scale=0` / `num_inference_steps=4`。 この時 **negative_prompt は無視される**
  → 「no fill/no shading」 が効かず**ベタ塗り**が出る(例: dog が黒塗り)。 制約は positive 側に書く必要。
- FLUX の線は**薄いグレー**で出ることが多く、 そのままだと vectorizer が拾えず **0/低ストローク**。
  → vectorize 前に**コントラストブースト**(<235 を黒へ寄せる)で回収できる(再生成不要)。chair 0→13 等。
- 配置は既存の [[framed_enrich_digital_line]] と同じ `_place_input_aligned`(guide1024→入力contain)。

プロトタイプ script は /tmp/flux_run.py (ephemeral)。 本採用するなら scripts/ に gen_flux として
productionize し、 ①positiveに line-art 強制 ②boost を pipeline 内蔵 ③CN強度/seed 調整 が TODO。
関連: [[winning_genart_recipe_lineart_cn05]]。
