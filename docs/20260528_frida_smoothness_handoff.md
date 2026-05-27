# Frida Smoothness 拡張 引き継ぎ (翌朝再開用)

> ブランチ: `claude/frida-smoothness-20260527` (dev ベース、 全 push 済)
> 作成 commit: `(this push)`
> 設計: `docs/20260528_0030_frida_smoothness_design.md`

---

## TL;DR (3 行)

1. **実装完了**: Frida ヒント 3 つ (TSP ordering + 曲率連動速度 + look-ahead descent) を `Robot.draw_strokes_panel_smooth()` に統合
2. **mock テスト全 PASS**: travel 49% 削減 (5 stroke 例)、 ベンチマークで scattered_dots は 82% 削減 / 2.78x speedup
3. **次セッションは実機検証**: `scripts/test_frida_smoothness.py` を mock=True から real mode に切り替えて Piper 実機で確認

---

## 何が出来たか

### 新規ファイル
| ファイル | 内容 |
|---|---|
| `modules/stroke_planner.py` | TSP 並べ替え / 曲率計算 / 速度プロファイル / look-ahead clear heights の純関数群 |
| `scripts/benchmark_stroke_smoothness.py` | 3 strategy (naive/arcs/smooth) の軌道メトリクス比較ベンチマーク (mock 不要) |
| `scripts/test_frida_smoothness.py` | mock Robot で `draw_strokes_panel_smooth` を E2E テスト |
| `docs/20260528_0030_frida_smoothness_design.md` | 設計ドキュメント |
| `docs/20260528_frida_smoothness_handoff.md` | 本ファイル |

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

```
=== scene: face_sketch (9 strokes) ===
metric            naive    arcs   smooth
travel_path_mm    169.7    169.7   112.1   (-34%)
est_time_s         10.0     10.0     6.9   (1.45x)

=== scene: scattered_dots (100 strokes) ===
metric            naive    arcs   smooth
travel_path_mm   4212.2   4212.2   748.1   (-82%)
est_time_s        87.4     87.4    31.4   (2.78x)

=== scene: zigzag_signature (1 stroke) ===
metric            naive    arcs   smooth
travel_path_mm      0.0      0.0     0.0   (1 stroke → TSP 無効)
est_time_s          8.0      7.5     7.7   (~同等、 速度プロファイルで微改善)
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

mock では accel/decel/MOVE_C overhead は反映されない。 実機での「本当に滑らかか」 は要計測:

```bash
cd ~/draw_piper
git fetch origin
git checkout claude/frida-smoothness-20260527
git pull

# CAN bring-up (必要に応じて)
sudo ip link set can0 up type can bitrate 1000000

# 1. ready pose に行く (前回セッションで動作確認済の手順)
python3 -m scripts.test_ready_pose move

# 2. test_frida_smoothness.py の mock=True を mock=False or 削除 して
#    実機で軽く 5 stroke 描画 (panel 上に何か小さい絵が出るはず)
# 編集箇所: scripts/test_frida_smoothness.py L25 あたり
#   r = Robot(mock=True)  →  r = Robot()    # auto-detect (SDK あれば実機)
python3 -m scripts.test_frida_smoothness

# 3. (任意) face_sketch シーンを実機で
#    benchmark_stroke_smoothness.py 内の scene_face_sketch() を取り出して
#    新しい test スクリプト or wall_drawing_gui に組み込む
```

実機テスト時の **確認ポイント**:
- 軌道が ぐにゃぐにゃせず滑らか (arc 接続部で停止しない)
- 曲率高い所で 自動的に減速してる感じ (速度ログで `speed=29` 等が出る)
- stroke 間 travel が短く感じる
- pen-up 高さが「次に近いか」 で変わる (近い→浅く、 遠い→深く)

### P2: Vectorizer 出力と接続

`modules/vectorizer.py::Vectorizer.vectorize_to_panel()` の出力 (polyline 列) を
そのまま `draw_strokes_panel_smooth()` に渡せるはず:

```python
from modules.vectorizer import Vectorizer
from modules.robot import Robot

vec = Vectorizer(panel_frame=robot.panel)
res = vec.vectorize_to_panel(generated_image)
strokes = [list(s) for s in res.strokes_panel_mm]   # list of (u, v) polylines

robot.draw_strokes_panel_smooth(strokes,
    draw_speed_base=30, near_threshold_mm=15.0)
```

`test_vlm_to_image.py` (M9 のスクリプト) の Vectorizer 出力を **そのまま実機描画**
する E2E テストにすると 「VLM → ImageGen → Vectorizer → Robot」 が完成。

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
gh pr create --title "Frida-inspired multi-stroke smoothness" \
  --body "$(cat docs/20260528_frida_smoothness_handoff.md)"
```

dev or main にマージで M13 提案候補:

```
● M13  Frida-inspired multi-stroke smoothness (TSP + curvature speed +
       look-ahead descent) 実機検証 PASS
       └ scattered_dots benchmark で travel 82% 削減 / 2.78x speedup
       └ Robot.draw_strokes_panel_smooth() 実装、 Vectorizer 出力連携
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

### 4. speed_max_pct は未使用

`speed_from_curvature` で 「直線部で加速する」 仕様は今は未実装 (base が上限)。
実機テストで「直線部は もっと速くしたい」 要望が出たら追加実装。

### 5. arc 長は chord-sum で近似

ベンチマークの draw_path_mm は 三つ組の chord-sum (a-b + b-c) であって、 真の
弧長より短い。 真の値が要るなら circle_arc_length() を追加実装。

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
