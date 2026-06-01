"""scatter placement: companion スプライトシートを個々のキャラに分割し、
入力に被らないよう周囲の空白セルへ散布する。

shift placement (modules/stroke_transform) が「1 体を最大空白へ移動」 なのに
対し、 scatter は「複数の小さなキャラを入力の周囲に撒く」 演出。
lineartLoRA char preset のスプライトシート出力 (複数ポーズ) を活かす。

パイプライン:
  1. split_characters: 塗りシルエットを連結成分で個々のキャラ crop に分割
  2. vectorize_characters: 各 crop を canny 輪郭で線画化 (塗り→輪郭線)
  3. free_cells:         入力 bbox と重ならない grid セルを列挙
  4. scatter_strokes:    各キャラを空きセルに縮小配置
  → input_strokes + 散布キャラ の合成 strokes を返す

座標は全て canvas pixel (x, y)。 出力 strokes は shift と同じ
「list of polyline (list of (x, y))」 形式なので、 既存の
render_strokes_to_image / strokes.json 出力にそのまま流せる。

See: docs/20260601_scatter_and_transparent_board.md
"""
from __future__ import annotations

import random
from typing import List, Tuple

import cv2
import numpy as np
from PIL import Image

Stroke = List[Tuple[float, float]]


def split_characters(sheet_img: Image.Image, *, dark_thresh: int = 110,
                     min_area: int = 1500, min_wh: int = 40,
                     dilate: int = 9) -> list:
    """塗りシルエットのスプライトシートを連結成分で分割。

    Returns: [(crop_gray (np.uint8), w, h), ...] (面積の大きい順)
    """
    gray = np.array(sheet_img.convert("L"))
    mask = (gray < dark_thresh).astype(np.uint8)
    # 各キャラ内の細切れ (手足の隙間等) を 1 成分にまとめるため dilate
    md = cv2.dilate(mask, np.ones((dilate, dilate), np.uint8))
    n, _lab, stats, _cent = cv2.connectedComponentsWithStats(md)
    chars = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < min_area or w < min_wh or h < min_wh:
            continue
        chars.append((gray[y:y + h, x:x + w], int(w), int(h), int(area)))
    chars.sort(key=lambda c: c[3], reverse=True)
    return [(c[0], c[1], c[2]) for c in chars]


def vectorize_characters(chars: list, vectorizer) -> list:
    """各キャラ crop を vectorizer で線画化。

    塗りシルエットなので canny (輪郭) mode の vectorizer を渡すこと。
    Returns: [(strokes (crop-local 座標), w, h), ...]
    """
    out = []
    for crop, w, h in chars:
        gi = Image.fromarray(crop).convert("RGB")
        r = vectorizer.vectorize(generated_image=gi, user_image=None)
        if r.n_strokes == 0:
            continue
        out.append((r.strokes, w, h))
    return out


def free_cells(canvas_wh: Tuple[int, int], input_bbox: Tuple[int, int, int, int],
               *, cols: int = 3, rows: int = 7) -> list:
    """grid セルのうち入力 bbox (x, y, w, h) と重ならないものを返す。"""
    cw_total, ch_total = canvas_wh
    ix, iy, iw, ih = input_bbox
    cw, ch = cw_total / cols, ch_total / rows
    cells = []
    for rr in range(rows):
        for cc in range(cols):
            x0, y0 = cc * cw, rr * ch
            # 入力 bbox と少しでも重なるセルは除外 (元絵を侵食しない)
            overlaps = not (x0 + cw <= ix or x0 >= ix + iw
                            or y0 + ch <= iy or y0 >= iy + ih)
            if overlaps:
                continue
            cells.append((x0, y0, cw, ch))
    return cells


def scatter_strokes(char_strokes: list, cells: list, *,
                    fill: float = 0.8, seed: int = 7,
                    jitter: float = 0.0) -> List[Stroke]:
    """各キャラ strokes を空きセルへ縮小配置 (アスペクト維持)。

    jitter>0 で、 各キャラをセル内でランダムに位置・スケールを揺らす
    (グリッド感を崩す random パターン)。 jitter は揺れ幅の割合 (0-1)。
    """
    rng = random.Random(seed)
    cells = list(cells)
    rng.shuffle(cells)
    placed: List[Stroke] = []
    for k, (strokes, w, h) in enumerate(char_strokes):
        if k >= len(cells):
            break
        cx0, cy0, ccw, cch = cells[k]
        scale_jit = 1.0 + rng.uniform(-0.25, 0.15) * (1.0 if jitter else 0.0)
        s = min(ccw / max(w, 1), cch / max(h, 1)) * fill * scale_jit
        # セル内の余白ぶんをランダムに振り分け (jitter=0 なら中央)
        free_x = max(0.0, ccw - w * s)
        free_y = max(0.0, cch - h * s)
        fx = rng.uniform(0, 1) if jitter else 0.5
        fy = rng.uniform(0, 1) if jitter else 0.5
        ox = cx0 + free_x * (fx if jitter else 0.5)
        oy = cy0 + free_y * (fy if jitter else 0.5)
        for st in strokes:
            placed.append([(x * s + ox, y * s + oy) for (x, y) in st])
    return placed


def scatter_companions(sheet_img: Image.Image,
                       input_strokes: List[Stroke],
                       input_bbox: Tuple[int, int, int, int],
                       canvas_wh: Tuple[int, int],
                       *, cols: int = 3, rows: int = 7,
                       fill: float = 0.8, seed: int = 7,
                       jitter: float = 0.0,
                       vectorizer=None) -> tuple:
    """高レベル API: sheet を分割→輪郭化→散布し、
    (合成 strokes, n_chars_placed, n_chars_found, n_cells) を返す。

    jitter>0 で位置・スケールをランダムに揺らす (グリッド感を崩す)。
    vectorizer 未指定なら canny mode で生成 (塗りシルエット→輪郭線)。
    """
    if vectorizer is None:
        from modules.vectorizer import Vectorizer, load_binarize_config
        vectorizer = Vectorizer(gen_line_mode="canny", **load_binarize_config())
    chars = split_characters(sheet_img)
    char_strokes = vectorize_characters(chars, vectorizer)
    cells = free_cells(canvas_wh, input_bbox, cols=cols, rows=rows)
    scattered = scatter_strokes(char_strokes, cells, fill=fill, seed=seed,
                                jitter=jitter)
    n_placed = min(len(char_strokes), len(cells))
    combined = list(input_strokes) + scattered
    return combined, n_placed, len(char_strokes), len(cells)
