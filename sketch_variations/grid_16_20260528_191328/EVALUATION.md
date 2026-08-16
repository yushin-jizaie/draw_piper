# 16 組 評価表 (画風 / 位置 × 3 段階)

評価軸 (3 段階):
- ◎ (3): 良好 — 期待通り、 採用に値する
- ○ (2): 部分的 — 一部期待外れだが用途次第で使える
- △ (1): 失敗 — 期待から大きく外れる

**画風**: Matsumoto タッチ + 線の質。

**位置**: mode の期待挙動と一致しているか。
- shift では 「入力からずれて配置」 = ◎
- align では 「入力位置を保持」 = ◎

| filename | sketch_id | mode | 画風 | 位置 | コメント (Claude 視点) |
|---|---|---|---|---|---|
| [B_round_smiley_shift.png](B_round_smiley_shift.png) | B_round_smiley | shift | ◎ (3) | ○ (2) | Matsumoto-style face を空白地帯に detailed 生成、 入力 smiley は保持。 shift の意図通り入力 + 別配置の合成 |
| [B_round_smiley_align.png](B_round_smiley_align.png) | B_round_smiley | align | ○ (2) | ◎ (3) | 入力位置に忠実、 ただし VLM auto-prompt 'face' が face のみ描画を誘導、 キャラ拡張 (phase-e 風) にはならない |
| [C_face_with_neck_shift.png](C_face_with_neck_shift.png) | C_face_with_neck | shift | ◎ (3) | ○ (2) | 首+肩 hint を活かして 体ボリュームのある character を生成、 入力は左下に保持、 shift の合成として良好 |
| [C_face_with_neck_align.png](C_face_with_neck_align.png) | C_face_with_neck | align | ○ (2) | ◎ (3) | 入力構図 (顔+首) を忠実に保持して stylize、 VLM 'person' で全身要素はやや薄め |
| [D_stick_figure_shift.png](D_stick_figure_shift.png) | D_stick_figure | shift | ◎ (3) | ○ (2) | 棒人間を「松本タッチで肉付けされた character」 に変換、 shift で空白地帯配置、 入力 stick は左下に小さく残存 |
| [D_stick_figure_align.png](D_stick_figure_align.png) | D_stick_figure | align | ◎ (3) | ◎ (3) | 棒人間構図を忠実に保持 + 松本タッチで肉付け、 align としてベスト出力に近い |
| [F_angry_face_shift.png](F_angry_face_shift.png) | F_angry_face | shift | ◎ (3) | ○ (2) | 怒り表情を保持 + 横に detailed character (24 strokes)、 shift 合成として良好 |
| [F_angry_face_align.png](F_angry_face_align.png) | F_angry_face | align | △ (1) | ◎ (3) | 位置は保持されているが、 SDXL が body を描き足さず風景画的になった。 phase-e の F_angry_face とは大きく違う出力。 画風としては失敗 |
| [house_shift.png](house_shift.png) | house | shift | ◎ (3) | ○ (2) | 木造の家、 草・木のディテール + 屋根構造で松本タッチ良好、 入力 house は右下 |
| [house_align.png](house_align.png) | house | align | ○ (2) | ◎ (3) | シンプル手書き家を構図保持で stylize、 strokes 数 9 と控えめだが align らしい |
| [tree_shift.png](tree_shift.png) | tree | shift | △ (1) | ○ (2) | tree 入力が smiley face と誤認 (○+線が単純すぎ)、 SDXL が tree と認識できず。 shift 失敗 (input が limit) |
| [tree_align.png](tree_align.png) | tree | align | △ (1) | ◎ (3) | tree 入力が smiley face と誤認、 align でも同じ失敗。 位置は保持されている |
| [cat_shift.png](cat_shift.png) | cat | shift | ◎ (3) | ○ (2) | 可愛い松本風猫 (黒猫風)、 表情あり、 41 strokes / 1066 pts。 shift 合成として理想的 |
| [cat_align.png](cat_align.png) | cat | align | ○ (2) | ◎ (3) | anthropomorphic 化 (立った猫キャラ、 棒人間風の体・脚)、 CHARACTER_TEMPLATE の 'manga style character' が誘導した副作用。 位置は保持 |
| [car_shift.png](car_shift.png) | car | shift | ◎ (3) | ○ (2) | 走る車、 速度線、 手書きライン、 88 strokes / 2189 pts。 shift で最も成功した object |
| [car_align.png](car_align.png) | car | align | ◎ (3) | ◎ (3) | 構図保持の detailed 車、 windshield 等の細部追加。 align で最も成功した object |

## モード別 集計 (画風 / 位置 平均)

| mode | 画風 平均 | 位置 平均 | 件数 |
|---|---|---|---|
| shift | 2.75 | 2.00 | 8 |
| align | 2.00 | 3.00 | 8 |

## 入力種別 × モード 集計 (画風 平均)

| 種別 | shift | align |
|---|---|---|
| キャラ系 (B/C/D/F) | 3.00 | 2.00 |
| object 系 (h/t/c/c) | 2.50 | 2.00 |

## ハイライト

- 最高評価 (画風 3 / 位置 3): **D_stick_figure_align.png** — 棒人間構図を忠実に保持 + 松本タッチで肉付け、 align としてベスト出力に近い
- 最低評価 (画風 1 / 位置 2): **tree_shift.png** — tree 入力が smiley face と誤認 (○+線が単純すぎ)、 SDXL が tree と認識できず。 shift 失敗 (input が limit)
