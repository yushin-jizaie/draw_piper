"""生成画像(色/トーン付き背景を含む)から線だけを抽出する OpenCV 処理。

FLUX/SDXL 生成は背景に薄い色やトーンが乗ることがあり、 単純な暗度しきい値
(hardboost) だと背景まで黒に拾って線化が汚れる。 ここでは
 ① 背景(色/トーン)を morphological closing で推定して division 正規化で除去
 ② 適応二値化で線を抽出 (照明/トーン残差に強い)
 ③ 微小ノイズ除去
し、 vectorizer が期待する「白地に黒線」 RGB を返す。
"""
from __future__ import annotations
import numpy as np
import cv2
from PIL import Image


def extract_lines(img, *, bg_ksize: int = 25, block: int = 21, c: int = 12,
                  min_area: int = 8, median: int = 3) -> Image.Image:
    """色/トーン背景を除去して線だけの白地黒線画像 (RGB) を返す。

    bg_ksize: 背景推定 closing カーネル径 (大きいほど太い構造まで背景扱い)。
    block/c : adaptiveThreshold の blockSize / 定数 (大きい block=粗い線も拾う)。
    min_area: これ未満の連結成分はノイズとして除去 (px)。
    """
    rgb = np.array(img.convert("RGB"))
    g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    # ① 背景(色/トーン)を closing で推定 → division 正規化で背景を白へ
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (bg_ksize, bg_ksize))
    bg = cv2.morphologyEx(g, cv2.MORPH_CLOSE, k)
    norm = cv2.divide(g, bg, scale=255)            # 背景≈255(白), 線は暗い
    # ② 適応二値化 (線=255)
    binv = cv2.adaptiveThreshold(norm, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                 cv2.THRESH_BINARY_INV, block | 1, c)
    if median >= 3:
        binv = cv2.medianBlur(binv, median)
    # ③ 微小成分除去
    n, lab, stats, _ = cv2.connectedComponentsWithStats(binv, 8)
    out = np.zeros_like(binv)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= min_area:
            out[lab == i] = 255
    # 白地に黒線の RGB (vectorizer 期待形式)
    return Image.fromarray(255 - out).convert("RGB")
