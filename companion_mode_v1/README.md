# Companion mode v1 (2026-05-28 11:30)

## ユーザ要望
- 「入力画像の角度違いの場合は、 むしろ入力画像から位置をずらす」
- 「cv2 blob で重心 + 空白地帯に描く」
- M16 (illustrious_v2_object) の画風は維持

## 実装

1. `modules/blob_detect.py`:
   - cv2 で input sketch の blob (connected component) 検出
   - 全 blob の union bbox 計算
   - 上/下/左/右の 4 帯から 最大面積の空白矩形を選択
2. `modules/stroke_transform.py`:
   - strokes の bbox → 別 bbox に translate + scale (contain/fill/max モード)
3. `scripts/test_companion_mode.py`:
   - Step 1: object mode v5 で生成 (中央 detailed)
   - Step 2: Vectorize 生成画像 → strokes
   - Step 3: cv2 で input blob → 空白地帯計算
   - Step 4: 生成 strokes を 空白地帯に translate+scale
   - Step 5: input sketch も Vectorize
   - Step 6: combine + render (input + generated companion)

## 検証 (4 patterns)

| Case | input 位置 | 生成 prompt | 出力位置 | 評価 |
|---|---|---|---|---|
| 01 | 左上 (cat icon) | cat | 右下 detailed cat | ◎ |
| 02 | 中央 (cat icon) | dog | 左 dog 大 (中央右に input cat 残) | ◎ |
| 03 | 右上 (cat icon) | tree | 左 大樹木 | ◎ |
| 04 | 右下 (cat icon) | house | 左上 house | ◎ |

全 4 で:
- ✅ M16 画風 (illustrious_v2_object v5 設定) 維持
- ✅ input sketch を そのまま元位置に保持
- ✅ generated を空白地帯に配置 (overlap なし)
- ✅ サイズは空白地帯に合わせて適切にリサイズ

## 使い方

```bash
./venv/bin/python -m scripts.test_companion_mode \
    --user-sketch logs/inputs/sketch_X.png \
    --prompt "a detailed Matsumoto-style <subject>, manga style, expressive ink lines" \
    --output logs/companion_<ts> \
    --seed 42
```

## 3 modes 完成形 (M16 後)

| Mode | 用途 | 位置 | 実装 |
|---|---|---|---|
| character | 顔/キャラ拡張 (顔→体・髪) | 元位置 | scripts/test_ip_adapter_two_stage.py --category character (M15) |
| object    | 物体 detailed (中央) | 中央 | scripts/test_ip_adapter_two_stage.py --category object (M16) |
| companion | 入力 + AI companion 並置 | 入力残し + 空白地帯 | scripts/test_companion_mode.py (本実装) |
