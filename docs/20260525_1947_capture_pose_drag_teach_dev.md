# Task B: ready/capture pose drag-teach 記録モードを dev GUI に追加

> 日時: 2026-05-25 19:47 (JST)
> 関連既存ファイル:
>   - `20260525_1851_canvas_calibration_v3_step6_8_dev_b4_b5.md` (B4/B5 dev)
>   - `20260525_1918_generated_image_drawing_dev_prep.md` (stroke drawing dev)
>   - chat 側プロンプト: PROMPT_for_claude_code_phase_a_b2.md
>     (Task A goto_ready_pose API / Task B record mode / Task C BL/TR / Task D in_bounds / Task E converter)
> ステータス: ⏳ **`wall_drawing_gui_dev.py` に Task B 追加完了・12 checks PASS・本番未マージ・実機未検証**

## 背景

chat 側で Phase A 設計判断 「B2 案」 が確定:
- カメラはエンドエフェクタ搭載
- **ready_pose = capture_pose** で同一ポーズ運用
- 1 サイクル = 1 回 capture + N ストローク一気描画
- ペン先映り込みは物理設計で対処
- Phase A 規約は変更なし(全体を 1024×1024 にマップ)

これを受けて、 ready_pose を drag-teach で確定する GUI モードが必要(Task B)。 ready_pose は `panel_frame.yaml` の `panel.ready_pose_deg`(現状 `null`) に書く。

## 追加した機能(`wall_drawing_gui_dev.py`)

### state

```python
self.calibrated_ready_pose = self._load_ready_pose_from_panel_yaml()
```

GUI 起動時に `panel_frame.yaml` から `panel.ready_pose_deg` を読み、 6 要素タプルとして保持(無ければ `None`)。

### helpers

| メソッド | 役割 |
|---|---|
| `_load_ready_pose_from_panel_yaml()` | yaml から `panel.ready_pose_deg` を読み込み tuple(6 floats) or None |
| `_save_ready_pose_to_panel_yaml(joints)` | yaml の `panel.ready_pose_deg` を書き換え。 他キー (`phase_a_calibration`、`panel.calibrated` 等) は保存 |
| `_clear_ready_pose_from_panel_yaml()` | `panel.ready_pose_deg` を `null` に設定(他キー無変更) |
| `_active_ready_pose()` | calibrated があればそれ、 無ければ `READY_POSE_V2_DEG` を返す |

### UI 追加(セクション 2 row_f)

```
Capture/Ready pose: [Record current as Ready/Capture pose] [Clear (revert to default)]
   calibrated: +X.XX +Y.YY +Z.ZZ +A.AA +B.BB +C.CC   ← or:  "default (uncalibrated): (-45, 60, ...)"
```

- **Record current as Ready/Capture pose**: master mode 中のみ enable。 現在の関節を pose として保存(yaml 即時書き込み + state 更新)
- **Clear (revert to default)**: yaml の値を null にして default 復帰

### handler

```python
def on_record_ready_pose(self):
    # master mode + 0x155-7 broadcast active が前提
    # listener.get_joints_deg() で現在関節を取得
    # 確認ダイアログ → _save_ready_pose_to_panel_yaml(joints)
    # state 更新 → ログ + _refresh_buttons()

def on_clear_ready_pose(self):
    # 確認ダイアログ → _clear_ready_pose_from_panel_yaml()
    # state を None に → ログ + _refresh_buttons()
```

### `READY_POSE_V2_DEG` → `_active_ready_pose()` への置換(4 箇所)

```python
# Before:
def _at_ready_pose(self, tol_deg=READY_JOINT_TOL_DEG):
    return self._at_pose(READY_POSE_V2_DEG, tol_deg)

# After:
def _at_ready_pose(self, tol_deg=READY_JOINT_TOL_DEG):
    return self._at_pose(self._active_ready_pose(), tol_deg)
```

同様に `_do_recover` (MOVE J target + verify err 計算) と `_do_draw_square` 末尾の戻りでも置換。

これにより:
- 「Recover to Ready」 → calibrated があればそこへ移動
- 「Draw Square」 終了後 → calibrated capture pose に戻る (= 次サイクルでカメラキャプチャ可能)
- ログには `MOVE J to calibrated capture pose ...` または `MOVE J to ready pose v2 default ...` と明示

### `_refresh_buttons` 拡張

- `btn_record_ready`: `is_recording = in_master + listener` 時のみ enable
- `btn_clear_ready`: calibrated_ready_pose が None でないとき enable(yaml 編集のみ、 master mode 不要)
- `lbl_ready_pose`: calibrated 時は値を表示(緑)、 未設定は default 値を表示(灰)

## 検証

### 単体テスト (`test_dev_capture_pose.py`)

12 テストケース、 全 PASS:

```
load_returns_none_when_missing_file       ← ファイル不在
load_returns_none_when_no_ready_pose      ← panel: は あるが ready_pose_deg なし
load_returns_none_for_malformed_value     ← 5 要素 (短すぎ)
load_returns_tuple_when_valid             ← 6 要素 → tuple
save_writes_panel_ready_pose              ← yaml 書き込み + 他キー保存
save_creates_file_if_missing              ← 新規ファイル + サブディレクトリ作成
save_validates_length                     ← 5 要素は ValueError
clear_nulls_ready_pose_only               ← null 化 + 他キー保存
clear_noop_when_file_missing              ← ファイル不在で no-op (raise しない)
active_ready_pose_prefers_calibrated      ← calibrated 設定で優先
active_ready_pose_falls_back_to_default   ← None で default fallback
round_trip_save_load                      ← save → load で同値
ALL CHECKS PASSED  (24 checks)
```

### 既存テスト無回帰

```
test_canvas_calibration_io.py     ALL CHECKS PASSED (50)
test_step2_v3_save.py             ALL CHECKS PASSED (35)
test_drag_sampling_thread.py      ALL CHECKS PASSED (27)
test_dev_b4_b5.py                 ALL CHECKS PASSED (18)
test_dev_stroke_drawing.py        ALL CHECKS PASSED (32)
test_dev_capture_pose.py          ALL CHECKS PASSED (24)
              合計 6 スイート          ALL CHECKS PASSED (186)
```

### Tk smoke (GUI build)

```
calibrated_ready_pose init: None
_active_ready_pose():       (-45.0, 60.0, -60.0, 0.0, 30.0, 0.0)
btn_record_ready:           "Record current as Ready/Capture pose"
btn_clear_ready:            "Clear (revert to default)"
lbl_ready_pose:             "default (uncalibrated): (-45.0, 60.0, -60.0, 0.0, 30.0, 0.0)"
```

## 想定ワークフロー(本番マージ後)

1. GUI 起動 → 起動ログに `Loaded defaults (v3) ...` + (panel_frame.yaml に ready_pose があれば) その値が `lbl_ready_pose` に表示
2. `Connect` → `Recover to Ready` (現状の default pose)
3. `Start Drag-Teach` で master mode 突入
4. **アームを手でドラッグして desired capture pose に移動**:
   - エンドエフェクタのカメラがホワイトボード全体を見える
   - ペン先が映り込まない(物理位置)
   - 関節 margin に余裕がある(下端で j5 限界に張り付かない)
5. `Record current as Ready/Capture pose` 押下 → 確認ダイアログで joint 値を確認 → Yes
6. → `panel_frame.yaml` の `panel.ready_pose_deg` に即時書き込み
7. → `lbl_ready_pose` が「calibrated: +X.XX ...」 緑表示に
8. (任意で) B1-B5 canvas calibration を続行
9. `Save YAML + Exit Master` → 電源リセット → Restart GUI
10. 起動時に新 ready_pose が自動 load される(`lbl_ready_pose` 緑)
11. 次回 `Recover to Ready` 押下時、 default ではなく calibrated capture pose に移動

## Task A (Robot.goto_ready_pose API) との関係

Task A は `modules/robot.py` 側の改修(panel_frame.yaml から ready_pose を読む)。 本実装(Task B)は **GUI 内で完結** しており、 Robot クラスを使わない (GUI は直接 piper_sdk を叩く)。

dev のままでは Robot.goto_ready_pose() は依然 DEFAULT_READY_POSE_DEG を使う。 マージ後に Task A 側で:
- `Robot.__init__` で panel_frame.yaml から `panel.ready_pose_deg` を読み、 self.ready_pose に設定
- もしくは `Robot.goto_ready_pose()` 呼び出し時に毎回 yaml を read

を追加する(現セッション範囲外)。 `draw_strokes_wall_dev.py` (前波で実装) は Robot.goto_ready_pose を呼ぶので、 Task A 完了後に capture pose 機能が完成する。

## 本番マージ手順

### Step 1: dev で実機検証

1. `wall_drawing_gui_dev.py` で master mode → 手で arm を desired capture pose に → Record 押下
2. `cat ~/draw_piper/calibration/panel_frame.yaml` で `panel.ready_pose_deg` が書き込まれた事を確認
3. Save & Exit Master → 電源リセット → 再起動
4. 起動ログに `lbl_ready_pose: calibrated: ...` が出る事を確認
5. `Recover to Ready` 押下 → arm が calibrated pose に移動する事を確認
6. ログに `MOVE J to calibrated capture pose ...` と出る事を確認

### Step 2: 本番化

dev で問題なければ `wall_drawing_gui.py` に同じ変更を適用:

```bash
# 現状: dev に B4 + B5 + capture pose が乗っている。 全部本番化したい場合:
cp ~/piper_test/wall_drawing_gui_dev.py ~/piper_test/wall_drawing_gui.py
```

または cherry-pick 派なら:

```bash
diff -u ~/piper_test/wall_drawing_gui.py ~/piper_test/wall_drawing_gui_dev.py
# capture pose 関連の hunk を選択して patch
```

capture pose 関連の追加シンボル(本番との衝突なし):
- `self.calibrated_ready_pose`(新規 attr)
- `_PANEL_YAML_PATH`(新規 class attr)
- `_load_ready_pose_from_panel_yaml`, `_save_ready_pose_to_panel_yaml`, `_clear_ready_pose_from_panel_yaml`, `_active_ready_pose`(新規 method)
- `btn_record_ready`, `btn_clear_ready`, `lbl_ready_pose`(新規 widget)
- `on_record_ready_pose`, `on_clear_ready_pose`(新規 handler)
- `row_f`(新規 frame in section 2)
- `import yaml`(再追加)
- 4 箇所の `READY_POSE_V2_DEG` → `_active_ready_pose()` 置換

### Step 3: マージ後の検証

```bash
cd ~/piper_test
python -c "import ast; ast.parse(open('wall_drawing_gui.py').read()); print('syntax ok')"

# 既存テスト + dev テスト全 PASS
for t in test_canvas_calibration_io.py test_step2_v3_save.py \
         test_drag_sampling_thread.py test_dev_b4_b5.py \
         test_dev_stroke_drawing.py test_dev_capture_pose.py; do
    /home/jizaiedev2026/draw_piper/venv/bin/python "$t" 2>&1 | tail -1
done
```

### Step 4: dev ファイル削除(マージ完了後)

```bash
rm ~/piper_test/wall_drawing_gui_dev.py
# test ファイルは本番テスト化:
mv ~/piper_test/test_dev_capture_pose.py ~/piper_test/test_capture_pose.py
sed -i 's/wall_drawing_gui_dev/wall_drawing_gui/g' ~/piper_test/test_capture_pose.py
```

## 設計判断の補足

### なぜ Record 時に即 yaml 書き込みか

途中で GUI クラッシュした際にも記録を失わないため。 また「Save YAML + Exit Master」 とは別 hook にすることで、 canvas calibration を後でやり直しても capture pose は維持される。 マイナス: 試し撮りで悪い pose を Record してしまうと上書きされる(ただし Clear で戻せる)。

### なぜ READY_POSE_V2_DEG をそのまま残したか

`_active_ready_pose()` の fallback として `READY_POSE_V2_DEG` を使う。 これは M11 確立の安全な default で、 capture pose 未設定時の挙動を変えない。 calibrated pose を消したいときは `Clear` ボタンで明示的に。

### なぜ Task A (Robot.goto_ready_pose 改修) と分離したか

Task B は GUI 内完結で実機検証可能(B4/B5 と同じくクラス内 method の置換)。 Task A は Robot クラスを使う他スクリプト (`draw_square_wall.py`, `test_vlm_to_image.py` 等) に影響するため、 chat 側で別途まとめて改修する範囲。

### なぜ「Save All」 ボタンを作らなかったか

`panel_frame.yaml` 編集と `canvas_calibration.yaml` 編集は別概念(panel = capture/ready pose、 canvas = 描画面のジオメトリ)。 統合 Save ボタンは Save が何をやってるか不明瞭になる。 Record ボタンで即書き込み + Save YAML for canvas は別、 が UX として明確。

## 既知の制限・残課題

- **Master mode の broadcast 待ち**: アームを手で動かしていない時は 0x155-7 frame が来ない(M10 N4 既知)。 Record ボタン押下時に 「WIGGLE the arm first」 警告が出る(既存 _capture_point 同様)
- **Record 確認ダイアログのキャンセル**: Yes 押下後の yaml 書き込みは即時。 巻き戻すには Clear → 別 pose を Record
- **`_active_ready_pose()` の戻り値が dict ではなく tuple**: 既存の `READY_POSE_V2_DEG` は tuple なので一致。 もし dict 形式が必要な箇所(`Robot(ready_pose=...)` 等)があれば、 適宜変換が必要(現 GUI 内では使われていない)
- **panel.ready_pose_deg の型**: yaml に書く時は `[round(v, 4) for v in joints]` でリスト化、 load 時は tuple 化。 yaml は list ⇄ tuple の往復で型が揺れるが、 read 側で tuple に統一しているため一貫
- **Task C (BL/TR 専用 record モード) の要否**: 設計判断保留中。 本実装は 「現在の任意関節」 をそのまま記録する general-purpose モード。 BL/TR 専用ガイド付きモードは別実装可能

## 戻ってきたユーザに伝えたいこと

1. **本番ファイル無変更** (`wall_drawing_gui.py` は Step 5 B3 まで)
2. **`wall_drawing_gui_dev.py` に Task B 追加完了**(B4/B5/stroke drawing と同じ dev 内)
3. **テスト 6 スイート 186 checks ALL PASS**
4. dev GUI 起動方法:
   ```
   /home/jizaiedev2026/draw_piper/venv/bin/python ~/piper_test/wall_drawing_gui_dev.py
   ```
5. capture pose 実機検証手順: 設計ドキュメント末尾の "想定ワークフロー" 参照
6. マージ判断は B4/B5、 stroke drawing、 capture pose の 3 テーマを個別に / まとめて選択可能(各 docs に手順あり)
