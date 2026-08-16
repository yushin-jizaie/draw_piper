"""手描き風 stroke wobble 後処理 (Plan F fallback)。

Vectorizer 出力の strokes (画像から抽出した polyline 群) に対し、
**直線部分をわざと曲げる** / **点列に微小ランダムオフセット** を加えて、
ロボット描画した時に「手描き風 (松本タッチ的な不規則さ)」 を出す試み。

LoRA / IP-Adapter で「松本タッチ画像」 を生成する経路がうまく行かなくても、
robot drawing 段階で 「人間が手で描いたような不規則さ」 を加えれば、
output が clean な vector で生成されていても 結果は rough になる。

依存: numpy のみ (cv2 不要、 sklearn 不要)。

使用:
  from modules.stroke_wobble import wobble_strokes
  rough_strokes = wobble_strokes(
      strokes,                    # List[List[(x, y)]]
      amplitude_px=2.5,           # 振幅
      frequency_hz=0.06,          # 空間周波数 (per pixel)
      seed=42,
  )

これを Vectorizer.vectorize() 後 + Robot.draw_stroke_panel() 前 に挟む。
"""
from __future__ import annotations

from typing import List, Sequence, Tuple

import numpy as np


StrokePoint = Tuple[float, float]
Stroke = List[StrokePoint]


def _stroke_to_array(stroke: Sequence[StrokePoint]) -> np.ndarray:
    """List[(x,y)] → ndarray (N, 2)"""
    return np.asarray(stroke, dtype=float).reshape(-1, 2)


def _array_to_stroke(arr: np.ndarray) -> Stroke:
    return [(float(x), float(y)) for x, y in arr]


def _perpendicular_normals(pts: np.ndarray) -> np.ndarray:
    """各点での 接線に垂直な単位ベクトル を返す (N, 2)。

    端点では片側差分、 内側では中央差分。
    """
    n = len(pts)
    if n < 2:
        # 1 点しかなければ y軸方向を返す (任意)
        return np.tile([0.0, 1.0], (n, 1))
    tangent = np.zeros_like(pts)
    # 内側 中央差分
    if n >= 3:
        tangent[1:-1] = pts[2:] - pts[:-2]
    # 端点 片側差分
    tangent[0] = pts[1] - pts[0]
    tangent[-1] = pts[-1] - pts[-2]
    # 単位ベクトル化
    norm = np.linalg.norm(tangent, axis=1, keepdims=True)
    norm = np.where(norm < 1e-9, 1.0, norm)
    tangent = tangent / norm
    # 90° 回転 (x, y) -> (-y, x) で 垂直
    perp = np.column_stack([-tangent[:, 1], tangent[:, 0]])
    return perp


def wobble_stroke(
    stroke: Sequence[StrokePoint],
    *,
    amplitude_px: float = 2.0,
    frequency_hz: float = 0.05,
    seed: int = 0,
) -> Stroke:
    """1 本の stroke に sin 波 + ランダム位相 で 垂直方向 wobble を加える。

    各点 p_i の位置 p_i' = p_i + amplitude * sin(freq * 累積長 + phase) * perp_i

    Parameters
    ----------
    stroke
        点列 [(x, y), ...] (px 単位)。
    amplitude_px
        wobble の振幅 (px)。 1024 px キャンバスで 1.5-3.0 程度が手描き風。
    frequency_hz
        空間周波数 (per pixel)。 0.03-0.08 程度。 大きいと波の刻みが細かい。
    seed
        sin の位相に使う random seed。 stroke 単位で変えると毎回違う wobble。

    Returns
    -------
    List[(x, y)] — wobble 加算後の点列
    """
    pts = _stroke_to_array(stroke)
    if len(pts) < 2:
        return _array_to_stroke(pts)

    # 累積長 (arclength) を 計算
    diffs = np.diff(pts, axis=0)
    seg_len = np.linalg.norm(diffs, axis=1)
    arclen = np.concatenate([[0.0], np.cumsum(seg_len)])

    perp = _perpendicular_normals(pts)
    rng = np.random.default_rng(seed)
    phase = float(rng.uniform(0.0, 2.0 * np.pi))

    # 直線の長さが短い場合は wobble 振幅も縮小 (不自然な誇張回避)
    total_len = float(arclen[-1])
    amp = amplitude_px
    if total_len < 30.0:
        amp *= total_len / 30.0

    wave = np.sin(2.0 * np.pi * frequency_hz * arclen + phase)
    offset = (amp * wave)[:, None] * perp
    pts_out = pts + offset
    return _array_to_stroke(pts_out)


def wobble_strokes(
    strokes: Sequence[Sequence[StrokePoint]],
    *,
    amplitude_px: float = 2.0,
    frequency_hz: float = 0.05,
    seed: int = 0,
    per_stroke_seed_jitter: bool = True,
) -> List[Stroke]:
    """全 stroke に wobble をかける。 各 stroke で seed を変えて 位相を分散。"""
    out: List[Stroke] = []
    for idx, stroke in enumerate(strokes):
        s = (seed + idx * 17) if per_stroke_seed_jitter else seed
        out.append(wobble_stroke(
            stroke,
            amplitude_px=amplitude_px,
            frequency_hz=frequency_hz,
            seed=s,
        ))
    return out


def _self_test():
    """簡易動作チェック (Bash 承認不要、 importable で即動く)。"""
    # 直線 → wobble で曲線になることを確認
    straight = [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0), (30.0, 0.0),
                (40.0, 0.0), (50.0, 0.0)]
    wobbly = wobble_stroke(straight, amplitude_px=3.0, frequency_hz=0.05,
                            seed=42)
    print(f"input  ({len(straight)} pts): {straight}")
    print(f"output ({len(wobbly)} pts): {wobbly}")
    # 出力の y 座標が 0 から離れている (= wobble 効いている) ことを確認
    ys = [y for _, y in wobbly]
    max_dev = max(abs(y) for y in ys)
    assert max_dev > 0.1, f"wobble had no effect (max_dev={max_dev})"
    print(f"max y deviation: {max_dev:.3f} px (expected > 0)")
    print("self test PASSED")


if __name__ == "__main__":
    _self_test()
