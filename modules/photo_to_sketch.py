"""ホワイトボード等を撮った写真 → クリーンな線画 (白背景・黒線) を抽出。

実機入力 (カメラで撮ったボード上の手描き) を想定:
  - 照明ムラ・グレア・反射・汚れがある不透明ボード + 黒マーカーの線
  - 適応的二値化 (局所閾値) で照明ムラに強くマーカー線を抽出
  - 小ノイズ + 主クラスタから離れた迷い線/ボード縁を除去
  - 線の bbox を margin 付きでクロップ → 白背景の正方キャンバスに contain 配置

HEIC は呼び出し側で PIL+pillow_heif で読み込んで渡すか、 path 指定で cv2 が
読める形式 (PNG/JPG) を渡す。
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def extract_sketch(gray: np.ndarray, canvas: int = 1024, margin: float = 0.10,
                   min_area: int = 80, cluster_expand: float = 0.7) -> np.ndarray | None:
    """グレースケール写真 → 白背景・黒線の正方キャンバス (np.uint8) を返す。

    線が見つからなければ None。
    """
    h, w = gray.shape
    sc = 1400.0 / max(h, w)
    g = cv2.resize(gray, (int(w * sc), int(h * sc)))
    bl = cv2.GaussianBlur(g, (5, 5), 0)
    # 適応的二値化 (暗い線=前景)。 blockSize 大きめで照明ムラに頑健。
    th = cv2.adaptiveThreshold(bl, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                               cv2.THRESH_BINARY_INV, 51, 15)
    n, lab, stats, cent = cv2.connectedComponentsWithStats(th)
    comps = [(stats[i, cv2.CC_STAT_AREA], i, cent[i])
             for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= min_area]
    if not comps:
        return None
    # 最大成分 (主題の輪郭) の bbox を基準に、 そこから離れた迷い線/縁を除去
    main = max(comps, key=lambda c: c[0])[1]
    mx, my, mw, mh = (stats[main, cv2.CC_STAT_LEFT], stats[main, cv2.CC_STAT_TOP],
                      stats[main, cv2.CC_STAT_WIDTH], stats[main, cv2.CC_STAT_HEIGHT])
    ex, ey = mw * cluster_expand, mh * cluster_expand
    x0e, y0e, x1e, y1e = mx - ex, my - ey, mx + mw + ex, my + mh + ey
    keep = np.zeros_like(th)
    for area, i, (cx, cy) in comps:
        if x0e <= cx <= x1e and y0e <= cy <= y1e:
            keep[lab == i] = 255
    ys, xs = np.where(keep > 0)
    if len(xs) == 0:
        return None
    x0, y0, x1, y1 = xs.min(), ys.min(), xs.max(), ys.max()
    bw, bh = x1 - x0, y1 - y0
    px, py = int(bw * margin), int(bh * margin)
    x0, y0 = max(0, x0 - px), max(0, y0 - py)
    x1, y1 = min(keep.shape[1], x1 + px), min(keep.shape[0], y1 + py)
    crop = keep[y0:y1, x0:x1]
    cb, cw = crop.shape
    s = canvas * 0.95 / max(cb, cw)
    nw, nh = max(1, int(cw * s)), max(1, int(cb * s))
    crop = cv2.resize(crop, (nw, nh), interpolation=cv2.INTER_AREA)
    out = np.full((canvas, canvas), 255, np.uint8)
    yo, xo = (canvas - nh) // 2, (canvas - nw) // 2
    out[yo:yo + nh, xo:xo + nw][crop > 30] = 0
    return out


def photo_file_to_sketch(src, dst, **kw) -> bool:
    """画像ファイル (PNG/JPG/HEIC) → 線画 PNG。 HEIC は pillow_heif で読む。"""
    src = str(src)
    if src.lower().endswith((".heic", ".heif")):
        from PIL import Image
        import pillow_heif
        pillow_heif.register_heif_opener()
        gray = np.array(Image.open(src).convert("L"))
    else:
        gray = cv2.imread(src, cv2.IMREAD_GRAYSCALE)
    if gray is None:
        return False
    out = extract_sketch(gray, **kw)
    if out is None:
        return False
    cv2.imwrite(str(dst), out)
    return True


if __name__ == "__main__":
    import sys
    src, dst = sys.argv[1], sys.argv[2]
    print("ok" if photo_file_to_sketch(src, dst) else "FAIL")
