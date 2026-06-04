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


def extract_lines_bold(img, *, bold: int = 3, block: int = 21, c: int = 12,
                       bg_ksize: int = 25, drop_area: int = 25,
                       fill_area: int = 1500, fill_solidity: float = 0.45) -> Image.Image:
    """生成画像を「太い線・塗りなし」 の白地黒線に整える (2026-06-04 ユーザー)。

    FLUX schnell は暗背景+細い明線+塗り を出しがちで、 そのまま vectorize すると
    細切れで微妙。 ここでは:
      ① 暗背景なら極性反転 (線を暗く・背景白)
      ② 背景トーン除去(divide) + 適応二値化で線抽出
      ③ 微小ノイズ(クラックル粒)を除去
      ④ 大きい塗り塊は morphological gradient で輪郭化 (塗りなし)
      ⑤ dilate で太線化
    bold: 太さ(dilate カーネル径)。 drop_area: これ未満の成分は除去。
    fill_area/fill_solidity: 面積大 & 充填率高 の成分を「塗り」とみなし輪郭化。
    """
    g = cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2GRAY)
    if g.mean() < 110:                                   # ① 暗背景→反転
        g = 255 - g
    bg = cv2.morphologyEx(g, cv2.MORPH_CLOSE,
                          cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (bg_ksize, bg_ksize)))
    norm = cv2.divide(g, bg, scale=255)                  # ② 背景除去
    binv = cv2.adaptiveThreshold(norm, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                 cv2.THRESH_BINARY_INV, block | 1, c)
    binv = cv2.medianBlur(binv, 3)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(binv, 8)
    lines = np.zeros_like(binv)
    k3 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    for i in range(1, n):
        a = stats[i, cv2.CC_STAT_AREA]
        if a < drop_area:                                # ③ 微小ノイズ除去
            continue
        w = stats[i, cv2.CC_STAT_WIDTH]; h = stats[i, cv2.CC_STAT_HEIGHT]
        comp = (lab == i).astype(np.uint8) * 255
        if a > fill_area and a / max(w * h, 1) > fill_solidity:
            comp = cv2.morphologyEx(comp, cv2.MORPH_GRADIENT, k3)  # ④ 塗り→輪郭
        lines = cv2.bitwise_or(lines, comp)
    bold_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (bold, bold))
    lines = cv2.dilate(lines, bold_k)                    # ⑤ 太線化
    return Image.fromarray(255 - lines).convert("RGB")
