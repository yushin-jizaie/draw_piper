"""SDXL生成画像のベクトル化前処理実験スクリプト

目的: SDXL Turbo + ControlNet が出した「写実的鉛筆画」から、
ロボットが描ける「線画」を後処理で抽出できるかを評価する。

このスクリプトは設計v0.4 のフロー検証のためだけのもので、
modules/ には入れない。結果次第で vectorizer.py に取り込む。

入力:
  ~/draw_piper/logs/sdxl_output_*.png (全部試す)

出力:
  ~/draw_piper/logs/canny_experiment/<元画像名>/<手法名>.png
  ~/draw_piper/logs/canny_experiment/summary.md (各画像のストローク数推定)

評価基準:
  1. 白地に黒線になっているか (二値画像)
  2. 線の総量がロボットで描ける範囲か (連結成分の数 or 総ピクセル数)
  3. 主要な輪郭が残っているか (目視)
"""

import logging
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np


# ----- パス設定 -----
HOME = Path.home()
LOG_DIR = HOME / "draw_piper" / "logs"
OUT_DIR = LOG_DIR / "canny_experiment"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ----- ロギング -----
log_file = OUT_DIR / f"experiment_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


# ----- 後処理メソッド -----

def method_canny_low(img_gray: np.ndarray) -> np.ndarray:
    """低閾値Canny。多めに線を拾う。"""
    edges = cv2.Canny(img_gray, 30, 100)
    # 反転して白地黒線に
    return 255 - edges


def method_canny_mid(img_gray: np.ndarray) -> np.ndarray:
    """中閾値Canny。標準的設定。"""
    edges = cv2.Canny(img_gray, 50, 150)
    return 255 - edges


def method_canny_high(img_gray: np.ndarray) -> np.ndarray:
    """高閾値Canny。主要輪郭だけ。"""
    edges = cv2.Canny(img_gray, 100, 200)
    return 255 - edges


def method_canny_blur_high(img_gray: np.ndarray) -> np.ndarray:
    """ガウスぼかし→高閾値Canny。細部を捨てる。"""
    blurred = cv2.GaussianBlur(img_gray, (5, 5), 1.5)
    edges = cv2.Canny(blurred, 80, 180)
    return 255 - edges


def method_canny_strong_blur(img_gray: np.ndarray) -> np.ndarray:
    """強ぼかし→中閾値Canny。さらに細部を捨てる。"""
    blurred = cv2.GaussianBlur(img_gray, (9, 9), 3.0)
    edges = cv2.Canny(blurred, 50, 150)
    return 255 - edges


def method_adaptive_thresh(img_gray: np.ndarray) -> np.ndarray:
    """適応的二値化。陰影を残さず線だけ抽出を狙う。"""
    binary = cv2.adaptiveThreshold(
        img_gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        blockSize=11, C=5,
    )
    return binary


def method_otsu(img_gray: np.ndarray) -> np.ndarray:
    """Otsu二値化。自動閾値。"""
    _, binary = cv2.threshold(img_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return binary


def method_canny_dilate(img_gray: np.ndarray) -> np.ndarray:
    """Canny→膨張で線をつなぐ→反転。短い切れ端を統合。"""
    edges = cv2.Canny(img_gray, 50, 150)
    kernel = np.ones((2, 2), np.uint8)
    dilated = cv2.dilate(edges, kernel, iterations=1)
    return 255 - dilated


METHODS = {
    "01_canny_low": method_canny_low,
    "02_canny_mid": method_canny_mid,
    "03_canny_high": method_canny_high,
    "04_canny_blur_high": method_canny_blur_high,
    "05_canny_strong_blur": method_canny_strong_blur,
    "06_adaptive_thresh": method_adaptive_thresh,
    "07_otsu": method_otsu,
    "08_canny_dilate": method_canny_dilate,
}


# ----- メトリクス -----

def estimate_stroke_metric(binary_img: np.ndarray) -> dict:
    """
    二値画像からストローク量を推定。
    入力は「白地に黒線」を想定 (黒線が0、白地が255)。
    """
    # 黒線部分(線)を抽出: 値が小さいピクセル
    line_mask = (binary_img < 128).astype(np.uint8)

    total_pixels = line_mask.size
    line_pixels = int(line_mask.sum())
    line_ratio = line_pixels / total_pixels

    # 連結成分の数(線分のかたまり数の目安)
    num_components, _ = cv2.connectedComponents(line_mask, connectivity=8)
    # 背景成分を引く
    num_strokes_estimate = num_components - 1

    return {
        "line_pixel_ratio": line_ratio,
        "estimated_strokes": num_strokes_estimate,
    }


# ----- メイン -----

def process_image(input_path: Path) -> dict:
    """1枚の画像に対して全手法を適用、メトリクスを集計。"""
    log.info(f"Processing: {input_path.name}")
    img = cv2.imread(str(input_path))
    if img is None:
        log.warning(f"  Failed to read: {input_path}")
        return {}

    img_gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # 出力ディレクトリ
    sub_dir = OUT_DIR / input_path.stem
    sub_dir.mkdir(parents=True, exist_ok=True)

    # オリジナル(参照用)
    cv2.imwrite(str(sub_dir / "00_original.png"), img)

    results = {}
    for name, method in METHODS.items():
        try:
            out = method(img_gray)
            out_path = sub_dir / f"{name}.png"
            cv2.imwrite(str(out_path), out)
            metrics = estimate_stroke_metric(out)
            results[name] = metrics
            log.info(
                f"  {name}: line_ratio={metrics['line_pixel_ratio']:.3%}, "
                f"strokes={metrics['estimated_strokes']}"
            )
        except Exception as e:
            log.error(f"  {name} failed: {e}")
            results[name] = {"error": str(e)}

    return results


def main():
    log.info("=" * 60)
    log.info("SDXL output post-processing experiment")
    log.info(f"Output dir: {OUT_DIR}")
    log.info("=" * 60)

    # 全SDXL出力を対象に
    input_files = sorted(LOG_DIR.glob("sdxl_output_*.png"))
    if not input_files:
        log.error("No SDXL output images found in logs/")
        return

    log.info(f"Found {len(input_files)} input images")

    # 全結果を集計
    all_results = {}
    for input_path in input_files:
        all_results[input_path.name] = process_image(input_path)

    # サマリー Markdown 出力
    summary_path = OUT_DIR / "summary.md"
    with summary_path.open("w") as f:
        f.write("# Canny experiment summary\n\n")
        f.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write("各画像 × 各手法での「線ピクセル率」と「推定ストローク数」\n\n")
        f.write("- **line_pixel_ratio**: 全ピクセル中の線部分の割合\n")
        f.write("  - 1% 前後 = 線画らしい\n")
        f.write("  - 10% 超 = ベタ塗りに近い (ダメ)\n")
        f.write("  - 0.1% 未満 = スカスカ (情報不足)\n")
        f.write("- **estimated_strokes**: 連結成分数 (線のかたまり数)\n")
        f.write("  - 50〜300: ロボットで2分以内に描ける範囲\n")
        f.write("  - 1000+: 多すぎ\n\n")

        for img_name, methods in all_results.items():
            f.write(f"## {img_name}\n\n")
            f.write("| 手法 | line_pixel_ratio | estimated_strokes |\n")
            f.write("|---|---|---|\n")
            for method_name, metrics in methods.items():
                if "error" in metrics:
                    f.write(f"| {method_name} | ERROR | {metrics['error']} |\n")
                else:
                    f.write(
                        f"| {method_name} | "
                        f"{metrics['line_pixel_ratio']:.3%} | "
                        f"{metrics['estimated_strokes']} |\n"
                    )
            f.write("\n")

    log.info(f"Summary written to: {summary_path}")
    log.info("Done.")
    log.info("")
    log.info("Next: visually compare each canny_experiment/<image>/*.png")
    log.info("Pick the best (method, image) combination and verify it's vectorizable.")


if __name__ == "__main__":
    main()
