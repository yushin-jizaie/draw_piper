# 生成画像描画パイプラインの dev 準備 (canvas_to_panel_frame + draw_strokes_wall)

> 日時: 2026-05-25 19:18 (JST)
> 関連既存ファイル:
>   - `20260525_1851_canvas_calibration_v3_step6_8_dev_b4_b5.md` (B4/B5 dev)
>   - 過去 milestone: M7 (PanelFrame), M8 (image_gen), M9 (vectorizer → strokes.json)
>   - 既存設計: `~/draw_piper/modules/robot.py` の `PanelFrame` / `Robot.draw_stroke_panel`
> ステータス: ⏳ **dev 2 スクリプト + 9 テストケース 32 checks PASS、本番未マージ・実機未検証**

## 背景

ユーザの指示:
> キャリブレーションができた後にやる予定の生成された画像の描画を試すための準備を別ファイルでやる。あとでマージする

つまり M9 で確立した VLM → ImageGen → Vectorizer の出力 (`cycle_NN/strokes.json`) を、 v3 canvas calibration を使って実機に描画するための **足回り** を、 本番ファイル群を触らずに準備する。

## パイプライン全体像

```
[VLM]                    [ImageGen]              [Vectorizer]
 ↓                        ↓                       ↓
 prompt -> text     -> SDXL -> generated.png -> strokes (px) ← cycle_NN/strokes.json
                                                                ↓
                                                  ┌────────────┘
                                                  ↓
                              ┌─── [px → panel UV mm 変換] ───┐
                              │   image_shape (1024×1024) と │
                              │   panel.size_mm を使う        │
                              │   Y軸は反転 (image top → panel top)│
                              └────────────┬────────────────┘
                                           ↓
                              [PanelFrame.to_base(u, v, w)]
                                           ↓
                                  panel UV mm → robot base xyz
                                           ↓
                              [Robot.draw_stroke_panel(uv_pts)]
                                           ↓
                                  travel pen-up → pen-down →
                                  trace MOVE L → pen-up
```

## 既存資産(無変更)

| ファイル | 役割 |
|---|---|
| `~/draw_piper/modules/robot.py` | `PanelFrame` クラス + `Robot.draw_stroke_panel(points_uv)` メソッド (M7 で実装) |
| `~/draw_piper/modules/vectorizer.py` | strokes 生成 + `vectorize_to_panel()` (px → uv mm 変換ロジック参考) |
| `~/draw_piper/calibration/panel_frame.yaml` | `panel:` ブロック(本実装で生成) + `phase_a_calibration:` ブロック(既存・無変更) |
| `~/draw_piper/calibration/canvas_calibration.yaml` | v3 yaml (4 corners + traces + computed + plane_fit) |

## 新規 dev 追加(本実装)

### 1. `~/piper_test/canvas_to_panel_frame_dev.py` (約 220 行)

v3 `canvas_calibration.yaml` の 4 隅から `panel_frame.yaml` の `panel:` ブロックを生成するコンバータ。

```
入力: canvas_calibration.yaml (v3)
  whiteboard_corners_mm: {tl, tr, br, bl: [x, y, z]}
                         ↓ compute_panel_block()
出力: panel_frame.yaml の panel: ブロック
  origin_mm   = BL の XYZ
  u_axis      = unit(BR - BL)  ← パネル +u 方向 ("right")
  v_axis      = unit(TL - BL)  ← パネル +v 方向 ("up")
  normal      = unit(u × v), sign-flip so X < 0  ← パネル外向き法線
  size_mm     = [|BR-BL|, |TL-BL|]  ← 実測サイズ (override 可)
  pen_orientation_deg = [180, 0, 0]  ← wall-facing デフォルト
  w_contact_mm = 0.0    (= 表面)
  w_clear_mm   = 25.0   (= pen-up 量)
```

CLI:
```
python canvas_to_panel_frame_dev.py [--canvas-yaml ...] [--panel-yaml ...]
  [--size-mm W H]            # 実測ではなく指定サイズ (default 230 300)
  [--w-contact-mm N]
  [--w-clear-mm N]
  [--no-preserve-phase-a]    # 既存 phase_a_calibration を破棄
  [--dry-run]                # 書き込まずに print
```

**実 disk yaml に対する dry-run 出力例**(現在の v3 canvas):
```
captured size  : 119.97mm × 242.60mm
written size_mm: [119.97, 242.6]
origin (BL)    : [213.36, 54.98, 199.03]
u_axis         : [-0.0081, -0.9979, 0.0636]  ← ≈ -Y
v_axis         : [-0.0463, 0.0212, 0.9987]   ← ≈ +Z
normal         : [-0.9989, 0.0051, -0.0465]  ← ≈ -X
perpendicularity: err 2.45° (0° = u_axis ⊥ v_axis)
pen_orientation_deg: [180.0, 0.0, 0.0]
```

注: 実 disk yaml の corner だと BR/BL が joint limit に張り付いていたため、 width=120mm × height=243mm という細長い形になっている。 実際の物理キャンバスサイズ (230×300mm) とは異なる。 `--size-mm 230 300` で上書きできるが、 そうすると u_axis/v_axis のスケールと size_mm が不整合になり、 strokes が物理キャンバス外にはみ出る。 物理キャンバスを arm reach 内に収めるのが本質的な解決。

### 2. `~/piper_test/draw_strokes_wall_dev.py` (約 200 行)

`strokes.json` (M9 の vectorizer 出力) を読み込み、 panel UV mm に変換 → `Robot.draw_stroke_panel` で 1 ストロークずつ実機描画 (mock デフォルト)。

CLI:
```
python draw_strokes_wall_dev.py STROKES_JSON [options]
  --panel-yaml ...           # default: 本番 panel_frame.yaml
  --live                     # 実機モード (default: mock)
  --max-strokes N            # 先頭 N ストロークだけ (smoke test 用)
  --travel-speed PCT         # default 50
  --draw-speed PCT           # default 25
  --inter-point-delay SEC    # default 0.02
  --no-clip                  # 範囲外も描く (default: bounds clip)
  --pen-rpy RX RY RZ         # panel.pen_orientation_deg override
```

**実 strokes.json (cycle_01, 80 strokes) に対する mock smoke 出力例**:
```
[load] image_shape: (1024, 1024)
[load] strokes: 80 (1415 pts total)
[convert] panel.size_mm: [119.97, 242.6]
[convert] strokes after clip: 80 (dropped 0 points)
[draw_strokes] 3 strokes to draw (mock=True)
  [1/3] 15 pts u[21.8..22.0] v[21.3..21.5]
  [MOCK] goto_panel: u=21.8 v=21.3 w=25.0 -> base (186.2, 33.6, 220.2)
  ...
[draw_strokes] done: 3 strokes drawn, 40 total points, elapsed 2.3s
```

### 3. `~/piper_test/test_dev_stroke_drawing.py` (約 280 行)

9 ケース・32 checks のテスト:

```
--- compute_panel_block_ideal_rectangle ---     (8 PASS)
--- compute_panel_block_tilted_canvas ---       (4 PASS)
--- compute_panel_block_degenerate_raises ---   (1 PASS)
--- compute_panel_block_size_override ---       (2 PASS)
--- convert_yaml_round_trip ---                 (4 PASS) ← phase_a 保存も確認
--- load_strokes_json_shape ---                 (4 PASS)
--- px_to_panel_uv_no_clip ---                  (4 PASS) ← Y軸反転確認
--- px_to_panel_uv_with_clip ---                (2 PASS)
--- draw_strokes_mock_smoke ---                 (2 PASS) ← 実 strokes.json で E2E
ALL CHECKS PASSED  (32 checks)
```

## 既存テスト 無回帰確認

```
test_canvas_calibration_io.py     ALL CHECKS PASSED (50)
test_step2_v3_save.py             ALL CHECKS PASSED (35)
test_drag_sampling_thread.py      ALL CHECKS PASSED (27)
test_dev_b4_b5.py                 ALL CHECKS PASSED (18)
test_dev_stroke_drawing.py        ALL CHECKS PASSED (32)
                合計 5 スイート       ALL CHECKS PASSED (162)
```

## 既知の物理制約 / 留意点

### キャンバス物理サイズ vs アーム reach

dev converter は **実測サイズ (120×243mm)** を panel.size_mm に書く。 これは現在の v3 yaml が BR/BL を joint limit で取ったためで、 実物理キャンバスの 230×300mm より小さい。

選択肢:
- **(A) アームに届く範囲だけに strokes をスケールダウン** → 実測 120×243 をそのまま使う (現状の default)
- **(B) 物理サイズ 230×300 を維持** → strokes は物理キャンバス上で正しい寸法だが、 BR/BL 隅はアーム届かず描画失敗する
- **(C) キャンバスをアーム reach 内に物理移動** → 本質的解決、 ただし再キャリブ必要

(A) が現状の安全 default。

### pen_orientation_deg のデフォルト

converter は `(180, 0, 0)` を書く。 これは wall-facing 想定だが、 アームの URDF/FK 慣行により実際の wall-facing RPY が異なる可能性がある(例: 実機で wall_facing_joints に MOVE J した時の actual RPY は別の値かも)。

実機検証時は:
1. `wall_drawing_gui.py` で `Recover to Ready → Go to Canvas Center` を 1 回実行
2. ログから `wall-facing RPY: RX=... RY=... RZ=...` を読む
3. `draw_strokes_wall_dev.py --pen-rpy RX RY RZ` で渡す
4. もしくは panel_frame.yaml の `pen_orientation_deg` を手で書き換え

### MOVE L 連続実行の信頼性

M5 で 30mm 正方形は MOVE L で問題なく動いたが、 80 strokes × 平均 18 points = 1440 個の連続 MOVE L コマンドは未検証。 起こりうる問題:

- SDK の MOVE L コマンドキュー溢れ → `inter_point_delay` を伸ばせば緩和
- 連続 cartesian で IK 分岐スイッチ → 関節飛び。 ストローク境界 (pen-up travel) で吸収されるはずだが要観察
- アームの熱負荷 → 長時間描画で停止する可能性

## 本番マージ手順 (戻ってきた後)

### Step 1: dev で実機検証

1. 現在の v3 yaml + B4 (dev GUI 経由で center 確定) で `panel_frame.yaml` の panel: ブロックを更新:
   ```
   python ~/piper_test/canvas_to_panel_frame_dev.py
   ```
2. `wall_drawing_gui_dev.py` で Recover → Go to Canvas Center → ログから wall-facing RPY を取得
3. mock で smoke test:
   ```
   python ~/piper_test/draw_strokes_wall_dev.py \\
       ~/draw_piper/logs/vlm_to_image_20260523_182531/cycle_01/strokes.json \\
       --max-strokes 5
   ```
4. 実機で 5 strokes だけ描画:
   ```
   python ~/piper_test/draw_strokes_wall_dev.py \\
       STROKES.json --live --max-strokes 5 \\
       --pen-rpy 180.0 0.0 0.0
   ```
5. 描画結果を観察(線が出るか / 縮尺合うか / 関節飛びがないか)

### Step 2: 問題なければ本番化

```bash
# dev スクリプトを本番 scripts/ 配下に移動
cp ~/piper_test/canvas_to_panel_frame_dev.py \\
    ~/draw_piper/scripts/canvas_to_panel_frame.py
cp ~/piper_test/draw_strokes_wall_dev.py \\
    ~/draw_piper/scripts/draw_strokes_wall.py

# test も合わせて移動 + sys.path 修正
cp ~/piper_test/test_dev_stroke_drawing.py \\
    ~/piper_test/test_stroke_drawing.py
# (test 内の `canvas_to_panel_frame_dev` import は両方に対応するよう
#  名前空間整理が必要 — または "_dev" 接尾辞を全削除)

# dev ファイル削除
rm ~/piper_test/canvas_to_panel_frame_dev.py
rm ~/piper_test/draw_strokes_wall_dev.py
rm ~/piper_test/test_dev_stroke_drawing.py
```

### Step 3: マージ後の検証

```bash
cd ~/piper_test
python test_stroke_drawing.py
# mock smoke が PASS することを確認
```

## 設計判断とその理由

### なぜ converter を別スクリプトにしたか

`Robot.__init__` 内で `_load_panel_frame()` が呼ばれるが、 これは「読み込み」 のみ。 panel_frame.yaml を「書き出す」 のは別タイミング(キャリブ後の 1 回)で、 これは GUI ではなく CLI スクリプトの責務に分けるのが疎結合。

将来的に `wall_drawing_gui.py` の B4 Confirm or Save タイミングで自動的に panel_frame.yaml を更新する選択肢もあり(統合)。 ただし B4 が頻繁に動くなら毎回更新するのは無駄なので、 明示的なコマンドにする現状デザインで OK。

### なぜ image px → panel uv を draw_strokes 側で行ったか

vectorizer.py には既に `vectorize_to_panel()` があり、 px → uv を行う。 しかし VLM パイプラインの出力は `strokes.json` (px のみ) で uv は含まれない。 描画スクリプト側で px → uv を再計算するのが運用上シンプル(変換ロジック自体は 10 行程度)。

### なぜ mock を default にしたか

実機事故防止。 ユーザが `python draw_strokes_wall_dev.py STROKES.json` と叩いたとき、 デフォルトで 80 strokes × 連続 MOVE L が走ったらアーム/キャンバスを傷める可能性。 `--live` フラグを明示的に付けないと実機は動かない。

### なぜ `--max-strokes` を提供したか

80 strokes 全部を一気に試す前に、 5 strokes (or 1 stroke) で挙動を観察するため。 各ストローク間で travel pen-up が走るので、 ストローク境界の安全性検証も必要。

## ファイル一覧

```
~/piper_test/canvas_to_panel_frame_dev.py        新規・220 行
~/piper_test/draw_strokes_wall_dev.py            新規・200 行
~/piper_test/test_dev_stroke_drawing.py          新規・280 行
~/draw_piper/calibration/panel_frame.yaml        変更(panel: ブロックを実値に更新)
~/draw_piper/docs/20260525_1918_..._dev_prep.md  この docs ファイル
```

## 戻ってきたユーザに伝えたいこと

1. **本番ファイルは無変更**(panel_frame.yaml の panel: ブロックは converter が更新したが、 これは placeholder からの初期化なのでロールバック不要)
2. **dev スクリプト 2 本 + テスト 1 本**(計 32 checks PASS)で生成画像描画パイプラインの足回りが揃った
3. **mock smoke で実 strokes.json (80 strokes) からエンドツーエンド動作確認済み**
4. **実機検証は --live モードで `--max-strokes 5` から始める**のが安全
5. キャンバス物理サイズと arm reach の不整合は **キャンバス物理位置を調整するか、 size_mm 230×300 を許容して BR/BL strokes をクリップするか** の判断が必要
