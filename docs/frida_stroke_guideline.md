# Frida 最適化ストローク生成ガイド (2026-05-30)

draw_piper の Frida Smooth Draw (`Robot.draw_strokes_panel_smooth` の planning +
GUI の JointCtrl 経路) は、 ストロークデータの **構造的特徴** から
速度・順序を自動最適化する。 ストローク側がその最適化と相性悪い構造だと
効果ゼロ or 逆効果になるため、 画像生成 + ベクトル化パイプラインが
「Frida で映える」 ストロークを出すよう設計する。

## Frida が最適化する 3 軸

| 機能 | 何を見る | 効くストローク特性 |
|---|---|---|
| **TSP 並び替え** (`reorder_strokes_tsp`) | stroke 両端の (u, v) | 空間的にクラスタ化 |
| **曲率連動速度** (`speed_profile_for_stroke`) | 3 点 triplet の曲率 1/R | 直線 + 緩 + 急 のミックス |
| **Look-ahead descent** (`plan_clear_heights`) | 連続 stroke 間の gap | 近接 sibling (gap < 15mm) |

## 推奨される構造

### 1. stroke 数 10〜50 本
- 1〜5 本: TSP の恩恵ゼロ
- 50 本超: 接続線オーバーヘッド + GUI プレビューが重い
- **「分けられるところは分ける、 でも細かすぎない」**
- 1 本の長 stroke (200+ 点) より 10 個の中 stroke (20-30 点) が望ましい

### 2. stroke 1 本あたり 5〜40 点
- < 3 点: triplet 作れず曲率計算 skip → speed_base 固定
- > 100 点: 中断 / 再開の粒度が粗い
- 描画時に `step_mm` で線形補間されるので元データは粗くて OK

### 3. 空間クラスタリング
**良い**:
- 左右対称 / 上下対称 (目、 ヒゲ、 腕) → TSP がペア化
- 連続要素 (●●●) → TSP が線形消化
- テキスト・細部 → look-ahead descent で travel 短縮

**悪い**:
- 全 stroke が canvas 全域に散らばっている
- 完全 random 順、 クラスタ無し

### 4. 曲率の多様性
1 つの stroke 内で曲率変化:
- 直線部 (曲率 ≈ 0) → speed_max
- 緩いカーブ (R 10-50mm) → speed_base
- 鋭いカーブ (R < 2mm) → speed_min

例:
- ◯ (円弧のみ): 全 stroke 同じ速度 → 曲率機能が無効化
- ⌒_⌒ (円弧+直線+円弧): 自然な速度変化
- 「人」「N」 (直線+鋭角): speed_min/max の幅をフル活用

### 5. 隣接 stroke の gap 設計
look-ahead descent は gap ≤ `near_mm` (default 15mm) で pen-up を浅くする:
- ヒゲ 3 本を 5-10mm 間隔 → 全部浅い pen-up で繋がる
- 5mm + `merge_mm=5` → ペン下げたまま接続線描画
- 50mm 以上 → 通常の安全 pen-up

## アンチパターン

| パターン | 問題 |
|---|---|
| 1 本の巨大 stroke (1000 点) | TSP 無意味、 失敗時の再開不可 |
| 全 stroke 2-3 点 | 曲率計算不可、 overhead が支配 |
| 同一座標重複 stroke | TSP 無限ループ気味、 IK 失敗連発 |
| canvas 外 (u, v 範囲外) | 描画時に座標フィルタで drop |
| 完全閉曲線 (●) 多数 | 開始 = 終了点で TSP 反転無意味 |

## 参考実装

`logs/frida_test_mouse/strokes.json` (27 strokes / 425 points / 3157px):
- 顔輪郭 (2 arc): 大きな円弧
- 耳 (4 circle): 左右対称 + 入れ子 → TSP クラスタ
- 目 / 鼻 / 口 (4 stroke): 顔の小要素クラスタ
- ヒゲ (6 line): 左右 3 本ずつ → look-ahead descent
- 体スクリブル (5 wavy_v): 縦線並列
- 「N ゅ u …」 (6 stroke): 文字クラスタ + 混在曲率

Frida の 3 機能を全て発動する最小例。

## 画像生成 + ベクトル化パイプライン

### 画像生成側 (SDXL 等の prompt 補強)

```
線画スタイル。 各要素 (目, 鼻, 口, 耳, ヒゲ 等) は独立した clean
contour として描く。 シェーディング / ハッチング / クロスハッチは禁止
(線の本数が爆発するため)。 全体 stroke 数の目安: 20-40 本。
主要要素 + 細部 + 装飾テキスト/記号 の 3 階層構造。
```

英語フレーズ (CHARACTER/COMPANION_TEMPLATE 用):
- `no hatching, no cross-hatching`
- `discrete clean contours per element`
- `20 to 40 separate strokes`
- `three-layer structure (main parts + small details + decorative text)`

### ベクトル化側 (Vectorizer / potrace 等)

```yaml
min_stroke_length_px: 8        # 短すぎる弧片は drop
max_strokes: 50                # これ以上は merge or 重要度で trim
min_points_per_stroke: 5
max_points_per_stroke: 80      # 超える場合は中間分割
close_loop_threshold_px: 3     # 始終点が近ければ閉路化
douglas_peucker_epsilon: 1.5   # 過度な polyline 単純化を防ぐ
merge_collinear: ON
output_sort: detection_order   # Frida 側で TSP 並び替えするので
```

### Frida 適合度チェック (生成後の sanity check)

`scripts/check_frida_friendly.py` を使う:

```bash
./venv/bin/python -m scripts.check_frida_friendly \
    logs/robot_input_set_v2_*/<key>/strokes.json
```

報告される warn:
- stroke 数 < 5 (TSP 無意味)
- stroke 数 > 80 (overhead 大)
- 平均点数 < 4 (短すぎ) / > 60 (長すぎ)
- stroke 間中央距離 > 80px (クラスタ化されてない)

## まとめ
- **stroke 数 20-40 / 平均 15-30 点/stroke** を狙う
- **空間クラスタ化** (近いもの同士を別 stroke にして並置)
- 各 stroke 内で **曲率を変化** (直線 + 緩 + 急 ミックス)
- 隣接 stroke の **gap < 15mm** で look-ahead descent
- `logs/frida_test_mouse/strokes.json` を視覚的参考に

このガイドに沿うと Frida は TSP / 曲率速度 / look-ahead を最大限活用
して「速くて滑らかな描画」 を実現できる。
