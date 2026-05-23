"""線分間引き + ベクトル化前哨実験

目的:
  diff_experiment で得た差分線画 (例: 04_diff_k51.png) から、
  「ロボットが実用的な時間で描けるストローク列」を抽出する。

  これは Step D (vectorizer.py) の前哨実験。
  ここで効果のあった手法を vectorizer.py に組み込む。

処理パイプライン:
  1. 入力: diff_experiment の 04_diff_k51.png (白地黒線)
  2. 連結成分フィルタ: 小さい成分を除去 (複数閾値で比較)
  3. モルフォロジー CLOSE: 線の切れ目を繋ぐ
  4. 細線化 (skeletonize): 1ピクセル幅へ
  5. findContours + approxPolyDP: ポリラインに変換
  6. 長さフィルタ: 短すぎるポリラインを除外
  7. ストローク数とピクセル統計を出力

出力:
  ~/draw_piper/logs/vectorize_experiment/<元画像名>/
    01_input.png
    02_filtered_<min_size>.png        <- 連結成分フィルタ後
    03_closed.png                     <- モルフォロジー後
    04_skeleton.png                   <- 細線化後
    05_polylines_<eps>.png            <- ポリライン化 (epsilon複数)
    06_final_<min_len>.png            <- 長さフィルタ後 (最終)
  summary.md
"""

import logging
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np


# ----- パス設定 -----
HOME = Path.home()
LOG_DIR = HOME / "draw_piper" / "logs"
DIFF_DIR = LOG_DIR / "diff_experiment"
OUT_DIR = LOG_DIR / "vectorize_experiment"
OUT_DIR.mkdir(parents=True, exist_ok=True)


# ----- ロギング -----
log_file = OUT_DIR / f"experiment_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(log_file), logging.StreamHandler()],
)
log = logging.getLogger(__name__)


# ----- 処理ステップ -----

def load_diff_image(path: Path) -> np.ndarray:
    """白地黒線画像を読み込んで「黒線=255, 白地=0」のマスクに変換"""
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise RuntimeError(f"Failed to read: {path}")
    # 反転して線=255
    mask = (img < 128).astype(np.uint8) * 255
    return mask


def filter_small_components(mask: np.ndarray, min_pixels: int) -> tuple[np.ndarray, int]:
    """連結成分フィルタ。min_pixels未満の成分を除去。"""
    num, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    
    # 背景成分は除外、面積で判定
    keep = np.zeros_like(mask)
    kept_count = 0
    for i in range(1, num):
        area = stats[i, cv2.CC_STAT_AREA]
        if area >= min_pixels:
            keep[labels == i] = 255
            kept_count += 1
    return keep, kept_count


def morphology_close(mask: np.ndarray, kernel_size: int = 3) -> np.ndarray:
    """モルフォロジー CLOSE で線の切れ目を埋める"""
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)


def skeletonize(mask: np.ndarray) -> np.ndarray:
    """細線化 (1ピクセル幅へ)。scikit-imageを使う"""
    try:
        from skimage.morphology import skeletonize as sk_skel
    except ImportError:
        log.error("scikit-image not installed. Run: pip install scikit-image")
        return mask
    
    binary = (mask > 0)
    skel = sk_skel(binary)
    return (skel * 255).astype(np.uint8)


def vectorize_polylines(mask: np.ndarray, epsilon: float, min_length: int) -> tuple[list, np.ndarray]:
    """findContours → approxPolyDP でポリラインに変換。
    
    引数:
      mask: 線マスク (線=255)
      epsilon: approxPolyDP の許容誤差 (大きいほど粗い近似)
      min_length: 最終的に残すポリラインの最小点数
    
    戻り値:
      polylines: list of np.ndarray (各要素は (N, 2) の点列)
      visual: 描画した結果 (白地に黒線)
    """
    # findContours は閉曲線も非閉曲線も拾う
    contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    
    polylines = []
    for cnt in contours:
        if len(cnt) < 2:
            continue
        # ポリライン近似
        approx = cv2.approxPolyDP(cnt, epsilon, closed=False)
        if len(approx) < min_length:
            continue
        polylines.append(approx.reshape(-1, 2))
    
    # 可視化
    h, w = mask.shape
    visual = np.ones((h, w), dtype=np.uint8) * 255
    for poly in polylines:
        for i in range(len(poly) - 1):
            p1 = tuple(poly[i].astype(int))
            p2 = tuple(poly[i + 1].astype(int))
            cv2.line(visual, p1, p2, 0, thickness=2)
    
    return polylines, visual


def stroke_total_length(polylines: list) -> float:
    """全ポリラインの総長 (ピクセル)"""
    total = 0.0
    for poly in polylines:
        for i in range(len(poly) - 1):
            d = np.linalg.norm(poly[i + 1] - poly[i])
            total += d
    return total


# ----- メイン処理 -----

def process_one(diff_image_path: Path, label: str) -> dict:
    """1枚の差分画像に対してパイプラインを実行"""
    log.info(f"=== Processing: {label} ===")
    log.info(f"  Input: {diff_image_path}")
    
    sub_dir = OUT_DIR / label
    sub_dir.mkdir(parents=True, exist_ok=True)
    
    # ステップ1: 読み込み (白地黒線 → マスク)
    mask = load_diff_image(diff_image_path)
    cv2.imwrite(str(sub_dir / "01_input.png"), 255 - mask)
    
    num_initial, _, _, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    log.info(f"  Initial components: {num_initial - 1}")
    
    results = {"initial_components": num_initial - 1}
    
    # ステップ2: 連結成分フィルタ (複数閾値で比較)
    best_filtered = None
    best_min_pixels = None
    for min_pixels in [10, 30, 50, 100, 200]:
        filtered, kept = filter_small_components(mask, min_pixels)
        cv2.imwrite(str(sub_dir / f"02_filtered_min{min_pixels:03d}.png"), 255 - filtered)
        results[f"filtered_min{min_pixels}"] = kept
        log.info(f"  Filter min_pixels={min_pixels}: kept {kept} components")
        # 50ピクセル基準を「最終パイプライン入力」とする
        if min_pixels == 50:
            best_filtered = filtered
            best_min_pixels = 50
    
    # ステップ3: モルフォロジー CLOSE (min_pixels=50 ベース)
    closed = morphology_close(best_filtered, kernel_size=3)
    cv2.imwrite(str(sub_dir / "03_closed.png"), 255 - closed)
    num_closed, _, _, _ = cv2.connectedComponentsWithStats(closed, connectivity=8)
    log.info(f"  After CLOSE: {num_closed - 1} components")
    results["after_close"] = num_closed - 1
    
    # ステップ4: 細線化
    skel = skeletonize(closed)
    cv2.imwrite(str(sub_dir / "04_skeleton.png"), 255 - skel)
    num_skel, _, _, _ = cv2.connectedComponentsWithStats(skel, connectivity=8)
    log.info(f"  After skeletonize: {num_skel - 1} components")
    results["after_skel"] = num_skel - 1
    
    # ステップ5: ポリライン化 (epsilon複数)
    polyline_results = {}
    for eps in [1.0, 2.0, 3.0, 5.0]:
        polylines, visual = vectorize_polylines(skel, epsilon=eps, min_length=2)
        cv2.imwrite(str(sub_dir / f"05_polylines_eps{eps:.1f}.png"), visual)
        total_points = sum(len(p) for p in polylines)
        total_len = stroke_total_length(polylines)
        log.info(
            f"  Polylines eps={eps}: {len(polylines)} strokes, "
            f"{total_points} points, total_length={total_len:.0f}px"
        )
        polyline_results[f"poly_eps{eps}"] = {
            "strokes": len(polylines),
            "total_points": total_points,
            "total_length": total_len,
        }
    results.update(polyline_results)
    
    # ステップ6: 長さフィルタ (eps=2.0 ベース、複数min_length)
    for min_len in [3, 5, 10, 20]:
        polylines, visual = vectorize_polylines(skel, epsilon=2.0, min_length=min_len)
        cv2.imwrite(str(sub_dir / f"06_final_eps2_minlen{min_len:02d}.png"), visual)
        total_points = sum(len(p) for p in polylines)
        total_len = stroke_total_length(polylines)
        log.info(
            f"  Final eps=2.0, min_len={min_len}: "
            f"{len(polylines)} strokes, {total_points} points, total_length={total_len:.0f}px"
        )
        results[f"final_minlen{min_len}"] = {
            "strokes": len(polylines),
            "total_points": total_points,
            "total_length": total_len,
        }
    
    return results


def main():
    log.info("=" * 60)
    log.info("Vectorize prelim experiment")
    log.info(f"Output: {OUT_DIR}")
    log.info("=" * 60)
    
    # 評価対象: 差分実験で良好だった代表画像 3〜4枚
    # 「ユーザの絵が消えてる(良)」「線がそこそこ多い(難題)」「線が少なめ(限界ケース)」
    targets = [
        ("141838_4step_k51",  # 線が多い: strokes=479
         DIFF_DIR / "sdxl_output_20260523_141838_4step" / "04_diff_k51.png"),
        ("141517_4step_k51",  # 中程度: strokes=147
         DIFF_DIR / "sdxl_output_20260523_141517_4step" / "04_diff_k51.png"),
        ("144723_4step_k51",  # 中程度: strokes=121
         DIFF_DIR / "sdxl_output_20260523_144723_4step" / "04_diff_k51.png"),
        ("141851_2step_k51",  # 少ない: strokes=38
         DIFF_DIR / "sdxl_output_20260523_141851_2step" / "04_diff_k51.png"),
    ]
    
    all_results = {}
    for label, path in targets:
        if not path.exists():
            log.warning(f"Skipping {label}: file not found at {path}")
            continue
        all_results[label] = process_one(path, label)
    
    # サマリー
    summary_path = OUT_DIR / "summary.md"
    with summary_path.open("w") as f:
        f.write("# Vectorize prelim experiment summary\n\n")
        f.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write("## 目標値\n\n")
        f.write("- **strokes**: 30〜150 (ペンアップ/ダウン回数の上限)\n")
        f.write("- **total_points**: 500〜2000 (軌道点の総数)\n")
        f.write("- **total_length**: 10000〜50000 px (実描画長)\n\n")
        f.write("## 各画像の処理推移\n\n")
        
        for label, results in all_results.items():
            f.write(f"### {label}\n\n")
            f.write(f"- Initial components: {results['initial_components']}\n")
            f.write(f"- After CLOSE: {results['after_close']}\n")
            f.write(f"- After skeletonize: {results['after_skel']}\n\n")
            
            f.write("#### 連結成分フィルタ (min_pixels)\n\n")
            f.write("| min_pixels | components kept |\n|---|---|\n")
            for k in [10, 30, 50, 100, 200]:
                f.write(f"| {k} | {results[f'filtered_min{k}']} |\n")
            
            f.write("\n#### ポリライン化 (epsilon、min_length=2)\n\n")
            f.write("| epsilon | strokes | total_points | total_length |\n|---|---|---|---|\n")
            for eps in [1.0, 2.0, 3.0, 5.0]:
                p = results[f"poly_eps{eps}"]
                f.write(
                    f"| {eps} | {p['strokes']} | {p['total_points']} | {p['total_length']:.0f} |\n"
                )
            
            f.write("\n#### 最終 (epsilon=2.0、min_length変化)\n\n")
            f.write("| min_length | strokes | total_points | total_length |\n|---|---|---|---|\n")
            for ml in [3, 5, 10, 20]:
                p = results[f"final_minlen{ml}"]
                f.write(
                    f"| {ml} | {p['strokes']} | {p['total_points']} | {p['total_length']:.0f} |\n"
                )
            f.write("\n")
    
    log.info(f"Summary written to: {summary_path}")
    log.info("")
    log.info("推奨確認順序:")
    log.info("  各 vectorize_experiment/<label>/06_final_eps2_minlen10.png を比較")
    log.info("  strokes が 30〜150、絵として認識できる線が残ってればOK")


if __name__ == "__main__":
    main()
