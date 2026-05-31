#!/usr/bin/env python3
"""strokes.json が Frida Smooth Draw に適合しているかチェック。

docs/frida_stroke_guideline.md の基準で sanity check し、 警告を report。
warn だけ出す (生成失敗にはしない)。

使用:
  ./venv/bin/python -m scripts.check_frida_friendly path/to/strokes.json
  ./venv/bin/python -m scripts.check_frida_friendly logs/robot_input_set_v2_*/*/strokes.json
  ./venv/bin/python -m scripts.check_frida_friendly --root logs/robot_input_set_v2_20260530_174341
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import median


def frida_friendly(strokes: list, canvas_w: int = 768) -> list:
    """strokes (list of polylines) を Frida 観点でチェックして warn を返す。

    Parameters
    ----------
    strokes : list of list of [x, y]
        ピクセル座標の polyline 群
    canvas_w : int
        canvas の幅 (px)、 stroke 間距離の閾値計算に使う

    Returns
    -------
    warns : list of str
        警告メッセージ
    """
    warns: list[str] = []
    n = len(strokes)
    if n == 0:
        return ["stroke 0 本 (生成失敗 or 全 drop)"]

    n_pts = sum(len(s) for s in strokes)
    avg_pts = n_pts / max(n, 1)

    # 1) stroke 数
    # 「少なすぎ」 は警告しない: 円=1 本、 単純な companion 等は少数 stroke が
    # 正常で、 TSP の恩恵が無いだけで描画上の問題ではない (n==0 は冒頭で警告済み)。
    if n > 80:
        warns.append(f"stroke 数 {n} が多すぎる (接続線 overhead 大)")

    # 2) stroke あたり点数
    if avg_pts < 4:
        warns.append(
            f"平均 {avg_pts:.1f} 点/stroke が短すぎ "
            "(曲率計算 skip → speed 一定)")
    elif avg_pts > 60:
        warns.append(
            f"平均 {avg_pts:.1f} 点/stroke が長すぎ "
            "(中断 / 再開粒度が粗い)")

    # 3) 巨大 stroke (1000 点 +) の警告
    huge = [i for i, s in enumerate(strokes) if len(s) > 200]
    if huge:
        warns.append(
            f"巨大 stroke (>200 点) が {len(huge)} 本: idx={huge[:3]}...")

    # 4) 極短 stroke (2-3 点)
    tiny = sum(1 for s in strokes if len(s) <= 3)
    if tiny > n // 2:
        warns.append(
            f"極短 stroke (≤3 点) が {tiny}/{n} 本、 "
            "曲率計算不可で overhead が支配")

    # 5) クラスタ密度: 各 stroke の最近接 stroke 距離の中央値
    centers = []
    for s in strokes:
        if not s:
            continue
        x0, y0 = s[0]
        x1, y1 = s[-1]
        centers.append(((x0 + x1) / 2.0, (y0 + y1) / 2.0))
    nn = []
    for i, c in enumerate(centers):
        best = float("inf")
        for j, c2 in enumerate(centers):
            if i == j:
                continue
            d = ((c[0] - c2[0]) ** 2 + (c[1] - c2[1]) ** 2) ** 0.5
            if d < best:
                best = d
        if best < float("inf"):
            nn.append(best)
    median_gap = median(nn) if nn else 0.0
    # 768 canvas で 80px ≈ 10% 程度をクラスタ閾値の目安
    cluster_thresh = canvas_w * 80 / 768
    # stroke が少ない単純図形では look-ahead 最適化自体が無意味なので、
    # 十分な stroke 数 (>= 8) のときだけクラスタ判定する。
    if n >= 8 and median_gap > cluster_thresh:
        warns.append(
            f"stroke 間中央距離 {median_gap:.0f}px > {cluster_thresh:.0f}px "
            "(クラスタ化されてない = look-ahead 無効)")

    # 6) 同一座標 stroke (両端が一致)
    dups = 0
    seen: set = set()
    for s in strokes:
        if not s:
            continue
        key = (round(s[0][0], 1), round(s[0][1], 1),
               round(s[-1][0], 1), round(s[-1][1], 1))
        if key in seen:
            dups += 1
        seen.add(key)
    if dups > 2:
        warns.append(f"両端が同一座標の重複 stroke が {dups} 本")

    return warns


def load_strokes(json_path: Path) -> tuple:
    """strokes.json を読んで (strokes_list, image_shape) を返す。"""
    data = json.loads(json_path.read_text())
    strokes = data.get("strokes", [])
    image_shape = data.get("image_shape", [768, 768])
    return strokes, image_shape


def check_one(json_path: Path) -> dict:
    """1 ファイルをチェックして結果を dict で返す。"""
    strokes, image_shape = load_strokes(json_path)
    canvas_w = image_shape[1] if len(image_shape) > 1 else 768
    warns = frida_friendly(strokes, canvas_w=canvas_w)
    return {
        "path": str(json_path),
        "n_strokes": len(strokes),
        "n_points": sum(len(s) for s in strokes),
        "avg_pts": (sum(len(s) for s in strokes) / max(len(strokes), 1)),
        "warns": warns,
        "ok": not warns,
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", type=Path,
                    help="strokes.json path (glob 展開済)")
    ap.add_argument("--root", type=Path,
                    help="root dir 配下の **/strokes.json をすべてチェック")
    ap.add_argument("--quiet", action="store_true",
                    help="OK ファイルは output しない (warn のみ)")
    args = ap.parse_args()

    targets: list[Path] = list(args.paths)
    if args.root:
        targets += list(args.root.glob("**/strokes.json"))
    if not targets:
        ap.error("paths も --root も無指定")

    ok_count = 0
    warn_count = 0
    for p in sorted(targets):
        if not p.exists():
            print(f"[skip] not found: {p}")
            continue
        res = check_one(p)
        if res["ok"]:
            ok_count += 1
            if not args.quiet:
                print(f"✅ {p.parent.name}: "
                      f"n={res['n_strokes']} "
                      f"pts={res['n_points']} "
                      f"avg={res['avg_pts']:.1f}")
        else:
            warn_count += 1
            print(f"⚠️  {p.parent.name}: "
                  f"n={res['n_strokes']} "
                  f"pts={res['n_points']} "
                  f"avg={res['avg_pts']:.1f}")
            for w in res["warns"]:
                print(f"     - {w}")

    print(f"\n=== summary: {ok_count} ok / {warn_count} warn "
          f"(total {ok_count + warn_count}) ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
