# align モード on objects (house / tree / cat / car)

2026-05-28 18:51 実行。 [objects_shift_20260528_183956](../objects_shift_20260528_183956/) で
shift モードを試した同じ 4 入力に対して、 今度は **align モード** (位置合わせ、 2-stage IP-Adapter) を実行。

## 入力 sketch

[`logs/sketches_objects_20260528_183943/`](../../logs/sketches_objects_20260528_183943/) (前回 shift と同じ)。

| 入力 | VLM 推定 | align 用 prompt (CHARACTER_TEMPLATE) |
|---|---|---|
| house | 家 (0.90) | `house, manga style character, dynamic pose, expressive ink lines, ...` |
| tree | 木 (0.70) | `tree, manga style character, ...` |
| cat | 猫 (0.70) | `cat, manga style character, ...` |
| car | 車 (0.70) | `car, manga style character, ...` |

注: align は `--placement align` が常に `--category character` 経路で test_ip_adapter_two_stage を呼ぶため、 prompt template も CHARACTER_TEMPLATE (「manga style **character**」 が混入)。

## 評価結果 (Claude 視点)

| 入力 | Stage 1 出力 | strokes / pts | 評価 |
|---|---|---|---|
| **house** | シンプルな手書き家、 動線追加、 **構図保持** | 9 / 135 | ✅ **成功** (align らしい sketch 忠実、 ただし shift より控えめ) |
| **tree** | smiley face + 草に誤認 (○ → 顔、 縦線 → 体) | 31 / 662 | ❌ **失敗** (shift と同じ問題、 単純構造の限界) |
| **cat** | **立った anthropomorphic 猫**、 三角耳 + 顔 + 棒人間風の体・脚 | 21 / 421 | ⚠️ **キャラ化** (動物らしさ < キャラっぽさ) |
| **car** | 車を維持、 windshield 等の細部追加、 速度線、 **構図保持で detailed** | 70 / 1338 | ✅ **成功** (align らしい sketch 忠実 + detailed) |

3/4 で動作 (1 失敗、 1 キャラ化)。

## shift vs align (objects) の比較

| 入力 | shift モード (objects_shift_*) | align モード (本ディレクトリ) | 推奨 |
|---|---|---|---|
| **house** | 木造の家、 草木 + 屋根構造、 detailed (61 strokes) | シンプル手書き家、 構図保持 (9 strokes) | **shift** (より詳細) |
| **tree** | smiley face と誤認 | smiley face と誤認 (両方失敗) | どちらも改善必要 |
| **cat** | 黒猫風、 表情あり、 自然な猫 (41 strokes) | anthropomorphic 化、 立った猫キャラ (21 strokes) | **shift** (動物らしさ) |
| **car** | 走る車、 速度感 (88 strokes) | 構図保持の detailed 車 (70 strokes) | 用途次第 (shift = 動的、 align = 忠実) |

## 興味深い知見

### 1. align は object でも「sketch 忠実」 の特性を保つ

car / house は align で **入力構造を尊重した出力** になった。 これは align モードの「sketch を hint ではなく構造として尊重する」 パラダイムが object でも機能することを示す。

### 2. ただし CHARACTER_TEMPLATE の「manga style character」 が効きすぎる

cat の align 出力は **anthropomorphic** (擬人化、 立った猫キャラ) になった。 prompt の "manga style character" が SDXL に「動物 + キャラ要素」 を誘導したため。

→ align モードを object でも素直に使うには、 prompt template に "character" を含めない object 用 template が必要。 例:

```python
# 案 (将来の改良):
OBJECT_ALIGN_TEMPLATE = (
    "{subject_en}, manga illustration, detailed lineart, "
    "expressive ink lines, single continuous black line on plain white background, "
    "clean smooth strokes, no shading"
)
```

または `--placement align --category object` で内部 preset を `illustrious_v2_object` (img2img + CN soft) に切り替える設計に拡張するのも一案。

### 3. tree のような単純構造は align / shift 両方で限界

input sketch (○+縦線+横線) が SDXL の **両 preset** で smiley face と誤認された。 これは preset の問題ではなく **input sketch の特徴量不足**。 改善は input 側で行う方が筋良い (枝を複数本、 葉を richer に)。

## 改良アイデア (今後の作業用)

1. **`OBJECT_ALIGN_TEMPLATE` を追加** (modules/prompt_builder.py): "character" を含まず、 「manga illustration of {subject}」 系で object 描画を誘導
2. **`--placement align --category {character, object}` の二段選択**: align モードでも category を選べるようにし、 内部で test_ip_adapter_two_stage に渡す `--category` を切り替える
3. **VLM の subject カテゴリ判定**: subject が「人 / 顔 / 子供」 系なら character、 「物 / 動物 / 建物 / 乗り物」 系なら object、 と category を自動振り分け

## 4 モード × 4 入力 マトリクス (実験完了状況)

| 入力 | shift (位置ずらし) | align (位置合わせ) |
|---|---|---|
| キャラ系 B/C/D/F | [dual_mode_multi/](../dual_mode_multi_20260528_181532/) ✓ | [dual_mode_multi/](../dual_mode_multi_20260528_181532/) ✓ |
| object 系 house/tree/cat/car | [objects_shift/](../objects_shift_20260528_183956/) ✓ | [objects_align/](.) ✓ (本ディレクトリ) |

## 再現コマンド

```bash
for obj in house tree cat car; do
  ./venv/bin/python -m scripts.test_companion_mode \
      --user-sketch logs/sketches_objects_20260528_183943/sketch_${obj}.png \
      --placement align --auto-prompt \
      --output sketch_variations/objects_align_<ts>/${obj} \
      --seed 42
done
```
