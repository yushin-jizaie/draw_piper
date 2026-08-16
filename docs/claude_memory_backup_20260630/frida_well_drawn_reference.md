---
name: frida-well-drawn-reference
description: Frida Smooth 描画で「実機できれいに描けた」リファレンスケースとその安定パラメータ
metadata: 
  node_type: memory
  type: project
  originSessionId: 33eae9b3-d6cc-4175-90fe-9104f72ae303
---

**2026-05-31 実機検証: 安定描画ケース**

- ファイル: `/home/jizaiedev2026/draw_piper/logs/robot_input_set_v2_20260530_172445/car_composition_align/strokes.json`
- 構造: 17 strokes / 474 points / 平均 28 点/stroke / 全 closed loop / thin racetrack 無し / 平均 stroke 長 ~150mm
- 結果: 60 strokes 中 37/60 完了時点で 安定描画 (`/tmp/wall_gui_*.log` の 23:27-23:29 ログ)、 揺れ無し / バウンド無し / 二度書き無し

**Why**: この設定が 「Frida Smooth で 1 つの正解」 = 安定描画の base line になる。 新 strokes.json を試す時の reference、 GUI default 値の根拠。

**How to apply**:
- 新規 strokes.json で Frida 描画前にこの設定を default として開始
- 不安定なら ここから 1 パラメータずつ動かして 整合式 (`要求速度 = step_mm × 1000 / 点間ms`、 `speed × 5 ≈ arm mm/s`) を再検算
- リファレンス再描画したい時は picker の 「生成日: 20260530」 + 「car_composition_align」 で再選択可能

**安定設定 (= GUI 新 default 値 = `wall_drawing_gui_full_dev.py` の `var_*` 初期値)**:
- 関節速度=15, 点間=100ms, 点密度=1.0mm (Normal 用、 Frida は step_mm 別)
- Frida: speed_base=1 / min=1 / max=1 / travel=3 / near_mm=20 / step_mm=3.0 / TSP=ON / merge=0
- 整合: 要求速度 30mm/s vs arm 5mm/s → 6 倍余裕で 点間 sleep 中も arm 動き続ける = 揺れ無し

関連: [[wall_drawing_full_dev_branch]]、 [[wall_gui_launch_command]]
