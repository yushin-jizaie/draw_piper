---
name: calibration_center_size_redesign
description: 中央+寸法→四隅生成の試みは廃止、wall_drawing_gui を 5/26 末(72c2c86)へ全戻し
metadata: 
  node_type: memory
  type: project
  originSessionId: c882fa92-7404-4689-8922-bc20a24bdce7
---

2026-06-02、wall_drawing_gui_full_dev.py のキャリブを「中央+サイズ→四隅生成(正対矩形)+位置優先IK」に作り替えたが、**実機で台形化・描画歪みが直らず、ユーザ指示で 5/26 末コミット `72c2c86`(piper_test, main) に全戻しした**。5/27〜6/02 の全機能(StrokePicker, MOVE_C, 3Dプレビュー, ペン向きロック, 台形/depth/bilinear補正, 末端負荷ボタン, 取り直しウィザード, 中央+寸法生成)も破棄。drag-teach 校正が現行に復活。私の未コミット版は /tmp/wdg_my_uncommitted_2004.py に退避。

**この作り替えは再挑戦しないこと**（5/26 の drag-teach + strokes_json 描画が known-good。clean螺旋実績は 5/26 16:35、git e827f1c の校正）。

★**5/26 版は drag-teach 中の励磁(保持トルク)が効き、自重で落ちない**（実機確認 2026-06-02）。「示教中 sag」は 5/26 以降の退行で本質ではなかった。6/01 の末端負荷(重力補償)ボタンは 5/26 に無い後付け対処。→「sag が真因だから示教を捨てる」という推論は誤り。[piper_end_load_gravity_comp] の sag=描画歪み真因 説もこの点で要再評価。

**恒久的に有用な調査結果（戻しても変わらない事実）**:
- 本機 firmware(S-V1.8-2) は **EndPoseCtrl(ネイティブIK)が動かない**。位置決め・描画とも GUI の `_move_xyz_via_ik` = `wall_facing_ik.solve_ik`(Python IK) + JointCtrl で実行。robot.py の draw_stroke_panel(EndPoseCtrl) は未使用。
- `wall_facing_ik.fk` は **link6(手首フランジ)** を返す。ツール/ペン先オフセット無し。ペン先 = link6 + 約90mm × tool_z (`contact_detect_wall.TOOL_LEN_MM=90`)。向きが変わると link6基準の形がペン先で歪む。
- この腕は縦長領域に単一固定ペン向きでは届かない(上下で必要な手首角が違う)。drag-teach は各点で自然な向きを取るので描けていた。
- 描画歪みの切り分け: プレビュー(点列)が正しくアームだけ違う形なら motion/IK 層。[drawing_distortion_diagnostic]
