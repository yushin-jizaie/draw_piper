# アーム側 作業引き継ぎ (2026-05-27 02:00 時点)

> 本ドキュメントは Claude Code のリモート(画像生成セッション) から
> ローカル(アーム実機セッション) への引き継ぎメモ。
>
> ブランチ: `claude/smooth-curve-rendering-e88Vb`(コミット ~10 個、 push 済)
> リモート側で追加・修正したコードは **mock テスト通過済**、 **実機未検証** の
> 部分が多い。 ローカル側の最優先タスクは「実機で動作確認」 と
> 「feedback ダウンの切り分け」。

---

## 0. ブランチ pull

```bash
cd ~/draw_piper
git fetch origin
git checkout claude/smooth-curve-rendering-e88Vb
git pull
```

---

## 1. リモート(本セッション) で追加した変更一覧

### A. ペン接触問題修正(`Robot.wait_for_pose`)

旧コード: `draw_stroke_panel` で travel→descent を `time.sleep(settle_s=2.0)` で
挟んでいた → 横移動完了前に descent コマンドが届き、 controller が中間位置から
新ターゲットへ MOVE_L 直線補間 → panel 座標で斜め降下になり、 ターゲット手前で
筆がキャンバスに接触する問題。

修正: `Robot.wait_for_pose(x, y, z, tol_mm, timeout_s, fallback_s)` を追加。
end-pose feedback を 50ms poll で読み、 tol_mm 内到達まで待つ。 feedback が
None / 0x2A 側 all-zero スタック時は `time.sleep(fallback_s)` に degrade。
`draw_stroke_panel`、 `draw_stroke` 両方の travel-to-first-point / descent /
final pen-up に挿入。

ファイル: `modules/robot.py:wait_for_pose`, `draw_stroke_panel`, `draw_stroke`

### B. MOVE_C 円弧補間(`Robot.draw_stroke_panel_arcs`)

`piper_sdk` の MOVE_C モード(`ModeCtrl(0x01, 0x03, ...)` + `EndPoseCtrl` ×3 +
`MoveCAxisUpdateCtrl(0x01/0x02/0x03)`) を使った滑らかな曲線描画。
ホスト側で polyline を Catmull-Rom or scipy splprep でスプライン平滑化 →
arc length 等間隔再サンプリング → 3 点ずつ MOVE_C トリプレット化。

追加 API:
- `modules/trajectory.py`:
  - `smooth_polyline(points, step_mm, smooth_lambda)`
  - `polyline_to_arc_triplets(points)` — 3 点ずつ、 端点共有で連続軌道
  - `smooth_strokes(strokes, ...)` — リスト適用版
- `modules/robot.py`:
  - `Robot.goto_arc(start, mid, end, rx/ry/rz, speed_pct)` — base 座標で MOVE_C 単発
  - `Robot.goto_arc_panel(start_uvw, mid_uvw, end_uvw, speed_pct)` — panel 版
  - `Robot.draw_stroke_panel_arcs(points_uv, w_contact, w_clear, ...)` — travel
    → descent → MOVE_C 連鎖 → pen-up。 各円弧間に wait_for_pose 挟む

mock smoke test 通過済み (`modules/trajectory.py` の `_smoke_test`)。
**実機未検証。**

### C. 実機テスト用ランナー

`scripts/test_draw_arc_panel.py`:
- `--inspect` — connect + end-pose 表示のみ、 描画なし
- `--shape sine|circle|spiral` — 形状選択
- `--linear` — MOVE_L 版で比較
- `--speed N` `--step-mm N`

例:
```bash
python3 -m scripts.test_draw_arc_panel --inspect
python3 -m scripts.test_draw_arc_panel --shape sine --speed 20
python3 -m scripts.test_draw_arc_panel --shape sine --speed 20 --linear  # 比較
```

### D. StrokePicker(`modules/stroke_picker.py`)

`logs/vlm_to_image_*/cycle_*/` をパースして生成画像 + ストローク PNG をペアで
カード表示するモーダル Tk ダイアログ。 OS filedialog の代替。

API:
```python
from modules.stroke_picker import StrokePicker
selected = StrokePicker.show(parent_tk_window)
if selected is None: return  # キャンセル
# selected = {cycle_dir, generated_image, strokes_png, strokes_json,
#             input_sketch, subject, timestamp}
```

スタンドアロン smoke test: `python3 -m modules.stroke_picker`(tk が動く環境で)

### E. piper_test 側 patch

`_patches/wall_drawing_gui_full_dev_stroke_picker.patch`:
`~/piper_test/wall_drawing_gui_full_dev.py` の `on_strokes_select_file` を
StrokePicker に置換するパッチ。 `_patches/README.md` に適用手順 + 検証手順。
**未適用。**

### F. その他(画像生成側、 参考)

- `modules/image_gen.py`:
  - `MODEL_PRESETS` (`sdxl_turbo_mistoline`, `animagine_xl_31_mistoline`,
    `matsumoto_taiyo_animagine` 等) と `ImageGenerator.from_preset()`
  - LoRA 着脱機能(`lora_path` / `lora_scale`)
  - `build_image_generator_from_config(cfg)` helper(yaml の `imagegen.preset`
    自動適用)
- `scripts/compare_imagegen_models.py` — preset 横並び比較
- `scripts/prepare_style_dataset.py`、 `scripts/train_style_lora.py` —
  Matsumoto Taiyō 風 LoRA 学習
- `scripts/pipeline_test_gui.py` の SDXL 設定窓に preset コンボボックス追加

**こちらはアーム側引き継ぎでは関係薄い**。 画像生成側は別セッションで継続。

---

## 2. 現在の最大の blocker: feedback ダウン

### 状況

`test_draw_arc_panel.py --inspect` 実行で:

```
[robot] 0x3A* feedback listener: no frames within 2s
[robot] cached orientation (deg): RX=0.00, RY=0.00, RZ=0.00
[test_arc] current end-pose: X=0.0 Y=0.0 Z=0.0mm  RX=0.0 RY=0.0 RZ=0.0 deg
```

つまり PiperFeedback(0x3A 系) も SDK 経由(0x2A 系) も all-zero。
**CAN bus からアーム状態が一切返ってきていない**。

### 影響

| 機能 | 動く? | 備考 |
|---|---|---|
| `goto_ready_pose` | ✅ JointCtrl 送信のみ | OK |
| `draw_stroke_panel(_arcs)` | ✅ 動くがフル効果なし | wait_for_pose が time.sleep fallback |
| ペン接触修正のフル効果 | ⚠️ 半分 | 旧コードの設計と実質同じ |
| 状態モニタ | ❌ 不可 | get_end_pose() が常に 0 |

注意: cached orientation = (0,0,0) でも、 `draw_stroke_panel_arcs` は
`panel.pen_orientation_deg` を毎回明示渡しするので、 アームが (0,0,0) deg に
暴れる心配は **無し**。

### 切り分け手順(ローカルでやる)

1. CAN bus 観察 (別ターミナル):
   ```bash
   candump can0 | head -60
   ```
   - 0x2A1〜0x2A7 流れている → SDK 側設定問題
   - 0x3A1〜0x3A7 流れている → PiperFeedback 側 race の可能性
   - どちらも流れない、 command echo (0x2A0/0x3A0) だけ → マスターモード or
     ブロードキャスト OFF
   - 完全に何も流れない → CAN 物理層 / `can0` 起動失敗

2. CAN bus 物理確認:
   ```bash
   ip -details link show can0 | head -5
   # state UP + bitrate 1000000 を確認
   ```

3. マスターモード復帰(該当の場合):
   - **アーム電源 OFF → 30 秒 → ON**(一番効く)
   - それで治らなければ MasterSlaveConfig(0xFC,0,0,0) で強制 slave 化:
     ```bash
     python3 -c "
     from piper_sdk import C_PiperInterface_V2
     import time
     p = C_PiperInterface_V2('can0')
     p.ConnectPort()
     time.sleep(1.0)
     p.MasterSlaveConfig(0xFC, 0, 0, 0)
     time.sleep(1.0)
     p.ClosePort()
     "
     ```
     その後アーム再電源 cycle

4. 復活確認:
   ```bash
   python3 -m scripts.test_draw_arc_panel --inspect
   # → end-pose が non-zero になっていれば OK
   ```

### 関連既存ドキュメント

- `docs/20260521_2000_piper_feedback_issue_debug.md` — 0x3A 問題の経緯
- `docs/20260522_1700_piper_jointctrl_solved.md` — Config Init で JointCtrl
  解決した経緯
- `modules/piper_feedback.py` — 0x3A リスナー実装

---

## 3. 残タスク(優先順)

### P0: feedback 復活

上記 2. の手順で `--inspect` で end-pose が non-zero になるところまで。
ここが解決しないと P1 以降のフル検証ができない(描画自体はできるが
wait_for_pose の効果が時間待ちフォールバックになる)。

### P1: MOVE_C 実機動作確認

```bash
# 1. inspect で end-pose 確認
python3 -m scripts.test_draw_arc_panel --inspect

# 2. 控えめな速度で sin 波
python3 -m scripts.test_draw_arc_panel --shape sine --speed 15

# 3. 比較: 同じ sin 波を MOVE_L で
python3 -m scripts.test_draw_arc_panel --shape sine --speed 15 --linear

# 4. 円 / 螺旋
python3 -m scripts.test_draw_arc_panel --shape circle --speed 15
python3 -m scripts.test_draw_arc_panel --shape spiral --speed 15
```

確認ポイント:
- MOVE_C モード切替が SDK で効くか (`ModeCtrl(0x01, 0x03, ...)` 後にエラー無く
  `MoveCAxisUpdateCtrl` が通るか)
- アークが弧を描くか(折れ線にならないか — `_patches/` には入っていないが、
  もし弧にならなければ piper_sdk の MOVE_C 仕様再確認)
- アーク間つなぎ目で停止 / 揺れが大きすぎないか / 逆に減速不足でアボートしないか

### P2: ペン接触修正のフル検証

feedback 復活後に `draw_stroke_panel`(従来 MOVE_L 直線) で従来描画パイプラインを
回す → 各ストローク先頭でターゲット点の真上に来てから真下に降りるか確認。

```bash
# wall_drawing_gui で 既存の strokes.json を描画
cd ~/piper_test
~/draw_piper/venv/bin/python wall_drawing_gui_full_dev.py
# 「5. 生成画像描画」 → 既存 strokes.json 選択 → 「描画」
```

### P3: StrokePicker 統合

```bash
# 1. patch 適用 (dry-run 先)
cd ~/piper_test
patch -p0 --dry-run < ~/draw_piper/_patches/wall_drawing_gui_full_dev_stroke_picker.patch
patch -p0           < ~/draw_piper/_patches/wall_drawing_gui_full_dev_stroke_picker.patch

# 2. GUI 起動 → 「選択...」 ボタンで StrokePicker 出るか
~/draw_piper/venv/bin/python wall_drawing_gui_full_dev.py

# 3. OK だったら piper_test 側に commit + push
cd ~/piper_test
git add wall_drawing_gui_full_dev.py
git commit -m "wall_drawing_gui_full_dev: 選択ダイアログを StrokePicker に置換"
git push
```

### P4: wall_drawing_gui_full_dev.py の MOVE_C 切替 (新規)

現在の wall_drawing_gui の draw 呼び出しは `draw_stroke_panel`(MOVE_L)。
P1 で MOVE_C が安定動作確認できたら、 wall_drawing_gui 側を `draw_stroke_panel_arcs`
に切替する追加 patch を作る。 caller の signature 互換性は同じだが、 追加引数
(`smooth=True`, `step_mm=2.0`) を渡す必要あり → GUI 上で切替可能にすると親切。

このパッチはまだ作っていない。 ローカル側で P1 PASS してから作るか、 リモートに
依頼するか。

### P5: MILESTONES.md 更新

`CLAUDE.md` の運用通り、 節目ごとに trunk へ `●` 追加を提案ベースで。
P1 / P2 / P3 PASS したら以下を提案:

- ● ペン接触問題修正 + MOVE_C 滑らか描画 単体 PASS
- ● StrokePicker 統合 + wall_drawing_gui patch 適用 PASS
- ● 結合動作(パイプライン → 実機描画) PASS

---

## 4. キーファイル早見

```
modules/
├── robot.py             # Robot, PanelFrame, wait_for_pose, goto_arc, draw_stroke_panel_arcs
├── trajectory.py        # smooth_polyline, polyline_to_arc_triplets
├── stroke_picker.py     # StrokePicker (Tk modal)
└── piper_feedback.py    # 0x3A* リスナー (firmware S-V1.8-2 ワークアラウンド)
scripts/
├── test_draw_arc_panel.py    # 実機 MOVE_C テストランナー
└── ...
_patches/
├── wall_drawing_gui_full_dev_stroke_picker.patch   # piper_test 側 patch
├── wall_drawing_gui_full_dev.patched.py            # 完全置換版
└── README.md            # 適用手順
calibration/
├── panel_frame.yaml     # 既存、 calibrated:true 確認済
└── ...
docs/
├── 20260526_2330_stroke_picker_integration.md   # StrokePicker 設計
├── 20260521_2000_piper_feedback_issue_debug.md  # 0x3A 経緯
└── 20260522_1700_piper_jointctrl_solved.md      # Config Init 解決
```

---

## 5. 補足

### piper_sdk MOVE_C プロトコル

`piper_ctrl_moveC.py`(demo) からの抜粋:

```python
piper.MotionCtrl_2(0x01, 0x03, 30, 0x00)   # MOVE_C モード
piper.EndPoseCtrl(START_X, START_Y, ...)
piper.MoveCAxisUpdateCtrl(0x01)             # mark start
time.sleep(0.001)
piper.EndPoseCtrl(MID_X, MID_Y, ...)
piper.MoveCAxisUpdateCtrl(0x02)             # mark mid
time.sleep(0.001)
piper.EndPoseCtrl(END_X, END_Y, ...)
piper.MoveCAxisUpdateCtrl(0x03)             # mark end -> execute
time.sleep(0.001)
```

`Robot.goto_arc` がこのシーケンスをラップ。 mock 動作のみ確認、 **実機での
円弧軌道検証はこれから**。

### panel calibration

`calibration/panel_frame.yaml` は `calibrated: true`、 origin
`(183.69, 45.09, 261.52)`、 size `96.6 x 181.4 mm`。 panel normal は
`(-0.9843, -0.1644, -0.0642)`(ほぼ -X)。 ペン降下方向は base 座標で +X 方向。

### 画像生成側との分離

このセッション以降のスレッドは **画像生成 (LoRA 学習・preset・GUI)** に
集中する。 アーム側の話はローカル Code 側で進めて、 必要なら別途引き継ぎを
作る。

---

## 6. ローカル Code への依頼プロンプト案

別途 `docs/handoff_to_local_arm_session_PROMPT.md` 参照。
