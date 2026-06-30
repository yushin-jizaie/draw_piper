---
name: winning_genart_recipe_lineart_cn05
description: 良い「絵」 を出す確定レシピ = lineart_char preset + ControlNet 0.5 + 占有率ルーティング
metadata: 
  node_type: memory
  type: project
  originSessionId: 66b6007c-50c0-4f19-8c75-54a94658fcb5
---

2026-06-01 検証で確定した、 デザイン性の高い生成の勝ち筋。

**レシピ: `illustrious_v2_lineart_char` + `controlnet_conditioning_scale=0.5`**
- 高CN (0.85〜0.9 = animagine/matsumoto/mistoline) は入力を**忠実トレース**するだけで
  退屈 (棒人間→棒人間)。 **cn0.5 が転換点**で、 ポーズを保ちつつ実際にキャラ化する
  (棒人間→本格ヒーロー)。 cn0.3 は自由すぎて崩れる、 cn0.35 は中間。
- seed で大きく変わるので gacha 的に複数 seed → 選択。

**占有率 + アスペクト ルーティング** (`modules/input_router.py`、 3 route):
入力の占有率と被写体の縦横比 (h/w) で自動切替:
- **いっぱい AND 細長い (h/w≥1.5: 木/人) → stylize**: 縦長のまま単一被写体に
  スタイル化 (lineart_char cn0.5、 位置保持)。
- **いっぱい AND 横長/コンパクト (車/家/猫の顔) → framed**: 縦長直接だと分裂/歪む
  ので、 OpenCV で正方形クロップ (余白少し) → 正方形生成 → 真っ白い縦長
  キャンバス中央に配置 (ユーザー提案)。 `modules/input_prep.py`。
  **生成は旧 align と同じ 2-stage** (`test_ip_adapter_two_stage`): Stage1 構図
  + Stage2 IP-Adapter 松本タッチ画風転写 (strength 0.45/ip 0.6)。 単発 ControlNet
  だと画風転写が無く地味だったため差し替え。 vectorize は binarize (単一線、
  canny は二重アウトライン NG)。 person→character / 物・動物→object style pool。
  注: 動物は object style pool (建物/街) が合わず希薄になる弱点あり (要調整)。
- **余白の多い入力 → scatter**: 縦長生成が自然にスプライト化する性質を活用し、
  分割してユーザーの線の周囲に散布。 v1=グリッド / v2,v3=ランダム(jitter)。
  被写体一貫 (cat→猫キャラ) or `--scatter-mode assoc` で VLM 連想の別物。
VLM: describe_literal=subject / classify_category=person/animal/object。

注: 正方形クロップ「だけ」 で生成入力を詰めるのは位置を壊すので NG (却下済) だが、
framed は「正方形生成→縦長中央配置」 で位置を中央固定にする点が異なり OK。

実装: `modules/input_router.py` (decide_route), `scripts/gen_routed.py` (統合ドライバ),
`modules/stroke_scatter.py`。 vectorize は canny (塗り→輪郭線、 ロボット描画可)。

**Why**: ユーザーは一貫して「線は簡潔でもデザイン性・構図が良い絵」 を求める
([[transparent_whiteboard_line_extraction]] 系の作業履歴参照)。 5/29-5/30 の方が
6/1 縦長バッチより良かったとの指摘から、 縦長強制 + 高CN忠実トレースが退屈の
原因と判明し、 このレシピに到達。
