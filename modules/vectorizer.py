"""Vectorization (v0.4.1).

設計 v0.4.1 (Step C 完了 + VLM/SDXL つなぎこみ完了) で確定したパイプライン:

  SDXL Turbo の出力 (写実画) + median 合成キャプチャ (ユーザの絵)
    -> [1] Canny strong_blur で線画化      (GaussianBlur σ=3.0 + Canny 50/150)
    -> [2] 差分検出                         (ユーザマスクを dilate して AND NOT)
    -> [3] 連結成分フィルタ                 (min_pixels=50)
    -> [4] モルフォロジー CLOSE             (kernel=3)
    -> [5] 細線化                           (skimage skeletonize)
    -> [6] approxPolyDP ポリライン化 + 長さフィルタ
                                            (epsilon=2.0, min_length=10-15)
    -> List[Stroke_px]                       ← Vectorizer.vectorize() の出力
    -> [7] mm 単位への座標変換              ← Vectorizer.vectorize_to_panel()
    -> List[Stroke_uv_mm]                    (PanelFrame の uv 座標)

Step C で確定した推奨パラメータをデフォルト値として持つ。

See:
  scripts/experiment_canny.py        — Canny 8 手法比較
  scripts/experiment_diff.py         — 差分 dilate kernel 比較
  scripts/experiment_vectorize.py    — 連結成分/min_length 比較
  docs/20260523_1530_step_c_completion.md
  docs/20260523_1640_vlm_to_image_integration.md
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple, Union

import cv2
import numpy as np
from PIL import Image


# ----- 型エイリアス --------------------------------------------------------

ImageLike = Union[Image.Image, str, Path, np.ndarray]
StrokePx = List[Tuple[float, float]]      # [(x_px, y_px), ...] 画像座標
StrokeMm = List[Tuple[float, float]]      # [(u_mm, v_mm), ...] パネル座標
PanelFrameLike = "PanelFrame"  # modules.robot.PanelFrame  (lazy)


# ----- デフォルトパラメータ (Step C 完了時の確定値) ------------------------

DEFAULT_CANNY_BLUR_KSIZE = 9
DEFAULT_CANNY_BLUR_SIGMA = 3.0
DEFAULT_CANNY_THRESH_LOW = 50
DEFAULT_CANNY_THRESH_HIGH = 150
DEFAULT_DIFF_DILATE_KSIZE = 21
# 2026-05-27: 細部 (顔の目・口・鼻 等) が大量に削除される問題への対処。
# min_pixels 50 → 20、 min_length 10 → 5、 approx_epsilon 2.0 → 1.0
# で短い曲線を保持しやすく。 旧値は vectorizer_config.yaml で上書き可。
# 2026-05-30 (robot 描画向け再調整): 細かいストローク量産を抑制。
# DEFAULT_MIN_PIXELS 20 → 40 (小さい連結成分のゴミ除外を強化)
# DEFAULT_MIN_LENGTH 5 → 15 (短い stroke を除外、 robot 描画時間短縮)
# DEFAULT_APPROX_EPSILON 1.0 → 2.0 (polyline 簡略化を強める、 細かい曲がりを直線化)
DEFAULT_MIN_PIXELS = 40
DEFAULT_CLOSE_KSIZE = 3
DEFAULT_APPROX_EPSILON = 2.0
DEFAULT_MIN_LENGTH = 15
# 中心線 skeleton の断片を端点でつなぐ最大ギャップ (px)。 0 で無効。
# 断片化 (極短 stroke 過多 / stroke 数過多) を緩和し Frida 適合度を上げる。
# 20: 木(104→75本)など過剰検出を warn0 にしつつ、 顔の近接特徴は誤結合せず保持
# (実測でバランス確認)。
DEFAULT_MERGE_GAP = 20


log = logging.getLogger(__name__)


# ----- Binarize 設定 yaml -------------------------------------------------

DEFAULT_BINARIZE_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent
    / "calibration" / "vectorizer_config.yaml"
)


def load_binarize_config(path: Optional[Path] = None) -> dict:
    """yaml から binarize + filter 設定を読み込む。 戻り値は
    Vectorizer.__init__ に **kwargs で渡せる dict。 ファイル無し /
    読み込み失敗時は 既定値を返す。
    """
    cfg_path = Path(path) if path else DEFAULT_BINARIZE_CONFIG_PATH
    defaults = {
        "binarize_method": "adaptive",
        "adaptive_block_size": 51,
        "adaptive_c": 10,
        "fixed_threshold": 128,
        # filter 系 (低くすると細部が残る)
        "min_pixels": DEFAULT_MIN_PIXELS,
        "min_length": DEFAULT_MIN_LENGTH,
        "approx_epsilon": DEFAULT_APPROX_EPSILON,
        "close_ksize": DEFAULT_CLOSE_KSIZE,
        "diff_dilate_ksize": DEFAULT_DIFF_DILATE_KSIZE,
    }
    if not cfg_path.exists():
        return defaults
    try:
        import yaml as _yaml
        with open(cfg_path) as f:
            data = _yaml.safe_load(f) or {}
    except Exception:
        return defaults
    bz = data.get("binarize") or {}
    fl = data.get("filter") or {}
    out = dict(defaults)
    if "method" in bz:
        out["binarize_method"] = str(bz["method"])
    if "adaptive_block_size" in bz:
        out["adaptive_block_size"] = int(bz["adaptive_block_size"])
    if "adaptive_c" in bz:
        out["adaptive_c"] = int(bz["adaptive_c"])
    if "fixed_threshold" in bz:
        out["fixed_threshold"] = int(bz["fixed_threshold"])
    if "min_pixels" in fl:
        out["min_pixels"] = int(fl["min_pixels"])
    if "min_length" in fl:
        out["min_length"] = int(fl["min_length"])
    if "approx_epsilon" in fl:
        out["approx_epsilon"] = float(fl["approx_epsilon"])
    if "close_ksize" in fl:
        out["close_ksize"] = int(fl["close_ksize"])
    if "diff_dilate_ksize" in fl:
        out["diff_dilate_ksize"] = int(fl["diff_dilate_ksize"])
    return out


def save_binarize_config(method: str,
                          adaptive_block_size: int,
                          adaptive_c: int,
                          fixed_threshold: int,
                          path: Optional[Path] = None,
                          min_pixels: Optional[int] = None,
                          min_length: Optional[int] = None,
                          approx_epsilon: Optional[float] = None,
                          close_ksize: Optional[int] = None,
                          diff_dilate_ksize: Optional[int] = None,
                          ) -> Path:
    """binarize + filter 設定を yaml に保存。
    filter 系 (min_pixels 等) は None のとき既存値を保持。
    """
    cfg_path = Path(path) if path else DEFAULT_BINARIZE_CONFIG_PATH
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    import yaml as _yaml
    # 既存読み込み (filter 部分の保持用)
    existing = {}
    if cfg_path.exists():
        try:
            with open(cfg_path) as f:
                existing = _yaml.safe_load(f) or {}
        except Exception:
            existing = {}
    old_filter = existing.get("filter") or {}
    new_filter = dict(old_filter)
    if min_pixels is not None:
        new_filter["min_pixels"] = int(min_pixels)
    if min_length is not None:
        new_filter["min_length"] = int(min_length)
    if approx_epsilon is not None:
        new_filter["approx_epsilon"] = float(approx_epsilon)
    if close_ksize is not None:
        new_filter["close_ksize"] = int(close_ksize)
    if diff_dilate_ksize is not None:
        new_filter["diff_dilate_ksize"] = int(diff_dilate_ksize)
    data = {
        "binarize": {
            "method": method,
            "adaptive_block_size": int(adaptive_block_size),
            "adaptive_c": int(adaptive_c),
            "fixed_threshold": int(fixed_threshold),
        }
    }
    if new_filter:
        data["filter"] = new_filter
    with open(cfg_path, "w") as f:
        _yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
    return cfg_path


# ----- 補助関数 ------------------------------------------------------------

def _normalize_image_gray(image: ImageLike) -> np.ndarray:
    """画像を 2D uint8 グレースケール ndarray に揃える。"""
    if isinstance(image, np.ndarray):
        arr = image
        if arr.ndim == 3:
            arr = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY) if arr.shape[2] >= 3 else arr[..., 0]
        if arr.dtype != np.uint8:
            arr = np.clip(arr, 0, 255).astype(np.uint8)
        return arr
    if isinstance(image, Image.Image):
        return np.array(image.convert("L"), dtype=np.uint8)
    if isinstance(image, (str, Path)):
        img = cv2.imread(str(image), cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise RuntimeError(f"failed to read image: {image}")
        return img
    raise TypeError(f"unsupported image type: {type(image)}")


def _to_white_bg_black_lines(line_mask: np.ndarray) -> np.ndarray:
    """線=255 のマスクを 「白地黒線」 のビジュアル画像に変換 (保存用)。"""
    return 255 - line_mask


# ----- データクラス --------------------------------------------------------

@dataclass
class VectorizeResult:
    """Vectorizer.vectorize() の戻り値。

    strokes はピクセル単位のポリライン群。strokes_mm が None なら mm 変換は
    していない (Vectorizer.vectorize_to_panel() を使うと埋まる)。

    diagnostics は段階ごとの統計 (連結成分数、総点数、総長 px 等) を持つ。
    """

    strokes: List[StrokePx]
    image_shape: Tuple[int, int]              # (height, width) in pixels
    strokes_mm: Optional[List[StrokeMm]] = None
    diagnostics: dict = field(default_factory=dict)

    @property
    def n_strokes(self) -> int:
        return len(self.strokes)

    @property
    def n_points(self) -> int:
        return sum(len(s) for s in self.strokes)

    @property
    def total_length_px(self) -> float:
        total = 0.0
        for poly in self.strokes:
            for i in range(len(poly) - 1):
                dx = poly[i + 1][0] - poly[i][0]
                dy = poly[i + 1][1] - poly[i][1]
                total += float(np.hypot(dx, dy))
        return total


# ----- パイプライン構成要素 (内部関数) -------------------------------------

def _canny_strong_blur(
    img_gray: np.ndarray,
    blur_ksize: int,
    blur_sigma: float,
    thresh_low: int,
    thresh_high: int,
) -> np.ndarray:
    """SDXL 生成画像 (写実画) を線画化。線=255, 背景=0 のマスクを返す。

    Step C の Canny 実験で 05_canny_strong_blur が最良 (line_ratio 1-3%、
    estimated_strokes 100-300) だった構成。
    """
    blurred = cv2.GaussianBlur(img_gray, (blur_ksize, blur_ksize), blur_sigma)
    edges = cv2.Canny(blurred, thresh_low, thresh_high)
    return edges


def _binarize_gen_centerline(
    img_gray: np.ndarray,
    blur_ksize: int = 3,
    blur_sigma: float = 1.0,
) -> np.ndarray:
    """SDXL 生成画像の「線そのもの」を塗りつぶした mask を返す (線=255, 背景=0)。

    Canny (_canny_strong_blur) は線の **輪郭 (内側/外側エッジ)** を返すため、
    太い線が二重線 (アウトライン) になり、 skeletonize しても中心線にならない。
    こちらは暗い画素 (= インク) を Otsu 二値化で塗るので、 太い線も後段の
    skeletonize で **中心線 1 本** に細線化される。 「手前 (抽出の最初)」 で
    中心線化する方式。
    """
    blurred = cv2.GaussianBlur(img_gray, (blur_ksize, blur_ksize), blur_sigma)
    # 暗い画素 = 線。 白/淡色背景と黒線を Otsu で分離して塗りつぶす。
    _, mask = cv2.threshold(
        blurred, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    return mask.astype(np.uint8)


def _binarize_user(user_gray: np.ndarray,
                    method: str = "adaptive",
                    adaptive_block_size: int = 51,
                    adaptive_c: int = 10,
                    fixed_threshold: int = 128) -> np.ndarray:
    """ユーザの絵 (median 合成キャプチャ / 単発撮影) を二値化。
    線=255 のマスクを返す (差分計算で「除外エリア」として使う)。

    method:
      "otsu"     : 旧版互換。 全体 1 閾値 Otsu (照明グラデに弱い)
      "adaptive" : cv2.adaptiveThreshold (局所平均、 既定)。
                   照明不均一・vignetting に強い。 block_size は奇数。
      "fixed"    : 固定閾値 (キャリブで使う絶対値指定)
    """
    if method == "otsu":
        _, binary = cv2.threshold(
            user_gray, 0, 255,
            cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        return binary
    if method == "fixed":
        _, binary = cv2.threshold(
            user_gray, int(fixed_threshold), 255,
            cv2.THRESH_BINARY_INV)
        return binary
    # adaptive (既定)
    block = int(adaptive_block_size)
    if block < 3:
        block = 3
    if block % 2 == 0:
        block += 1
    binary = cv2.adaptiveThreshold(
        user_gray, 255,
        cv2.ADAPTIVE_THRESH_MEAN_C,
        cv2.THRESH_BINARY_INV,
        block, int(adaptive_c))
    return binary


def _compute_diff(
    generated_mask: np.ndarray,
    user_mask: np.ndarray,
    dilate_ksize: int,
) -> np.ndarray:
    """ユーザマスクを dilate して generated から引く。

    「ユーザがすでに描いた部分の近傍」を除外し、ロボットが描き足すべき
    部分だけを残す。Step C の実験で k=21 以上が安定。
    """
    kernel = np.ones((dilate_ksize, dilate_ksize), np.uint8)
    user_dilated = cv2.dilate(user_mask, kernel, iterations=1)
    diff_mask = cv2.bitwise_and(generated_mask, cv2.bitwise_not(user_dilated))
    return diff_mask


def _filter_small_components(mask: np.ndarray, min_pixels: int) -> Tuple[np.ndarray, int]:
    """連結成分フィルタ。min_pixels 未満の成分を除去。"""
    num, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    keep = np.zeros_like(mask)
    kept_count = 0
    for i in range(1, num):
        area = stats[i, cv2.CC_STAT_AREA]
        if area >= min_pixels:
            keep[labels == i] = 255
            kept_count += 1
    return keep, kept_count


def _morphology_close(mask: np.ndarray, kernel_size: int) -> np.ndarray:
    """モルフォロジー CLOSE で線の切れ目を埋める。"""
    if kernel_size <= 1:
        return mask
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (kernel_size, kernel_size)
    )
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)


def _skeletonize(mask: np.ndarray) -> np.ndarray:
    """細線化 (1ピクセル幅へ)。scikit-image を使う。"""
    try:
        from skimage.morphology import skeletonize as sk_skel
    except ImportError as e:
        raise ImportError(
            "scikit-image is required for Vectorizer. "
            "Install with: pip install scikit-image"
        ) from e
    binary = mask > 0
    skel = sk_skel(binary)
    return (skel * 255).astype(np.uint8)


def _trace_skeleton(mask: np.ndarray) -> List[np.ndarray]:
    """1px-wide skeleton mask を 中央線 polyline の列に変換 (graph trace)。

    cv2.findContours は 1px 線の 「境界」 を返してしまい forward+backward
    の racetrack が出力されるので、 skeleton 自体を 8-connectivity の
    graph として 端点 → 端点 (or 端点 → 分岐点) で trace する。

    閉ループ (端点なし) は任意のピクセルから 1 周 trace。

    Returns
    -------
    list of (N, 2) int ndarray、 各要素は [(x, y), ...] 順。
    """
    if mask.size == 0:
        return []
    binary = (mask > 0).astype(np.uint8)
    h, w = binary.shape

    # 各 pixel の 8-neighbor count
    nb = np.zeros_like(binary, dtype=np.int32)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy == 0 and dx == 0:
                continue
            # 境界処理しつつ shift
            ys0, ys1 = max(0, dy), min(h, h + dy)
            xs0, xs1 = max(0, dx), min(w, w + dx)
            yt0, yt1 = max(0, -dy), min(h, h - dy)
            xt0, xt1 = max(0, -dx), min(w, w - dx)
            nb[yt0:yt1, xt0:xt1] += binary[ys0:ys1, xs0:xs1]
    nb *= binary  # 非 skeleton pixel は count 0

    visited = np.zeros_like(binary, dtype=bool)
    polylines: List[np.ndarray] = []

    def _walk_from(sy, sx):
        """sy/sx から trace。 分岐点 / 既訪問 / 範囲外で停止。"""
        poly = [(int(sx), int(sy))]
        visited[sy, sx] = True
        cy, cx = sy, sx
        while True:
            best = None
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dy == 0 and dx == 0:
                        continue
                    ny, nx = cy + dy, cx + dx
                    if not (0 <= ny < h and 0 <= nx < w):
                        continue
                    if binary[ny, nx] == 0 or visited[ny, nx]:
                        continue
                    # 分岐点 (count >= 3) は cell 1 個分だけ含めて停止
                    best = (ny, nx, nb[ny, nx] >= 3)
                    if not best[2]:
                        break
                if best is not None and not best[2]:
                    break
            if best is None:
                break
            ny, nx, is_junction = best
            poly.append((int(nx), int(ny)))
            visited[ny, nx] = True
            if is_junction:
                break
            cy, cx = ny, nx
        return poly

    # 1. 端点 (count == 1) から trace
    endpoints = np.argwhere((binary == 1) & (nb == 1))
    for y, x in endpoints:
        if not visited[y, x]:
            poly = _walk_from(int(y), int(x))
            if len(poly) >= 2:
                polylines.append(
                    np.array(poly, dtype=np.int32))

    # 2. 残った pixel (= 端点無し閉ループ or 分岐点) を消化
    while True:
        unvisited = np.argwhere((binary == 1) & (~visited))
        if len(unvisited) == 0:
            break
        y, x = unvisited[0]
        poly = _walk_from(int(y), int(x))
        if len(poly) >= 2:
            polylines.append(np.array(poly, dtype=np.int32))

    return polylines


def _merge_polylines(
    polylines: List[np.ndarray], max_gap: float
) -> List[np.ndarray]:
    """端点が max_gap px 以内の polyline 同士を貪欲に連結して 1 本にまとめる。

    中心線 skeleton は途切れやすく、 細かい断片が大量に出る。 それを端点で
    つなぎ直すと、 stroke 数が減り 1 本あたりの点数が増えて Frida 観点
    (極短 stroke 過多 / 平均点数不足 / stroke 数過多) が改善する。
    各 polyline は両端どちらでも接続でき、 必要なら反転する。
    """
    if max_gap <= 0 or len(polylines) <= 1:
        return polylines

    def _d(a, b) -> float:
        return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))

    polys = [p for p in polylines if len(p) > 0]
    used = [False] * len(polys)
    out: List[np.ndarray] = []
    for i in range(len(polys)):
        if used[i]:
            continue
        used[i] = True
        chain = [tuple(pt) for pt in polys[i]]
        extended = True
        while extended:
            extended = False
            head, tail = chain[0], chain[-1]
            best = None  # (j, position, segment_points, dist)
            for j in range(len(polys)):
                if used[j]:
                    continue
                seg = [tuple(pt) for pt in polys[j]]
                s, e = seg[0], seg[-1]
                # tail に append (s 近い=順, e 近い=逆)
                for d, where, sg in (
                    (_d(tail, s), "tail", seg),
                    (_d(tail, e), "tail", seg[::-1]),
                    (_d(head, e), "head", seg),
                    (_d(head, s), "head", seg[::-1]),
                ):
                    if d <= max_gap and (best is None or d < best[3]):
                        best = (j, where, sg, d)
            if best is not None:
                j, where, sg, _dd = best
                chain = chain + sg if where == "tail" else sg + chain
                used[j] = True
                extended = True
        out.append(np.array(chain, dtype=np.int32))
    return out


def _vectorize_polylines(
    mask: np.ndarray, epsilon: float, min_length: int, merge_gap: float = 0.0
) -> List[np.ndarray]:
    """skeleton trace → approxPolyDP でポリラインに変換し、長さ < min_length を捨てる。

    返り値は (N, 2) の int 座標 ndarray のリスト ((x, y) 順、画像座標)。
    2026-05-31 修正: 旧版は cv2.findContours で skeleton の境界 (racetrack)
    を取ってしまい forward+backward の二度書き polyline を出していた。
    skeleton を graph として trace する _trace_skeleton に置換。
    2026-05-31 修正(2): min_length フィルタを approxPolyDP の「前」、生トレース
    点数 (≒弧長 px) に対して適用する。 approxPolyDP 後の頂点数で足切りすると、
    滑らかな曲線が少数頂点 (<15) に簡略化されて全部 drop され 0 strokes になる
    回帰があった (skeleton trace 化で頂点が正しく減ったため顕在化)。
    """
    raw_polylines = _trace_skeleton(mask)
    # 断片を端点で連結 (Frida 観点: 極短 stroke 過多 / stroke 数過多を緩和)。
    # min_length / approxPolyDP の前に行うことで、 連結後の長い弧は点数も増える。
    if merge_gap and merge_gap > 0:
        raw_polylines = _merge_polylines(raw_polylines, merge_gap)
    polylines: List[np.ndarray] = []
    for poly in raw_polylines:
        # 生トレース点数 = 1px skeleton をたどった点数 ≒ 弧長(px)。
        # ここで長さフィルタをかける (簡略化後の頂点数ではない)。
        if len(poly) < max(2, min_length):
            continue
        approx = cv2.approxPolyDP(
            poly.reshape(-1, 1, 2).astype(np.int32),
            epsilon, closed=False)
        if len(approx) < 2:
            continue
        polylines.append(approx.reshape(-1, 2))
    return polylines


def _polylines_to_strokes(polylines: List[np.ndarray]) -> List[StrokePx]:
    """ndarray のリストを純粋な Python のタプル列に変換 (シリアライズ容易性)。"""
    return [[(float(p[0]), float(p[1])) for p in poly] for poly in polylines]


# ----- メインクラス --------------------------------------------------------

class Vectorizer:
    """SDXL 生成画像 → 描画ストローク列の変換器。

    8 ステップパイプライン (Step C 完了時の確定構成) を 1 クラスにまとめる。
    ステートを持たない (重みもキャッシュも不要) ので load/unload は不要。

    使い方:
        v = Vectorizer()
        result = v.vectorize(generated_image, user_image)
        for stroke in result.strokes:
            # stroke = [(x_px, y_px), ...]
            ...

    mm 変換まで一気に:
        result = v.vectorize_to_panel(generated_image, user_image, panel)
        for stroke_uv in result.strokes_mm:
            # stroke_uv = [(u_mm, v_mm), ...]
            ...

    user_image=None の場合は差分検出をスキップして、生成画像を純粋に
    ベクトル化する (本番運用前のデバッグや動作確認用)。
    """

    def __init__(
        self,
        canny_blur_ksize: int = DEFAULT_CANNY_BLUR_KSIZE,
        canny_blur_sigma: float = DEFAULT_CANNY_BLUR_SIGMA,
        canny_thresh_low: int = DEFAULT_CANNY_THRESH_LOW,
        canny_thresh_high: int = DEFAULT_CANNY_THRESH_HIGH,
        diff_dilate_ksize: int = DEFAULT_DIFF_DILATE_KSIZE,
        min_pixels: int = DEFAULT_MIN_PIXELS,
        close_ksize: int = DEFAULT_CLOSE_KSIZE,
        approx_epsilon: float = DEFAULT_APPROX_EPSILON,
        min_length: int = DEFAULT_MIN_LENGTH,
        merge_gap: float = DEFAULT_MERGE_GAP,
        binarize_method: str = "adaptive",
        adaptive_block_size: int = 51,
        adaptive_c: int = 10,
        fixed_threshold: int = 128,
        gen_line_mode: str = "binarize",
        verbose: bool = False,
    ):
        # 生成画像の線抽出方式:
        #   "binarize" (既定) = 線そのものを塗る → skeletonize で中心線 1 本。
        #                       太い線が二重 (アウトライン) にならない。
        #   "canny"           = 旧来のエッジ検出 (輪郭 2 本)。 後方互換用。
        self.gen_line_mode = str(gen_line_mode)
        self.canny_blur_ksize = canny_blur_ksize
        self.canny_blur_sigma = canny_blur_sigma
        self.canny_thresh_low = canny_thresh_low
        self.canny_thresh_high = canny_thresh_high
        self.diff_dilate_ksize = diff_dilate_ksize
        self.min_pixels = min_pixels
        self.close_ksize = close_ksize
        self.approx_epsilon = approx_epsilon
        self.min_length = min_length
        self.merge_gap = merge_gap
        self.binarize_method = binarize_method
        self.adaptive_block_size = adaptive_block_size
        self.adaptive_c = adaptive_c
        self.fixed_threshold = fixed_threshold
        self.verbose = verbose

    # ------ パブリック API --------------------------------------------------

    def vectorize(
        self,
        generated_image: ImageLike,
        user_image: Optional[ImageLike] = None,
        *,
        debug_dir: Optional[Union[str, Path]] = None,
    ) -> VectorizeResult:
        """生成画像 (+ ユーザ画像) を pixel 単位のストローク列に変換する。

        Parameters
        ----------
        generated_image : ImageLike
            SDXL Turbo + MistoLine の出力 (写実画)。
        user_image : ImageLike, optional
            median 合成キャプチャ (ユーザの絵)。指定すれば差分検出を行う。
            None の場合は差分スキップで純粋な生成画像ベクトル化に退化する。
        debug_dir : Path, optional
            指定すると各ステージの中間画像 (PNG) と diagnostics.json を保存する。
            本番運用ではデフォルト None。

        Returns
        -------
        VectorizeResult
            strokes (pixel 単位)、image_shape、diagnostics を持つ。
        """
        t_start = time.time()

        # ステップ 0: 画像読み込みと前処理
        gen_gray = _normalize_image_gray(generated_image)
        h, w = gen_gray.shape
        diagnostics: dict = {"image_shape": (h, w), "params": self._params_dict()}

        debug_path: Optional[Path] = None
        if debug_dir is not None:
            debug_path = Path(debug_dir)
            debug_path.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(debug_path / "00_generated_input.png"), gen_gray)

        user_gray: Optional[np.ndarray] = None
        if user_image is not None:
            user_gray = _normalize_image_gray(user_image)
            if user_gray.shape != gen_gray.shape:
                user_gray = cv2.resize(
                    user_gray, (w, h), interpolation=cv2.INTER_LANCZOS4
                )
            if debug_path is not None:
                cv2.imwrite(str(debug_path / "00_user_input.png"), user_gray)

        # ステップ 1: 生成画像を線画化。
        #   binarize (既定): 線そのものを塗る → 後段 skeletonize で中心線 1 本。
        #   canny          : エッジ検出 (輪郭 2 本、 太線は二重線になる)。
        if self.gen_line_mode == "canny":
            gen_mask = _canny_strong_blur(
                gen_gray,
                self.canny_blur_ksize,
                self.canny_blur_sigma,
                self.canny_thresh_low,
                self.canny_thresh_high,
            )
            stage1_label = "Canny"
        else:
            gen_mask = _binarize_gen_centerline(
                gen_gray,
                self.canny_blur_ksize,
                self.canny_blur_sigma,
            )
            stage1_label = "binarize(centerline)"
        diagnostics["stage1_canny_pixels"] = int(gen_mask.sum() // 255)
        if debug_path is not None:
            cv2.imwrite(
                str(debug_path / "01_generated_canny.png"),
                _to_white_bg_black_lines(gen_mask),
            )
        if self.verbose:
            px = diagnostics["stage1_canny_pixels"]
            log.info(
                "[vectorizer] stage1 %s: line_px=%d (%.2f%%)",
                stage1_label, px, px / (h * w) * 100,
            )

        # ステップ 2: 差分検出 (user_image があるとき)
        if user_gray is not None:
            user_mask = _binarize_user(
                user_gray,
                method=self.binarize_method,
                adaptive_block_size=self.adaptive_block_size,
                adaptive_c=self.adaptive_c,
                fixed_threshold=self.fixed_threshold)
            current_mask = _compute_diff(
                gen_mask, user_mask, self.diff_dilate_ksize
            )
            diagnostics["stage2_diff_pixels"] = int(current_mask.sum() // 255)
            if debug_path is not None:
                cv2.imwrite(
                    str(debug_path / "02a_user_binary.png"),
                    _to_white_bg_black_lines(user_mask),
                )
                cv2.imwrite(
                    str(debug_path / "02b_diff.png"),
                    _to_white_bg_black_lines(current_mask),
                )
            if self.verbose:
                log.info(
                    "[vectorizer] stage2 diff (k=%d): line_px=%d",
                    self.diff_dilate_ksize,
                    diagnostics["stage2_diff_pixels"],
                )
        else:
            current_mask = gen_mask
            diagnostics["stage2_diff_pixels"] = None  # skipped
            if self.verbose:
                log.info("[vectorizer] stage2 diff: skipped (user_image=None)")

        # ステップ 3: 連結成分フィルタ
        current_mask, kept_components = _filter_small_components(
            current_mask, self.min_pixels
        )
        diagnostics["stage3_components_after_filter"] = kept_components
        if debug_path is not None:
            cv2.imwrite(
                str(debug_path / "03_filtered.png"),
                _to_white_bg_black_lines(current_mask),
            )
        if self.verbose:
            log.info(
                "[vectorizer] stage3 filter (min_pixels=%d): kept %d components",
                self.min_pixels,
                kept_components,
            )

        # ステップ 4: モルフォロジー CLOSE
        current_mask = _morphology_close(current_mask, self.close_ksize)
        if debug_path is not None:
            cv2.imwrite(
                str(debug_path / "04_closed.png"),
                _to_white_bg_black_lines(current_mask),
            )

        # ステップ 5: 細線化
        skel = _skeletonize(current_mask)
        diagnostics["stage5_skeleton_pixels"] = int(skel.sum() // 255)
        if debug_path is not None:
            cv2.imwrite(
                str(debug_path / "05_skeleton.png"),
                _to_white_bg_black_lines(skel),
            )
        if self.verbose:
            log.info(
                "[vectorizer] stage5 skeleton: line_px=%d",
                diagnostics["stage5_skeleton_pixels"],
            )

        # ステップ 6: 断片連結 + approxPolyDP ポリライン化 + 長さフィルタ
        polylines_np = _vectorize_polylines(
            skel, self.approx_epsilon, self.min_length, self.merge_gap
        )
        strokes = _polylines_to_strokes(polylines_np)

        if debug_path is not None:
            visual = np.full((h, w), 255, dtype=np.uint8)
            for poly in polylines_np:
                for i in range(len(poly) - 1):
                    cv2.line(
                        visual,
                        tuple(poly[i].astype(int)),
                        tuple(poly[i + 1].astype(int)),
                        0,
                        thickness=2,
                    )
            cv2.imwrite(str(debug_path / "06_strokes.png"), visual)

        diagnostics["stage6_n_strokes"] = len(strokes)
        diagnostics["stage6_n_points"] = sum(len(s) for s in strokes)
        diagnostics["elapsed_s"] = time.time() - t_start

        if self.verbose:
            log.info(
                "[vectorizer] stage6 polylines (eps=%.1f, min_length=%d): "
                "%d strokes, %d points, elapsed=%.2fs",
                self.approx_epsilon,
                self.min_length,
                diagnostics["stage6_n_strokes"],
                diagnostics["stage6_n_points"],
                diagnostics["elapsed_s"],
            )

        result = VectorizeResult(
            strokes=strokes, image_shape=(h, w), diagnostics=diagnostics
        )

        if debug_path is not None:
            import json
            (debug_path / "diagnostics.json").write_text(
                json.dumps(
                    self._diagnostics_to_jsonable(diagnostics),
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

        return result

    def vectorize_to_panel(
        self,
        generated_image: ImageLike,
        user_image: Optional[ImageLike],
        panel: PanelFrameLike,
        *,
        debug_dir: Optional[Union[str, Path]] = None,
        clip_to_bounds: bool = True,
    ) -> VectorizeResult:
        """生成画像をベクトル化し、画像座標 (px) を panel uv 座標 (mm) に変換する。

        画像は panel の描画領域 size_mm = (width_u, height_v) 全体にマップ
        されると仮定する (実機キャリブ未完了時の暫定マッピング)。

        画像座標 (x_px, y_px) → パネル座標 (u_mm, v_mm):
            u_mm = x_px / image_width  * panel.size_mm[0]
            v_mm = (image_height - y_px) / image_height * panel.size_mm[1]
            (画像は左上原点・Y下向き、パネルは左下原点・v上向き)

        Parameters
        ----------
        panel : PanelFrame
            modules.robot.PanelFrame インスタンス。size_mm を用いる。
            未キャリブ (panel.calibrated == False) の場合は警告ログを出す。
        clip_to_bounds : bool
            True なら panel.size_mm の外に出る点を切り捨てる (1 ストローク内で
            外に出た時点で分割せず、そのままドロップ)。False なら範囲外でも残す。

        Returns
        -------
        VectorizeResult
            strokes (px) と strokes_mm (uv mm) の両方が埋まる。
        """
        result = self.vectorize(
            generated_image, user_image, debug_dir=debug_dir
        )

        if not getattr(panel, "calibrated", False):
            log.warning(
                "[vectorizer] panel frame is not calibrated; using placeholder "
                "size_mm=%s for px-to-mm mapping",
                tuple(panel.size_mm),
            )

        h, w = result.image_shape
        width_u, height_v = panel.size_mm[0], panel.size_mm[1]
        scale_u = width_u / w
        scale_v = height_v / h

        strokes_mm: List[StrokeMm] = []
        n_dropped = 0
        for stroke_px in result.strokes:
            stroke_uv: StrokeMm = []
            for (x_px, y_px) in stroke_px:
                u_mm = x_px * scale_u
                v_mm = (h - y_px) * scale_v  # Y 軸反転
                if clip_to_bounds and not panel.in_bounds(u_mm, v_mm):
                    continue
                stroke_uv.append((u_mm, v_mm))
            if len(stroke_uv) >= 2:
                strokes_mm.append(stroke_uv)
            else:
                n_dropped += 1

        result.strokes_mm = strokes_mm
        result.diagnostics["mm_scale_u_per_px"] = scale_u
        result.diagnostics["mm_scale_v_per_px"] = scale_v
        result.diagnostics["mm_n_strokes_dropped"] = n_dropped

        if self.verbose:
            log.info(
                "[vectorizer] mm convert: %d strokes (%d dropped, "
                "scale=%.4f x %.4f mm/px)",
                len(strokes_mm),
                n_dropped,
                scale_u,
                scale_v,
            )

        return result

    # ------ 内部ヘルパ ------------------------------------------------------

    def _params_dict(self) -> dict:
        return {
            "canny_blur_ksize": self.canny_blur_ksize,
            "canny_blur_sigma": self.canny_blur_sigma,
            "canny_thresh_low": self.canny_thresh_low,
            "canny_thresh_high": self.canny_thresh_high,
            "diff_dilate_ksize": self.diff_dilate_ksize,
            "min_pixels": self.min_pixels,
            "close_ksize": self.close_ksize,
            "approx_epsilon": self.approx_epsilon,
            "min_length": self.min_length,
        }

    @staticmethod
    def _diagnostics_to_jsonable(d: dict) -> dict:
        """tuple や None を JSON シリアライズ可能な形に整える。"""
        out = {}
        for k, v in d.items():
            if isinstance(v, tuple):
                out[k] = list(v)
            elif isinstance(v, dict):
                out[k] = Vectorizer._diagnostics_to_jsonable(v)
            else:
                out[k] = v
        return out


# ----- スモークテスト ------------------------------------------------------

def _build_synthetic_inputs(size: int = 512) -> Tuple[np.ndarray, np.ndarray]:
    """ユーザ画像 + 生成画像のダミーペアを作る。

    - ユーザ画像: 中心に円 + 目2つ (median 合成キャプチャを模す)
    - 生成画像:   同じ円・目 + 顔の輪郭 + 口 + 鼻 (SDXL が補完したと仮定)
    """
    user = np.full((size, size), 255, dtype=np.uint8)
    cx, cy = size // 2, size // 2
    cv2.circle(user, (cx, cy), size // 6, 0, thickness=2)         # 顔の中央枠
    cv2.circle(user, (cx - 30, cy - 20), 6, 0, thickness=-1)      # 左目
    cv2.circle(user, (cx + 30, cy - 20), 6, 0, thickness=-1)      # 右目

    gen = user.copy()
    cv2.circle(gen, (cx, cy), size // 4, 50, thickness=3)         # 顔の輪郭 (グレー)
    cv2.ellipse(gen, (cx, cy + 30), (25, 10), 0, 0, 180, 80, 2)   # 口
    cv2.line(gen, (cx, cy - 5), (cx, cy + 15), 80, 2)             # 鼻
    # SDXL 風のノイズ (細線が大量) を模す
    rng = np.random.default_rng(0)
    for _ in range(30):
        x0, y0 = rng.integers(0, size, size=2)
        x1 = int(np.clip(x0 + rng.integers(-10, 11), 0, size - 1))
        y1 = int(np.clip(y0 + rng.integers(-10, 11), 0, size - 1))
        cv2.line(gen, (int(x0), int(y0)), (x1, y1), 120, 1)

    return user, gen


def _smoke_test() -> None:
    """python3 -m modules.vectorizer または python3 vectorizer.py で実行可能。

    1. 合成入力でパイプラインを通す
    2. デフォルトパラメータでの strokes 数を表示
    3. PanelFrame を作って mm 変換まで通す
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    print("=== Vectorizer smoke test ===")
    user_img, gen_img = _build_synthetic_inputs(size=512)

    v = Vectorizer(verbose=True)

    # ケース 1: ユーザ画像あり (本番想定)
    print("\n--- case 1: with user_image ---")
    result = v.vectorize(gen_img, user_img)
    print(f"  n_strokes      = {result.n_strokes}")
    print(f"  n_points       = {result.n_points}")
    print(f"  total_length_px= {result.total_length_px:.1f}")
    print(f"  image_shape    = {result.image_shape}")
    print(f"  diagnostics    = {result.diagnostics}")

    # ケース 2: ユーザ画像なし (退化: 純粋な Canny ベクトル化)
    print("\n--- case 2: without user_image ---")
    result2 = v.vectorize(gen_img, None)
    print(f"  n_strokes = {result2.n_strokes}")
    print(f"  diff_pixels (should be None) = "
          f"{result2.diagnostics['stage2_diff_pixels']}")

    # ケース 3: mm 変換 (PanelFrame があれば実物を、なければ簡易フェイク)
    print("\n--- case 3: vectorize_to_panel ---")
    try:
        # try real PanelFrame from sibling robot.py
        from modules.robot import PanelFrame  # type: ignore
    except Exception:
        try:
            from robot import PanelFrame  # type: ignore
        except Exception:
            PanelFrame = None  # type: ignore

    if PanelFrame is None:
        print("  PanelFrame import failed; using fake panel")

        class _FakePanel:
            size_mm = (300.0, 200.0)
            calibrated = False

            def in_bounds(self, u, v):
                return 0.0 <= u <= self.size_mm[0] and 0.0 <= v <= self.size_mm[1]

        panel = _FakePanel()
    else:
        panel = PanelFrame(
            origin_mm=(350.0, -150.0, 100.0),
            u_axis=(0.0, 1.0, 0.0),
            v_axis=(0.0, 0.0, 1.0),
            normal=(-1.0, 0.0, 0.0),
            pen_orientation_deg=(0.0, 0.0, 0.0),
            size_mm=(300.0, 200.0),
            calibrated=False,
        )

    result3 = v.vectorize_to_panel(gen_img, user_img, panel)  # type: ignore[arg-type]
    print(f"  n_strokes (px)  = {result3.n_strokes}")
    print(f"  n_strokes (mm)  = {len(result3.strokes_mm or [])}")
    print(f"  n_dropped       = {result3.diagnostics['mm_n_strokes_dropped']}")
    if result3.strokes_mm:
        first = result3.strokes_mm[0]
        print(f"  first stroke[0..2 of {len(first)}] = {first[:2]}")

    # ケース 4: debug_dir に中間画像を吐く
    print("\n--- case 4: with debug_dir ---")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        result4 = v.vectorize(gen_img, user_img, debug_dir=td)
        produced = sorted(Path(td).iterdir())
        print(f"  debug files ({len(produced)}):")
        for p in produced:
            print(f"    {p.name}")
        print(f"  final strokes={result4.n_strokes}")

    print("\n=== smoke test OK ===")


if __name__ == "__main__":
    _smoke_test()
