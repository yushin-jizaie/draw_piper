"""framed stylize 用の前処理: 正方形クロップ → (生成) → 縦長中央配置。

== 背景 (2026-06-01、 ユーザー提案) ==
横長/コンパクトなオブジェクト (車・家・猫の顔) を縦長フレームで直接生成すると、
単一被写体ではフレームを埋められず分裂/歪む (車の良い構図が出ない)。
解決: OpenCV で被写体の周りに余白を多少残して**正方形クロップ** → そこで
生成 (正方形は SDXL が得意で良い構図が出る) → 生成結果を真っ白い**縦長
キャンバスの中央に配置**。 位置は「中央」 固定 (実キャンバスでもロボットは
中央に描く)。

縦長の被写体 (木・立った人) は直接縦長生成で良いので、 framed は
横長/コンパクト被写体専用。
"""
from __future__ import annotations

from typing import List, Tuple

import numpy as np
from PIL import Image

Stroke = List[Tuple[float, float]]


def content_bbox(img: Image.Image, white_thresh: int = 200):
    a = np.array(img.convert("L"))
    ys, xs = np.where(a < white_thresh)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def square_crop_with_margin(img: Image.Image, pad: float = 0.22,
                            out_size: int = 1024,
                            white_thresh: int = 200) -> Image.Image:
    """被写体 bbox の周りに pad 割合の余白を残して正方形クロップ (白背景)。

    pad=0.22 → 一辺 = max(bw,bh) * (1 + 2*pad)。 はみ出しは白で埋める。
    """
    bbox = content_bbox(img, white_thresh)
    rgb = img.convert("RGB")
    if bbox is None:
        return rgb.resize((out_size, out_size), Image.LANCZOS)
    x0, y0, x1, y1 = bbox
    bw, bh = x1 - x0 + 1, y1 - y0 + 1
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    side = int(max(bw, bh) * (1.0 + 2.0 * pad))
    half = side // 2
    sq = Image.new("RGB", (side, side), (255, 255, 255))
    sx0, sy0 = cx - half, cy - half
    region = rgb.crop((max(0, sx0), max(0, sy0),
                       min(rgb.width, sx0 + side), min(rgb.height, sy0 + side)))
    sq.paste(region, (max(0, -sx0), max(0, -sy0)))
    return sq.resize((out_size, out_size), Image.LANCZOS)


def place_strokes_centered(strokes: List[Stroke], dst_wh: Tuple[int, int],
                           fill: float = 0.9) -> List[Stroke]:
    """strokes (任意座標) の bbox を縦長キャンバス中央に contain 配置。

    fill = キャンバス長辺方向の占有率上限 (アスペクト維持)。
    """
    if not strokes:
        return strokes
    xs = [p[0] for st in strokes for p in st]
    ys = [p[1] for st in strokes for p in st]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    bw, bh = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
    CW, CH = dst_wh
    s = min(fill * CW / bw, fill * CH / bh)
    ox = (CW - bw * s) / 2.0 - x0 * s
    oy = (CH - bh * s) / 2.0 - y0 * s
    return [[(p[0] * s + ox, p[1] * s + oy) for p in st] for st in strokes]
