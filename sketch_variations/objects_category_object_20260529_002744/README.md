# object pool 5 枚投入後の効果検証 (--category object 直接呼び)

2026-05-29 00:27 実行。 ブランチ `claude/style-pool-rebalance-20260529` で
`STYLE_REF_POOLS["object"]` を空 → 5 枚 (raw から、 街 + キャラの作品) に変更後、
`test_ip_adapter_two_stage --category object` を直接呼んで 4 種で IP-Adapter Stage 2
が有効化されることを確認。

## 投入した object pool 5 枚

| ファイル | 内容 | 注意点 |
|---|---|---|
| `EdvzOK7U8AAqpwO.jpg` | 建物群 + 漫画コマ | 吹き出しテキスト混入リスク |
| `IMG_4314.JPG` | 街並み逆さま + 落下キャラ | キャラ主役、 街は背景 |
| `IMG_4310.JPG` | graniph collab、 街並み + キャラ | 「松本大洋 collaboration」 ロゴ + 著作権テキスト |
| `1090748_300.jpg` | 海岸 + 子供 | raw 唯一のカラー画像 (他は白黒) |
| `IMG_4315.JPG` | 街並み + キャラ 2 人 | 看板テキスト混入 |

→ 純粋な object 画像は raw に存在しないため、 「街+キャラ」 作品で代用。
事前評価ではテキスト混入リスクで懸念したが、 試行結果は混入なし (IP-Adapter の
特性として「絵柄」 を抽出してテキストは弱く反映される模様)。

## 結果評価 (Claude 視点)

| 入力 | Stage 2 出力 | strokes / pts | 評価 | 前回 objects_align との比較 |
|---|---|---|---|---|
| **house** | 家の輪郭 + 草木 (墨ベタアクセント)、 街並み風 | 63 / 729 | ✅ **改善** | objects_align/house (9 strokes) → IP-Adapter で情報量増加 |
| **tree** | ほぼ消失 (黒点 3 つだけ) | 8 / 137 | ❌ **更に悪化** | objects_align/tree (31 strokes、 smiley face 誤認) → IP-Adapter で潰された |
| **cat** | シンプルな黒猫 (四足、 普通の猫) | 28 / 573 | ✅ **大成功** | objects_align/cat (21 strokes、 **anthropomorphic 化**) → 擬人化を完全回避 |
| **car** | 車 + 街並み風墨ベタ、 スポーツカー的 | 34 / 394 | ✅ **改善** | objects_align/car (70 strokes、 普通の車) → 街要素が style 伝播 |

**3/4 で改善、 cat の擬人化解消が一番の成果。**

## 仮説検証

仮説: 「object pool が空 → IP-Adapter off → object モード で松本タッチが弱い」

→ **半分検証された**:
- ✅ cat (動物) で character pool の人物 ref → 擬人化を回避できた
- ✅ car / house で街+キャラ ref → 街要素が副次的に建物・乗り物に style 転写
- ❌ tree は Stage 1 が弱すぎて IP-Adapter でも救えない (input sketch の限界)

## ファイル構造

各 `<object>/` 配下:
- `stage1/illustrious_v2_object.png` — Stage 1 (img2img + CN soft)
- `10_init_from_stage1.png` — Stage 2 init image (768px)
- `11_style_ref.png` — IP-Adapter ref (object pool から seed=42 で auto pick)
- `20_stage2_str0.45_ip0.60.png` — **★Stage 2 結果 (IP-Adapter 適用後)**
- `30_vectorized_strokes.png` — **★robot 描画用 strokes**

## 実行コマンド (再現)

```bash
for obj in house tree cat car; do
  ./venv/bin/python -m scripts.test_ip_adapter_two_stage \
      --user-sketch logs/sketches_objects_20260528_183943/sketch_${obj}.png \
      --category object \
      --stage1-prompt "a detailed Matsumoto-style ${obj}, manga style, ink lineart, ..." \
      --output sketch_variations/objects_category_object_<ts>/${obj} \
      --stage2-strength 0.45 --ip-scale 0.6 --seed 42 \
      --stage1-resolution 1024 --resolution 768
done
```

注: `test_companion_mode.py --placement align` は **--category character 固定**
で内部呼び出しするため、 object pool を使うには上記のように
`test_ip_adapter_two_stage` を直接 `--category object` で呼ぶ必要がある。

## 次の改良アイデア

1. **`test_companion_mode --placement align --category {char, obj}` 二段選択** —
   現状の align モードでも object pool を選べるように拡張
2. **tree のような単純構造には IP-Adapter off (Stage 1 のみ)** — input 特徴量
   不足の場合は Stage 2 で潰されるリスクあるため
3. **IMG_4310 (graniph、 ロゴあり) を除外して試す** — 副作用の可能性
4. **raw から街並みコマだけクロップ** — 元提案の前処理を実施、 より純粋な object ref

## まとめ

- object pool が空 → IP-Adapter off の制約は **5 枚投入で解消可能** だった
- 「街+キャラ」 作品でも IP-Adapter の style 抽出は機能 (テキスト・ロゴは弱く反映)
- 最大の成果: **cat の擬人化問題解消** (character pool 人物 ref の副作用回避)
- tree のような単純構造の限界は引き続き要対策
