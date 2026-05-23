"""ユーザ絵 vs SDXL生成画像の差分検出実験スクリプト

目的: SDXL Turbo + ControlNet が出した「完成形画像」から、
「ユーザがまだ描いていない部分(=ロボットが描き足すべき部分)」を抽出する。

設計判断:
  - generated_image を Canny strong_blur で線画化 (実験1で良い結果が出た方法)
  - user_image を二値化
  - user_mask を dilate (膨張) してから差分を取る
  - これで「ユーザの線の近傍」を「描き足し不要エリア」として除外

入力:
  user_image:      ~/draw_piper/scripts/test_sketch.jpg (これがmedian合成相当)
  generated_image: ~/draw_piper/logs/sdxl_output_*.png (全部試す)

出力:
  ~/draw_piper/logs/diff_experiment/<元画像名>/
    01_user_binary.png         <- ユーザ絵の二値化
    02_user_dilated_<n>.png    <- ユーザマスク膨張版 (複数カーネルサイズ)
    03_generated_canny.png     <- SDXL出力のCanny線画
    04_diff_<n>.png            <- 差分結果 (複数カーネルサイズ)
  ~/draw_piper/logs/diff_experiment/summary.md

評価:
  - 差分後にユーザの円+点が消えているか
  - 「描き足し部分」として意味のある線が残っているか
  - ロボットで描けるストローク数か (50〜300)
"""

import logging
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np


# ----- パス設定 -----
HOME = Path.home()
LOG_DIR = HOME / "draw_piper" / "logs"
OUT_DIR = LOG_DIR / "diff_experiment"
OUT_DIR.mkdir(parents=True, exist_ok=True)

SCRIPT_DIR = HOME / "draw_piper" / "scripts"
USER_IMAGE_PATH = SCRIPT_DIR / "test_sketch.jpg"


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


# ----- メトリクス -----

def estimate_stroke_metric(binary_white_bg_black_lines: np.ndarray) -> dict:
    """白地に黒線の二値画像からストローク量を推定。"""
    line_mask = (binary_white_bg_black_lines < 128).astype(np.uint8)
    total = line_mask.size
    line_px = int(line_mask.sum())
    num_components, _ = cv2.connectedComponents(line_mask, connectivity=8)
    return {
        "line_pixel_ratio": line_px / total,
        "estimated_strokes": num_components - 1,
    }


# ----- 個別処理 -----

def binarize_user(user_img_gray: np.ndarray) -> np.ndarray:
    """ユーザの絵を二値化。黒線=255, 白地=0 のマスク (差分計算用)。
    
    入力: ユーザの絵(白地に黒線のグレースケール)
    出力: マスク(黒線のあった場所=255, それ以外=0)
    """
    # ユーザの絵は単純な線なのでOtsuで十分
    _, binary = cv2.threshold(
        user_img_gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )
    # binary は「黒線があった場所=255, 白地=0」
    return binary


def canny_strong_blur(img_gray: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """SDXL生成画像から線画を抽出。
    
    戻り値:
      mask: 黒線=255, 白地=0 のマスク
      visual: 白地に黒線 (人間視認用)
    """
    blurred = cv2.GaussianBlur(img_gray, (9, 9), 3.0)
    edges = cv2.Canny(blurred, 50, 150)
    # edges は「線=255, 背景=0」のマスクそのもの
    return edges, 255 - edges


# ----- 差分処理本体 -----

def compute_diff(
    user_mask: np.ndarray,
    generated_mask: np.ndarray,
    dilate_kernel_size: int,
) -> tuple[np.ndarray, np.ndarray]:
    """ユーザマスクを dilate して差分を取る。
    
    引数:
      user_mask:      ユーザ絵の線マスク (線=255, 背景=0)
      generated_mask: SDXL生成線画のマスク (線=255, 背景=0)
      dilate_kernel_size: ユーザマスクの膨張カーネルサイズ
    
    戻り値:
      diff_mask: 差分マスク (線=255, 背景=0)
      diff_visual: 白地に黒線の見やすい画像
    """
    kernel = np.ones((dilate_kernel_size, dilate_kernel_size), np.uint8)
    user_dilated = cv2.dilate(user_mask, kernel, iterations=1)
    
    # AND NOT: generated にあって user_dilated にない部分
    diff_mask = cv2.bitwise_and(generated_mask, cv2.bitwise_not(user_dilated))
    
    # 視認用に反転
    diff_visual = 255 - diff_mask
    return diff_mask, diff_visual


# ----- メイン処理 -----

def process_image(
    generated_path: Path,
    user_img_gray: np.ndarray,
    user_mask: np.ndarray,
) -> dict:
    """1枚の生成画像に対して、複数の dilate サイズで差分を取る。"""
    log.info(f"Processing: {generated_path.name}")
    
    gen_img = cv2.imread(str(generated_path))
    if gen_img is None:
        log.warning(f"  Failed to read: {generated_path}")
        return {}
    
    gen_gray = cv2.cvtColor(gen_img, cv2.COLOR_BGR2GRAY)
    
    # ユーザ画像と同サイズに揃える (生成画像が1024x1024で、ユーザは1024x768なので揃える)
    h, w = gen_gray.shape
    if user_img_gray.shape != gen_gray.shape:
        user_resized = cv2.resize(user_img_gray, (w, h), interpolation=cv2.INTER_LANCZOS4)
        user_mask_resized = cv2.resize(user_mask, (w, h), interpolation=cv2.INTER_NEAREST)
    else:
        user_resized = user_img_gray
        user_mask_resized = user_mask
    
    # 出力ディレクトリ
    sub_dir = OUT_DIR / generated_path.stem
    sub_dir.mkdir(parents=True, exist_ok=True)
    
    # 参照画像を保存
    cv2.imwrite(str(sub_dir / "00_user_resized.png"), user_resized)
    cv2.imwrite(str(sub_dir / "00_generated_original.png"), gen_img)
    cv2.imwrite(str(sub_dir / "01_user_binary_mask.png"), user_mask_resized)
    
    # 生成画像のCanny線画化
    gen_mask, gen_visual = canny_strong_blur(gen_gray)
    cv2.imwrite(str(sub_dir / "03_generated_canny.png"), gen_visual)
    gen_metrics = estimate_stroke_metric(gen_visual)
    log.info(
        f"  generated_canny: line_ratio={gen_metrics['line_pixel_ratio']:.3%}, "
        f"strokes={gen_metrics['estimated_strokes']}"
    )
    
    # 複数の dilate サイズで差分を取る
    results = {"generated_canny": gen_metrics}
    for kernel_size in [5, 11, 21, 31, 51]:
        # ユーザマスク膨張版を保存
        kernel = np.ones((kernel_size, kernel_size), np.uint8)
        user_dilated = cv2.dilate(user_mask_resized, kernel, iterations=1)
        cv2.imwrite(str(sub_dir / f"02_user_dilated_k{kernel_size:02d}.png"), 255 - user_dilated)
        
        # 差分
        diff_mask, diff_visual = compute_diff(user_mask_resized, gen_mask, kernel_size)
        cv2.imwrite(str(sub_dir / f"04_diff_k{kernel_size:02d}.png"), diff_visual)
        
        metrics = estimate_stroke_metric(diff_visual)
        results[f"diff_k{kernel_size}"] = metrics
        log.info(
            f"  diff_k{kernel_size:02d}: line_ratio={metrics['line_pixel_ratio']:.3%}, "
            f"strokes={metrics['estimated_strokes']}"
        )
    
    return results


def main():
    log.info("=" * 60)
    log.info("Diff detection experiment: user vs generated")
    log.info(f"Output dir: {OUT_DIR}")
    log.info("=" * 60)
    
    # ユーザ画像読み込み
    if not USER_IMAGE_PATH.exists():
        log.error(f"User image not found: {USER_IMAGE_PATH}")
        return
    
    user_img = cv2.imread(str(USER_IMAGE_PATH))
    user_gray = cv2.cvtColor(user_img, cv2.COLOR_BGR2GRAY)
    log.info(f"User image: {USER_IMAGE_PATH.name}, shape={user_gray.shape}")
    
    user_mask = binarize_user(user_gray)
    user_metrics = estimate_stroke_metric(255 - user_mask)
    log.info(
        f"User binary: line_ratio={user_metrics['line_pixel_ratio']:.3%}, "
        f"strokes={user_metrics['estimated_strokes']}"
    )
    
    # SDXL生成画像を全部処理
    input_files = sorted(LOG_DIR.glob("sdxl_output_*.png"))
    if not input_files:
        log.error("No SDXL output images found")
        return
    log.info(f"Found {len(input_files)} generated images")
    
    all_results = {}
    for input_path in input_files:
        all_results[input_path.name] = process_image(input_path, user_gray, user_mask)
    
    # サマリー
    summary_path = OUT_DIR / "summary.md"
    with summary_path.open("w") as f:
        f.write("# Diff detection experiment summary\n\n")
        f.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(f"User image: `{USER_IMAGE_PATH.name}`\n")
        f.write(f"  - line_pixel_ratio: {user_metrics['line_pixel_ratio']:.3%}\n")
        f.write(f"  - estimated_strokes: {user_metrics['estimated_strokes']}\n\n")
        f.write("各生成画像 × dilate kernel size での差分結果\n\n")
        f.write("- kernel=5: ほぼピンポイント差分(リング残るかも)\n")
        f.write("- kernel=11: 標準\n")
        f.write("- kernel=21: ユーザ線周辺をしっかり除外\n")
        f.write("- kernel=31: 広めに除外(描き足し部分が遠方になる)\n")
        f.write("- kernel=51: 大きく除外(ユーザ絵周辺全部消える)\n\n")
        f.write("目標: ストロークが 50〜300、線が顔の意味のある部分に残ること\n\n")
        
        for img_name, methods in all_results.items():
            f.write(f"## {img_name}\n\n")
            f.write("| 手法 | line_pixel_ratio | estimated_strokes |\n")
            f.write("|---|---|---|\n")
            for method_name, metrics in methods.items():
                f.write(
                    f"| {method_name} | "
                    f"{metrics['line_pixel_ratio']:.3%} | "
                    f"{metrics['estimated_strokes']} |\n"
                )
            f.write("\n")
    
    log.info(f"Summary written to: {summary_path}")
    log.info("")
    log.info("Recommended viewing order for each image folder:")
    log.info("  00_user_resized.png         <- ユーザの絵 (リサイズ済み)")
    log.info("  00_generated_original.png   <- SDXL出力 (元)")
    log.info("  03_generated_canny.png      <- Canny線画化後 (差分前)")
    log.info("  04_diff_k11.png             <- 差分結果 (kernel=11)")
    log.info("  04_diff_k21.png             <- 差分結果 (kernel=21) 推奨開始点")


if __name__ == "__main__":
    main()
