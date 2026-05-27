# Frida Smoothness 拡張 引き継ぎ

> ブランチ: `claude/frida-smoothness-20260527` (dev ベース、 全 push 済)
> 最新 HEAD: `6cd000a` (午前作業含む 6 commit)
> 設計: `docs/20260528_0030_frida_smoothness_design.md`
> 親プロジェクト目的: Piper アームでホワイトボードに描く (M9 の先の探索)

---

## TL;DR (5 行)

1. **コア実装完了**: Frida ヒント 3 つ (TSP + 3-region 速度プロファイル + look-ahead descent) を `Robot.draw_strokes_panel_smooth()` に統合
2. **E2E パイプライン完成**: 画像 → Vectorizer → Robot.smooth まで通る (`scripts/test_pipeline_smooth.py`)
3. **視覚化ツール追加**: 実機なしで「ロボットが何を描くか」 確認可 (`--render-out` で TSP 効果も可視化)
4. **ベンチマーク** (純関数 sim): face 1.71x / scattered **2.98x** / zigzag 1.38x speedup (旧 1.45/2.78/1.04)
5. **次の最優先**: 実機検証 (P1)。 P2 (Vectorizer 連携) と P3 (改善 3 つ) は実装済

---

## 何が出来たか

### 新規ファイル
| ファイル | 内容 |
|---|---|
| `modules/stroke_planner.py` | TSP / 曲率 / 3-region 速度マップ / look-ahead clear / 真の弧長 / jerk metric / curvature histogram |
| `modules/stroke_visualizer.py` | strokes_mm → PNG レンダリング (TSP 比較 grid 含む) |
| `scripts/benchmark_stroke_smoothness.py` | 3 strategy の軌道メトリクス + jerk + curvature histogram (mock 不要) |
| `scripts/test_frida_smoothness.py` | mock Robot で `draw_strokes_panel_smooth` を E2E smoke |
| `scripts/test_pipeline_smooth.py` | **画像 → Vectorizer → Robot smooth** 通し E2E + `--render-out` preview |
| `scripts/test_draw_strokes_smooth.py` | **実機テスト用** (face_lite / scattered / zigzag 3 シーン) |
| `scripts/preflight_check.py` | 実機描画前の dry-run チェック (in_bounds / TSP / 推定 timing) |
| `scripts/bench_robot_timing.py` | naive vs arcs vs smooth の **実機 A/B/C timing 比較** |
| `scripts/gui_frida_draw.py` | **Tkinter GUI** (画像/scene → preflight → preview → 描画) 独立小 GUI |
| `_patches/wall_drawing_gui_full_dev_frida_smooth.patch` | **wall_drawing_gui 統合 patch** (~160 行)、 既存 GUI に `✨ Frida Smooth` ボタン追加 |
| `_patches/wall_drawing_gui_full_dev.patched.py` | stroke_picker + frida_smooth 両適用済の完全版 |
| `docs/20260528_0030_frida_smoothness_design.md` | 設計ドキュメント |
| `docs/20260528_frida_smoothness_handoff.md` | 本ファイル |

### 既存ファイル変更
| ファイル | 変更内容 |
|---|---|
| `modules/robot.py` | `Robot.draw_strokes_panel_smooth()` + `PanelFrame.from_base()` 逆変換 |

### 既存ファイル変更
- `modules/robot.py`: `Robot.draw_strokes_panel_smooth()` メソッド追加 (約 200 行)

### 既存実装で活用したもの
- `modules/trajectory.py::smooth_polyline()` — 既に scipy splprep / Catmull-Rom フォールバックで実装済
- `modules/trajectory.py::polyline_to_arc_triplets()` — MOVE_C 三つ組変換、 既存
- `Robot.draw_stroke_panel_arcs()` — 1 stroke の arc 描画、 既存

つまり Frida ヒント:
- ✅ **案 1 (Bezier 化)** — 既存 (trajectory.py + arcs)
- ✅ **案 2 (曲率連動速度)** — 新規 (stroke_planner + robot.py)
- ✅ **案 3 (TSP ordering)** — 新規 (stroke_planner + robot.py)
- ✅ **案 4 (look-ahead descent)** — 新規 (stroke_planner + robot.py)
- ❌ **案 5 (カメラフィードバック)** — 範囲外、 別タスク

---

## 動作確認結果

### benchmark (純関数シミュレータ、 mock 不要)

**2026-05-28 更新 v2**: TSP 2-opt 改善 + jerk clipping 追加:
- scattered_dots: greedy 748mm → 2-opt **650mm** (-13.1% 追加削減)
- face_sketch: greedy 112mm → 2-opt **101mm** (-9.7% 追加削減)
- jerk-clip (max_jerk_per_step=10): per-stroke jerk_max が確実に下がる

**2026-05-28 更新**: 3-region speed mapping (直線加速 max=50%) + 真の弧長計算
を追加 → 全シーンで speedup 向上 (face +18%, scattered +7%, zigzag +33%):

```
=== scene: face_sketch (9 strokes) ===
metric            naive    arcs   smooth
travel_path_mm    169.7    169.7   112.1   (-34%)
speed_mean_pct     25.0     25.0    41.4   (旧 28.1 → 41.4、 直線部で加速)
est_time_s         10.0     10.0     5.8   (1.71x, 旧 1.45x)

=== scene: scattered_dots (100 strokes) ===
metric            naive    arcs   smooth
travel_path_mm   4212.2   4212.2   748.1   (-82%)
speed_mean_pct     25.0     25.0    50.0   (短 stroke 全部直線で max)
est_time_s        87.4     87.4    29.3   (2.98x, 旧 2.78x)

=== scene: zigzag_signature (1 stroke) ===
metric            naive    arcs   smooth
travel_path_mm      0.0      0.0     0.0   (1 stroke → TSP 無効)
speed_mean_pct     25.0     25.0    35.1   (smoothing 後は緩い曲線多い → 加速大)
est_time_s          8.0      7.9     5.8   (1.38x, 旧 1.04x)
```

### mock Robot smoke test

```
$ python3 -m scripts.test_frida_smoothness
=== Frida smoothness smoke test (mock Robot) ===
5 input strokes (raw order: oval, mouth, body, eye_l, eye_r)
draw completed in 2.68s (mock)

reorder_indices        [0, 4, 3, 1, 2]    ← oval → eye_r → eye_l → mouth → body
n_arcs                 37
travel_before_mm       98.05
travel_after_mm        50.01
travel_saved_mm        48.04 (49.0%)
speed_min_pct          29
speed_max_pct          30
clear_heights_mm       [10.0, 10.0, 10.0, 30.0, 30.0]   ← near の所は浅く、 遠は深く
OK — smoke test passed.
```

---

## 翌朝の次手 (優先順)

### P1: 実機検証 (要 Piper アーム + can0 接続)

**事前推奨フロー**:

```bash
# step 0: 任意 scene の preflight check (描画せず安全性検証)
python3 -m scripts.preflight_check --scene face_lite
python3 -m scripts.preflight_check --image scripts/test_sketch.jpg
# 推定 timing と TSP 効果が出る、 "OK — safe to draw" なら次へ

# step 1: 視覚 preview (実機描画 と同じものを画像化、 視認確認)
python3 -m scripts.test_pipeline_smooth \
    --image scripts/test_sketch.jpg --vectorize-only --render-out /tmp/preview.png
xdg-open /tmp/preview.png
```


mock では accel/decel/MOVE_C overhead は反映されない。 実機での「本当に滑らかか」 は要計測:

```bash
cd ~/draw_piper
git fetch origin
git checkout claude/frida-smoothness-20260527
git pull

# CAN bring-up (必要に応じて)
sudo ip link set can0 up type can bitrate 1000000

# 1. ready pose
python3 -m scripts.test_ready_pose move

# 2. 一番小さい face_lite (5 stroke、 ~30秒) で初動確認
#    実機モードは --inspect/--scene で制御 (mock=False 内蔵)
python3 -m scripts.test_draw_strokes_smooth --inspect
python3 -m scripts.test_draw_strokes_smooth --scene face_lite

# 3. 動けば scattered で TSP 効果実感 (~2 分)
python3 -m scripts.test_draw_strokes_smooth --scene scattered
# --no-reorder と切替えて A/B 比較するとさらに分かりやすい
python3 -m scripts.test_draw_strokes_smooth --scene scattered --no-reorder

# 4. (本命) 生成画像を実機で描く
#    user 環境にある logs/imagegen_comparison_*/illustrious_v2_inpaint.png 等を渡す
python3 -m scripts.test_pipeline_smooth \
    --image logs/imagegen_comparison_*/illustrious_v2_inpaint.png \
    --render-out /tmp/preview.png    # 描画前に preview 確認
xdg-open /tmp/preview.png
python3 -m scripts.test_pipeline_smooth \
    --image logs/imagegen_comparison_*/illustrious_v2_inpaint.png \
    --real

# 5. (定量比較) A/B/C ベンチマーク — naive vs arcs vs smooth の壁時計時間
python3 -m scripts.bench_robot_timing \
    --scene face_lite --real --countdown 5    # 紙交換 5 秒
# 結果は logs/robot_timing_<ts>/timing_face_lite.json に保存
```

実機テスト時の **確認ポイント**:
- 軌道が ぐにゃぐにゃせず滑らか (arc 接続部で停止しない)
- 曲率高い所で 自動的に減速してる感じ (速度ログで `speed=29` 等が出る)
- stroke 間 travel が短く感じる
- pen-up 高さが「次に近いか」 で変わる (近い→浅く、 遠い→深く)

### P2: Vectorizer 連携 (E2E スクリプト実装済 ✅)

`scripts/test_pipeline_smooth.py` で 「画像 → Vectorizer → Robot smooth」 が
通せるようにした。 実機モードもあり:

```bash
# 既存生成画像 → 実機描画 (Plan E の Illustrious 出力で確認推奨)
python3 -m scripts.test_pipeline_smooth \
    --image logs/imagegen_comparison_*/illustrious_v2_inpaint.png \
    --real

# Vectorizer + 計画メトリクスだけ (Robot 起動不要、 設定確認用)
python3 -m scripts.test_pipeline_smooth \
    --image scripts/test_sketch.jpg \
    --vectorize-only
```

mock 動作確認済 (sandbox):
- test_sketch.jpg → 8 strokes / 140 arcs / TSP で 37% travel 削減
- 速度プロファイル mean 41.0% (旧 28.4%)、 max 50% (3-region 加速の効果)

VLM → ImageGen 部分は test_vlm_to_image.py を別途使い、 その出力画像を
`--image` に渡せば 完全な VLM → ImageGen → Vectorizer → Robot 連携。

### P3: パラメータチューニング (実機の感触次第)

`draw_strokes_panel_smooth()` の調整余地:

| パラメータ | デフォルト | 動かす理由 |
|---|---|---|
| `draw_speed_base` | 30 | 30 で遅すぎなら 40-50 へ |
| `draw_speed_min` | 10 | 鋭い曲線で停止寸前なら 5、 加速度許容なら 15 |
| `curvature_break` | 0.1 (1/mm = R 10mm) | 緩い扱いの基準。 R 5mm までは速い方が滑らかなら 0.2 へ |
| `curvature_steep` | 0.5 (= R 2mm) | 細部 (目・口) の減速基準 |
| `near_threshold_mm` | 15 | look-ahead 距離。 短いほど安全 / 長いほど高速 |
| `w_clear_near` | (max - contact) / 3 | 浅すぎると 干渉、 深すぎると効果なし |

### P4: PR 作成 (実機検証 PASS 後)

```bash
gh pr create --base dev --title "Frida-inspired multi-stroke smoothness" \
  --body "$(cat docs/20260528_frida_smoothness_handoff.md)"
```

dev or main にマージで M13 提案候補:

```
● M13  Frida-inspired multi-stroke smoothness 実機検証 PASS
       └ stroke_planner (TSP + 3-region curvature speed + look-ahead descent)
       └ Robot.draw_strokes_panel_smooth() + Vectorizer → Robot E2E
       └ stroke_visualizer で実機なし preview 可
       └ scattered_dots benchmark で travel 82% 削減 / 2.98x speedup
       └ face_sketch 1.71x / zigzag 1.38x speedup
```

---

## 触らない方がいいもの (落とし穴)

### 1. `wait_for_pose` の mock = 50ms sleep が累積

100 strokes × 37 arcs × 4 wait = 数千 calls × 50ms。 大規模 stroke の mock 描画は数分かかる。
ベンチマークは **`benchmark_stroke_smoothness.py` (mock 不要、 純関数)** を使う。
mock smoke test は scripts/test_frida_smoothness.py で stroke 数を絞る。

### 2. `Robot.draw_strokes_panel_smooth` は panel frame 必須

`r.panel is None` の場合は明示的にエラーになるよう書いてある (`_require_panel()`)。
`calibration/panel_frame.yaml` (placeholder でも可) が必要。

### 3. start_uv は現状 `None` 決め打ち

`get_end_pose()` の base 座標 → panel uv の逆変換が なくて、 TSP の起点を
strokes[0][0] にしてる。 ロボット現在位置から最近の stroke を選びたければ、
panel.base_to_uv() の逆変換実装が要 (将来追加候補)。

### 4. speed_max_pct で直線部加速は実装済 ✅ (2026-05-28)

3-region mapping (curvature_straight 以下で max_speed_pct) を導入。
デフォルト straight=0.02 (R≥50mm)、 max=50%。 face_sketch で 1.71x、
zigzag で 1.38x まで speedup 改善。

### 5. arc 長は真の弧長で計算済 ✅ (2026-05-28)

`compute_arc_length(triplet)` で R*angle の真値を計算、 benchmark に統合。
ベンチマーク draw_path は chord-sum 比 +1-6% で正確化。

---

## ファイル早見表

| 何 | パス |
|---|---|
| 本引き継ぎ | `docs/20260528_frida_smoothness_handoff.md` |
| 設計 | `docs/20260528_0030_frida_smoothness_design.md` |
| stroke planner 純関数 | `modules/stroke_planner.py` |
| Robot 拡張メソッド | `modules/robot.py::draw_strokes_panel_smooth` |
| ベンチマーク (mock 不要) | `scripts/benchmark_stroke_smoothness.py` |
| mock smoke test | `scripts/test_frida_smoothness.py` |
| (参考) 1 stroke arc 描画 | `modules/robot.py::draw_stroke_panel_arcs` |
| (参考) 既存 smoothing | `modules/trajectory.py` |

---

## 命令一発で再開する場合のプロンプト

```
Frida smoothness 拡張の続き。 docs/20260528_frida_smoothness_handoff.md
を読んで現状把握、 P1 (実機検証) から進めて。
ブランチ: claude/frida-smoothness-20260527
```
