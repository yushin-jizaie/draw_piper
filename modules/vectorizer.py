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
DEFAULT_MIN_PIXELS = 50
DEFAULT_CLOSE_KSIZE = 3
DEFAULT_APPROX_EPSILON = 2.0
DEFAULT_MIN_LENGTH = 10


log = logging.getLogger(__name__)


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


def _binarize_user(user_gray: np.ndarray) -> np.ndarray:
    """ユーザの絵 (median 合成キャプチャ) を二値化。

    線=255 のマスクを返す (差分計算で「除外エリア」として使う)。
    ユーザの絵は黒線が単純なので Otsu で十分。
    """
    _, binary = cv2.threshold(
        user_gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )
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


def _vectorize_polylines(
    mask: np.ndarray, epsilon: float, min_length: int
) -> List[np.ndarray]:
    """findContours → approxPolyDP でポリラインに変換し、点数 < min_length を捨てる。

    返り値は (N, 2) の int 座標 ndarray のリスト ((x, y) 順、画像座標)。
    """
    contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    polylines: List[np.ndarray] = []
    for cnt in contours:
        if len(cnt) < 2:
            continue
        approx = cv2.approxPolyDP(cnt, epsilon, closed=False)
        if len(approx) < min_length:
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
        verbose: bool = False,
    ):
        self.canny_blur_ksize = canny_blur_ksize
        self.canny_blur_sigma = canny_blur_sigma
        self.canny_thresh_low = canny_thresh_low
        self.canny_thresh_high = canny_thresh_high
        self.diff_dilate_ksize = diff_dilate_ksize
        self.min_pixels = min_pixels
        self.close_ksize = close_ksize
        self.approx_epsilon = approx_epsilon
        self.min_length = min_length
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

        # ステップ 1: 生成画像を Canny strong_blur で線画化
        gen_mask = _canny_strong_blur(
            gen_gray,
            self.canny_blur_ksize,
            self.canny_blur_sigma,
            self.canny_thresh_low,
            self.canny_thresh_high,
        )
        diagnostics["stage1_canny_pixels"] = int(gen_mask.sum() // 255)
        if debug_path is not None:
            cv2.imwrite(
                str(debug_path / "01_generated_canny.png"),
                _to_white_bg_black_lines(gen_mask),
            )
        if self.verbose:
            log.info(
                "[vectorizer] stage1 Canny: line_px=%d (%.2f%%)",
                diagnostics["stage1_canny_pixels"],
                diagnostics["stage1_canny_pixels"] / (h * w) * 100,
            )

        # ステップ 2: 差分検出 (user_image があるとき)
        if user_gray is not None:
            user_mask = _binarize_user(user_gray)
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

        # ステップ 6: approxPolyDP ポリライン化 + 長さフィルタ
        polylines_np = _vectorize_polylines(
            skel, self.approx_epsilon, self.min_length
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
