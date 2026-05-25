# Canvas calibration v3 — Step 3: drag_sampling_thread 実装

> 日時: 2026-05-25 15:29 (JST)
> 関連既存ファイル: `20260525_1447_canvas_calibration_v3_design.md` (設計),
>                    `20260525_1457_canvas_calibration_v3_step1_yaml_io.md` (Step 1),
>                    `20260525_1517_canvas_calibration_v3_step2_gui_wiring.md` (Step 2),
>                    `20260523_1820_master_mode_drag_teach_calibration.md` (N4: master mode SDK starvation)
> ステータス: ✅ **モジュール完成・27 checks PASS、GUI 未配線**(Step 4 で B2 と一緒に配線予定、実機検証も Step 4 で初めて)

## 実施したこと

設計ドキュメント「実装順序の推奨」 step 3 として、`~/piper_test/` に新規 2 ファイル:

- `drag_sampling_thread.py` (約 230 行) — `DragSamplingThread` クラス + ヘルパ
- `test_drag_sampling_thread.py` (約 350 行) — 単体テスト 12 ケース 27 checks

### モジュール API (`drag_sampling_thread.py`)

```python
class DragSamplingThread(threading.Thread):
    def __init__(
        self,
        listener,                       # any object with get_joints_deg() -> 6 floats
        fk_fn,                          # joints_deg -> (xyz_mm[3], R[3,3])
        euler_fn,                       # R -> (rx, ry, rz) deg
        *,
        sampling_interval_mm=10.0,      # YZ-plane distance threshold
        poll_interval_s=0.1,            # listener polling cadence
        joint_limits_deg=...,           # default = DEFAULT_JOINT_LIMITS_DEG
        joint_warning_margin_deg=5.0,
        on_point=None,                  # callback(record) when sample accepted
        on_warning=None,                # callback(idx, val, reason) on joint near limit
        on_error=None,                  # callback(exc) on FK errors
    ): ...

    def start(): ...
    def stop(join_timeout=2.0): ...     # request shutdown + join
    def step_once() -> Optional[dict]:  # single deterministic step (testable)
    def get_points() -> List[dict]:     # snapshot the v3 records collected
    def get_point_count() -> int:
    def reset() -> None:                # clear state (intended pre-start)
```

主な設計判断:

1. **`step_once()` を分離** — `run()` のループ本体を 1 ステップずつ呼べるようにし、テストが時間に依存しないようにした。 `time.sleep()` のない deterministic な検証ができる
2. **listener / FK / euler を injection** — `wall_facing_ik` や `CandumpListener` への hard dependency を持たず、 任意のモック可能なオブジェクトを受け取る
3. **distance gate は (Y, Z) 平面のみ** — 設計ドキュメント通り base_link X は無視。 アームのアプローチ方向に微振動しても点が増えない
4. **joint 警告に hysteresis** — 一度警告した (joint, side) は danger zone から抜けるまで再警告しない。 master mode 中アームを動かし続ける UX なので、 spam log を防ぐ
5. **callback 例外を吸収** — `on_point` / `on_warning` / `on_error` が raise してもサンプリングスレッドは死なない(GUI の log_safe が稀に Tk タイミングで失敗するケース等を想定)

### N4 を踏まえた設計

設計ドキュメントには「`GetArmJointMsgs()` + `GetArmEndPoseMsgs()` を 100ms 間隔で読む」とあるが、 N4 ([20260523_1820](20260523_1820_master_mode_drag_teach_calibration.md)) の検証で master mode 中の SDK feedback は完全に starve することが分かっている。 そこで本実装は:

- **joint は listener (= 別プロセス candump) 経由で読む**
- **end_pose は SDK から取らず、 FK で導出する** (`wall_facing_ik.fk(joints)` → xyz + R)
- **これは既存 `wall_drawing_gui._capture_point()` と同じ戦略**で、再現性が確認済

### 重要な実装上のハマり: `threading.Thread._stop` の name clash

最初の実装で `self._stop = threading.Event()` と書いたところ、`threading.Thread` の内部 method `_stop` を上書きしてしまい、 `thread.join()` 中に `TypeError: 'Event' object is not callable` が発生。 修正:

- `self._stop` → `self._stop_event` にリネーム
- コメントで "NB: don't use _stop -- threading.Thread already defines that." を残した

これは Python の `threading` モジュールの hidden footgun で、 初学者が踏みやすい。 ローカル attribute は `threading.Thread` の内部名と被らないよう接頭辞 / 接尾辞を付けるのが安全。

### テスト一覧 (27 checks)

```
$ /home/jizaiedev2026/draw_piper/venv/bin/python \
    /home/jizaiedev2026/piper_test/test_drag_sampling_thread.py

--- make_v3_record_shape ---                    (1 check)
--- check_joint_warnings_helper ---             (3 checks)
--- step_once_skips_zero_joints ---             (2 checks)
--- step_once_first_sample_recorded ---         (2 checks)
--- step_once_distance_gating ---               (2 checks)  Y 0..50mm 5mm 刻み, 10mm 閾値 -> 6/11 acc
--- step_once_diagonal_threshold ---            (2 checks)  対角 (3,4)=5mm rejected, (6,8)=10mm acc
--- step_once_fk_error_routed ---               (2 checks)  on_error にルーティング、サンプル捨て
--- reset_clears_state ---                      (3 checks)
--- on_point_callback_fires ---                 (2 checks)  callback 内 raise でも samp 続行
--- joint_warnings_fire_once_then_hysteresis -- (3 checks)  2 回目クロスで再発火
--- thread_lifecycle_smoke ---                  (3 checks)  start/stop 実スレッド
--- stop_returns_quickly ---                    (2 checks)  poll_interval 1.0s でも stop は <0.5s
ALL CHECKS PASSED (27 checks)
```

回帰確認:

```
test_canvas_calibration_io.py      ALL CHECKS PASSED (50)
test_step2_v3_save.py              ALL CHECKS PASSED (33)
test_drag_sampling_thread.py       ALL CHECKS PASSED (27)
                          合計      ALL CHECKS PASSED (110)
```

## 安全マージンに関する設計遵守

設計ドキュメント「安全マージンに関する強い要請」を満たす:

- ✅ **j5/j3 警告**: `joint_warning_margin_deg=5.0` で全関節を監視、 設計通り 限界 -5° で発火
- ✅ **警告は止めず通知だけ**: スレッドは停止せず、 `on_warning` callback で GUI ログに残す
- ✅ **B5 (面なぞり) の `reachable_polygon_yz` 外警告**: これは step 7 の joint_map.py で実装する。 本モジュールは関節限界のみカバー

## GUI 配線(Step 4 でやる)

本モジュールは **単独テストが終わり、 GUI からはまだ使われていない**。 Step 4 で:

- `wall_drawing_gui.py` に B2 (外周トレース) セクションを新設
- `Start B2` ボタン: master mode 有効化 → CandumpListener + DragSamplingThread 起動 → ライブカウンタ表示
- `Stop B2` ボタン: DragSamplingThread.stop() → CandumpListener.stop() → master mode 解除 → 電源リセット要求モーダル
- 収集点を `self.dt_traces["perimeter"]` に格納し `_fit_and_save` の `traces.perimeter` に渡す
- スレッド入出力: `on_point=` で GUI ラベル更新、 `on_warning=` で log_safe()

Step 4 は **実機初検証**になる。 GUI 完成後にユーザに「実機で 1 周なぞって観察」依頼の確認を取る。

## 次にやること (Step 4)

`wall_drawing_gui.py` 改修:

1. 既存 "2. Drag-Teach" セクションのレイアウトを v3 設計の Phase 構成 (B1 / B2 / B3 / B4 / B5) に対応するよう書き換え準備
   - **Step 4 のスコープは B2 だけ**。 B3/B4/B5 は次以降の Step で順次追加
   - B1 (4 corners ボタン) は既存維持。 ラベルだけ「Phase B1」明示に更新
2. B2 セクション: Sampling interval Spinbox + Start/Stop ボタン + 点数ラベル
3. master mode 突入は B1 完了後に自動 (or 初回 B2 Start で) — 設計ドキュメントは "B2 は B1 完了後" と依存関係指定済
4. dt_corners / dt_plane_points の代わりに dt_traces["perimeter"] 等の trace dict 構造に切替
5. `_fit_and_save` を v3 traces.perimeter に対応するよう拡張 (Step 2 の transitional surface マッピングからの卒業)

### 実機検証前のチェックリスト(Step 4 で使う)

- [ ] GUI 起動 → ステータスバー の CAN UP / Connected を確認
- [ ] Recover to Ready で ready pose v2 へ
- [ ] B1 (TL→TR→BR→BL) を従来通り
- [ ] B2 Start → master mode → "WIGGLE arm" → 外周をなぞる → Stop
- [ ] 点数が 10mm 間隔ほどで貯まることを目視確認 (1 周 ~1000mm なら 80~120 点)
- [ ] 警告ログに j5/j3 の限界近接が出るかチェック (出ない方が望ましい = 安全な姿勢で実施できている)
- [ ] Save → yaml view で `traces.perimeter` に N 件、 `traces.surface` が空であることを確認
- [ ] Restart GUI → 起動時 schema_version=3 のロードを確認

### 実機検証で詰まった場合の戻り先

- M11 (`f43e8e4`): pre-v3 (Step 2 前) の状態。 GUI は 2-phase だが 1 回の描画動作確認済み
- M12 (this commit) : v3 IO + 配線完了の状態。 Step 4 の GUI 改修前

## Step 3 で学んだこと

- **`threading.Thread._stop` は予約名**。 サブクラスで `self._stop` を使ってはいけない。 攻撃的なシンボル衝突
- **`step_once()` を public にする** パターンは、 ループの単一反復をテスト可能にし、 thread と timing を分離してテストが書きやすくなる(`run()` は単に `step_once + sleep` の薄い wrapper)
- **callback 内の例外吸収** は GUI コードと組み合わせる場合の必須プラクティス。 thread を巻き込んで死ぬと UX が極端に悪化する
- **distance gate を YZ のみ** にすると X 振動に影響されない一方、 「壁から離れた所で動いても貯まる」 リスクは別途 reach_check で見ないといけない。 これは Step 7 の joint_map で
