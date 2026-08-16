---
name: ip_matsumoto_radial_ink_framing
description: IP-松本two-stageの放射状inkを出す条件=被写体を小さく正方枠中央・余白たっぷり
metadata: 
  node_type: memory
  type: project
  originSessionId: e1718c7c-ca5d-4817-bc64-119d2b1aaea8
---

IP-松本 two-stage ルート(ip_matsumoto)で 5/28 gacha のような**放射状 ink**を出す条件（2026-06-04 に長時間の切り分けで確定）:

**真因**: stage1 の inpaint(illustrious_v2_inpaint) は「被写体の**周りの余白**に放射状 ink を描き足す」。余白が多いほど放射状が強い。被写体が枠いっぱい/写真/縦長だと余白が無く放射状が出ず顔だけになる。

**3条件すべて必要**:
1. **フレーミング**: 被写体を小さく(枠の~38%)・正方枠の中央・余白たっぷり。`modules/route_driver.frame_subject(crop, frac=0.38, size=1024)` が自動化(Otsuでインククロップ→縮小→白正方中央)。IpMatsumotoBackend.generate_object_image で適用済。検証: 同入力同seedで余白ゼロ12本→縦長24本→余白38%で34本(放射状)。
2. **短い主語**: stage1_prompt の主語は `describe_literal`(1-2語, "face"/"person")。`describe_scene`の長文だとモデルが顔だけに集中。5/28も "face"/"person"。
3. **入力がクリーン**: 透明ボード越しの生写真(IMG_4357等)は緑ボードが暗く閾値で線を分離不可(98%暗画素)→GUIの「線抽出→入力に設定」(背景キャプチャ要)でクリーン白背景黒線にしてから。

**効かなかった対策(記録)**: square_pad単体/crop-to-fill(枠いっぱい)は余白を消すので**逆効果**。stage2_strength/ip_scaleの上げすぎ(0.75/0.9)は線が霧に溶けて0本。

**確実に出る参照入力**: sketch_variations/_inputs/B_round_smiley.png (1024², 小さいスマイリー+大余白)。これ+seed83810+diff OFFで route_final=50本放射状。

settings: route=ip_matsumoto / category=character / 加筆のみ(diff) OFF / stage2強度0.45 / ip_scale0.6。関連: [[winning_genart_recipe_lineart_cn05]] [[transparent_whiteboard_line_extraction]] [[robot_draws_only_additions]]
