# Canvas calibration v3 — Step 6 (B4) + Step 8 (B5) を dev ファイルで先行実装

> 日時: 2026-05-25 18:51 (JST)
> 関連既存ファイル: `20260525_1447_canvas_calibration_v3_design.md` (設計),
>                    `20260525_1545_canvas_calibration_v3_step4_b2_gui.md` (Step 4 B2),
>                    (Step 5 B3 進捗ログは未作成 — `wall_drawing_gui.py` に直接実装済み)
> ステータス: ⏳ **dev ファイル `wall_drawing_gui_dev.py` に B4 + B5 を追加完了・18 checks PASS・本番ファイル未マージ**

## 背景: なぜ dev ファイルで先行か

ユーザが実機検証中に「描画が視覚中心とズレる」 問題を発見(B1 BR/BL コーナーが joint limit に張り付いていたため 4 隅平均 Z=321 が視覚中心 Z≈370 とズレる)。 この場で B4 (中心微調整) を入れたいが、 ユーザは食事に出ていて即時マージ確認できない。

ユーザの指示:
> 別ファイルで内面キャリブの実装を進めておいて、後でマージする

→ 本番 `wall_drawing_gui.py` には触らず、 並行ファイル `wall_drawing_gui_dev.py` に B4+B5 を追加。 ユーザ復帰後に diff レビューして本番にマージする。

## 追加した機能

### B4 — Center Adjustment (Step 6)

| 項目 | 仕様 |
|---|---|
| **動作タイミング** | master mode の **外** (Save+restart 後の通常操作) |
| **UI 配置** | `3.5. Center Adjustment` セクションを Tune Contact (3) と Draw Square (4) の間に新設 |
| **ボタン (上段)** | `Go to B3 center (pen-down)` / `Go to corner-avg center` / `Confirm as Center` / `Cancel (Lift Pen)` |
| **ボタン (下段)** | `Y nudge ±0.5 / ±1.0` (4 個) + `Z nudge ±0.5 / ±1.0` (4 個) + 状態ラベル |
| **Tk var 共有** | `var_cy` / `var_cz` を Tune Contact + Draw Square と共有 → Confirm 後即 Draw Square に反映 |
| **yaml 更新** | `_b4_persist_center(y, z)` が `whiteboard_computed.center_mm` と `center_adjustment_mm` のみ書き換え。 corners / traces / plane_fit は無変更 |
| **`Go to B3 center` の挙動** | yaml の `center_calc_mm` (対角線 fit 交点) を読み、 そこに MOVE L (pen-down)。 ない場合エラー |
| **`Go to corner-avg center` の挙動** | yaml の `center_corner_avg_mm` を読み、 そこに MOVE L (pen-down)。 ない場合は現在の `var_cy/var_cz` を使用(後方互換) |
| **状態フラグ** | `self.b4_active`(in B4 session), `self.b4_pen_down`(pen state) |
| **干渉防止** | B4 開始時に `tune_x_active=False` をクリア(Tune Contact の X nudge と競合させない) |

### B5 — Surface zigzag (Step 8)

| 項目 | 仕様 |
|---|---|
| **動作タイミング** | master mode 内、 B2/B3 と同じ DragSamplingThread パターン |
| **UI 配置** | section 2 に `row_e` を追加: `[Start surface zigzag]` + `surface: N` ラベル |
| **handler** | `on_b5_start_trace` → 既存の `_start_trace("surface")` を呼ぶだけ。 Stop は共通 `Stop trace` ボタン |
| **phase 表示** | `b5_recording` / `b5_done` を `_refresh_buttons` の indicator に追加 |
| **yaml 出力** | 既存 `_fit_and_save` の `traces.surface` slot を埋める(変更不要) |

### `_refresh_buttons` 拡張

- 既存の `can_start` フラグで B5 Start ボタンを enable/disable
- B5 count label をライブ更新(in-progress + accumulated)
- B4 ボタン群を `b4_active` の有無で enable 切替
- phase indicator に `b5_recording` ケース追加
- Done 状態は `("b2_done", "b3_done", "b5_done")` を統合表示

## 検証(dev ファイル単独)

```
$ /home/jizaiedev2026/draw_piper/venv/bin/python \
    /home/jizaiedev2026/piper_test/test_dev_b4_b5.py

--- b4_load_candidates_from_yaml ---           (3 PASS)
--- b4_persist_updates_center_mm_only ---      (7 PASS) ← corners/traces/plane_fit 不変を確認
--- b4_persist_rejects_v1_v2 ---               (1 PASS) ← v3 でないと書かない
--- b5_trace_label_mapping ---                 (1 PASS)
--- b5_save_includes_surface_in_breakdown ---  (3 PASS) ← source_breakdown.surface=3
--- b4_var_cy_cz_shared_with_tune_contact ---  (2 PASS) ← Draw Square で即反映確認
ALL CHECKS PASSED  (18 checks)
```

既存テスト無回帰:
```
test_canvas_calibration_io.py   ALL CHECKS PASSED (50)
test_step2_v3_save.py           ALL CHECKS PASSED (35)
test_drag_sampling_thread.py    ALL CHECKS PASSED (27)
```

Tk smoke: dev GUI 構築 → B5 ボタン / B4 4 ボタン + nudge 8 個 + ラベル全て存在確認 → teardown OK。

## 想定ワークフロー(本番マージ後のユーザ手順)

### B4 (中心微調整) を試すケース

前提: B1+B2+B3 を済ませて Save → power cycle → GUI restart 済み(disk 上 v3 yaml に `center_calc_mm` または `center_corner_avg_mm` あり)。

1. GUI 起動 → `Loaded defaults (v3) ...` ログ確認
2. `Connect` → `Recover to Ready`
3. **`3.5. Center Adjustment` セクション**で:
   - `Go to B3 center (pen-down)` 押下 → アームが対角線交点(center_calc)に pen-down
   - もしくは `Go to corner-avg center` で 4 隅平均位置に pen-down
4. アーム位置を観察 → 視覚的なキャンバス中心とずれている場合 `Y nudge` / `Z nudge` で調整
5. ぴったり合ったら **`Confirm as Center`** 押下 → yaml の `whiteboard_computed.center_mm` 更新
6. (任意) `Cancel (Lift Pen)` でやり直し
7. そのまま `Draw Square` で 30mm 正方形描画 → 確定中心で描画される(GUI restart 不要)
8. 次回 GUI 起動時、`_load_calib_defaults()` が新 center_mm を読んで Tune Contact の `Center Y/Z` に反映

### B5 (面ジグザグ) を試すケース

前提: master mode 中、 B1 完了、 (任意) B2/B3 完了。

1. `Start surface zigzag` 押下 (section 2 row_e)
2. アームを手でジグザグになぞる(中央付近の内部領域)
3. `Stop trace` 押下
4. surface count が緑表示で点数確認可能
5. `Save YAML + Exit Master` で v3 yaml の `traces.surface` に保存される。 plane fit にも投入される

## 本番マージ手順

### 推奨アプローチ: 一括 cherry-pick(個別 hunk なし)

差分は 448 行・12 hunk と中規模だが、 全 hunk が *追加のみ*(既存ロジック書き換えなし) なので一括コピーが安全。

#### Step 1: 差分確認

```bash
diff -u ~/piper_test/wall_drawing_gui.py ~/piper_test/wall_drawing_gui_dev.py | less
```

または unified diff をファイルに保存:
```bash
diff -u ~/piper_test/wall_drawing_gui.py ~/piper_test/wall_drawing_gui_dev.py > /tmp/b4_b5.diff
```

#### Step 2: 干渉なしを確認

dev に追加されたシンボル / セクションは以下のみで、 本番側の既存名と衝突しない:

- 定数: `Y_NUDGE_STEPS` / `Z_NUDGE_STEPS`(新規)
- インスタンス変数: `self.b4_active` / `self.b4_pen_down`(新規)
- ウィジェット: `self.btn_b5_start` / `self.lbl_b5_count` / `self.btn_b4_go_calc` / `self.btn_b4_go_corner_avg` / `self.btn_b4_confirm` / `self.btn_b4_cancel` / `self.btn_b4_nudge_y` / `self.btn_b4_nudge_z` / `self.lbl_b4_status`(全て新規)
- メソッド: `on_b5_start_trace` / `_b4_load_center_candidate` / `on_b4_go_calc_center` / `on_b4_go_corner_avg` / `_b4_go_to` / `_do_b4_go_to` / `on_b4_nudge_y` / `on_b4_nudge_z` / `_do_b4_nudge` / `on_b4_confirm` / `_do_b4_confirm` / `_b4_persist_center` / `on_b4_cancel` / `_do_b4_cancel`(全て新規)
- `_refresh_buttons` への追加: B5 Start ボタンの enable、 surface count label 更新、 phase indicator に b5/b5_done 追加、 B4 4 ボタン + nudge enable 制御、 状態ラベル更新。 全て 「if 文を追加」 する形で既存ロジック保持

#### Step 3: 実行

`wall_drawing_gui_dev.py` を `wall_drawing_gui.py` に置換するのが最短:

```bash
# Step 5 (B3) は既に本番に入っている。 dev ファイルは本番ベース + B4+B5 なので、
# dev を本番に上書きすれば B4+B5 がマージされる(B3 は既に dev にも含まれている)。
cp ~/piper_test/wall_drawing_gui_dev.py ~/piper_test/wall_drawing_gui.py
```

または cherry-pick で hunk ごとに適用したい場合は `patch` コマンドで:

```bash
diff -u ~/piper_test/wall_drawing_gui.py ~/piper_test/wall_drawing_gui_dev.py > /tmp/b4_b5.diff
cd ~/piper_test
patch -p0 wall_drawing_gui.py < /tmp/b4_b5.diff
```

#### Step 4: マージ後の検証

```bash
cd ~/piper_test
/home/jizaiedev2026/draw_piper/venv/bin/python -c "import ast; ast.parse(open('wall_drawing_gui.py').read()); print('syntax ok')"

# Tk smoke
/home/jizaiedev2026/draw_piper/venv/bin/python -c "
import sys
sys.path.insert(0, '.')
import tkinter as tk
root = tk.Tk()
from wall_drawing_gui import WallDrawingGUI
gui = WallDrawingGUI(root)
assert hasattr(gui, 'btn_b4_confirm'), 'B4 missing'
assert hasattr(gui, 'btn_b5_start'), 'B5 missing'
print('OK')
root.destroy()
"

# Regression (既存テストが PASS することを確認)
for t in test_canvas_calibration_io.py test_step2_v3_save.py test_drag_sampling_thread.py test_dev_b4_b5.py; do
    /home/jizaiedev2026/draw_piper/venv/bin/python "$t" 2>&1 | tail -1
done
```

#### Step 5: dev ファイル削除(マージ完了後)

```bash
rm ~/piper_test/wall_drawing_gui_dev.py
# test_dev_b4_b5.py は本番化:
mv ~/piper_test/test_dev_b4_b5.py ~/piper_test/test_b4_b5.py
# test 内の import を wall_drawing_gui_dev → wall_drawing_gui に書き換え
sed -i 's/wall_drawing_gui_dev/wall_drawing_gui/g' ~/piper_test/test_b4_b5.py
```

## 設計判断と理由

### なぜ B4 を master mode の **外** にしたか

設計ドキュメントを読み直すと B3 → B4 → B5 の流れだが、 piper SDK の挙動上:

- master mode (0xFA) 中は **SDK の EndPoseCtrl が効かない**(モーター torque off、 hand-drag 専用)
- B4 は `Go to ...` → MOVE L → nudge を必要とするので SDK 制御が必須
- よって B4 は master mode を抜けた後 = Save 後の通常セッションでやる

ユーザ視点では:
- Calibration session 1: master mode → B1 + B2 + B3 → Save → power cycle → restart
- Restart 後: B4 (yaml の center_calc_mm を読んで微調整) → Confirm → そのまま描画

これは設計ドキュメントの 「B4 はキャリブの 4 phase 目」 という記述とは少しズレるが、 SDK 制約上やむを得ない。 実装の方が現実的。

### なぜ B4 で `var_cy` / `var_cz` を共有したか

別の `var_b4_y` / `var_b4_z` を作ると Tune Contact / Draw Square の `Center Y/Z` と二重管理になる。 共有することで:

- Confirm 後 GUI restart 不要(`var_cy`/`var_cz` がそのまま Draw Square に渡る)
- Tune Contact の `Go to Canvas Center (pen-down)` も新中心で動く
- 値の一貫性が保たれる

### なぜ B4 で yaml を 部分更新 (`_b4_persist_center`) にしたか

設計ドキュメント上、 center_mm 確定は再 fit を必要としない(corners / traces は変更しない)。 `_fit_and_save` を呼び直すと plane_fit を再計算してしまうので、 そこは触らず:

- `whiteboard_computed.center_mm` (B4 確定値)
- `whiteboard_computed.center_adjustment_mm` (確定値 - corner_avg)

の 2 フィールドだけ書き換えて `write_v3` を再呼び出し。 他のキーは既存値を渡し直すだけ。 v1/v2 yaml への書き換えは拒否(RuntimeError)。

## 既知の制限・残課題

- **B4 を master mode 中に呼んでも何も起きない**: ボタンは tune_x_active と排他にしているが、 master mode 排他チェックは追加していない(現状の `connected_idle` フラグが in_master と排他なのでカバーされている)
- **B5 のジグザグ パターン提案 UI なし**: ユーザは手でなぞる軌跡を考える。 設計ドキュメント図にあるような自動パターン補助は未実装
- **B4 の "Go to ..." での RPY**: cached_wall_rpy を使う前提だが、 初回呼び出し時は `_ensure_wall_facing()` で取得する
- **B4 確定値の draw_x**: contact_x_mm はそのまま使う(B4 では Y/Z だけ調整、 X は Tune Contact の責務)

## 戻ってきたユーザに伝えたいこと

1. **本番ファイルは無変更**(`wall_drawing_gui.py` は Step 5 B3 までのまま)
2. **dev ファイル `wall_drawing_gui_dev.py` で B4 + B5 が動くようになった**(全テスト PASS)
3. **マージは「dev で実機検証 → 問題なければ本番に cp」が簡単**(または diff で hunk ずつ確認)
4. 実機で B4 を試したい場合:
   ```
   /home/jizaiedev2026/draw_piper/venv/bin/python ~/piper_test/wall_drawing_gui_dev.py
   ```
   で dev GUI を直接起動できる(本番 GUI と同じ yaml を読み書きするので、 dev で B4 確定すれば本番 GUI 起動時もその center が反映される)
5. テスト追加: `test_dev_b4_b5.py` (18 checks)
6. マージ手順: 上記 "Step 1-5" 参照
