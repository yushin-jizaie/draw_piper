"""入力の被写体占有率を測り、 生成ルートを自動選択する。

== 背景 (2026-06-01) ==
実キャンバスは縦長で、 ロボットは同じ物理ボードに描き戻すので、 生成は
**ユーザーが描いた位置・スケールを保つ** 必要がある (正方形クロップで詰めると
位置がズレる)。 一方、 小さく描かれた入力をそのまま縦長生成すると被写体が
小さく余白だらけで退屈になる。

→ 入力の占有率で自動切替:
  - 占有率 低 (余白が多い): companion ルート。 ユーザーの線はそのまま (位置保持)、
    空いた縦長スペースに生成キャラ/モチーフを足してフレームを埋める (shift)。
  - 占有率 高 (いっぱいに描かれてる): stylize ルート。 縦長のまま、 描かれた
    位置・大きさで線画をスタイル化 (ControlNet で layout 保持)。

占有率は入力画像の固有性質 (どれだけフレームを埋めて描いたか) なので、
入力画像そのものの content bbox / 画像サイズで測れる (縦横比に依らない)。

See: docs/20260601_scatter_and_transparent_board.md
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

# 切替閾値: bbox の長辺がフレーム長辺の何割か (linear) で判定。
# 0.60 = 「片側 6 割以上を占めて描いてる」 → いっぱい寄り → stylize。
LINEAR_FILL_THRESH = 0.60
# 面積比 (bbox 面積 / 画像面積) の補助閾値。 細い被写体 (棒人間) は linear が
# 高くても面積は小さいので、 両方見て総合判断する。
# 0.18: 大きく描かれた平たいオブジェクト (車 area≈0.20) も占有判定は通すが、
# 下の縦横比判定で横長物は scatter 側に振る。
AREA_FILL_THRESH = 0.18
# 縦長被写体のみ stylize (縦長ボードを単一被写体で埋められる)。 横長/コンパクトな
# 物 (家 h/w≈0.88、 車 ≈0.54) は単一だと分裂/歪むので scatter (複数=シーン化) へ。
# 2026-06-01 検証: 木(h/w1.57)/人(縦長)=stylize OK、 家/車=分裂。
TALL_THRESH = 1.05  # bbox 高さ / 幅 がこれ以上で「縦長被写体」


@dataclass
class RouteDecision:
    route: str           # "stylize" | "companion"
    linear_ratio: float  # max(bbox_w/W, bbox_h/H)
    area_ratio: float    # bbox面積 / 画像面積
    bbox: tuple          # (x0, y0, x1, y1) or None
    reason: str


def _content_bbox(img: Image.Image, white_thresh: int = 200):
    a = np.array(img.convert("L"))
    ys, xs = np.where(a < white_thresh)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def decide_route(img: Image.Image,
                 linear_thresh: float = LINEAR_FILL_THRESH,
                 area_thresh: float = AREA_FILL_THRESH,
                 tall_thresh: float = TALL_THRESH,
                 white_thresh: int = 200) -> RouteDecision:
    """入力画像から生成ルートを決定する。

    stylize 条件 = 「いっぱい (占有率高) AND 縦長被写体」。 横長/コンパクトな物は
    縦長フレームを単体で埋められず分裂/歪むので scatter (複数=シーン化) へ。
    """
    W, H = img.size
    bbox = _content_bbox(img, white_thresh)
    if bbox is None:
        return RouteDecision("companion", 0.0, 0.0, None,
                             "白紙 (線なし) → companion")
    x0, y0, x1, y1 = bbox
    bw, bh = x1 - x0 + 1, y1 - y0 + 1
    linear = max(bw / W, bh / H)
    area = (bw * bh) / (W * H)
    tall = bh / max(bw, 1)
    full = (linear >= linear_thresh) and (area >= area_thresh)
    is_tall = tall >= tall_thresh
    if full and is_tall:
        return RouteDecision(
            "stylize", linear, area, bbox,
            f"いっぱい(linear {linear:.2f}/area {area:.2f}) かつ "
            f"縦長被写体(h/w {tall:.2f}>={tall_thresh}) → stylize (位置保持)")
    if full and not is_tall:
        return RouteDecision(
            "companion", linear, area, bbox,
            f"いっぱいだが横長被写体(h/w {tall:.2f}<{tall_thresh}) → "
            f"単体だと分裂/歪む → scatter (複数=シーン化)")
    return RouteDecision(
        "companion", linear, area, bbox,
        f"linear {linear:.2f} / area {area:.2f} → 余白多い → scatter (空白を埋める)")


def decide_route_file(path: str | Path, **kw) -> RouteDecision:
    return decide_route(Image.open(path), **kw)


if __name__ == "__main__":
    import sys
    paths = sys.argv[1:]
    if not paths:
        import glob
        paths = sorted(glob.glob("sketch_variations/_inputs/*.png"))
    print(f"{'input':22} {'route':10} {'linear':>7} {'area':>6}  reason")
    print("-" * 90)
    for p in paths:
        d = decide_route_file(p)
        name = Path(p).stem
        print(f"{name:22} {d.route:10} {d.linear_ratio:7.2f} "
              f"{d.area_ratio:6.2f}  {d.reason}")
