"""ロボット描画用のストローク制約強制 (2026-06-04 ユーザー指定)。

6軸アームはマーカーで透明ボード(panel ≈ 145x264mm)に物理描画する。 急峻な曲率
(小さい曲率半径)には追従できず、 遅れて切り込み線がズレる/潰れる(遅れ累積で図形が
数十mm横ズレすることも)。 そこで生成ストロークを次の制約に整える:

  - 最小特徴サイズ < 8mm のストローク(微小ディテール)は除去。
  - 微小閉ループ(周長 < ~25mm、 渦巻き/微小同心円)は除去。
  - 曲率半径 < 8mm の区間は平滑化して半径を緩める(描けない急カーブを丸める)。
  - 高周波ジグザグは resample + 平滑化で緩和。

入力 strokes は **mm 座標**で渡すこと (panel mm 空間で曲率を評価するため)。
gen 側で px↔mm 変換して呼ぶ。
"""
from __future__ import annotations
import math


def _d(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _circumradius(a, b, c):
    """3 点を通る円の半径 (mm)。 ほぼ直線なら inf。"""
    ab = _d(a, b); bc = _d(b, c); ca = _d(c, a)
    area2 = abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))
    if area2 < 1e-9:
        return float("inf")
    return (ab * bc * ca) / (2.0 * area2)


def _resample(pts, step):
    """polyline を ~step 間隔で再サンプル (密集+急変を緩和)。"""
    if len(pts) < 2 or step <= 0:
        return [tuple(p) for p in pts]
    out = [tuple(pts[0])]; a = tuple(pts[0]); acc = 0.0
    for i in range(1, len(pts)):
        b = tuple(pts[i]); d = _d(a, b)
        while d > 0 and acc + d >= step:
            t = (step - acc) / d
            nx = a[0] + (b[0] - a[0]) * t; ny = a[1] + (b[1] - a[1]) * t
            out.append((nx, ny)); a = (nx, ny); d = _d(a, b); acc = 0.0
        acc += d; a = b
    if _d(out[-1], tuple(pts[-1])) > 1e-6:
        out.append(tuple(pts[-1]))
    return out


def _smooth(pts, alpha=0.3):
    if len(pts) < 3:
        return pts
    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        mx = 0.5 * (pts[i - 1][0] + pts[i + 1][0]); my = 0.5 * (pts[i - 1][1] + pts[i + 1][1])
        out.append(((1 - alpha) * pts[i][0] + alpha * mx,
                    (1 - alpha) * pts[i][1] + alpha * my))
    out.append(pts[-1])
    return out


def _turning(pts):
    """polyline の総旋回角 (rad, 絶対値積算)。 渦巻き/多重周回を検出する。"""
    tot = 0.0
    for i in range(1, len(pts) - 1):
        ax = pts[i][0] - pts[i - 1][0]; ay = pts[i][1] - pts[i - 1][1]
        bx = pts[i + 1][0] - pts[i][0]; by = pts[i + 1][1] - pts[i][1]
        na = math.hypot(ax, ay); nb = math.hypot(bx, by)
        if na < 1e-9 or nb < 1e-9:
            continue
        cross = ax * by - ay * bx
        dot = ax * bx + ay * by
        tot += abs(math.atan2(cross, dot))
    return tot


def _delete_bad_points(s, min_radius_mm):
    """残った急曲率点(cusp/hairpin)を削除して半径を確保する (平滑化で開けない箇所の最終手段)。

    最も尖った内点(circumradius 最小)を 1 つずつ削除。 削除で隣接点が直結し曲率が緩む。
    全点 OK になる or 2 点(直線)になるまで続けるので、 出力は必ず半径>=min を満たす。
    """
    s = [tuple(p) for p in s]
    while len(s) > 2:
        worst_i = -1; worst_r = min_radius_mm
        for i in range(1, len(s) - 1):
            r = _circumradius(s[i - 1], s[i], s[i + 1])
            if r < worst_r:
                worst_r = r; worst_i = i
        if worst_i < 0:
            break
        del s[worst_i]
    return s


def _bad_frac(s, min_radius_mm):
    if len(s) < 3:
        return 0.0
    bad = sum(1 for i in range(1, len(s) - 1)
              if _circumradius(s[i - 1], s[i], s[i + 1]) < min_radius_mm)
    return bad / (len(s) - 2)


def enforce_robot_constraints(strokes, min_radius_mm=8.0, min_feature_mm=8.0,
                              min_loop_perim_mm=25.0, resample_mm=2.5, max_iters=60,
                              spiral_turn_rad=12.0 * math.pi, drop_bad_frac=0.5):
    # spiral_turn_rad: 総旋回角がこれを超えるストロークを「描けない渦巻き」として除去。
    # 4π は羽根状の葉/細密な曲線を誤除去するので 12π に緩和 (描画可能性は min_radius が別途保証)。
    """mm 座標の strokes をロボット描画可能(曲率半径>=8mm, 微小なし)に整えて返す。

    微小ディテール・微小ループ・渦巻き(多重周回)・平滑化しても急曲率が解けない
    ストロークは **除去**。 残りは曲率半径>=min_radius になるよう Laplacian で平滑化。
    """
    out = []
    dropped_tiny = dropped_loop = dropped_spiral = dropped_kinky = 0
    for st in strokes:
        if len(st) < 2:
            continue
        xs = [p[0] for p in st]; ys = [p[1] for p in st]
        bbox = max(max(xs) - min(xs), max(ys) - min(ys))
        perim = sum(_d(st[i], st[i + 1]) for i in range(len(st) - 1))
        closed = _d(st[0], st[-1]) < min_feature_mm * 0.5
        if bbox < min_feature_mm:                 # 微小ディテール → 除去
            dropped_tiny += 1; continue
        if closed and perim < min_loop_perim_mm:  # 微小ループ/渦/同心円 → 除去
            dropped_loop += 1; continue
        # 連続する近接重複点を除去 (曲率計算の退化を防ぐ)
        s = [tuple(st[0])]
        for p in st[1:]:
            if _d(s[-1], tuple(p)) > 0.2:
                s.append(tuple(p))
        s = _resample(s, resample_mm)
        if len(s) < 3:
            if len(s) >= 2:
                out.append(s)
            continue
        if _turning(s) > spiral_turn_rad:         # 渦巻き/多重周回 → 描けない → 除去
            dropped_spiral += 1; continue
        s = _smooth(s, 0.2)                        # 先に高周波ジグザグを軽く均す
        # 曲率半径 < min_radius の点を隣接中点へフル移動 (Laplacian)。 反復で半径が広がる。
        # これを最後に置くことで、 出力は急曲率なし(=収束状態)で終わる。
        for _ in range(max_iters):
            bad = 0
            ns = [s[0]]
            for i in range(1, len(s) - 1):
                if _circumradius(s[i - 1], s[i], s[i + 1]) < min_radius_mm:
                    bad += 1
                    ns.append((0.5 * (s[i - 1][0] + s[i + 1][0]),
                               0.5 * (s[i - 1][1] + s[i + 1][1])))   # フル midpoint
                else:
                    ns.append(s[i])
            ns.append(s[-1]); s = ns
            if bad == 0:
                break
        # 平滑化しても急曲率が大量に残る = 本質的に描けない → 除去
        if _bad_frac(s, min_radius_mm) > drop_bad_frac:
            dropped_kinky += 1; continue
        s = _delete_bad_points(s, min_radius_mm)  # 残った cusp を削除し半径>=min を保証
        if len(s) >= 2:
            out.append(s)
    return out, {"dropped_tiny": dropped_tiny, "dropped_loop": dropped_loop,
                 "dropped_spiral": dropped_spiral, "dropped_kinky": dropped_kinky,
                 "kept": len(out)}
