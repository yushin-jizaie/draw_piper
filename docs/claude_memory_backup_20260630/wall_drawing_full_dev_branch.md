---
name: wall-drawing-full-dev-branch
description: piper_test の wall_drawing_gui_full_dev.py が現行 dev、 実機テスト待ち
metadata: 
  node_type: memory
  type: project
  originSessionId: 295a77c2-64b1-4d3e-9780-f431dd13d88e
---

`~/piper_test/wall_drawing_gui_full_dev.py` (main branch) が現在の作業対象。
本番 `wall_drawing_gui.py` には反映せず、 実機検証後にマージする計画。

**Why:** dev で並走していた B4/B5/capture-pose/UX 2nd round/Section 5 (生成画像
描画) を 1 GUI に統合し、 明日以降の実機テストで一気通貫検証する方針。
本番マージの判断は dev 検証後。

**How to apply:**
- 起動: `~/draw_piper/venv/bin/python ~/piper_test/wall_drawing_gui_full_dev.py`
- 編集対象: `wall_drawing_gui_full_dev.py` (本番ファイルは触らない)
- 関連 dev モジュール: `canvas_to_panel_frame_dev.py`,
  `draw_strokes_wall_dev.py` (load_strokes_json のみ参照)
- テストスイート 6 本 (test_canvas_calibration_io / test_step2_v3_save /
  test_drag_sampling_thread / test_dev_b4_b5 / test_dev_stroke_drawing /
  test_dev_capture_pose) が回帰チェック。 `python <file>` で直接実行
- 2026-05-26 時点の最新コミット: `86c8479` (main)

**最近の追加 (2026-05-26)**:
- GUI を 4 タブ Notebook に分割 (① 接続・リーチ / ② キャリブ / ③ 中央・接触
  深さ / ④ 描画)
- Section 4 「試し書きモード」 に 6 図形: 中心に丸、 中心に三角、 隅合わせ
  正方形 (TL/TR/BR/BL)
- 中心に丸は Section 5 と同じく `EndPoseCtrl` ストリーム配信 (48 点, 30ms/点)
  → sagitta 0.03mm、 1 周 1.5 秒。 三角と正方形は従来の settle MOVE L

**実機テスト待ち項目** (2026-05-27 以降):
- 4 タブ切替が機能するか
- 「位置決め関節補間」 (MOVE J モード) が低速で滑らかに動くか (Edit J)
- 中心に丸ストリーム配信で詰まらないか (詰まったら [[piper-can-tx-physical-fault]]
  ではなく Python 側で適応スキップ + ENOBUFS 対応を追加)
- 隅合わせ正方形がキャンバス端まで実際に届くか (関節限界に当たる可能性)
- panel_frame.yaml 更新ボタン → mock strokes 描画で base 座標範囲確認

**仕様参照ドキュメント**:
- `~/draw_piper/docs/20260525_2210_full_dev_integration_section5.md`
- `~/draw_piper/docs/20260525_2155_dev_ux_session2_settle_smooth_japanese_layout.md`
