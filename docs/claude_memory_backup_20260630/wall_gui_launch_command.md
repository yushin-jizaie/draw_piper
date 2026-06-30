---
name: wall_gui-launch-command
description: 壁面描画 GUI (wall_drawing_gui_full_dev.py) に変更を加えたら、 毎回起動コマンドをメッセージに提示する
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 0a175968-8af3-4704-a643-cbd2e8707a7e
---

壁面描画 GUI (`~/piper_test/wall_drawing_gui_full_dev.py` または `~/draw_piper/modules/` 配下の依存 module) を編集したら、 **毎回** ターミナルにそのまま貼れる 1 行起動コマンドをメッセージの最後に記載する。

**起動コマンド (1 行)** — 2026-06-04 時点の現行は **wall_gui2**:
```bash
~/draw_piper/scripts/wall_gui2
```

`wall_gui2` = `piper_test/wall_drawing_gui_full_dev2.py`(調整用フォーク、 採寸ベース歪み補正
`draw_warp_correction` 機能あり) を起動するラッパー。 旧 `~/draw_piper/scripts/wall_gui` は
`wall_drawing_gui_full_dev.py`(無印) を起動する旧版。 dev2 は dev と同じ
`draw_strokes_wall_dev.load_strokes_json` + 同じ panel_uv→UV 変換(`scale_u=size_w/w`、
u/v 独立スケール)、 同じ `calibration/panel_frame.yaml`(size_mm 159.1×243.6) を使う。
→ panel_uv_mm のテスト図形は **make_test_strokes.py が panel_frame.yaml の実機 size_mm で
生成する**ことで su=sv=1.0 となり真円になる(ハードコード 96.62×181.38 だと u/v 独立スケールで楕円化)。

**Why**: ユーザーが 2026-05-28 のセッションで明示要求 — 「GUI の立ち上げ方法をコマンド一行で立ち上がるようにしてください。 また、 変更を加えたら毎回そのコマンドを教えてください」。 毎回 cd と venv path を打つ手間を省きたい意図。

**How to apply**:
- `~/piper_test/wall_drawing_gui_full_dev.py` を編集した時
- `~/draw_piper/modules/{robot,vectorizer,stroke_planner,stroke_visualizer,stroke_picker,panel_geometry,...}.py` など GUI が import する module を編集した時
- ↑ いずれの編集後も、 メッセージ末尾に上記 1 行コマンドを記載する。 ラッパースクリプト自体を編集した場合も同じ。
- 編集が無い query (質問だけ) では不要。

関連: [[wall_drawing_full_dev_branch]]
