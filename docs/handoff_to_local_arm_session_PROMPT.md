# ローカル Claude Code へ貼り付け用プロンプト案

下記をローカル(オフィス) の Claude Code セッション起動直後に貼り付けて
ください。 リモートで進んだ状態と、 ローカルで何をすべきかを 1 メッセージで
伝える構成。

---

## ▼ プロンプト本文(ここから下をコピー)

別セッション(リモート、 画像生成側) でアーム描画まわりの実装を進めて
push 済み。 ローカル実機で続きをお願いします。

### 前提

- プロジェクト: `~/draw_piper`(本ターミナルの cwd を想定)
- ブランチ: `claude/smooth-curve-rendering-e88Vb`
- アーム実機 + CAN bus (can0) + GPU(RTX 2000 Ada 16GB) ローカル環境
- 関連別リポ: `~/piper_test/`(`wall_drawing_gui_full_dev.py` 等)

### 最初に読んで

引き継ぎ完全版: `docs/20260527_0200_handoff_to_local_arm_session.md`
(現状・blocker・残タスク P0〜P5・キーファイル早見 が網羅されている)

### 最優先タスク

**P0: feedback ダウンの原因切り分け**

直近のリモート側テストで `test_draw_arc_panel --inspect` を実行したところ:

```
[robot] 0x3A* feedback listener: no frames within 2s
[robot] cached orientation (deg): RX=0.00, RY=0.00, RZ=0.00
[test_arc] current end-pose: X=0.0 Y=0.0 Z=0.0mm  RX=0.0 RY=0.0 RZ=0.0 deg
```

つまり 0x3A 系も 0x2A 系も無音。 切り分け手順は引き継ぎドキュメント §2 に
書いてあるので、 まず `candump can0 | head -60` で何が流れているか確認 →
原因に応じて電源 cycle / MasterSlaveConfig 復帰。

復活したら `python3 -m scripts.test_draw_arc_panel --inspect` で end-pose が
non-zero になることを確認。

### その後やってほしいタスク (P1〜P4、 順番に)

引き継ぎドキュメント §3 を順に消化:

- **P1**: MOVE_C 実機動作確認(`test_draw_arc_panel` で sin / circle / spiral、
  `--linear` 比較)
- **P2**: ペン接触修正フル検証(既存 strokes.json で `wall_drawing_gui_full_dev`
  描画 → ストローク先頭で筆がターゲットの真上に来てから降りるか)
- **P3**: StrokePicker 統合(`_patches/` の patch を `~/piper_test/` に当てる)
- **P4**: wall_drawing_gui の `draw_stroke_panel` →  `draw_stroke_panel_arcs`
  切替 patch 新規作成

P1 で MOVE_C がアークにならず折れ線になる、 等の挙動異常があれば
`piper_sdk` の `MoveCAxisUpdateCtrl` シーケンスを再確認(引き継ぎ §5 に
プロトコル抜粋あり)。

### 進め方の希望

- 各 P タスクの開始前に何をするか 1-2 文で予告
- 実機操作前に CAN 状態 / panel calibration 状態を確認
- 危険な動きの恐れがあればまず低速 + 小図形で
- 節目ごとに `MILESTONES.md` 更新を **提案** (`CLAUDE.md` の運用通り)
- 詰まったら推測で進めず、 candump / get_end_pose / get_joints の出力を
  提示して止まる

### 画像生成側

並行してリモート側スレッドで画像生成 (LoRA 学習・モデル比較・GUI preset) を
続けるので、 アーム側セッションでは画像生成系の `modules/image_gen.py` /
`scripts/compare_imagegen_models.py` / `scripts/train_style_lora.py` 等の
変更には触らない方針。 触る必要が出たら一旦相談。

### 開始

まずは `candump can0 | head -60` の出力を見せてください。
そこから feedback 復活の打ち手を決めます。

---

## ▲ プロンプト本文(ここまでコピー)
