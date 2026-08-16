# Stage 1 強化 試行 2: LoRA scale 0.4 → 0.2 sweep

2026-05-29 01:14 実行。 前回 [stage1_lora_20260529_005952](../stage1_lora_20260529_005952/) の
LoRA 0.4 で object 系がシンプル化過剰だった問題への対処として、 character / object 両 preset
の LoRA scale を **0.4 → 0.2** に下げて再評価。

## 変更箇所

`modules/image_gen.py`:
- `illustrious_v2_inpaint_mt`: lora_scale `0.4 → 0.2`
- `illustrious_v2_object_mt`: lora_scale `0.4 → 0.2`

## stroke 数比較 (0.4 vs 0.2)

| 入力 | 経路 | LoRA 0.4 | **LoRA 0.2** | 変化 |
|---|---|---|---|---|
| B_round_smiley | character | 21 / 431 | 21 / 419 | 〜 |
| C_face_with_neck | character | 18 / 401 | 17 / 378 | 微減 |
| **D_stick_figure** | character | 26 / 224 | **34 / 449** | **大幅増** ✅ |
| F_angry_face | character | 23 / 563 | 23 / 583 | 〜 |
| **house** | object | 6 / 84 | **10 / 165** | **増** ✅ |
| **tree** | object | 0 / 0 | **0 / 0** | 変わらず ❌ |
| cat | object | 6 / 74 | 6 / 62 | 〜 (むしろ減) |
| car | object | 51 / 902 | 53 / 967 | 〜 |

## 評価 (Claude 視点)

✅ **改善した 2 件**:
- **D_stick_figure** (224 → 449 pts): 棒人間 + 走り書きの密度が増、 LoRA 効果と detail 両立
- **house** (84 → 165 pts): シンプル化過剰が緩和、 家らしさが復活

❌ **改善しなかった 2 件** (LoRA scale 以外の原因):
- **tree** (0 → 0): preset/prompt 自体の限界、 input sketch が単純すぎ
- **cat** (74 → 62): むしろ更にミニマル化、 ○+耳の input に対し output が「耳と口だけ」 に

〜 **ほぼ変わらなかった 4 件** (B/C/F/car): 元々問題なかったので影響少

## 知見

1. **LoRA 0.2 は inpaint mode (character) で良好** — シンプル化過剰がなく、 LoRA タッチも残る
2. **object 経路 (text2img + CN 0.65) の根本問題は LoRA scale では救えない** — house は救えたが cat/tree は preset/prompt 由来
3. **tree のような単純構造は input 側を richer にしないと解決しない** — 既知の問題が再確認された

## 次の対策案

- **object 経路の `img2img_strength` を 0.0 → 0.3 に上げる** (cat の構造保持に効きそう)
- **tree 専用に input sketch を richer 化** (枝複数、 葉複数の sketch 再生成)
- **car と house は OK なので、 LoRA 0.2 を採用、 cat/tree は別 sweep 継続**

## 視覚比較

8 grid 画像: [`sketch_variations/grid_stage1lora02_20260529_012123/`](../grid_stage1lora02_20260529_012123/)

| 入力 | LoRA 0.4 grid | LoRA 0.2 grid |
|---|---|---|
| B_round_smiley | [stage1lora](../grid_stage1_lora_20260529_010709/B_round_smiley_character_stage1lora.png) | [stage1lora02](../grid_stage1lora02_20260529_012123/B_round_smiley_character_stage1lora02.png) |
| C_face_with_neck | [stage1lora](../grid_stage1_lora_20260529_010709/C_face_with_neck_character_stage1lora.png) | [stage1lora02](../grid_stage1lora02_20260529_012123/C_face_with_neck_character_stage1lora02.png) |
| D_stick_figure | [stage1lora](../grid_stage1_lora_20260529_010709/D_stick_figure_character_stage1lora.png) | [stage1lora02](../grid_stage1lora02_20260529_012123/D_stick_figure_character_stage1lora02.png) |
| F_angry_face | [stage1lora](../grid_stage1_lora_20260529_010709/F_angry_face_character_stage1lora.png) | [stage1lora02](../grid_stage1lora02_20260529_012123/F_angry_face_character_stage1lora02.png) |
| house | [stage1lora](../grid_stage1_lora_20260529_010709/house_object_stage1lora.png) | [stage1lora02](../grid_stage1lora02_20260529_012123/house_object_stage1lora02.png) |
| tree | [stage1lora](../grid_stage1_lora_20260529_010709/tree_object_stage1lora.png) | [stage1lora02](../grid_stage1lora02_20260529_012123/tree_object_stage1lora02.png) |
| cat | [stage1lora](../grid_stage1_lora_20260529_010709/cat_object_stage1lora.png) | [stage1lora02](../grid_stage1lora02_20260529_012123/cat_object_stage1lora02.png) |
| car | [stage1lora](../grid_stage1_lora_20260529_010709/car_object_stage1lora.png) | [stage1lora02](../grid_stage1lora02_20260529_012123/car_object_stage1lora02.png) |

## 再現

```bash
# scale 切替 (lora_scale 0.4 → 0.2 を modules/image_gen.py で書換 or 旧 preset 復元)
# 同じ実行コマンドで OK
./venv/bin/python -m scripts.test_ip_adapter_two_stage \
    --user-sketch <SKETCH> --category <character|object> --skip-stage2 \
    --output <OUT>/<sk> --stage1-prompt "..." --seed 42
```
