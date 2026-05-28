# phase-e v4/v5 設定への巻き戻し (LoRA 抜き、 character は Stage 2 IP-Adapter ON)

2026-05-29 01:39 実行。 ブランチ `claude/style-pool-rebalance-20260529`。
ユーザー判断「[phase-e の multi_mode_v5_object_detailed フォルダ全体が画風が良い](https://github.com/yushin-jizaie/draw_piper/tree/phase-e-results-20260528/multi_mode_v5_object_detailed)」 +
「キャラはステージ2を当ててるのか」 (= [v4 README](https://github.com/yushin-jizaie/draw_piper/blob/phase-e-results-20260528/multi_mode_v4_object/README.md) で character mode は IP-Adapter ON) を反映。

## 設定 (phase-e v4/v5 完全等価)

| 経路 | preset | LoRA | Stage 2 |
|---|---|---|---|
| character | `illustrious_v2_inpaint` (inpaint mode) | **なし** | **ON** (IP-Adapter、 character pool ref) |
| object | `illustrious_v2_object` (text2img + CN 0.65) | **なし** | **OFF** (--skip-stage2) |

LoRA matsumoto_taiyo.safetensors は v0 (smiley face コマ等で学習されたバイアスあり) →
画像生成経路では使わず、 LoRA 再学習までは保留。

## stroke 数の劇的改善 (LoRA 比)

| 入力 | LoRA 0.4 | LoRA 0.2 | **LoRA 抜き (phase-e v5)** | 倍率 |
|---|---|---|---|---|
| B_round_smiley (char+S2) | 21 / 431 | 21 / 419 | **63 / 1498** | 3× |
| C_face_with_neck (char+S2) | 18 / 401 | 17 / 378 | **75 / 1426** | 4× |
| D_stick_figure (char+S2) | 26 / 224 | 34 / 449 | **61 / 1585** | 2× |
| F_angry_face (char+S2) | 23 / 563 | 23 / 583 | **47 / 911** | 2× |
| house (obj, S1 only) | 6 / 84 | 10 / 165 | **47 / 1025** | 5× |
| tree (obj, S1 only) | 0 / 0 | 0 / 0 | **14 / 474** | ∞ (復活!) |
| cat (obj, S1 only) | 6 / 74 | 6 / 62 | **48 / 942** | 8× |
| car (obj, S1 only) | 51 / 902 | 53 / 967 | **78 / 1447** | 1.5× |

phase-e v5 README の参照値: house 37, tree 89, cat 34, car 111。 私の結果は seed=42 単発で
ばらつくが、 概ね近いオーダー (house ✅、 cat ✅ phase-e 超え、 car やや弱、 tree やや弱)。
gacha で複数 seed 試行すれば best variant で phase-e 同等 or 超えるはず。

## 評価 (Claude 視点、 視覚確認)

- **F_angry_face (character + S2)**: 怒り顔少年が **全身** で描かれ、 spiky 髪 + 体 + 服。
  phase-e F_angry_face の理想出力と類似 (走り pose ではなく立ち pose、 prompt の "standing" 影響)
- **cat (object S1 only)**: 可愛い猫の顔 + ひげ、 松本タッチのペン揺らぎ。
  ただし下部に「Nillit...」 のような文字 hallucination あり (raw 36 枚にコミックページ
  caption が含まれる副作用、 LoRA bias の名残)

## 重要な学び

1. **v0 LoRA (matsumoto_taiyo.safetensors) は画像生成経路では入れない方が良い** —
   smiley face コマ等で学習されたシンプル化バイアスが detail を激減させる
2. **phase-e v4/v5 設定は LoRA 抜き** だった (今まで誤解していた)
3. **character mode は Stage 2 IP-Adapter ON が必須** — Stage 1 inpaint だけだと
   キャラ全身が描かれない (F_angry_face で確認済、 [前回 align skip-stage2](../grid_stage1_lora_20260529_010709/F_angry_face_character_stage1lora.png) は風景画的に)
4. **object mode は Stage 1 のみで OK** — text2img + CN 0.65 だけで detail + 構図保持

## 視覚比較

8 grid 画像: [`sketch_variations/grid_phase_e_v5_20260529_014939/`](../grid_phase_e_v5_20260529_014939/)

| 入力 | LoRA 0.2 (前) | **LoRA 抜き = phase-e v5 (本)** |
|---|---|---|
| B_round_smiley | [stage1lora02](../grid_stage1lora02_20260529_012123/B_round_smiley_character_stage1lora02.png) | [phase_e_v5](../grid_phase_e_v5_20260529_014939/B_round_smiley_character_phase_e_v5.png) |
| C_face_with_neck | [stage1lora02](../grid_stage1lora02_20260529_012123/C_face_with_neck_character_stage1lora02.png) | [phase_e_v5](../grid_phase_e_v5_20260529_014939/C_face_with_neck_character_phase_e_v5.png) |
| D_stick_figure | [stage1lora02](../grid_stage1lora02_20260529_012123/D_stick_figure_character_stage1lora02.png) | [phase_e_v5](../grid_phase_e_v5_20260529_014939/D_stick_figure_character_phase_e_v5.png) |
| F_angry_face | [stage1lora02](../grid_stage1lora02_20260529_012123/F_angry_face_character_stage1lora02.png) | [phase_e_v5](../grid_phase_e_v5_20260529_014939/F_angry_face_character_phase_e_v5.png) |
| house | [stage1lora02](../grid_stage1lora02_20260529_012123/house_object_stage1lora02.png) | [phase_e_v5](../grid_phase_e_v5_20260529_014939/house_object_phase_e_v5.png) |
| tree | [stage1lora02](../grid_stage1lora02_20260529_012123/tree_object_stage1lora02.png) | [phase_e_v5](../grid_phase_e_v5_20260529_014939/tree_object_phase_e_v5.png) |
| cat | [stage1lora02](../grid_stage1lora02_20260529_012123/cat_object_stage1lora02.png) | [phase_e_v5](../grid_phase_e_v5_20260529_014939/cat_object_phase_e_v5.png) |
| car | [stage1lora02](../grid_stage1lora02_20260529_012123/car_object_stage1lora02.png) | [phase_e_v5](../grid_phase_e_v5_20260529_014939/car_object_phase_e_v5.png) |

## 次の改良候補

1. **gacha で seed sweep** — phase-e は 3 variants で best 選定。 generate_gacha で同じ手法に
2. **「Nillit...」 文字 hallucination 対策** — negative prompt に "text, letters, comic frame" 追加
3. **tree 専用 input richer 化** — programmatic sketch を枝複数本に
4. **LoRA 再学習** (将来) — 漫画ページ追加 + コミックページ caption 修正で v1 LoRA 作成

## 再現

```bash
# character (Stage 2 IP-Adapter ON)
for sk in B_round_smiley C_face_with_neck D_stick_figure F_angry_face; do
  ./venv/bin/python -m scripts.test_ip_adapter_two_stage \
      --user-sketch logs/sketch_variations_20260528_084706/inputs/sketch_${sk}.png \
      --category character \
      --output sketch_variations/stage1_phase_e_v5_<ts>/${sk} \
      --stage1-prompt "1boy, solo, young boy with full body, messy hair, surprised expression, simple t-shirt, standing" \
      --stage2-strength 0.45 --ip-scale 0.6 --seed 42 \
      --stage1-resolution 1024 --resolution 768
done

# object (Stage 1 のみ、 --skip-stage2)
for obj in house tree cat car; do
  ./venv/bin/python -m scripts.test_ip_adapter_two_stage \
      --user-sketch logs/sketches_objects_20260528_183943/sketch_${obj}.png \
      --category object --skip-stage2 \
      --output sketch_variations/stage1_phase_e_v5_<ts>/${obj} \
      --stage1-prompt "a detailed Matsumoto-style ${obj}, manga style, expressive ink lines, detailed lineart, single continuous black line on plain white background, fur details, clean smooth strokes, no shading" \
      --seed 42
done
```
