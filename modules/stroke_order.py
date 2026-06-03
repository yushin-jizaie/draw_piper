"""ストローク描画順の最適化。

ロボットアームは「キャンバス中心に近い位置を始点とした連続的な動き」でしか
滑らかに描けない。 そこで合成後キャンバスの中心に最も近い点から描き始め、
連続的に外側へ向かう順に並べ替える。 オブジェクト単位でまとめて描く
(1 オブジェクトを描き切ってから次へ)。 2026-06-03 ユーザー要望。
"""
from __future__ import annotations


def _d2(a, b):
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def order_strokes_center_out(object_stroke_lists, center):
    """中心→外側・オブジェクト単位・連続化した描画順の flat strokes を返す。

    object_stroke_lists: 各オブジェクトの strokes (list of [ (x,y), ... ]) のリスト。
    center: (cx, cy) 合成キャンバスの中心。

    - オブジェクトは「中心に最も近い点」が近い順に並べる (中心 → 外側)。
    - 各オブジェクト内は貪欲最近傍でつなぎ、 各 stroke は直前ペン位置に近い端点から
      始まる向きに必要なら反転 (= ペン移動を最小化し連続的に)。
    - ペンを center から開始するので、 最初の始点は中心最寄りになる。
    """
    cx, cy = center

    def stroke_min_center(st):
        return min((x - cx) ** 2 + (y - cy) ** 2 for x, y in st)

    objs = [[list(s) for s in strokes if len(s) >= 2]
            for strokes in object_stroke_lists]
    objs = [o for o in objs if o]
    # オブジェクト順: 中心に最も近い点を持つ順 (中心 → 外側)
    objs.sort(key=lambda o: min(stroke_min_center(s) for s in o))

    out = []
    pen = (cx, cy)
    for strokes in objs:
        remaining = list(strokes)
        while remaining:
            best_i = best_d = None
            best_flip = False
            for i, s in enumerate(remaining):
                d0 = _d2(s[0], pen)
                d1 = _d2(s[-1], pen)
                d = d0 if d0 <= d1 else d1
                if best_d is None or d < best_d:
                    best_d, best_i, best_flip = d, i, (d1 < d0)
            s = remaining.pop(best_i)
            if best_flip:
                s = s[::-1]
            out.append(s)
            pen = s[-1]
    return out
