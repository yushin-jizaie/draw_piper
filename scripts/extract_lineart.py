"""
学習用 raw 画像から 線画(lineart) を抽出するスクリプト。

目的:
    matsumoto LoRA を「松本の線」 だけで学習させる。 元の raw は漫画パネル
    (screen tone + ハッチング + 吹き出し etc) なので、 そのまま学習すると
    LoRA が「線 + tone + 塗り」 をセットで覚えてしまい、 ロボット描画 (線のみ
    で描ける) には適さない。 学習前に線画化することで「線だけ」 を学ばせる。

入力: training/<name>/raw/ 内の jpg/png/webp
出力:
    training/<name>/lineart_preview/<image>_preview.png
        左から [orig | method_A (adaptive threshold) | method_B (ML)]
    training/<name>/lineart_<method>/<image>.png
        method 単体結果 (--apply <method> 指定時のみ)

使い方:
    # 1. preview 生成だけ (全 36 枚を 2 手法で並べる)
    python3 -m scripts.extract_lineart \
        --input  training/matsumoto_taiyo/raw \
        --output training/matsumoto_taiyo \
        --preview

    # 2. preview を確認した後、 採用する method で本実行
    python3 -m scripts.extract_lineart \
        --input  training/matsumoto_taiyo/raw \
        --output training/matsumoto_taiyo \
        --apply b
        # → training/matsumoto_taiyo/lineart_b/ に 36 枚出力
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


_RAW_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".JPG", ".JPEG",
             ".PNG", ".WEBP", ".GIF")


def _load_rgb(path: Path, max_edge: int = 1024) -> np.ndarray:
    """Load image as RGB ndarray, resize so longest edge <= max_edge."""
    img = Image.open(path).convert("RGB")
    w, h = img.size
    scale = max_edge / max(w, h)
    if scale < 1.0:
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    return np.array(img)


# ---- Method A: adaptive threshold ----------------------------------------
def method_a_threshold(rgb: np.ndarray,
                        block_size: int = 21,
                        C: int = 7) -> np.ndarray:
    """暗い細線だけを残し screen tone のグレーは飛ばす adaptive threshold。

    block_size 大きめ + C 大きめ で local average より一定以上暗い画素のみを
    線と判定。 screen tone の中間グレーは local average 内に収まるので除外
    される。
    """
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    bw = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        blockSize=block_size if block_size % 2 == 1 else block_size + 1,
        C=C,
    )
    # 後処理: tone の小ノイズを morphological opening で除去
    kernel = np.ones((2, 2), np.uint8)
    bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, kernel)
    return bw   # uint8, 0(line)/255(bg)


# ---- Method B: ML LineartAnimeDetector -----------------------------------
_detector_cache = {}


def _get_detector():
    """Lazy-load controlnet_aux LineartAnimeDetector。 初回は ~50MB
    モデルを HF Hub から DL。"""
    if "b" in _detector_cache:
        return _detector_cache["b"]
    try:
        from controlnet_aux import LineartAnimeDetector
    except ImportError as e:
        print("[extract_lineart] controlnet_aux が未インストール:")
        print("  → pip install controlnet_aux")
        raise SystemExit(1) from e
    print("[extract_lineart] loading LineartAnimeDetector...")
    detector = LineartAnimeDetector.from_pretrained("lllyasviel/Annotators")
    _detector_cache["b"] = detector
    return detector


def method_b_anime_lineart(rgb: np.ndarray) -> np.ndarray:
    """controlnet_aux LineartAnimeDetector。 anime/manga 線画抽出。
    出力: 黒線 on 白背景 (uint8 grayscale)。
    元画像と同 shape で返す (detector は内部で 64 倍数に resize するので
    最後に元 shape へ戻す)。
    """
    detector = _get_detector()
    pil = Image.fromarray(rgb)
    out_pil = detector(pil,
                       detect_resolution=min(rgb.shape[:2]),
                       image_resolution=min(rgb.shape[:2]))
    # detector は 64 の倍数に勝手 resize するので 元 shape (W, H) に戻す
    h, w = rgb.shape[:2]
    if out_pil.size != (w, h):
        out_pil = out_pil.resize((w, h), Image.LANCZOS)
    out = np.array(out_pil)
    if out.ndim == 3:
        out = cv2.cvtColor(out, cv2.COLOR_RGB2GRAY)
    return 255 - out


def _bolden(lineart_gray: np.ndarray,
             threshold: int = 180,
             dilate: int = 1) -> np.ndarray:
    """薄い lineart (grayscale) を 2 値化 + dilate で bold 化。

    Method B (LineartAnimeDetector) は線が淡いグレースケールで出るので、
    学習データとして使うには 「はっきりした黒線 on 白背景」 に変換した方が
    SDXL/LoRA が学習しやすい。

    Parameters
    ----------
    lineart_gray : np.ndarray (H,W) uint8
        低い値 = 線、 高い値 = 背景
    threshold : int
        < threshold を 線(=0) とみなす
    dilate : int
        線を太らせる px (0=off、 1-2 推奨)。 ロボットアームのペン太さに
        合わせるなら 1-2 が自然

    Returns
    -------
    np.ndarray (H,W) uint8
        0 = 線 / 255 = 背景 の 2 値画像
    """
    bw = np.where(lineart_gray < threshold, 0, 255).astype(np.uint8)
    if dilate >= 1:
        # 線(=0) を太らせるには 背景(=255) を erode する = invert→dilate→invert
        lines = 255 - bw
        kernel = np.ones((int(dilate), int(dilate)), np.uint8)
        lines = cv2.dilate(lines, kernel, iterations=1)
        bw = 255 - lines
    return bw


# ---- Compose preview -----------------------------------------------------
def _to_rgb(gray: np.ndarray) -> np.ndarray:
    """grayscale uint8 → RGB uint8 (3-ch)。"""
    if gray.ndim == 2:
        return cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)
    return gray


def _compose_strip(orig: np.ndarray,
                    method_a: np.ndarray,
                    method_b: np.ndarray,
                    label: str = "") -> Image.Image:
    """[orig | A | B] 横並び with thin black separators + labels."""
    h, w = orig.shape[:2]
    sep_w = 4
    canvas = np.full((h + 28, w * 3 + sep_w * 2, 3), 255, dtype=np.uint8)
    canvas[28:, 0:w] = orig
    canvas[28:, w + sep_w: w * 2 + sep_w] = _to_rgb(method_a)
    canvas[28:, w * 2 + sep_w * 2: w * 3 + sep_w * 2] = _to_rgb(method_b)
    # separators
    canvas[28:, w:w + sep_w] = 0
    canvas[28:, w * 2 + sep_w: w * 2 + sep_w * 2] = 0
    img = Image.fromarray(canvas)
    # captions
    try:
        from PIL import ImageDraw, ImageFont
        draw = ImageDraw.Draw(img)
        font = ImageFont.load_default()
        draw.text((10, 6), f"{label}  |  orig", fill=(0, 0, 0), font=font)
        draw.text((w + sep_w + 10, 6), "A: adaptive threshold",
                  fill=(0, 0, 0), font=font)
        draw.text((w * 2 + sep_w * 2 + 10, 6), "B: lineart-anime (ML)",
                  fill=(0, 0, 0), font=font)
    except Exception:
        pass
    return img


def _process_one(path: Path, max_edge: int):
    rgb = _load_rgb(path, max_edge=max_edge)
    a = method_a_threshold(rgb)
    b = method_b_anime_lineart(rgb)
    return rgb, a, b


# ---- Main ---------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--input", type=Path, required=True,
                    help="raw 画像が入っているディレクトリ")
    ap.add_argument("--output", type=Path, required=True,
                    help="出力先プロジェクトディレクトリ "
                         "(lineart_preview/ と lineart_<method>/ を作る)")
    ap.add_argument("--preview", action="store_true",
                    help="プレビュー (orig | A | B 横並び) を生成")
    ap.add_argument("--apply", choices=("a", "b"), default=None,
                    help="採用する method を 1 つ選んで本適用 "
                         "(<output>/lineart_<method>/ に書き出す)")
    ap.add_argument("--max-edge", type=int, default=1024,
                    help="読み込み時 長辺 px (default 1024)")
    ap.add_argument("--max-images", type=int, default=None,
                    help="先頭 N 枚だけ処理 (動作確認用)")
    ap.add_argument("--exclude", type=str, default="",
                    help="除外するファイル名 stem の comma list "
                         "(例: 'IMG_4310,IMG_4316,images')。 "
                         "preview / apply 両方で適用")
    ap.add_argument("--bolden", action="store_true",
                    help="apply 時に B 出力を 2 値化 + dilate で bold 化。 "
                         "Method B の薄い線画を 学習用にクリーン化")
    ap.add_argument("--bolden-threshold", type=int, default=180,
                    help="bolden の threshold (default 180、 上げると線が増える)")
    ap.add_argument("--bolden-dilate", type=int, default=1,
                    help="bolden の dilate ksize (default 1、 0=太らせない)")
    args = ap.parse_args()

    if not args.preview and args.apply is None:
        ap.error("--preview か --apply <method> のどちらか指定してください")

    raw_dir = args.input
    if not raw_dir.is_dir():
        sys.exit(f"input dir not found: {raw_dir}")

    paths = [p for p in sorted(raw_dir.iterdir())
             if p.suffix in _RAW_EXTS]
    # exclude stem 適用
    exclude_set = {s.strip() for s in args.exclude.split(",") if s.strip()}
    if exclude_set:
        before = len(paths)
        paths = [p for p in paths if p.stem not in exclude_set]
        skipped = before - len(paths)
        print(f"[extract_lineart] excluded {skipped} images "
              f"(by stem: {sorted(exclude_set)})")
    if args.max_images:
        paths = paths[:args.max_images]
    if not paths:
        sys.exit(f"no images to process in {raw_dir}")
    print(f"[extract_lineart] {len(paths)} images")

    if args.preview:
        out_dir = args.output / "lineart_preview"
        out_dir.mkdir(parents=True, exist_ok=True)
        for i, p in enumerate(paths, 1):
            print(f"[extract_lineart] [{i}/{len(paths)}] {p.name}")
            try:
                rgb, a, b = _process_one(p, args.max_edge)
                strip = _compose_strip(rgb, a, b, label=p.stem)
                strip.save(out_dir / f"{p.stem}_preview.png")
            except Exception as e:
                print(f"  ! failed: {e}")
        print(f"[extract_lineart] preview saved to {out_dir}/")
        print(f"[extract_lineart] open with: "
              f"xdg-open {out_dir.absolute()}")

    if args.apply:
        suffix = f"_{args.apply}" + ("_bold" if args.bolden else "")
        out_dir = args.output / f"lineart{suffix}"
        out_dir.mkdir(parents=True, exist_ok=True)
        fn = method_a_threshold if args.apply == "a" else method_b_anime_lineart
        for i, p in enumerate(paths, 1):
            print(f"[extract_lineart] [{i}/{len(paths)}] {p.name}")
            try:
                rgb = _load_rgb(p, max_edge=args.max_edge)
                lineart = fn(rgb)
                if args.bolden:
                    lineart = _bolden(lineart,
                                       threshold=args.bolden_threshold,
                                       dilate=args.bolden_dilate)
                # 元の resize 後解像度で出力 (学習時にまた resize されるので)
                Image.fromarray(lineart).save(out_dir / f"{p.stem}.png")
            except Exception as e:
                print(f"  ! failed: {e}")
        print(f"[extract_lineart] lineart saved to {out_dir}/")
        print(f"[extract_lineart] open with: "
              f"xdg-open {out_dir.absolute()}")


if __name__ == "__main__":
    main()
