# キャンバスキャリブレーション v3 — 多点平面fit + 中心微調整 + joint_map

> 日時: 2026-05-25 14:47 (JST)
> 関連既存ファイル: `20260523_2100_wall_drawing_gui_consolidation.md`, `20260523_1820_master_mode_drag_teach_calibration.md`, `20260522_2246_wall_drawing_calibration_j5_fault.md`
> ステータス: **設計確定、未実装**。本ドキュメントは Claude Code への実装引き継ぎ。

## 目的と背景

現状 (v2) のキャリブは「B1 四隅 4点 + B2 平面用追加点 ≥4 (任意位置)」の 2-phase drag-teach。M10 の 31 点で <0.5mm 精度が出ているが、以下の課題が残る:

1. **キャンバス中心の決定が曖昧**: 現状は四隅の単純平均。台形歪みがあると幾何的に正しくない。GUI の「Go to Canvas Center」もこの曖昧な中心に向かう。
2. **B2 の追加点が「任意位置」で再現性がない**: ユーザが面のどこをどう触ったかで、平面 fit に投入される点群の分布が毎回変わる。
3. **アームの曲げ方 (関節空間) の情報が捨てられている**: サンプリング時に (Y, Z) しか記録していない。同じ (Y, Z) でも IK 解が複数あり、描画時に warm-start テーブルが欲しい。

v3 ではこれらを解決するため、 **構造化された 4 phase + オプション 1 phase** の構成にする。

## Phase 構成

| # | Phase | 操作 | サンプリング | 目的 |
|---|---|---|---|---|
| **B1** | 四隅ティーチ | TL→TR→BR→BL の4ボタン押下 | 押下時のみ | キャンバスのフチ確定 |
| **B2** | 外周トレース | drag-teach (TL→TR→BR→BL→TL) | 10mm 自動 | 外周の関節挙動・端の可動域確認 |
| **B3** | 対角線トレース | drag-teach (TL→BR、TR→BL) | 10mm 自動 | 中心の幾何的決定 + 中央付近の平面/関節データ |
| **B4** | 中心微調整 | アームが B3 交点へ移動 → ユーザが nudge して確定 | 確定時のみ | 中心の最終確定 (描画時に使う採用値) |
| **B5** | 面なぞり (オプション) | drag-teach でジグザグ | 10mm 自動 | 中間領域の joint_map 充実 |

**注意**: 順序は B1 → B2 → B3 → B4 → B5 で固定。 B4 (中心微調整) が B3 (対角線トレース) の直後に来るのは、B3 で計算した中心をすぐに確認・補正できるようにするため。 B5 はオプションで、 B4 完了後の品質チェックで不満があれば追加実行する。

### Phase 間の依存関係

- B2 は B1 完了後 (四隅が確定していないと外周をなぞる起点・終点が決まらない)
- B3 は B1 完了後 (四隅が対角線の起点・終点)
- B4 は B3 完了後 (B3 の対角線 fit 交点に移動するため)
- B5 は B1〜B4 完了後を推奨 (面のジグザグ起点を中心に取れる)
- plane_fit と joint_map の計算は最後にまとめて実行

## GUI 変更点 (`wall_drawing_gui.py`)

現状の `2. Drag-Teach (2-phase)` セクションを、以下の構成に書き換える。

```
═══════ 2. Calibration (5-phase) ═══════

[B1] Four corners:
  [TL]  [TR]  [BR]  [BL]              status: 4/4 ✓

[B2] Perimeter trace:
  Sampling interval: [10] mm
  [Start] [Stop]                       points: 87

[B3] Diagonal trace:
  Sampling interval: [10] mm
  [TL → BR]  [TR → BL]                points: 42 + 38
  Computed center: (y=145.2, z=295.7)

[B4] Center adjustment:
  [Go to computed center]
  Y nudge: [-1.0] [-0.5] [+0.5] [+1.0]
  Z nudge: [-1.0] [-0.5] [+0.5] [+1.0]
  Current: (y=145.2, z=295.7)
  [Confirm as center]                  status: confirmed ✓

[B5] Surface trace (optional):
  Sampling interval: [10] mm
  [Start] [Stop]                       points: 134

═══════════════════════════════════════
[Run plane fit & save]
  rms residual: 0.4 mm
  max residual: 1.2 mm (perimeter[55])
  status: saved to canvas_calibration.yaml
```

### B1 (既存維持)

現状の "B1: Capture corners" 4 ボタンをそのまま使う。 yaml への書き込みキーは `whiteboard_corners_mm` で v2 と同じ。

### B2 / B3 / B5 (新規: 自動サンプリング)

実装方針:

- **別スレッドで poll**: `threading.Thread` で `GetArmJointMsgs()` + `GetArmEndPoseMsgs()` を 100ms 間隔で読む。
- **距離閾値**: 前回保存点から **キャンバス UV 平面上 (Y, Z)** で `>= sampling_interval_mm` 動いたら追加。停止中は記録しない (前回保存点と同位置のままなら追加されない、自然に動作)。
- **デフォルト閾値**: 10mm (B2/B3/B5 で共通の Spinbox 1つを置く案 vs Phase 別に持つ案。初期実装は共通 1つで OK)。
- **Master mode 制御**: `Start` で Master mode 有効化 + サンプリングスレッド開始、`Stop` で Master mode 解除 + スレッド停止。
- **ライブ表示**: 現在点数を 100〜500ms ごとに GUI ラベル更新。
- **B3 は 2 段階**: `TL → BR` を押すと TL から BR への drag-teach 開始、Stop で停止。続けて `TR → BL` を押すと別のサンプル配列に貯める。

### B4 (新規: 中心微調整)

実装方針:

- **`Go to computed center` ボタン**: B3 の対角線 fit 交点 (`center_calc_mm`) にアームを移動 (ペンダウン)。既存の "Go to Canvas Center" と同じ仕組みでよい。
- **Y/Z nudge**: 既存の "Tune Contact" セクションの X nudge と同じ Spinbox + ボタン群を Y と Z に対して用意。1回押すごとに ±0.5mm or ±1.0mm 動く。
- **`Confirm as center` ボタン**: 現在の end_pose の (Y, Z) を `center_mm` として yaml に確定保存。

### B5 (新規: 面なぞり、オプション)

B2/B3 と同じサンプリング機構を再利用。GUI 上は「(optional)」と明示し、 B4 確定後にユーザ判断で実行。

## yaml schema v3

```yaml
schema_version: 3
created_at: "2026-05-25T14:47:38+09:00"

# B1: 四隅 (既存と互換)
whiteboard_corners_mm:
  tl: {y: ..., z: ...}
  tr: {y: ..., z: ...}
  br: {y: ..., z: ...}
  bl: {y: ..., z: ...}

# B2/B3/B5: トレース生データ
traces:
  perimeter:
    - pen_yz_mm: [y, z]
      joints_deg: [j1, j2, j3, j4, j5, j6]
      end_pose_mm_deg: [x, y, z, rx, ry, rz]
      timestamp: "2026-05-25T14:48:12.345"
    - ...
  diagonal_tl_br:
    - {pen_yz_mm: [...], joints_deg: [...], end_pose_mm_deg: [...], timestamp: "..."}
    - ...
  diagonal_tr_bl:
    - {...}
  surface:
    - {...}             # B5 未実行なら空配列 []

# B3 計算 + B4 ユーザ確定の中心情報
whiteboard_computed:
  center_calc_mm: [y, z]              # B3 対角線 fit 交点 (自動計算)
  center_corner_avg_mm: [y, z]        # 四隅平均 (参考)
  center_mm: [y, z]                   # B4 でユーザ確定 (実描画ではこれを使う)
  center_adjustment_mm: [dy, dz]      # B4 で入れた補正量 (center_mm - center_calc_mm)
  width_mm: ...                       # |TR - TL| と |BR - BL| の平均
  height_mm: ...                      # |TL - BL| と |TR - BR| の平均

# 全点 (B1 + B2 + B3 + B5) で最小二乗 fit
plane_fit:
  normal: [a, b, c]                   # 単位ベクトル
  centroid_mm: [x, y, z]
  rms_residual_mm: 0.x
  max_residual_mm: 0.x
  worst_point_source: "perimeter[42]" # どの点が一番外れたか
  n_points: 187
  source_breakdown:                   # 各 phase の貢献点数
    corners: 4
    perimeter: 87
    diagonal_tl_br: 42
    diagonal_tr_bl: 38
    surface: 134                      # B5 未実行なら 0
    center: 1                         # B4 で確定した center も平面 fit に投入

# joint_map: 描画時 warm-start 用 + 可動域チェック
joint_map:
  reachable_polygon_yz: [[y, z], ...] # B2 外周トレース点群の凸包 (or alphashape)
  flagged_regions:
    - center_yz: [y, z]
      reason: "j5 near limit"
      source: "perimeter[55]"
    - center_yz: [y, z]
      reason: "joint jump (IK branch switch)"
      source: "diagonal_tl_br[18]→[19]"
```

### 後方互換 (v2 yaml の読み込み)

`schema_version` キーの有無で判定:

- 存在しない / `<3` → v2 として読む (既存ロジック)
- `3` → v3 として読む (新ロジック)

新規キャリブは常に v3 で書き出す。

## 平面 fit の実装

### 入力点

`whiteboard_corners_mm` の4点 + `traces.perimeter` + `traces.diagonal_tl_br` + `traces.diagonal_tr_bl` + `traces.surface` + `whiteboard_computed.center_mm` の (X, Y, Z) を全て投入 (重みなし)。各点の X は `end_pose_mm_deg[0]` から取る。

### アルゴリズム

`scipy.optimize.least_squares` または `numpy.linalg.lstsq` で `ax + by + cz = d` を解く。

```python
import numpy as np
points = np.array([[x1,y1,z1], [x2,y2,z2], ...])  # (N, 3)
centroid = points.mean(axis=0)
centered = points - centroid
# SVD で平面の法線を求める
_, _, vh = np.linalg.svd(centered)
normal = vh[-1]                                    # 最小特異値に対応する右特異ベクトル
# 残差
residuals = np.abs(centered @ normal)
rms = np.sqrt((residuals**2).mean())
max_res = residuals.max()
worst_idx = int(residuals.argmax())
```

### 警告ロジック

- `rms_residual_mm > 1.0` → 「平面 fit の残差が大きい。板が反っている可能性」
- `max_residual_mm > 3.0` → 「外れ値あり: `worst_point_source` を確認してティーチし直し推奨」
- B4 で `|center_adjustment_mm| > 5mm` → 「対角線 fit からの中心ズレが大きい。四隅ティーチを見直すべき」

## 中心の決定ロジック (B3)

1. `traces.diagonal_tl_br` の点群の (Y, Z) を最小二乗で直線 fit (`y = m1*z + c1` 形式 or パラメトリック)
2. `traces.diagonal_tr_bl` の点群の (Y, Z) を最小二乗で直線 fit
3. 2直線の交点を解析的に計算 → `center_calc_mm`
4. `center_corner_avg_mm` も計算 (四隅平均): `(TL+TR+BR+BL)/4`
5. B4 でユーザが nudge した最終値を `center_mm` として保存

直線 fit の入力点数は片側 30〜50 点を想定。最小二乗で十分ロバスト。

## joint_map の解析

### `reachable_polygon_yz`

`traces.perimeter` の (Y, Z) 点群の凸包を `scipy.spatial.ConvexHull` で計算。これが「実際に描けると確認された YZ 領域」となる。描画時、この多角形の内側にある描画ストロークだけを許可するチェックに使える。

### `flagged_regions`

サンプル全点 (B1〜B5 全て) について以下をチェック:

- `abs(j5_deg) > 65` → `"j5 near limit"` (可動域 ±70°)
- `j3_deg < -160` → `"j3 near limit"` (可動域 -170°)
- 連続するサンプル点間 (B2/B3/B5 内) で `max(abs(diff(joints_deg))) > 5°` → `"joint jump (IK branch switch)"`

これらは描画時の警告材料。描画スクリプトが flagged_regions 内の点を通る前に「ここで関節飛びの可能性あり」とログ出力できる。

## 実装順序の推奨

実装はリスクの低い順に積む。各ステップごとに動作確認してから次へ。

1. **yaml schema v3 のリーダー/ライターを先に作る** (`canvas_calibration_io.py` 新規)
   - v2 yaml の読み込み (後方互換)
   - v3 yaml の読み書き
   - 単体テスト (mock データで往復)

2. **B1 の出力を v3 形式に切り替え** (既存ロジックの吐き出し先変更だけ)
   - GUI 動作は変えない、yaml 形式だけ v3 化

3. **自動サンプリングスレッドの実装** (`drag_sampling_thread.py` 新規)
   - Master mode 中に poll、距離閾値判定、停止フラグ
   - 単体テストはモックで OK、実機検証は次ステップで

4. **B2 (外周トレース) を GUI に追加**
   - サンプリングスレッド + Start/Stop ボタン
   - 実機で 1 周トレース、点数とサンプル分布を目視確認

5. **B3 (対角線トレース) を GUI に追加**
   - B2 と同じ機構、サンプル配列を 2 つ持つだけ
   - 対角線 fit 交点の計算実装

6. **B4 (中心微調整) を GUI に追加**
   - Go to computed center ボタン
   - Y/Z nudge (既存の X nudge を流用)
   - Confirm as center で yaml に書き込み

7. **plane_fit と joint_map の計算実装** (`plane_fit.py`, `joint_map.py` 新規)
   - SVD で法線、残差計算
   - 凸包、flagged_regions 判定
   - 「Run plane fit & save」ボタンから呼ぶ

8. **B5 (面なぞりオプション) を GUI に追加**
   - B2 と同じ機構の再利用
   - plane_fit と joint_map の再計算

9. **エンドツーエンド検証**
   - 全 phase を実行してキャリブし、 既存の描画スクリプトで 30mm 正方形を描く
   - 既存の <0.5mm 精度が出ることを確認 (後退してないか)

## Claude Code への引き継ぎ事項

### このファイルを読んだら最初にやること

1. 本ファイルと、関連既存ファイル (`20260523_2100_wall_drawing_gui_consolidation.md`, `20260523_1820_master_mode_drag_teach_calibration.md`) の3つを読む
2. ユーザに「読み込みました、現状こうですね」と1段落で要約して確認
3. **すぐに実装に入らない**。「実装順序の推奨」のどこから着手するか、別の順序を選ぶか、ユーザと相談

### 実機を動かす前の必須確認

- CAN0 が up しているか (GUI の Status bar で確認)
- ready pose v2 にいるか (`recover_to_ready.py` で復帰可能)
- 既存 GUI (`wall_drawing_gui.py`) が起動し既存の B1 が動作することを確認 (リグレッション防止)
- 自動サンプリング機構を初実装するときは、まず Master mode に入らない dry-run で動作確認 (関節 poll だけ動かして点を貯める)

### 安全マージンに関する強い要請

- Master mode の自動サンプリング中、ユーザがアームを **可動域端近くまで動かす可能性** がある。j5/j3 の角度をスレッド内でも監視し、限界 -5° に達したら警告ログを出す (停止までは不要、ユーザに気付かせる)。
- B4 の Y/Z nudge は **+1.0mm ステップが上限**。 これ以上のステップは drag-teach でやり直すべき。
- B5 (面なぞり) は描画範囲を逸脱しがち。 `reachable_polygon_yz` 外に出たら警告ログ。

### コード配置の規約

- GUI 本体: `~/piper_test/wall_drawing_gui.py` (既存ファイル改修)
- 新規モジュール: `~/piper_test/` 直下に `canvas_calibration_io.py`, `drag_sampling_thread.py`, `plane_fit.py`, `joint_map.py`
- yaml 出力先: `~/draw_piper/calibration/canvas_calibration.yaml` (既存パス維持)

### 進捗ログ

区切りごとに `project_instructions.md` の規約に従い `YYYYMMDD_HHMM_テーマ.md` を新規生成 (既存ファイル書き換えではない)。

## 既知の残課題 (このセッションの範囲外)

- 🔲 描画スクリプト側の対応 (`draw_square_wall.py` 等が v3 yaml を読めるよう改修、`center_mm` を使う、warm-start に joint_map を使う)
- 🔲 `panel_frame.yaml` の `panel:` ブロック生成 converter の v3 対応
- 🔲 Phase A (camera_px ↔ panel_mm) との整合 (chat 側 `calibrate_panel.py` の更新が必要かは別途検討)
- 🔲 重み付き最小二乗 fit の検討 (現状は重みなしで実装、四隅を重く扱う必要が出たら追加)
