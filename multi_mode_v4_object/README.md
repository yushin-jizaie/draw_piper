# Multi-mode v4: 3 modes 確定版 (2026-05-28 09:54)

## 完成した 3 mode の役割

| Mode | Stage 1 preset | IP-Adapter | 用途 |
|---|---|---|---|
| `character` | illustrious_v2_inpaint (inpaint) | ON (松本キャラ ref pool) | 人間の顔/キャラ |
| `object`    | illustrious_v2_object (img2img, strength=0.65) | OFF | 物体・動物・植物・乗り物 |
| `other`     | illustrious_v2_inpaint (inpaint) | OFF | フォールバック |

## 検証結果 一覧

### 👦 character mode (人間顔)

| ファイル | 内容 |
|---|---|
| `sketch_B_round_smiley.png` | 入力 (smiley face) |
| `result_character_smiley_5gacha.png` | gacha × 5 variants の grid |
| `result_character_v3_strokes.png` | best variant (v3、 spiky 髪+体) の純線画 |

### 🏠 object mode (家・木・猫・車)

| sketch | 入力 | gacha grid | v1 strokes |
|---|---|---|---|
| house | `sketch_house.png` | `result_house_object_3gacha.png` | `result_house_object_v1_strokes.png` |
| tree  | `sketch_tree.png`  | `result_tree_object_3gacha.png`  | `result_tree_object_v1_strokes.png`  |
| cat   | `sketch_cat.png`   | `result_cat_object_3gacha.png`   | `result_cat_object_v1_strokes.png`   |
| car   | `sketch_car.png`   | `result_car_object_3gacha.png`   | `result_car_object_v1_strokes.png`   |

## 主要観察

1. **character + gacha (5 variants)**: 同 sketch から 5 種 pose の Matsumoto キャラ。
   pose 多様化問題 解消。 v3 が 一番松本らしい (spiky 髪、 dynamic 体)。
2. **object mode**: 家/木/猫/車 すべてで クリーン純線画 + 元 sketch 構造保持 +
   ディテール (窓・ヒゲ・タイヤ等) 追加。
3. **3 modes 設計の妥当性**: 人間 = inpaint + IP-Adapter、 非人間 = img2img、
   と分離する事で 両方のユースケースで成功。

## 再現コマンド

### Character mode (gacha)

```bash
venv/bin/python -m scripts.generate_gacha \
    --user-sketch scripts/test_sketch.jpg \
    --category character \
    --output logs/gacha_char_<ts> \
    --n 5 \
    --master-seed 100
```

### Object mode (例: 家)

```bash
venv/bin/python -m scripts.generate_gacha \
    --user-sketch logs/inputs/sketch_house.png \
    --category object \
    --output logs/gacha_house_<ts> \
    --n 3 \
    --stage1-prompt "a house, simple house, building, no human, no character" \
    --master-seed 1000
```

## 過去 demo との関係

- `phase_e_demo/` (M13): Plan E のみ、 顔保持 + 純線画
- `matsumoto_v1_ip_adapter/` (前駆): IP-Adapter 単段、 顔位置ズレ
- `matsumoto_v2_two_stage/` (M15): two-stage で 顔位置 fix + 松本タッチ
- `sketch_variations/`: 6 種類入力 robust 性検証
- `multi_mode_v3/`: 3 modes 初期提案 (urban mode 廃止判明)
- **multi_mode_v4_object/ (current)**: 3 modes 確定版
