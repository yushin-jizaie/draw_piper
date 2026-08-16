# shift モード on objects (house / tree / cat / car)

2026-05-28 18:39 実行。 ユーザー仮説: 「位置ずらしモードはキャラ以外だと良い結果が得られそう」 の検証。

## 入力 sketch

`scripts/gen_test_sketches_objects.py` で programmatic 生成 (1024×1024、 単純な黒線アートワーク):

| 入力 | 内容 | VLM 推定 (subject_ja, conf) |
|---|---|---|
| house | 三角屋根 + 四角壁 + ドア + 窓 | 家 (0.90) |
| tree | 円 (葉) + 縦線 (幹) + 横線 (枝) | 木 (0.70) |
| cat | 円顔 + 三角耳 + 目 + ひげ | 猫 (0.70) |
| car | 車体側面 + 窓 + タイヤ × 2 | 車 (0.70) |

入力 sketch は [`logs/sketches_objects_20260528_183943/`](../../logs/sketches_objects_20260528_183943/) (untracked、 後で sketch_variations/inputs_objects/ にコピー予定)。

## 評価結果 (Claude 視点)

| 入力 | Stage 1 出力品質 | strokes / pts | 評価 |
|---|---|---|---|
| **house** | 木造の家、 草・木のディテール + 屋根構造で松本タッチ良好 | 61 / 782 | ✅ **成功** (object preset が input の構造を正しく解釈) |
| **tree** | 「smiley face のキャラ」 になっている (○ + 線を顔+体と誤認) | 12 / 444 | ❌ **失敗** (sketch が単純すぎて SDXL が tree と認識できず) |
| **cat** | 可愛い松本風猫、 目 + 表情 + 黒猫テクスチャ | 41 / 1066 | ✅ **成功** (三角耳が cat の決定的特徴になった) |
| **car** | 走っている車、 速度線あり、 手書きラインで松本タッチ | 88 / 2189 | ✅ **成功** (車体側面 + タイヤが車として認識された) |

3/4 で成功。 ユーザー仮説 (object 系は shift モードで良い結果) は **概ね妥当**。

## 各 object の出力ファイル

```
<object>/
├── 00_auto_prompt.txt              VLM 推定 + COMPANION_TEMPLATE prompt
├── stage1/
│   ├── illustrious_v2_object.png   ★M16 object 生成 (中央 detailed、 最も重要)
│   ├── 00_guide.png / grid.png / summary.json
├── 20_input_strokes.png            入力 sketch を Vectorize
├── 21_transformed_gen_strokes.png   生成 strokes を 入力の空白地帯に移動
└── 30_companion_strokes.png        ★最終出力: 入力 + 移動生成 の合成
```

## 失敗ケース (tree) の原因

tree sketch は「○ + 縦線 + 横線」 という極めて単純な構造。 SDXL の illustrious_v2_object preset (img2img + CN 0.65 soft hint) は input の構造特徴を尊重して生成するため、 sketch から「木らしさの特徴」 を抽出できなかった。

代わりに `○ = 顔`, `縦線 = 体`, `横線 = 腕` と人型に誤認。 VLM 推定は正しく「木」 で prompt も `"a detailed Matsumoto-style tree"` だが、 ControlNet hint が input 構造に引っ張られた結果。

### 改善余地

- input sketch を richer に (枝を複数本、 葉を複数の円で、 等)
- CN conditioning_scale を下げる (0.65 → 0.4 等)
- もしくは shift モードを 「tree のような単純構造」 では使わない (cat/car/house のような特徴ある object 向き)

## 比較: キャラ系 (前回の dual_mode_multi) との対比

| 入力種別 | shift モード結果 | 観察 |
|---|---|---|
| キャラ系 (B/C/D/F) | 9-24 strokes / 200-411 pts | 入力 sketch + 隣に detailed character を合成、 ユーザー曰く「ダメ」 (改良予定) |
| object 系 (house/cat/car) | 41-88 strokes / 782-2189 pts | object 単体が高密度 stroke で生成、 input は控えめに表示 |
| object 失敗 (tree) | 12 / 444 pts | 単純すぎて誤認識 |

object 系の方が **stroke 数が圧倒的に多い** (高密度 detailed)。 これは object preset の特性 (img2img で sketch を stylize、 詳細な質感を加える) によるもの。 robot 描画時の strokes 量としても十分。

## 再現コマンド

```bash
# sketch 生成
./venv/bin/python -m scripts.gen_test_sketches_objects \
    --out-dir logs/sketches_objects_<ts>

# 各 object で shift
for obj in house tree cat car; do
  ./venv/bin/python -m scripts.test_companion_mode \
      --user-sketch logs/sketches_objects_<ts>/sketch_${obj}.png \
      --auto-prompt \
      --output sketch_variations/objects_shift_<ts>/${obj} \
      --seed 42
done
```

## 次の改良アイデア (ユーザー要望「位置ずらしはダメなので改良していく」 への hint)

1. **object 系の cv2 blob 配置の改善**: input sketch が大きい (canvas の中央占有) と「空白地帯」 が小さくなりすぎ、 generated object が小さく潰れる。 padding 調整、 input scale 調整。
2. **tree のような単純 sketch には別 preset**: CN scale 0.4 or text2img (img2img なし) で SDXL の自由度を上げる選択肢。
3. **shift mode を「object 専用」 と明示**: キャラ系では align を推奨、 object 系では shift を推奨、 と user に明示誘導 (CLI の `--placement` help に書く)。
