# panel_crop.py rotation 対応 (Q-R1 (a)) — 配置と動作確認

## 配置

```bash
cp ~/Downloads/panel_crop.py ~/draw_piper/modules/panel_crop.py
cd ~/draw_piper
git diff modules/panel_crop.py | head -100
```

## 主な変更点

### 新規追加

- `_ROTATION_CODE` 定数 (deg → cv2.rotate コード)
- `_apply_rotation_deg()` ヘルパ
- `PanelCropper.camera_rotation_deg` 属性 (yaml から読む、デフォルト 0)
- `PanelCropper.camera_image_size_pre_rotation` 属性 (回転前サイズ推定)
- `PanelCropper._normalize_input_image()` メソッド (shape ベースで回転適用判定)

### warp() の挙動変更

`warp(image_bgr)` の入り口で `_normalize_input_image()` を呼び、入力画像が:

- **回転済み (shape == camera_image_size)** → そのまま warp
- **生画像 (shape == camera_image_size_pre_rotation)** → 内部で cv2.rotate 適用してから warp
- **どちらでもない** → 警告 + best effort で rotate 適用してから warp

`camera_rotation_deg = 0` のときは内部回転がノーオペになるので、従来通り。

### 後方互換

`camera_rotation_deg` フィールドの無い古い yaml は `camera_rotation_deg=0` として読み込まれ、既存挙動を維持。

## 単体動作確認 (済)

`/home/claude/new/panel_crop.py` を直接動かして以下を確認済み:

- 生画像 (720×1280) と回転済み画像 (1280×720) を `warp()` に渡すと結果が完全一致 (diff max=0)
- 4 隅の色が panel UV の正しい位置に warp される
- `camera_rotation_deg` フィールドが yaml に無い場合は 0 として動作 (後方互換)
- 4 点 exact solution で reproj_err = 0

## 実機での動作確認手順

### 1. Phase A キャリブ実行 (まだなら)

```bash
cd ~/draw_piper
python scripts/calibrate_panel.py --panel-width-mm 230 --panel-height-mm 300
```

これで `calibration/panel_frame.yaml` の `phase_a_calibration:` ブロックに
`camera_rotation_deg` フィールドが書かれる。

### 2. yaml 内容確認

```bash
cat calibration/panel_frame.yaml
```

`phase_a_calibration.camera_rotation_deg` フィールドの値を確認。

### 3. panel_crop.py 単体スモークテスト

`panel_frame.yaml` を Phase A で書いた後、以下で warp 動作を確認:

```bash
# キャリブに使った median 画像 (生画像、もし --save-capture オプションを使ったなら)
# が無ければ、新しく capture して試す:
python -m modules.panel_crop \
    --yaml calibration/panel_frame.yaml \
    --image <生 or 回転済みの captured.png> \
    --out /tmp/cropped_test.png
```

期待:

- 生画像 (1280×720 等) を渡しても `cropped_test.png` (1024×1024) に warp される
- ログに `PanelCropper: 4 pts (4 inliers), rotation=<deg> deg, ...` と表示される

### 4. 二重回転がないことの確認

念のため、回転済み画像 (cv2.rotate 適用済み) を渡した場合の挙動も確認:

```bash
python3 -c "
import cv2, sys
sys.path.insert(0, '.')
from modules.panel_crop import PanelCropper

cropper = PanelCropper('calibration/panel_frame.yaml', verbose=True)
img_raw = cv2.imread('<生画像 path>')
rotated = cv2.rotate(img_raw, cv2.ROTATE_90_CLOCKWISE)  # 仮定: yaml の rot=90
warped_a = cropper.warp(img_raw)
warped_b = cropper.warp(rotated)
diff = (warped_a.astype(int) - warped_b.astype(int)).__abs__().max()
print(f'diff: {diff}')  # 0 が期待値
"
```

## 次のタスク (Q-R2): test_vlm_to_image.py の改修

panel_crop.py の動作が確認できたら、test_vlm_to_image.py の STAGE 0 を以下に改修する:

```python
# STAGE 0: Camera median capture + panel crop
# yaml から phase_a_calibration を読み、PanelCropper を初期化
panel_cropper = PanelCropper("calibration/panel_frame.yaml", verbose=True)

# Camera は rotation_deg=0 で生画像を取る (PanelCropper が内部で揃える)
camera = Camera(
    device_id=args.camera_device,
    width=args.camera_width,
    height=args.camera_height,
    rotation_deg=0,
    verbose=True,
)
camera.open()
try:
    raw = camera.capture_median(...)
finally:
    camera.close()

# captured.png (raw) と cropped.png (panel UV 1024x1024) の両方を保存
cv2.imwrite(cycle_dir / "captured.png", raw)
cropped = panel_cropper.warp(raw)
cv2.imwrite(cycle_dir / "cropped.png", cropped)

# 以降の VLM/SDXL/Vectorizer は cropped を入力にする
sketch_path_for_cycle = cycle_dir / "cropped.png"
```

これで:

- Phase A キャリブが効いて、ホワイトボード正対向きの 1024×1024 が後段に流れる
- カメラ 90 度回転も自動的に補正される
- 中間ファイル `cropped.png` が増えて、cycle ごとに warp 結果を確認できる
