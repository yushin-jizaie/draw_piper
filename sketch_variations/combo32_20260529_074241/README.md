# 32 通り評価: 8 入力 × 4 経路 (seed=42 単発)

2026-05-29 07:42 実行。 ブランチ `claude/style-pool-rebalance-20260529`。
ユーザー要望「キャラクター/オブジェクトの元画像をどちらのパイプラインにも通して、 さらに align/shift のバリエーションも」 を反映。

## 4 経路の定義

| 略称 | 経路 | スクリプト |
|---|---|---|
| **char+S2** | `--category character` + Stage 2 IP-Adapter ON (phase-e v4 character 設定) | `test_ip_adapter_two_stage` |
| **obj only** | `--category object --skip-stage2` (phase-e v5 object 設定) | `test_ip_adapter_two_stage` |
| **align** | `--placement align --auto-prompt` (位置合わせ、 内部で char-S2 経路 + VLM prompt) | `test_companion_mode` |
| **shift** | `--placement shift --auto-prompt` (位置ずらし、 M16 object + cv2 blob 配置) | `test_companion_mode` |

LoRA は全経路で **抜き** (前回 rollback で v0 LoRA を画像生成経路から除外)。 seed=42 単発。

## 8 grid (各 sketch ごと 4 経路を 1 枚に集約)

[`sketch_variations/grid_combo32_20260529_080904/`](../grid_combo32_20260529_080904/)

各 grid: 上段 = strokes only、 下段 = input (青) + strokes (黒) の重ね合わせ。 列 = INPUT / char+S2 / obj only / align / shift

| 入力種別 | 入力 | grid 直リンク |
|---|---|---|
| character | B_round_smiley | [B_round_smiley.png](../grid_combo32_20260529_080904/B_round_smiley.png) |
| character | C_face_with_neck | [C_face_with_neck.png](../grid_combo32_20260529_080904/C_face_with_neck.png) |
| character | D_stick_figure | [D_stick_figure.png](../grid_combo32_20260529_080904/D_stick_figure.png) |
| character | F_angry_face | [F_angry_face.png](../grid_combo32_20260529_080904/F_angry_face.png) |
| object | house | [house.png](../grid_combo32_20260529_080904/house.png) |
| object | tree | [tree.png](../grid_combo32_20260529_080904/tree.png) |
| object | cat | [cat.png](../grid_combo32_20260529_080904/cat.png) |
| object | car | [car.png](../grid_combo32_20260529_080904/car.png) |

## 期待される傾向 (経路 × 入力種別)

| | character 入力 (B/C/D/F) | object 入力 (house/tree/cat/car) |
|---|---|---|
| **char+S2** | ◎ 期待通り (松本キャラ全身) | ⚠️ 動物 → anthropomorphic 化リスク (人物 ref 適用) |
| **obj only** | ⚠️ 顔だけ / シンプル化 | ◎ 期待通り (detailed object) |
| **align** | ○ 入力位置保持 + キャラ拡張 | ⚠️ object でも character 経路扱い (anthropomorphic) |
| **shift** | ⚠️ cv2 blob 配置で曖昧 | ⚠️ M16 object 生成だが配置で曖昧 |

D_stick_figure を Claude が視覚確認した結果:
- INPUT: 棒人間
- **char+S2**: 怒り髪 + detailed キャラ全身 ◎ (理想的)
- obj only: 線数本だけ、 ほぼ消失 ✗
- align: 髪 + 部分的体 ○
- shift: 棒人間 + 隣に円 3 つ ✗

→ **character 入力には char+S2 が圧勝** という仮説。 残り 7 入力で確認は user 判断に委ねる。

## ファイル構造

各 `<sketch_id>/` 配下:

```
<sketch_id>/
├── char_S2/          # char+S2 経路 (Stage 1 + Stage 2 IP-Adapter)
│   ├── stage1/illustrious_v2_inpaint.png
│   ├── 10_init_from_stage1.png
│   ├── 11_style_ref.png
│   ├── 20_stage2_str0.45_ip0.60.png
│   └── 30_vectorized_strokes.png
├── obj_only/         # obj 経路 (Stage 1 のみ、 LoRA なし)
│   ├── stage1/illustrious_v2_object.png
│   ├── 20_final_no_ip_adapter.png
│   └── 30_vectorized_strokes.png
├── align/            # test_companion_mode --placement align --auto-prompt
│   ├── stage1/
│   ├── 00_auto_prompt.txt
│   ├── 20_stage2_str0.45_ip0.60.png
│   └── 30_vectorized_strokes.png
└── shift/            # test_companion_mode --placement shift --auto-prompt
    ├── stage1/illustrious_v2_object.png
    ├── 00_auto_prompt.txt
    ├── 20_input_strokes.png
    ├── 21_transformed_gen_strokes.png
    └── 30_companion_strokes.png
```

## 用途別ベストプラクティス (Claude 仮説、 grid 視覚確認で要検証)

| やりたいこと | 推奨経路 |
|---|---|
| キャラ正面顔 → 松本キャラ全身を描き足したい | **char+S2** |
| object (家・車・猫等) を松本タッチで detailed 化 | **obj only** |
| キャラ入力の位置を保持して画風だけ変えたい | **align** |
| キャラ入力 + その隣に別 object を配置したい | **shift** (位置ずらし) |

## 関連実行

- gacha sweep (char×5 + obj×3、 別実行): `sketch_variations/gacha_sweep_*` 参照
- 前回 phase-e v5 等価結果 (char+S2 + obj only): `sketch_variations/stage1_phase_e_v5_*`
