"""strokes (polyline 群) の bbox 間 translate + scale 変換。

companion mode で「中央生成された detailed cat strokes」 を「空白地帯の指定領域」
に移動するために 使用。
"""
from __future__ import annotations

from typing import List, Sequence, Tuple


StrokePoint = Tuple[float, float]
Stroke = List[StrokePoint]


def compute_strokes_bbox(strokes: Sequence[Sequence[StrokePoint]]
                         ) -> Tuple[float, float, float, float]:
    """strokes 全体の bbox (x, y, w, h)。 空なら (0, 0, 0, 0)。"""
    xs = []
    ys = []
    for stroke in strokes:
        for x, y in stroke:
            xs.append(x); ys.append(y)
    if not xs:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def transform_strokes(
    strokes: Sequence[Sequence[StrokePoint]],
    src_bbox: Tuple[float, float, float, float],
    dst_bbox: Tuple[float, float, float, float],
    *,
    fit: str = "contain",
) -> List[Stroke]:
    """strokes を src_bbox → dst_bbox に translate + scale して返す。

    Parameters
    ----------
    fit
        "contain" — アスペクト保持で dst に収まる最大 (= 余白あり可)
        "fill"   — dst を埋める (= スケール x/y で違う、 歪み可)
        "max"    — scale x/y の大きい方を使う (= dst からはみ出る可)
    """
    sx, sy, sw, sh = src_bbox
    dx, dy, dw, dh = dst_bbox
    if sw <= 0 or sh <= 0:
        return [list(s) for s in strokes]

    sx_ratio = dw / sw
    sy_ratio = dh / sh
    if fit == "contain":
        scale_x = scale_y = min(sx_ratio, sy_ratio)
    elif fit == "fill":
        scale_x, scale_y = sx_ratio, sy_ratio
    elif fit == "max":
        scale_x = scale_y = max(sx_ratio, sy_ratio)
    else:
        raise ValueError(f"unknown fit: {fit}")

    # src bbox の中心を dst bbox の中心に揃える
    src_cx = sx + sw / 2
    src_cy = sy + sh / 2
    dst_cx = dx + dw / 2
    dst_cy = dy + dh / 2

    out: List[Stroke] = []
    for stroke in strokes:
        new = []
        for px, py in stroke:
            nx = (px - src_cx) * scale_x + dst_cx
            ny = (py - src_cy) * scale_y + dst_cy
            new.append((nx, ny))
        out.append(new)
    return out


def combine_strokes(*stroke_sets: Sequence[Sequence[StrokePoint]]
                    ) -> List[Stroke]:
    """複数 strokes セットを concatenate。"""
    out: List[Stroke] = []
    for ss in stroke_sets:
        for s in ss:
            out.append(list(s))
    return out


def _self_test():
    # 中央 (480-540) の strokes を 左上 (50-150) に変換
    strokes = [[(500, 500), (510, 510), (520, 500)]]
    src = compute_strokes_bbox(strokes)
    dst = (50.0, 50.0, 100.0, 100.0)
    out = transform_strokes(strokes, src, dst)
    print(f"src bbox: {src}")
    print(f"dst bbox: {dst}")
    print(f"original: {strokes[0]}")
    print(f"transformed: {out[0]}")
    # 結果は dst の範囲内に
    for x, y in out[0]:
        assert dst[0] <= x <= dst[0] + dst[2] + 1, f"x out of range: {x}"
        assert dst[1] <= y <= dst[1] + dst[3] + 1, f"y out of range: {y}"
    print("self test PASSED")


if __name__ == "__main__":
    _self_test()
