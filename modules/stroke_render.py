"""strokes (polyline 群) を PNG / PIL.Image に rendering する utility。

Vectorizer.vectorize() / stroke_wobble.wobble_strokes() の出力を視覚化する
ために 使う (debug / 比較用)。

依存: PIL のみ (cv2/numpy 不要、 軽量)。

使用:
  from modules.stroke_render import render_strokes_to_image
  img = render_strokes_to_image(
      strokes,                # List[List[(x, y)]]
      width=1024, height=1024,
      line_width=3,
      bg_color=(255, 255, 255),
      stroke_color=(0, 0, 0),
  )
  img.save("rendered.png")
"""
from __future__ import annotations

from typing import List, Sequence, Tuple

from PIL import Image, ImageDraw


def render_strokes_to_image(
    strokes: Sequence[Sequence[Tuple[float, float]]],
    *,
    width: int = 1024,
    height: int = 1024,
    line_width: int = 3,
    bg_color: Tuple[int, int, int] = (255, 255, 255),
    stroke_color: Tuple[int, int, int] = (0, 0, 0),
) -> Image.Image:
    """strokes を 1 枚の PIL Image に描画して返す。

    Parameters
    ----------
    strokes
        polyline 群。 各 polyline は (x, y) tuple のシーケンス (px 単位)。
    width, height
        出力画像サイズ (px)。
    line_width
        線幅 (px)。 1 はピクセル単位、 3 で目立つ、 5 で太め。
    bg_color, stroke_color
        色 RGB (0-255)。

    Returns
    -------
    PIL.Image (RGB, width x height)
    """
    img = Image.new("RGB", (width, height), bg_color)
    draw = ImageDraw.Draw(img)
    for stroke in strokes:
        if len(stroke) < 2:
            # 1 点だけなら 小さな点を打つ
            if len(stroke) == 1:
                x, y = stroke[0]
                r = max(1, line_width // 2)
                draw.ellipse((x - r, y - r, x + r, y + r), fill=stroke_color)
            continue
        pts = [(float(x), float(y)) for x, y in stroke]
        draw.line(pts, fill=stroke_color, width=line_width, joint="curve")
    return img


def render_compare_grid(
    strokes_list: Sequence[Sequence[Sequence[Tuple[float, float]]]],
    labels: Sequence[str],
    *,
    width: int = 1024,
    height: int = 1024,
    line_width: int = 3,
    cols: int = 2,
) -> Image.Image:
    """複数 strokes セットを横並びで grid 化する (wobble 強度比較に便利)。"""
    if len(strokes_list) != len(labels):
        raise ValueError("strokes_list と labels の長さが違う")
    rows = (len(strokes_list) + cols - 1) // cols
    out = Image.new("RGB", (cols * width, rows * height), (240, 240, 240))
    draw = ImageDraw.Draw(out)
    for i, (strokes, label) in enumerate(zip(strokes_list, labels)):
        r, c = i // cols, i % cols
        sub = render_strokes_to_image(
            strokes, width=width, height=height, line_width=line_width)
        out.paste(sub, (c * width, r * height))
        draw.text((c * width + 8, r * height + 8), label, fill=(80, 80, 80))
    return out


def _self_test():
    """簡易動作テスト (importable / Bash 不要)。"""
    strokes = [
        [(50, 50), (200, 50), (200, 200), (50, 200), (50, 50)],   # 四角
        [(300, 100), (400, 200), (500, 100)],                       # 山形
        [(600, 50), (700, 300)],                                    # 直線
    ]
    img = render_strokes_to_image(strokes, width=800, height=400,
                                   line_width=2)
    out_path = "/tmp/stroke_render_self_test.png"
    img.save(out_path)
    print(f"rendered 3 strokes to {out_path}")
    print(f"image size: {img.size}")
    return img


if __name__ == "__main__":
    _self_test()
