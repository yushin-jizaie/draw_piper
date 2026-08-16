---
name: robot_draws_only_additions
description: ロボットは入力線を再描画しない。入力は既にボード上にある→出力から入力線を差し引いて加筆分だけ描く
metadata: 
  node_type: memory
  type: feedback
  originSessionId: e1718c7c-ca5d-4817-bc64-119d2b1aaea8
---

**ユーザーの入力線は既に物理的にホワイトボードに描かれている**。だから生成・出力にその線は不要で、
ロボットが描くべきは **加筆された(新規)デザイン部分だけ**。(2026-06-02 ユーザー「入力した線は既に
書かれてるワケだから生成されなくていい。opencvで消してたくらい。忘れないで」)

**Why:** 入力線を再描画すると、 既存の手描き線の上に二重描き(ズレれば汚く重なる)になる。 マーカー
描画では無駄＆品質劣化。 アライン基準としては入力線を使う ([[design_align_warp_compose]]) が、
**最終のロボット出力(strokes)からは入力線に対応する分を除去**するのが正しい。

**How to apply:** 合成/warp の出力ストロークから、 入力線 (vectorize した入力 or 元のカメラ画像の
線) に重なるものを差し引く。 既存 `_anchor_hybrid` の「入力 bbox 内側のストロークを捨てる」が原型だが、
bbox でなく **入力線そのものとの重なり**で差し引くべき。 透明ボード越しの線抽出は
[[transparent_whiteboard_line_extraction]] (modules/line_extract.py) と整合させる。
→ 出力 = (デザイン完成形を入力へアラインしたもの) − (既にボード上にある入力線)。
