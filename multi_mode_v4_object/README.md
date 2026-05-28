# Multi-mode v4: object mode 完成 (2026-05-28 09:46)

## 改訂 (v3 → v4)

v3 で urban mode 廃止、 object mode (img2img + IP-Adapter off) に統合。
v4 で Vectorizer の diff bug fix と合わせて、 非人間 sketch の生成が成功。

### Vectorizer bug

`vectorize(generated_image, user_image)` は「diff = generated - user」 で
「追加された線」 だけ抽出する仕様。 これは inpaint mode (元 sketch は keep 領域
で 100% 保持) には正しいが、 img2img mode で 元 sketch が混ざっている場合に
**全部 diff で消える** バグ。

修正: object mode は `user_image=None` で全 strokes 抽出。

## 検証結果 (全 PASS)

| sketch | strokes (3 variants 平均) | 出力 |
|---|---|---|
| house | 13 | 三角屋根 + 壁 + 窓 + ドア の家 (元 sketch を 松本風に stylize) |
| tree  | 3-8 | 円 (葉) + 縦線 (幹) のシンプル木 |
| cat   | 17 | 三角耳 + 目 + 鼻 + ヒゲ の猫顔 |
| car   | 12 | 車体 + タイヤ + 窓 + グリル |

img2img_strength=0.65 で「元 sketch 35% 残し + 65% stylize」。 結果は元 sketch
の構造を保持しつつ ディテール (窓・タイヤ・ヒゲ等) が追加される。

## 完成した 3 modes

| Mode | Stage 1 preset | IP-Adapter | 用途 |
|---|---|---|---|
| `character` | illustrious_v2_inpaint (inpaint) | ON (松本キャラ ref) | 人間の顔/キャラ |
| `object`    | illustrious_v2_object (img2img) | OFF | 物体・動物・植物・乗り物 |
| `other`     | illustrious_v2_inpaint (inpaint) | OFF | character 風だが ref 不要なケース |

## 再現コマンド

```bash
# Character (gacha 5)
venv/bin/python -m scripts.generate_gacha \
    --user-sketch scripts/test_sketch.jpg \
    --category character \
    --output logs/gacha_char_<ts> \
    --n 5 \
    --master-seed 100

# Object (家 / 猫 / 車 / 木)
venv/bin/python -m scripts.generate_gacha \
    --user-sketch logs/inputs/sketch_house.png \
    --category object \
    --output logs/gacha_house_<ts> \
    --n 3 \
    --stage1-prompt "a house, simple house, building, no human, no character" \
    --master-seed 1000
```

## 残課題

- prompt 設計が手動 (家なら "a house, simple house, ..." と書く必要)
  → 将来 VLM 自動分類 (Level 3) で 解消できる
- object mode の `style_hint` は 「松本タッチを意図的に弱める」 = clean lineart 寄り
  → 「松本タッチを object にも乗せる」 別 LoRA / 別 ref が欲しいなら別実装
