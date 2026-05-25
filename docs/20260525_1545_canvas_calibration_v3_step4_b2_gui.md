# Canvas calibration v3 — Step 4: B2 perimeter trace を GUI に配線

> 日時: 2026-05-25 15:45 (JST)
> 関連既存ファイル: `20260525_1447_canvas_calibration_v3_design.md` (設計),
>                    `20260525_1457_canvas_calibration_v3_step1_yaml_io.md` (Step 1 IO),
>                    `20260525_1517_canvas_calibration_v3_step2_gui_wiring.md` (Step 2 GUI 配線),
>                    `20260525_1529_canvas_calibration_v3_step3_sampling_thread.md` (Step 3 sampling thread)
> ステータス: ⏳ **コード変更完了・dry-run 110+ checks PASS、Tk 構築 smoke 成功、実機未検証** — ユーザによる drag-teach 実機テスト待ち

## 実施したこと

### 設計遵守

設計ドキュメント section "Phase 構成" + "B2 / B3 / B5 (新規: 自動サンプリング)" の B2 部分を `wall_drawing_gui.py` に実装。 旧 2-phase ティーチ (B1 4 corners + B2 任意 plane 点) は **完全に B2 自動サンプリング** に置き換え。

### GUI 改修(7 sub-step)

1. **Step 4a — 状態 / Tk var / import**
   - `from drag_sampling_thread import DragSamplingThread`
   - 状態を再構成: `dt_phase` (None / "corners" / "b2_idle" / "b2_recording" / "b2_done"), `dt_traces` dict, `sampling_thread`
   - 旧 `dt_plane_points` と `MIN_PLANE_EXTRAS` を削除
   - `var_sampling_interval = tk.IntVar(value=10)` 追加

2. **Step 4b — UI レイアウト**
   - LabelFrame title 更新: "2. Canvas Calibration v3 (B1 corners + B2 perimeter trace)"
   - 既存 row_b (Record / Undo / Save / Abort) はそのまま
   - 新規 row_c 追加: `Sampling interval` Spinbox + `Start B2 trace` / `Stop B2 trace` ボタン + ライブ点数ラベル

3. **Step 4c — B2 handler methods**
   - `on_b2_start_trace()`: 検証 → DragSamplingThread を作成・開始 → dt_phase="b2_recording"
   - `on_b2_stop_trace()`: thread.stop → points を dt_traces["perimeter"] に extend → dt_phase="b2_done"
   - `_on_b2_point_added(record)`: sampling thread 側コールバック、 `root.after(0, ...)` で Tk 安全
   - `_on_b2_joint_warning(idx, val, msg)`: j5/j3 等の限界近接ログ
   - `_on_b2_error(e)`: FK エラーログ
   - `_stop_sampling_thread_if_running()`: Save/Abort/Exit 共通の clean-up

4. **Step 4d — `_refresh_buttons` を新 phase 対応**
   - Record ボタン: B1 のみ、 B1 完了後は disabled (text "Record (B1 done)")
   - B2 Start/Stop: phase + sampling thread の生存で enable 制御
   - Save: corners=4 AND not b2_active
   - 5 種類の phase 表示文字列を `lbl_phase` で表示
   - ライブ点数 (in-progress + accumulated) を `lbl_b2_count` で表示

5. **Step 4e — `on_record_point` / `on_undo_point` の B1 限定化**
   - `on_record_point`: dt_phase != "corners" なら no-op、 4 corner 完了で `b2_idle` 遷移
   - `on_undo_point`: B1 corner のみ undo 可、 B2 trace は Undo 対象外 (Stop→Start で破棄か Abort)

6. **Step 4f — session ライフサイクル**
   - `_do_start_drag`: dt_traces を初期化
   - `_exit_master`: 最初に `_stop_sampling_thread_if_running()` を呼んで master mode 終了より先に sampling を止める
   - `on_abort_drag`: B2 in-progress 点数も "abort 確認" モーダルに含める

7. **Step 4g — `_fit_and_save` を traces.perimeter に対応**
   - 旧 Step 2 transitional の `traces.surface ← dt_plane_points` マッピングを撤廃
   - B1 corner 点 → `_capture_to_v3()` で v3 record 化(従来 logic)
   - B2 perimeter → DragSamplingThread が既に v3 形式で吐き出すのでそのまま使う
   - plane fit 入力点群を corners + traces.perimeter + (将来) diagonals + surface に拡張
   - `source_breakdown` の `perimeter` フィールドが実値を返す

### 受け入れ条件(Step 4 完了基準)

- [x] GUI が tkinter 起動できる (smoke test: `DISPLAY=:0` で WallDrawingGUI 構築成功)
- [x] 全 16 method が class に存在 (`hasattr` check pass)
- [x] dry-run save+load: 4 corners + 8 perimeter → schema_version=3 yaml round-trip
- [x] corners-only セーブパス: perimeter=0 でも保存可能 (確認ダイアログ経由)
- [x] v1 (M10 disk) yaml の読み込み回帰なし
- [x] 既存 3 テストスイート全 PASS (50 + 35 + 27 = 112 checks)
- [ ] **実機 drag-teach 検証** — ユーザによる確認待ち(下記)

## テスト結果

```
$ /home/jizaiedev2026/draw_piper/venv/bin/python \
    /home/jizaiedev2026/piper_test/test_canvas_calibration_io.py
ALL CHECKS PASSED  (50 checks)

$ /home/jizaiedev2026/draw_piper/venv/bin/python \
    /home/jizaiedev2026/piper_test/test_step2_v3_save.py
ALL CHECKS PASSED  (35 checks) [書き換え: Step 4 シマンティクス対応]

$ /home/jizaiedev2026/draw_piper/venv/bin/python \
    /home/jizaiedev2026/piper_test/test_drag_sampling_thread.py
ALL CHECKS PASSED  (27 checks)
```

GUI 構築 smoke test:
```
DISPLAY=:0 ⇒ Tk ok, GUI built, btn_b2_start='Start B2 trace',
  var_sampling_interval=10, dt_traces keys=[perimeter, diagonal_tl_br,
  diagonal_tr_bl, surface], dt_phase=None, teardown ok
```

## 実機検証計画 (ユーザにお願いするフロー)

実機検証は **power cycle 1 回消費** を前提。 M11 の手順を踏襲。

### 前提条件
- [ ] アームが電源 ON、 USB-CAN が接続済み
- [ ] CAN0 が UP (またはこれから GUI の `CAN up (sudo)` で上げる)
- [ ] 物理キャンバス(垂直アクリル板)が前回と同じ位置

### 実行ステップ

1. **GUI 起動**
   ```
   /home/jizaiedev2026/draw_piper/venv/bin/python ~/piper_test/wall_drawing_gui.py
   ```
   起動時に Loaded defaults ログに **`schema_version=1`** が出ること(disk 上の M10 yaml を読むため)。

2. **接続 + ready pose 復帰**
   - `CAN up (sudo)` → pkexec password
   - `Connect`
   - `Recover to Ready` (joint speed 5%)
   - Status bar が `● Connected` + `(normal)` になっていることを確認

3. **B1 corners**
   - `Start Drag-Teach (enter master mode)` → 確認モーダルで Yes
   - "MASTER MODE ACTIVE" + "WIGGLE THE ARM" ログを確認
   - アームを手で軽く揺らして 0x155-7 broadcast 開始
   - Phase 表示 `B1 Corners: 0/4 done -> next: TL`
   - アームを TL コーナーに当てて `Record [Enter]`(または Enter キー)
   - 同様に TR → BR → BL を順に
   - 4 corner 完了で **Phase 表示が `B2 idle. Corners ✓. ...` に切替**
   - Save ボタンが灰色 → enable に変わる

4. **B2 perimeter trace (今回の新規部分)**
   - Sampling interval を `10` (mm) に確認 — 必要なら調整(2-50 の範囲)
   - `Start B2 trace` 押下
   - Phase 表示 `B2 RECORDING. Drag pen along perimeter (TL→TR→BR→BL→TL)` 赤
   - 点数ラベルが `points: 0` から増えていくのを確認
   - アームを手でゆっくりとキャンバス外周に沿って TL→TR→BR→BL→TL と一周
     - 速度の目安: 10mm 間隔でサンプリングするので、1 秒間に 20~50mm くらいの速度で動かすと自然
   - 一周後 `Stop B2 trace` 押下
   - 点数ラベルが緑に変わり最終点数を表示(目安: 100-200 点)
   - Phase 表示 `B2 done. NN perimeter points recorded.`
   - 警告ログを確認: j5/j3 限界近接が出ていたら姿勢を変えてやり直す方が良い(再 Start で続行可能)

5. **保存**
   - `Save YAML + Exit Master` 押下
   - "MasterSlaveConfig(0xFC, 0, 0, 0)" + master exit + save ログ
   - 保存先 yaml に `schema_version: 3`、 `traces.perimeter` に NN 件、 `source_breakdown: {corners: 4, perimeter: NN, ...}` が書かれていることを確認
   - 電源リセット要求モーダル

6. **電源リセット + GUI restart**
   - アーム電源 OFF → 30 秒待つ → ON
   - ターミナルで CAN up (もしくは新 GUI の `CAN up (sudo)`)
   - `Restart GUI` 押下 → 新プロセス起動

7. **load 確認**
   - 起動時 Loaded defaults ログに **`schema_version=3`** が出ること
   - Center Y / Z / contact_x が前回 (M11) と同じ値の近辺に来ていること(corners 平均は v2 と同じ計算)

8. **(オプション) 描画回帰**
   - M11 と同じ手順で Tune Contact → Draw Square で 30mm 正方形を描画
   - 精度が <0.5mm を維持していることを確認

### 詰まった場合の戻り先

- 詰まった現象:
  - B2 trace で点が貯まらない → CandumpListener 動いてる? 0x155-7 broadcast 状態を確認 (wiggle 必要)
  - j5/j3 警告が連発 → ティーチ姿勢に問題、 別姿勢で B2 を再 Start
  - Save 押せない → 4 corners + B2 not_active を確認
- 戻り先: M12 (`<this commit>`) — v3 IO + 配線完了の状態。 GUI 起動・save・load は確認済み
- それ以下に戻る場合: M11 (`f43e8e4`) — v2 schema の旧 2-phase 状態

## Step 4 で意識的に残した制約

- **B2 perimeter trace は OPTIONAL**: 0 点での save も確認ダイアログ経由で可能。 Step 6 で B4 (中心微調整) を入れる時に「最低 1 周必要」等の制約を入れる予定
- **B3 / B4 / B5 ボタンは未実装**: dt_traces dict に "diagonal_tl_br"/"diagonal_tr_bl"/"surface" の slot は用意してあるが、 GUI からはまだ書き込まない
- **Run plane fit & save ボタンは未分離**: 設計ドキュメントの Step 7 で `Save` から独立した `Run plane fit & save` を作る予定。 現状は Save が両方やる
- **plane fit ロジックが `_fit_and_save` 内インライン**: Step 7 で plane_fit.py に外出し予定

## 次のステップ

実機検証が PASS したら:
1. **MILESTONES.md** に M13 を追加提案 (B2 trace 実機検証成功)
2. **Step 5** (B3 対角線トレース) に進む。 同じ DragSamplingThread を 2 配列で使うだけなので増分は小さい

実機検証で詰まったら:
1. 症状と再現手順をログに残す
2. `_capture_point()` / DragSamplingThread / FK の挙動を切り分け
3. 必要なら listener の生 candump 出力を `candump can0 | grep 155` で確認

## Step 4 で学んだこと

- **段階的 (sub-step) 編集** にすることで diff レビューがしやすく、回帰検出も早い(7 つの sub-step 各々で構文 OK を確認しながら進んだ)
- **`threading.Thread._stop` の name clash** (Step 3 の振り返り)が GUI 統合時にも 1 回引っかかるところだった — sampling thread の `self._stop_event` は引き続き安全
- **dry-run テストの書き換え** で旧 Step 2 transitional 仕様(plane_extras→surface)が完全に消えた。 古い仕様の痕跡を残さずクリーンに移行できた
- **DragSamplingThread の callback exception 吸収** が GUI 統合で意義を発揮: Tk widget が destroy 中などに callback が走っても sampling 側は死なない
