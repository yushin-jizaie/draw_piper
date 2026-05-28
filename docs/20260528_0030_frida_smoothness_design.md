# Frida-inspired Stroke Smoothness 拡張 設計 (2026-05-28)

> ブランチ: `claude/frida-smoothness-20260527`
> 親プロジェクト: draw_piper (Piper × ホワイトボード描画)
> 参考: https://github.com/cmubig/Frida

## 目的

現状の `Robot.draw_stroke_panel_arcs` (MOVE_C による smooth 描画) を更に改善し、
複数 stroke を **連続でロボットらしく綺麗に** 描けるようにする。

Frida (CMU painting robot) を参考に 以下 3 つを追加実装:

1. **Stroke ordering (TSP 近似)** — stroke 順を 距離最適に並べ替え、 travel 時間を削減
2. **曲率連動の速度プロファイル** — 各 arc の曲率に応じて速度を可変、 鋭い曲線で減速 / 緩い曲線で加速 → jerk 削減
3. **Look-ahead descent height** — 次 stroke が近ければ pen-up 高さを浅く、 遠ければ深く

## 現状 (拡張前)

既存実装で完了している部分:

- `modules/trajectory.py::smooth_polyline()` — スプライン平滑 + 一様再サンプル
- `modules/trajectory.py::polyline_to_arc_triplets()` — MOVE_C 三つ組への変換
- `modules/robot.py::draw_stroke_panel_arcs()` — 1 stroke の arc 描画
- `Vectorizer` が出す strokes は polyline 列 (順序最適化は無し)

つまり **「1 stroke 内の smooth curve 化」** は 完了済。 **「複数 stroke 間の最適化」**
が未実装で、 ここに Frida ヒント 3 つを追加する。

## 拡張アーキテクチャ

### 新モジュール: `modules/stroke_planner.py`

純関数ライブラリ (Robot 依存無し)。 単体テスト可能。

```
reorder_strokes_tsp(strokes, start_point=None) -> reordered, indices
  greedy nearest-neighbor で stroke 順を並べ替え。 各 stroke は
  先頭/末尾の どちらから始めても OK なので両端を考慮 (reverse 含む)。

compute_arc_curvature(arc_triplet) -> float (1/mm)
  3 点から円周の曲率を計算。 1 / R = 4 * area / (a * b * c) のヘロン式。

speed_from_curvature(curvature, base_speed_pct, *,
                     min_speed_pct=10, max_speed_pct=50,
                     curvature_break_mm=10.0) -> int
  曲率 → 速度% マッピング。 緩い (1/R < break) では base、
  鋭い (1/R > break) では min まで線形減速。

plan_clear_heights(strokes, base_clear_mm=30.0, *,
                   near_threshold_mm=10.0, near_clear_mm=10.0) -> list[float]
  各 stroke の終端と 次 stroke の始端の距離を見て、 終端の pen-up
  高さを下げる (短距離 travel 用 = look-ahead descent)。
```

### 新メソッド: `modules/robot.py::Robot.draw_strokes_panel_smooth()`

複数 stroke を 一括で受け取って Frida-inspired 描画:

```python
def draw_strokes_panel_smooth(self, strokes, *,
                              w_contact=None, w_clear_max=None,
                              w_clear_near=None,
                              travel_speed=60,
                              draw_speed_base=30,
                              draw_speed_min=10,
                              draw_speed_max=50,
                              curvature_break_mm=10.0,
                              near_threshold_mm=10.0,
                              step_mm=2.0,
                              smooth_lambda=0.0,
                              reorder=True,
                              ...):
    """Multi-stroke smooth drawing (Frida-inspired).

    Pipeline:
      strokes (list of polylines)
        1. (optional) reorder via TSP greedy nearest-neighbor
        2. compute look-ahead pen-up heights per stroke gap
        3. for each stroke:
           a. smooth_polyline -> arc_triplets
           b. travel to first point at pen-up height
           c. descend
           d. for each triplet: speed = speed_from_curvature(...)
              MOVE_C with that speed
           e. pen-up to look-ahead height (浅 or 深)
        4. final pen-up to safe height
    """
```

### ベンチマークスクリプト: `scripts/benchmark_stroke_smoothness.py`

mock モードで合成 stroke set を 3 種類の方法で描画して、 軌道メトリクスを比較:

- `naive`: `draw_stroke_panel` (MOVE_L 連続)
- `arcs`: `draw_stroke_panel_arcs` (MOVE_C smooth、 既存)
- `smooth`: `draw_strokes_panel_smooth` (本拡張)

メトリクス:
- 総 path 長 (描画 + pen-up travel)
- pen-up travel の累積 (短いほど良い)
- 推定描画時間 (各 segment の速度から)
- 速度プロファイルの分散 (低いほど jerk 小)
- 曲率ヒストグラム

## テスト方針

- 純関数 `stroke_planner.py` の単体テスト (TSP / 曲率 / look-ahead)
- mock Robot での 描画 dry-run、 出力 trajectory を verify
- 実機テストは ユーザ側で実施 (本作業範囲外)

## 範囲外 (今は実装しない)

- カメラフィードバック loop (Frida 案 5) — 別途カメラ統合タスク
- 微分可能 stroke renderer (Frida 案 6) — 学習要素、 別 project
- MoveIt2 移行 — 大規模、 別検討
