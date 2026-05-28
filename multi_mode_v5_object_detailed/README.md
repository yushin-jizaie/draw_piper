# Multi-mode v5: object mode 詳細化 (2026-05-28 10:33)

## ユーザフィードバック反映

v4 の object mode は「入力 sketch を複製してるだけ」 と指摘。 検証で:
- img2img mode は ControlNet (0.85) と img2img (0.65) 両方で sketch を強保持
  → どんな strength でも 入力ほぼそのまま
- text2img + CN OFF は detailed 出力だが sketch を完全無視

最終的に **hybrid 構成** で解決:
- text2img mode (img2img_strength=0.0)
- ControlNet を soft hint (scale=0.65) で sketch 構造を緩く保持
- prompt で詳細 (manga style, expressive ink, fur details 等) を指示
- Vectorizer で純線画化

## 新 preset

`modules/image_gen.py:illustrious_v2_object` を v2 に更新:
| 項目 | v1 (旧) | v2 (新) |
|---|---|---|
| img2img_strength | 0.65 | **0.0** (text2img) |
| controlnet_conditioning_scale | 0.85 | **0.65** (soft hint) |

## 検証結果 (全 PASS)

| sketch | v1 strokes (旧) | v2 strokes (新) | 結果 |
|---|---|---|---|
| house | 13 | **37** | 屋根+窓+ドアのディテール、 3 variants で異なる構図 |
| tree  | 3   | **89** | 樹皮 + 葉 + 枝 のディテール |
| cat   | 17  | **34** | 顔+体+尻尾+毛のディテール、 表情豊か |
| car   | 12  | **111** | ホイール+窓+ドア+グリル のディテール |

3 variants × 4 種類 (12 画像) を `result_*_3gacha.png` で確認可。

## 重要な発見

「sketch を strokes に変える」 のではなく、 「sketch を hint に detailed 画像を
生成する → Vectorizer で純線画化」 が object mode の正解。

これで character mode (人間) と object mode (物体・動物) の両方で:
- ✅ 詳細な Matsumoto-style 線画
- ✅ ロボット描画適合 (純線画、 strokes 数 30-110)
- ✅ gacha で多様性
