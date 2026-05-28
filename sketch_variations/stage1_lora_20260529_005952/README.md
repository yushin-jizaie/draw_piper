# Stage 1 強化試行 (A+C+G): LoRA 0.4 + Stage 2 skip

2026-05-29 00:59 実行。 ブランチ `claude/style-pool-rebalance-20260529`。 ユーザー所感
「アラインもシフトも Stage 1 だけで良さそう、 むしろ Stage 1 を強化したい」 への対処。

## 変更内容 (A + C + G)

| ID | 内容 | ファイル |
|---|---|---|
| A | `character` 経路を `illustrious_v2_inpaint` → `illustrious_v2_inpaint_mt` (matsumoto LoRA 0.4) | [scripts/test_ip_adapter_two_stage.py](../../scripts/test_ip_adapter_two_stage.py) |
| C | `object` 経路用に `illustrious_v2_object_mt` (LoRA 0.4) 新規追加 | [modules/image_gen.py](../../modules/image_gen.py) |
| G | `--skip-stage2` フラグ追加 (Stage 2 IP-Adapter を skip、 実行時間 ~64s → ~42s) | [scripts/test_ip_adapter_two_stage.py](../../scripts/test_ip_adapter_two_stage.py) |

LoRA は今まで使われていなかった `training/lora/matsumoto_taiyo.safetensors` (v0、 93MB) が初めて働く状態に。

## 結果 (Claude 視点、 8 入力 + LoRA 0.4 + Stage 2 skip)

| 入力 | 経路 | strokes / pts | 評価 | 所見 |
|---|---|---|---|---|
| B_round_smiley | character | 21 / 431 | ✅ | 顔保持 + 周囲に走り書き墨ベタ |
| C_face_with_neck | character | 18 / 401 | ✅ | 顔+首+肩構図保持、 LoRA タッチが乗った |
| D_stick_figure | character | 26 / 224 | ✅ | 棒人間保持 + 周囲に **走り書き墨ベタ** (松本タッチ顕現) |
| F_angry_face | character | 23 / 563 | ✅ | 顔保持 + 周囲に叢 (前回 align より松本らしさ強い) |
| house | object | **6 / 84** | ⚠️ | 極端にシンプル化 (建物 + 草) |
| tree | object | **0 / 0** | ❌ | **生成画像が事実上空白** (Vectorize で 0 strokes) |
| cat | object | **6 / 74** | ⚠️ | 耳と顔だけのミニマル (擬人化はないがシンプル過ぎ) |
| car | object | 51 / 902 | ✅ | 綺麗な lineart 維持、 detail あり |

character 系 4/4 で改善 (LoRA タッチ顕現)、 object 系 1/4 で問題なし (car のみ)。

## 副作用: object 系の **シンプル化過剰**

LoRA 0.4 + text2img mode (object_mt) で object 系の strokes 数が極端に減った
(house=6, cat=6, tree=0)。 robot 描画では「描くものが無い」 状態。 原因仮説:

- text2img mode は input sketch を ControlNet hint のみで扱う
- LoRA + ControlNet 0.65 + Danbooru tag prompt の組合せが「ミニマル lineart」 を強く誘導
- 既存 base preset (LoRA 抜き) の方が detail を保持していた (前回 house 61 strokes、 cat 41 strokes)

→ object 経路は **LoRA scale を 0.4 → 0.2 に下げる** か、 **img2img_strength を 0 → 0.3 に上げる**
で input sketch の構造をもっと保持する調整余地あり。

## 視覚比較 (grid 16 と同フォーマット)

8 grid 画像: [`sketch_variations/grid_stage1_lora_20260529_010709/`](../grid_stage1_lora_20260529_010709/)

| 入力 | 旧 (LoRA なし、 IP-Adapter あり) | 新 (LoRA 0.4、 IP-Adapter skip) |
|---|---|---|
| B_round_smiley | [B_round_smiley_align.png](../grid_16_20260528_191328/B_round_smiley_align.png) | [B_round_smiley_character_stage1lora.png](../grid_stage1_lora_20260529_010709/B_round_smiley_character_stage1lora.png) |
| C_face_with_neck | [C_face_with_neck_align.png](../grid_16_20260528_191328/C_face_with_neck_align.png) | [C_face_with_neck_character_stage1lora.png](../grid_stage1_lora_20260529_010709/C_face_with_neck_character_stage1lora.png) |
| D_stick_figure | [D_stick_figure_align.png](../grid_16_20260528_191328/D_stick_figure_align.png) | [D_stick_figure_character_stage1lora.png](../grid_stage1_lora_20260529_010709/D_stick_figure_character_stage1lora.png) |
| F_angry_face | [F_angry_face_align.png](../grid_16_20260528_191328/F_angry_face_align.png) | [F_angry_face_character_stage1lora.png](../grid_stage1_lora_20260529_010709/F_angry_face_character_stage1lora.png) |
| house | [house_align.png](../grid_16_20260528_191328/house_align.png) | [house_object_stage1lora.png](../grid_stage1_lora_20260529_010709/house_object_stage1lora.png) |
| tree | [tree_align.png](../grid_16_20260528_191328/tree_align.png) | [tree_object_stage1lora.png](../grid_stage1_lora_20260529_010709/tree_object_stage1lora.png) |
| cat | [cat_align.png](../grid_16_20260528_191328/cat_align.png) | [cat_object_stage1lora.png](../grid_stage1_lora_20260529_010709/cat_object_stage1lora.png) |
| car | [car_align.png](../grid_16_20260528_191328/car_align.png) | [car_object_stage1lora.png](../grid_stage1_lora_20260529_010709/car_object_stage1lora.png) |

## 実行時間比較

- 旧 (Stage 1 + Stage 2 IP-Adapter): ~64 秒/枚
- 新 (Stage 1 + LoRA、 Stage 2 skip): **~42 秒/枚** (34% 削減)
- 副次効果: VLM auto-prompt + Stage 2 の連鎖が無いので、 prompt → 出力の関係が直接的で デバッグ易

## 次の改良アイデア (今回 commit 範囲外)

1. **object 経路 LoRA scale sweep**: 0.4 → 0.3 / 0.2 / 0.5 で house/cat/tree のシンプル化過剰を緩和
2. **object 経路に img2img_strength=0.3 試行**: input sketch を初期画像扱いで構造保持強化
3. **tree 専用対策**: input sketch を richer に (枝複数本)、 もしくは tree は特別 preset で扱う
4. **LineAniRedmond LoRA との重ね**: 線画品質特化、 シンプル化抑制の可能性

## 再現コマンド

```bash
# character (B/C/D/F)
for sk in B_round_smiley C_face_with_neck D_stick_figure F_angry_face; do
  ./venv/bin/python -m scripts.test_ip_adapter_two_stage \
      --user-sketch logs/sketch_variations_20260528_084706/inputs/sketch_${sk}.png \
      --category character --skip-stage2 \
      --output sketch_variations/stage1_lora_<ts>/${sk} \
      --stage1-prompt "character, manga style, mt_taiyo_style, dynamic pose, expressive ink lines, ..." \
      --seed 42
done

# object (house/tree/cat/car)
for obj in house tree cat car; do
  ./venv/bin/python -m scripts.test_ip_adapter_two_stage \
      --user-sketch logs/sketches_objects_20260528_183943/sketch_${obj}.png \
      --category object --skip-stage2 \
      --output sketch_variations/stage1_lora_<ts>/${obj} \
      --stage1-prompt "a detailed Matsumoto-style ${obj}, mt_taiyo_style, ..." \
      --seed 42
done
```
