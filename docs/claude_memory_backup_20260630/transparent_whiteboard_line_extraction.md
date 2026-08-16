---
name: transparent_whiteboard_line_extraction
description: カメラは透明ボードを撮るので線+奥の人間+背景が重なる。背景差分+色で線抽出
metadata: 
  node_type: memory
  type: project
  originSessionId: 66b6007c-50c0-4f19-8c75-54a94658fcb5
---

入力カメラ (1280×720) は **透明** ホワイトボードを撮る。1 枚の画像に
重なって写るもの: 手前=自分の線 / 奥=もう1人の人間が描いた線 /
さらに奥=その人間の体 + 現実空間の背景の映り込み。線の色は統一。

このため生のカメラ画像をそのまま vectorize すると人体・背景が混入する。
対処 = **背景差分 (空ボード基準) AND 特定色フィルタ**:
- 静的な映り込み/室内背景 → 背景差分で除去
- 人体 (肌・服) → 線色フィルタで除去
実装: `modules/line_extract.py` (extract_lines / COLOR_PRESETS dark/blue/red/green)、
`scripts/pipeline_test_gui.py` の「透明ボード線抽出」 UI。

仕様 (2026-06-01 朝 ユーザー確認済): near/far は単一カメラでは分離困難なため
**両側まとめて抽出で OK**。線の色は GUI で調整可能にする方針で OK
(既定 dark=低V、 dark/black/blue/red/green + 差分閾値 + 暗線V上限)。
背景は空ボード 1 枚キャプチャ基準で OK。

詳細: docs/20260601_scatter_and_transparent_board.md
GUI 起動/ローカル webapp は [[wall_gui_launch_command]] と同様、
`scripts/pipeline_test_gui.py` / `scripts/webapp_local`。
