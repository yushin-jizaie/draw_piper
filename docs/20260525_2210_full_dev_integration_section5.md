# full_dev: 全 dev 機能 + 生成画像描画パイプライン を 1 GUI に統合

> 日時: 2026-05-25 22:10 (JST)
> 関連既存ファイル: 全ての ...dev_*.md
> ステータス: ⏳ **`wall_drawing_gui_full_dev.py` 新規作成、 全機能統合、 smoke + 6 テスト 186 checks PASS、 実機未検証**

## 目的

dev で並走していた各機能(B4/B5/capture pose、 UX 第 2 ラウンド、 生成画像描画パイプライン)を **1 つの GUI** にまとめた統合版を作る。 本番マージ前の preview として使い、 実機検証を一気通貫で行えるようにする。

## 新規ファイル

`~/piper_test/wall_drawing_gui_full_dev.py` (約 3300 行)

- ベース: `wall_drawing_gui_dev.py` (本番 + B4/B5/capture/UX 第 2 ラウンド)
- 追加: **セクション 5「生成画像描画 (vectorizer の strokes.json を実機描画、実験的)」**

## Section 5 の中身

```
5. 生成画像描画 (実験的)
   ストロークJSON: [パス表示...] [選択...]
   [panel_frame.yaml を更新 (canvas → panel)]  最大本数: [0]  点間遅延(ms): [30]  ☐ 実機で描画 (オフ=mock プレビュー)
   [描画開始] [中止]  進捗: 0/0 本
```

### 機能

| ボタン/フィールド | 動作 |
|---|---|
| `選択...` | ファイル選択ダイアログで `strokes.json` を指定。 初期ディレクトリは `~/draw_piper/logs/` |
| `panel_frame.yaml を更新` | `canvas_to_panel_frame_dev.convert_canvas_yaml_to_panel()` を呼ぶ。 canvas yaml の 4 隅 → panel の origin / u_axis / v_axis / normal / size_mm を生成し、 既存の phase_a_calibration は保護 |
| `最大本数` | 先頭 N 本だけ描画(0 = 全本数)。 smoke テストには小さい値 |
| `点間遅延(ms)` | stroke 内の連続 EndPoseCtrl コマンド間の wait。 デフォルト 30ms (SDK のストリーム pen-trace) |
| `☐ 実機で描画` | OFF = mock(座標を log に出すだけ、 アーム動かさず安全)。 ON = 実機 |
| `描画開始` | 別スレッドで pipeline 実行 |
| `中止` | 現在のストローク終了後に停止 |
| `進捗:` | ライブ更新の進捗ラベル |

### 描画パイプラインの 7 段階

```python
def _do_strokes_draw(self):
    1. load_strokes_json(path) → image_shape, strokes_px, meta
    2. panel_frame.yaml の panel: ブロック読込み (origin/u_axis/v_axis/normal/size_mm)
    3. 画像 px → panel UV mm 変換 (簡易線形 + bounds clip)
       u_mm = x_px / w * size_w
       v_mm = (h - y_px) / h * size_h  # Y軸反転
    4. 最大本数 制限
    5. 実機モードなら wall-facing 確認 + cached_wall_rpy 取得
    6. 各ストロークごとに:
       a. pen-up travel to first point (_move_endpose, force_move_l=True)
       b. pen-down (_move_endpose, force_move_l=True)
       c. trace: piper.EndPoseCtrl を直接ストリーム (interpoint_ms 間隔)
       d. pen-up at last point
    7. 完了後にホーム/撮影位置へ復帰
```

### 安全機構

- **mock デフォルト**: ユーザが明示的にチェック入れない限り実機は動かない
- **未接続時の実機モード拒否**: `自動的にエラーダイアログ`
- **panel: 未確定時の警告**: `calibrated: false` だったら確認ダイアログ
- **strokes_abort_flag** で 中止 ボタン対応(ストローク終了で停止)
- **描画ストロークは強制 MOVE L** (`force_move_l=True`) で関節補間モードを無効化、 軌跡は直線を維持
- **進捗ラベル**: ライブ更新で残り本数を可視化
- **bounds clip**: panel size_mm 外の点は自動削除(strokes が物理キャンバスを はみ出さない)

## 依存関係

`wall_drawing_gui_full_dev.py` は以下を `~/piper_test/` から import:

- `canvas_calibration_io` (本番)
- `drag_sampling_thread` (本番)
- `wall_facing_ik` (本番)
- `canvas_to_panel_frame_dev` (dev, 統合)
- `draw_strokes_wall_dev` (dev, `load_strokes_json` のみ使用)

panel UV → robot base 変換は `_do_strokes_draw` 内に **inline 関数 `uv_to_base()`** で実装(modules.robot の PanelFrame に依存させない)。

## 検証

```
$ /home/jizaiedev2026/draw_piper/venv/bin/python -c "from wall_drawing_gui_full_dev import WallDrawingGUI; ..."
syntax ok
full_dev smoke ok:
  btn_panel_convert, btn_strokes_draw, btn_strokes_abort, lbl_strokes_progress 全部存在
  var_strokes_live default: False (安全)
  var_strokes_max default: 0
  var_strokes_interpoint_ms default: 30

既存テスト 6 スイート 186 checks ALL PASS
```

## 想定ワークフロー(明日の実機テストで使う)

```
1. 通常キャリブ (B1+B2+B3、 不安なら B5、 B4 で視覚中心確定)
   ↓
2. 「panel_frame.yaml を更新」ボタンを 1 クリック
   → panel: ブロックが canvas の 4 隅から生成される
   ↓
3. 「ホーム/撮影位置へ」(ready_pose = capture_pose で同一)
   → カメラ撮影位置 (B2 設計)
   ↓
4. (将来) VLM + ImageGen + Vectorizer パイプライン実行
   → cycle_NN/strokes.json が生成
   ↓
5. 「選択...」で strokes.json をピック
   ↓
6. mock チェックのまま「描画開始」 → 全 base xyz を log で確認(範囲妥当性チェック)
   ↓
7. 問題なければ「最大本数 5」「実機で描画」ON で smoke テスト
   ↓
8. 良ければ最大本数 0 (全本数) で本番描画
   ↓
9. ホーム/撮影位置へ復帰 → 次サイクル
```

## 既存 dev ファイルとの関係

| ファイル | 用途 | 統合後 |
|---|---|---|
| `wall_drawing_gui.py` (本番) | M12 までの実装 | 無変更 |
| `wall_drawing_gui_dev.py` | dev 機能の積み上げ | 無変更(historical reference) |
| `wall_drawing_gui_full_dev.py` (**新規**) | dev 全機能 + Section 5 統合 | **明日のテスト主役** |
| `canvas_to_panel_frame_dev.py` | コンバータ CLI | full_dev から import で使用 |
| `draw_strokes_wall_dev.py` | drawer CLI (Robot wrapper 使用) | full_dev は load_strokes_json のみ拝借 |

## 本番マージ判断(後日)

- full_dev で実機テストが通ったら、 `wall_drawing_gui.py` (本番) を `wall_drawing_gui_full_dev.py` で置換(`cp` 1 発)
- もしくは Section 5 だけ抜き出して本番に append
- dev/full_dev は historical reference として残すか削除

## 試験用 strokes.json サンプル

`~/draw_piper/logs/vlm_to_image_20260523_182531/cycle_01/strokes.json` (80 本 1415 点) が手元にある。 これで mock smoke 可能。

## 次セッション以降の TODO

- [ ] 明日: `wall_drawing_gui_full_dev.py` を起動して実機テスト
  - 位置決め関節補間モード (Edit J) が動くか
  - 関節限界張付の警告ダイアログが出るか
  - panel_frame.yaml 変換ボタンが動くか
  - mock モードで strokes.json を読んで base 座標が範囲内か
  - 余裕があれば実機 5 ストロークだけ描画
- [ ] テスト結果次第で 本番マージ
- [ ] (Step 9) E2E 検証
- [ ] chat 側 Task A (Robot.goto_ready_pose を panel_frame.yaml ベースに)
