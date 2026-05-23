# 2026-05-23 20:30 — Phase A キャリブ実装 + 90 度回転対応

## 概要

前セッション (M10 drag-teach 完了、`221f0fb`) を引き継ぎ、Phase A
(カメラ画像 4 点クリックキャリブ) の実装を進めた。途中でユーザから
「90 度回転が考慮されていない」という指摘が入り、capture 直後に画像を
回転して以降のパイプライン全部を「正対向き画像」前提に統一する設計に
切り替えた。GUI ベースで都度回転を選べる方式。

並行して Claude Code 側に状況確認を投げ、`canvas_calibration.yaml` の
取扱と Phase A/B の責務分割について合意を取った。

## 合意整理 (Claude Code 側との議論結果)

| 項目 | 結論 | 担当 |
|------|------|------|
| Q-R1: panel_crop.py rotation | (a) `__init__` で yaml の rotation を読み、`warp()` 入口で適用 | chat 側 |
| Q-R2: test_vlm_to_image.py 改修 | Q-R1 完了後 | chat 側 |
| Q-R3: panel UV 規約 | (A) ホワイトボード座標 (= 230×300 mm) | Claude Code 側 (converter + GUI 拡張) |
| canvas_calibration.yaml | drag-teach raw 証跡として温存 | 触らない |
| panel_frame.yaml の `panel:` block | 新規 converter `canvas_to_panel_frame.py` で作成 | Claude Code 側 |
| panel_frame.yaml の `phase_a_calibration:` block | `calibrate_panel.py` で作成 | chat 側 (完了) |
| ホワイトボード現物サイズ | `phase_a_calibration.panel_size_mm` に記録 → converter で `panel:` block へ転写 | chat 側 → Claude Code 側 |

要点: **`panel_frame.yaml` を panel の単一正本** とし、`panel:` と
`phase_a_calibration:` の 2 ブロック並走で相互不干渉。`canvas_calibration.yaml`
は drag-teach 31 点 + plane fit の raw 証跡として残す。

## 実装

### 1. `modules/camera.py` (rotation_deg 対応)

| 追加項目 | 内容 |
|---------|------|
| `Camera.__init__(rotation_deg=0)` | {0, 90, 180, 270} のみ受け付ける |
| `_ROTATION_CODE` 定数 | deg → cv2.rotate コード |
| `Camera._read_one()` 内で `cv2.rotate()` 適用 | `capture_single/burst/median` 全部に透過的に効く |
| `Camera.set_rotation(deg)` / `get_rotation()` / `get_rotated_size()` | 動的変更可、キャリブ GUI で利用 |
| `VALID_ROTATION_DEGS` export | calibrate_panel.py が import |
| 後方互換 | 既存呼び出し `Camera(device_id=0)` は rotation_deg=0 で従来通り動く |

### 2. `scripts/calibrate_panel.py` (4 ステージ化)

| Stage | 名前 | 主なキー |
|-------|------|---------|
| 1 | capture (median) | (自動) |
| 2 | rotation preview **(新規)** | `r=rotate` / `Enter/Space=accept` / `q=quit` |
| 3 | 4-corner click | 左クリック / `u=undo` / `d=reset all` / `Enter/s=next` / `q=quit` |
| 4 | warp preview | `s=save` / `d=redo click` / `r=redo rotation` / `q=quit` |

以前は Stage 3 で `r=reset all`、Stage 4 で `r=redo` だったが、Stage 2
で `r=rotate` を新設したのに伴い `d=` 系に振り直した。

CORNER_DESC は "TOP-LEFT of WHITEBOARD" 等の文言に変更 (UI 明確化、
90 度回転議論の名残)。

`--initial-rotation-deg` 引数を追加 (Stage 2 の初期値ヒント、{0, 90, 180, 270})。
panel サイズは引数指定のみ (GUI 上は表示のみ、案 B 採用)。

### 3. `modules/panel_crop.py` (Q-R1 (a) rotation 自動判定)

| 追加項目 | 内容 |
|---------|------|
| `_ROTATION_CODE` 定数 / `_apply_rotation_deg()` ヘルパ | 共通の回転 helper |
| `PanelCropper.camera_rotation_deg` | yaml から読む、デフォルト 0 (後方互換) |
| `PanelCropper.camera_image_size_pre_rotation` | 回転前サイズ推定 |
| `PanelCropper._normalize_input_image()` | shape ベースで生画像/回転済みを判定し、必要なら内部回転 |
| `warp(image_bgr)` 入口で `_normalize_input_image()` 呼出 | 呼び出し側は `Camera(rotation_deg=0)` で生画像を渡せばよい |

**動作確認 (単体テスト、`/home/claude/new/panel_crop.py` で実行)**:
- 生画像 (720×1280) と回転済み画像 (1280×720) を `warp()` に渡すと結果が完全一致 (diff max=0)
- 4 隅の色が panel UV の正しい位置に warp される (TL=red, TR=yellow, BR=green, BL=blue)
- `camera_rotation_deg` フィールドが yaml に無い場合は 0 として動作 (後方互換)
- 4 点 exact solution で reproj_err = 0.00

### 4. `calibration/panel_frame.yaml` schema 拡張

新フィールド `camera_rotation_deg` を `phase_a_calibration:` に追加。
`camera_image_size` は**回転後**サイズで記録 (correspondences の `camera_px`
座標と一致させる)。

## ドライラン結果

`logs/vlm_to_image_20260523_182531/cycle_01/captured.png` (前セッションの
median 画像、1280×720) を `--input-image` に渡して全 Stage を通過。

保存された yaml:
```yaml
phase_a_calibration:
  calibrated: true
  calibrated_at: '2026-05-23T20:47:54+09:00'
  camera_rotation_deg: 0
  camera_image_size: [1280, 720]
  panel_image_size: [1024, 1024]
  panel_size_mm: [230.0, 300.0]
  correspondences:
    - camera_px: [544.0, 176.0], panel_mm: [0.0, 300.0],   label: tl
    - camera_px: [854.0, 179.0], panel_mm: [230.0, 300.0], label: tr
    - camera_px: [899.0, 572.0], panel_mm: [230.0, 0.0],   label: br
    - camera_px: [544.0, 665.0], panel_mm: [0.0, 0.0],     label: bl
  reproj_error_px: { mean: 2.5e-13, max: 3.3e-13 }
  method: manual_4pt_click
```

`panel:` block (placeholder) はそのまま残っており、相互不干渉が確認できた。

ホワイトボード 4 隅は画像中央〜右下の約 310×489 px 領域に集中していて
小さい。実機キャリブで縦長カメラに切り替えると全画素の大部分を使えるはず。

## 残課題

### 直近 (chat 側)

1. **実機キャリブ実走** (ユーザ作業)
   - カメラを物理的に縦長設置 (Logitech 想定) → ホワイトボードが画像全域近くに写る
   - Stage 2 で必要に応じて `r` で回転調整、Stage 3/4 を経て yaml 保存
   - 保存された `phase_a_calibration:` の `camera_rotation_deg` / `camera_image_size`
     / `correspondences` の整合確認

2. **Q-R2: `test_vlm_to_image.py` 改修**
   - STAGE 0 で `PanelCropper("calibration/panel_frame.yaml")` 初期化
   - `Camera(rotation_deg=0)` で生画像 capture (`PanelCropper` が内部で揃える)
   - `cropped = panel_cropper.warp(raw)` で 1024×1024 を取得
   - `captured.png` (raw) と `cropped.png` (panel UV) の両方を保存
   - 以降のステージは `cropped` を入力
   - **注意**: 今 `scripts/test_vlm_to_image.py` は HEAD から +153 行で modified
     状態。Claude Code 側か別のセッションで触られている可能性あり。改修前に
     `git diff scripts/test_vlm_to_image.py` で現状を把握すること。

3. **`vectorizer.py` の `vectorize_to_panel()` 連携**
   - 現状 `panel.size_mm` で暫定マッピング
   - Phase A 済みなら `PanelCropper.panel_uv_to_panel_mm()` を使う形に切替
   - これで「画像 px → panel mm」が本物のキャリブを通る

### Claude Code 側に依頼 (Q-R3 関連)

- `wall_drawing_gui.py` に「Record Whiteboard BL/TR」モード追加
- `scripts/canvas_to_panel_frame.py` converter 実装 (canvas_calibration + phase_a_calibration → panel: block)
- `PanelFrame.in_bounds()` に reach 制限チェック追加 ((i) bounding box 採用)

## 配置・コミット予定

未コミットの変更 (`git diff --stat`):

```
calibration/canvas_calibration.yaml | 348 ++------     (Claude Code 側 M10 31点版、別途コミット予定)
calibration/panel_frame.yaml        | 114 ++-          (今日のドライラン結果)
modules/camera.py                   | 471 ++++++++++-  (今日の rotation 対応)
scripts/test_vlm_to_image.py        | 153 +++-         (要確認、誰の変更?)
```

新規 untracked:

```
modules/panel_crop.py        (Phase A の chat 側実装、今日 rotation 対応に拡張)
scripts/calibrate_panel.py   (Phase A の chat 側実装、今日 4 ステージ化)
```

コミット粒度の提案 (次セッション冒頭):

1. `feat: M11 phase A calibration with rotation handling` で
   - `modules/camera.py` (rotation_deg)
   - `modules/panel_crop.py` (新規)
   - `scripts/calibrate_panel.py` (新規)
   - `calibration/panel_frame.yaml` (ドライラン結果)
   - `MILESTONES.md` に M11 追記
   - `docs/20260523_2030_phase_a_calibration_rotation.md` (本ドキュメント)
2. `scripts/test_vlm_to_image.py` の現状確認は別コミット (Q-R2 着手時)
3. `calibration/canvas_calibration.yaml` は Claude Code 側に任せる

## 次セッション冒頭のチェックリスト

- [ ] `git log --oneline -5` で M10 (`221f0fb`) と `8783760` (M10 ハッシュ記入) 以降にコミットが増えていないか
- [ ] `git status` で `scripts/test_vlm_to_image.py` の修正源 (Claude Code or 別?) を特定
- [ ] `cat MILESTONES.md` の現在地マーカが M10 か M11 か
- [ ] 実機キャリブが既に走っていれば yaml の中身を確認
- [ ] Claude Code 側で Q-R3 の進捗があれば取り込む
