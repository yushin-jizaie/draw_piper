# Companion mode v2: 同題材 companion (2026-05-28 11:48)

## 前バージョン v1 の問題

v1 (companion_mode_v1/) で「input が cat なのに companion を house にした」 → 不自然な
組み合わせ ばかり demo していた。 ユーザ指摘: 「インプットが猫なのに家の絵が
描かれてるのはおかしくない?」

**v1 の実装自体は正しい** (prompt 依存)。 demo の prompt 選び方が悪かっただけ。
本来の典型使用は **同じ題材を input + detailed companion** で並置するパターン。

## v2 demo (同題材で再実演)

| Case | input | prompt | 結果 |
|---|---|---|---|
| 01 | 左上に小猫 icon | "detailed Matsumoto cat..." | 右下に big detailed cat |
| 02 | 中央に小猫 face | "detailed Matsumoto cat..." | 左に big detailed cat |
| 03 | 中央下に簡素な家 | "detailed Matsumoto house..." | 上部に big detailed house |

各 case の dir に:
- `00_input_sketch.png` ユーザの入力
- `10_generated.png` Stage 1 (object mode v5) の生成画像
- `20_result_combined.png` Vectorize + composite (input + transformed gen)

## 典型 use case

「ユーザがラフな猫を描く → AI が同じ canvas の空白地帯に **詳細な松本タッチの
猫** を描く」 = 学習 / inspiration / comparison。

prompt は **同題材** にするのが自然 (入力 cat → prompt "cat...")。
将来 VLM (Level 3 提案) で 自動的に「ユーザが描いたものは cat → prompt も
cat」 と判定できるようにすると、 prompt 編集なしで運用可能。

## prompt を変えれば 異種を companion にも可能

v1 の「cat → house」 も同 pipeline でできる。 用途は scene composition
(ユーザ: 「cat を描いたから 横に house も追加して」)。 prompt 次第。

## 既存 companion_mode_v1/ との関係

v1 dir は「prompt 自由度の demo」 として 残す (cat→dog, cat→tree, cat→house)。
v2 dir は「同題材の典型 use case」 として 推奨例を見せる。
