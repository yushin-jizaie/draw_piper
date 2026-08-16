"""cv2 ベース blob 検出 と 空白地帯計算。

ユーザの入力 sketch から:
- 各 blob (connected component) の bbox + 重心
- 全 blob を避けた 最大の axis-aligned 空白矩形

companion mode (= input + generated を空白地帯に並置) で使用。

依存: cv2, numpy (PIL は呼び出し側)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np


@dataclass
class Blob:
    """1 つの connected component"""
    bbox: Tuple[int, int, int, int]   # (x, y, w, h)
    centroid: Tuple[float, float]      # (cx, cy)
    area: int                          # px count


def detect_blobs(
    image_gray_or_pil,
    *,
    threshold: int = 200,
    min_area: int = 30,
) -> List[Blob]:
    """grayscale 画像 (or PIL Image) から 暗い領域 (=線画) の blob を列挙。

    Parameters
    ----------
    image_gray_or_pil
        cv2 grayscale ndarray もしくは PIL.Image
    threshold
        pixel < threshold を foreground (= 線) として扱う。 200 で 軽いノイズも拾う、
        128 で本格的な線のみ。
    min_area
        この px 数 未満の小 blob は ノイズ扱いで無視。

    Returns
    -------
    List[Blob]
        bbox / centroid / area で降順 (大きい順)。
    """
    if hasattr(image_gray_or_pil, "convert"):
        img = np.array(image_gray_or_pil.convert("L"))
    else:
        img = np.asarray(image_gray_or_pil)
        if img.ndim == 3:
            img = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)

    _, binary = cv2.threshold(img, threshold, 255, cv2.THRESH_BINARY_INV)
    # ノイズ除去 (open + close で 細切れ blob を統合)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=2)

    n_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(
        binary, connectivity=8)

    blobs: List[Blob] = []
    # label 0 = 背景なのでスキップ
    for i in range(1, n_labels):
        x, y, w, h, area = stats[i]
        if area < min_area:
            continue
        cx, cy = centroids[i]
        blobs.append(Blob(
            bbox=(int(x), int(y), int(w), int(h)),
            centroid=(float(cx), float(cy)),
            area=int(area),
        ))
    blobs.sort(key=lambda b: -b.area)
    return blobs


def union_bbox(blobs: List[Blob]) -> Optional[Tuple[int, int, int, int]]:
    """全 blob を包含する 単一 bbox。 空なら None。"""
    if not blobs:
        return None
    xs = [b.bbox[0] for b in blobs]
    ys = [b.bbox[1] for b in blobs]
    xs2 = [b.bbox[0] + b.bbox[2] for b in blobs]
    ys2 = [b.bbox[1] + b.bbox[3] for b in blobs]
    return (min(xs), min(ys), max(xs2) - min(xs), max(ys2) - min(ys))


def find_largest_empty_rect(
    occupied_bbox: Optional[Tuple[int, int, int, int]],
    canvas_w: int,
    canvas_h: int,
    *,
    padding: int = 30,
    expand_bbox: int = 20,
) -> Tuple[int, int, int, int]:
    """`occupied_bbox` (input) を 避けた 最大の axis-aligned 空白矩形を返す。

    実装: input bbox の **上/下/左/右** の 4 帯から 最も面積が大きいものを選ぶ。
    境界 padding と input への expand で 重なり防止。

    Returns
    -------
    (x, y, w, h)
        canvas 上の dst 領域。 必ず正の面積。 input が無いなら canvas 全体 -2*padding。
    """
    if occupied_bbox is None:
        return (padding, padding,
                canvas_w - 2 * padding, canvas_h - 2 * padding)
    ix, iy, iw, ih = occupied_bbox
    # input を少し膨らませる (重なり防止)
    ix -= expand_bbox; iy -= expand_bbox
    iw += 2 * expand_bbox; ih += 2 * expand_bbox
    ix = max(0, ix); iy = max(0, iy)
    iw = min(canvas_w - ix, iw); ih = min(canvas_h - iy, ih)

    candidates = []
    # 上の帯
    top_h = iy - padding
    if top_h > 50:
        candidates.append((padding, padding,
                           canvas_w - 2 * padding, top_h))
    # 下の帯
    bot_y = iy + ih + padding
    bot_h = canvas_h - bot_y - padding
    if bot_h > 50:
        candidates.append((padding, bot_y,
                           canvas_w - 2 * padding, bot_h))
    # 左の帯
    left_w = ix - padding
    if left_w > 50:
        candidates.append((padding, padding,
                           left_w, canvas_h - 2 * padding))
    # 右の帯
    right_x = ix + iw + padding
    right_w = canvas_w - right_x - padding
    if right_w > 50:
        candidates.append((right_x, padding,
                           right_w, canvas_h - 2 * padding))

    if not candidates:
        # fallback: canvas 全体 (overlap 不可避)
        return (padding, padding,
                canvas_w - 2 * padding, canvas_h - 2 * padding)
    candidates.sort(key=lambda r: -(r[2] * r[3]))
    return candidates[0]


def _self_test():
    """簡易動作確認: 中央に oval があると、 周囲の 4 帯から最大を選ぶ。"""
    from PIL import Image, ImageDraw
    img = Image.new("L", (1024, 1024), 255)
    d = ImageDraw.Draw(img)
    # 中央上に oval
    d.ellipse((400, 250, 600, 450), outline=0, width=3)
    blobs = detect_blobs(img)
    print(f"detected {len(blobs)} blobs")
    for b in blobs:
        print(f"  bbox={b.bbox} centroid={b.centroid} area={b.area}")
    ub = union_bbox(blobs)
    print(f"union bbox: {ub}")
    empty = find_largest_empty_rect(ub, 1024, 1024)
    print(f"largest empty rect: {empty}")
    # 中央上に oval なので、 下の帯が最大のはず
    assert empty[1] > 400, "empty rect should be below the oval"
    print("self test PASSED")


if __name__ == "__main__":
    _self_test()
