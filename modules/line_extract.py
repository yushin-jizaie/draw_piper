"""透明ホワイトボード用の線抽出 (背景差分 + 特定色フィルタ)。

== 仕様 (2026-06-01 確認) ==
カメラは *透明* ホワイトボードに描かれた線を撮る。 そのため 1 枚の画像に:
  - 手前 (カメラ側): 自分が描いた線
  - 奥 (反対側):     もう 1 人の人間が描いた線
  - さらに奥:        その人間の体 + 現実空間の背景が映り込む
が重なって写る。 線の色は統一だが、 背景・人体・映り込みを除いて
「描かれた線だけ」 を取り出したい。

== 手法 ==
2 つの手がかりを AND で組み合わせる:
  1. 背景差分: 空ボードを 1 枚撮って基準 (background) とし、
     |frame - background| が閾値超の画素 = 「新しく加わったもの」。
     静的な映り込み・室内背景を除去する。
  2. 色フィルタ: 線の色 (HSV 範囲、 または「暗い線」 = 低 V) に一致する
     画素のみ残す。 人体 (肌・服) や色付き背景を除去する。
線 = 「新規」 AND 「線の色」。 → 人体は色で、 静的背景は差分で落ちる。

注: 単一カメラでは手前/奥の線の分離は困難なので、 まず両側の線を
まとめて抽出する (near/far 分離は将来課題)。

出力は白背景・黒線の RGB 画像で、 既存パイプライン (vectorizer) の
入力スケッチとしてそのまま使える。

See: docs/20260601_scatter_and_transparent_board.md
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image

HSVRange = Tuple[Tuple[int, int, int], Tuple[int, int, int]]

# OpenCV HSV: H 0-179, S 0-255, V 0-255。 red は hue が 0 と 179 で巻くので 2 範囲。
COLOR_PRESETS: dict = {
    "dark": None,    # 特殊: 低 V (暗い線) として扱う
    "black": None,
    "blue":  [((100, 70, 40), (130, 255, 255))],
    "red":   [((0, 70, 50), (10, 255, 255)), ((168, 70, 50), (179, 255, 255))],
    "green": [((38, 50, 35), (88, 255, 255))],
}


def color_mask(frame_bgr: np.ndarray, mode: str = "dark",
               dark_v_max: int = 90,
               hsv_ranges: Optional[List[HSVRange]] = None) -> np.ndarray:
    """線の色に一致する画素の 0/255 mask を返す。

    hsv_ranges を直接渡せば任意色。 None なら mode (COLOR_PRESETS) を使う。
    mode が "dark"/"black" (= preset 値 None) のときは V < dark_v_max。
    """
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    if hsv_ranges is None:
        hsv_ranges = COLOR_PRESETS.get(mode, None)
    if hsv_ranges is None:
        # 暗い線モード: 明度が低い画素 (色相に依らない)
        return ((hsv[:, :, 2] < dark_v_max).astype(np.uint8)) * 255
    mask = np.zeros(frame_bgr.shape[:2], np.uint8)
    for lo, hi in hsv_ranges:
        mask |= cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
    return mask


def bg_diff_mask(frame_bgr: np.ndarray, background_bgr: np.ndarray,
                 diff_thresh: int = 30) -> np.ndarray:
    """背景差分。 |frame - background| のチャネル最大が閾値超 = 前景 (0/255)。"""
    if frame_bgr.shape != background_bgr.shape:
        background_bgr = cv2.resize(background_bgr,
                                    (frame_bgr.shape[1], frame_bgr.shape[0]))
    diff = cv2.absdiff(frame_bgr, background_bgr)
    return ((diff.max(axis=2) > diff_thresh).astype(np.uint8)) * 255


def extract_lines(frame_bgr: np.ndarray,
                  background_bgr: Optional[np.ndarray] = None,
                  mode: str = "dark",
                  dark_v_max: int = 90,
                  hsv_ranges: Optional[List[HSVRange]] = None,
                  diff_thresh: int = 30,
                  open_ksize: int = 3,
                  close_ksize: int = 3,
                  min_blob: int = 12) -> np.ndarray:
    """背景差分 + 色フィルタで線 mask (0/255, 255=線) を返す。

    background_bgr が None のときは色フィルタのみ (背景差分なし)。
    """
    mask = color_mask(frame_bgr, mode, dark_v_max, hsv_ranges)
    if background_bgr is not None:
        fg = bg_diff_mask(frame_bgr, background_bgr, diff_thresh)
        mask = cv2.bitwise_and(mask, fg)
    if open_ksize > 0:
        mask = cv2.morphologyEx(
            mask, cv2.MORPH_OPEN, np.ones((open_ksize, open_ksize), np.uint8))
    if close_ksize > 0:
        mask = cv2.morphologyEx(
            mask, cv2.MORPH_CLOSE, np.ones((close_ksize, close_ksize), np.uint8))
    if min_blob > 0:
        n, lab, stats, _ = cv2.connectedComponentsWithStats(
            (mask > 0).astype(np.uint8))
        keep = np.zeros_like(mask)
        for i in range(1, n):
            if stats[i, cv2.CC_STAT_AREA] >= min_blob:
                keep[lab == i] = 255
        mask = keep
    return mask


def mask_to_lineart(mask: np.ndarray) -> Image.Image:
    """線 mask (0/255) → 白背景・黒線の RGB 画像 (パイプライン入力用)。"""
    canvas = np.full((mask.shape[0], mask.shape[1], 3), 255, np.uint8)
    canvas[mask > 0] = (0, 0, 0)
    return Image.fromarray(canvas)


def extract_lines_image(frame_bgr: np.ndarray,
                        background_bgr: Optional[np.ndarray] = None,
                        **kw) -> Image.Image:
    """高レベル: frame(+background) → 白背景・黒線の PIL 画像。"""
    return mask_to_lineart(extract_lines(frame_bgr, background_bgr, **kw))


# --- CLI / スモークテスト -----------------------------------------------------

def _cli() -> int:
    import argparse
    from pathlib import Path
    ap = argparse.ArgumentParser(description="透明ボード線抽出 (背景差分+色)")
    ap.add_argument("--frame", type=Path, required=True, help="線を含む撮影")
    ap.add_argument("--background", type=Path, default=None, help="空ボード基準")
    ap.add_argument("--mode", default="dark", choices=list(COLOR_PRESETS))
    ap.add_argument("--dark-v-max", type=int, default=90)
    ap.add_argument("--diff-thresh", type=int, default=30)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    frame = cv2.imread(str(a.frame))
    bg = cv2.imread(str(a.background)) if a.background else None
    img = extract_lines_image(frame, bg, mode=a.mode,
                              dark_v_max=a.dark_v_max, diff_thresh=a.diff_thresh)
    img.save(a.out)
    print(f"saved → {a.out}")
    return 0


def _smoke() -> None:
    """合成画像で「線は残り、 人体・静的映り込みは落ちる」 を検証。"""
    H, W = 200, 200
    # 背景: 灰色ボード + 静的な映り込み (青っぽい矩形)
    bg = np.full((H, W, 3), 200, np.uint8)
    bg[20:60, 20:120] = (180, 120, 60)   # 青系の映り込み (BGR)
    # frame: 背景 + 黒い線 + 「人体」 (肌色の塊、 新規だが線色でない)
    frame = bg.copy()
    cv2.line(frame, (30, 150), (170, 160), (20, 20, 20), 3)   # 黒い線 (新規)
    cv2.circle(frame, (150, 50), 25, (120, 150, 200), -1)     # 肌色の人体 (新規)
    mask = extract_lines(frame, bg, mode="dark", dark_v_max=90, diff_thresh=30)
    line_px = int((mask[140:170, 25:175] > 0).sum())
    human_px = int((mask[25:75, 125:175] > 0).sum())
    refl_px = int((mask[20:60, 20:120] > 0).sum())
    print(f"line px (期待 多): {line_px}")
    print(f"human px (期待 ~0): {human_px}")
    print(f"static reflection px (期待 ~0): {refl_px}")
    assert line_px > 50, "線が抽出できていない"
    assert human_px < 30, "人体が混入している"
    assert refl_px < 30, "静的映り込みが除去できていない"
    print("OK: 線のみ抽出 (人体・静的背景を除去)")


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        _smoke()
    else:
        raise SystemExit(_cli())
