#!/usr/bin/env python3
"""16 ファイルの評価表 (画風 / 位置 3 段階) を CSV / Markdown で生成。

各行: 1 ファイル (B_round_smiley_shift.png 等)、 16 行。
列:
  filename / sketch_id / mode / 画風 / 位置 / コメント (Claude 初期評価) /
  ユーザー画風 / ユーザー位置 (空欄、 ユーザー上書き用)

評価軸:
  画風 (Matsumoto タッチ + 線の良さ):  3=良好 / 2=普通 / 1=失敗
  位置 (mode の期待挙動と一致):       3=良好 / 2=部分的 / 1=失敗
    - shift モードでは「入力からずれて配置」 = 良
    - align モードでは「入力位置を保持」 = 良
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

# (filename, sketch_id, mode, 画風, 位置, コメント) — Claude 初期評価
# 画風 / 位置 は 3 (◎) / 2 (○) / 1 (△)
ROWS = [
    # キャラ系 8 行
    ("B_round_smiley_shift.png",  "B_round_smiley",   "shift", 3, 2,
     "Matsumoto-style face を空白地帯に detailed 生成、 入力 smiley は保持。 "
     "shift の意図通り入力 + 別配置の合成"),
    ("B_round_smiley_align.png",  "B_round_smiley",   "align", 2, 3,
     "入力位置に忠実、 ただし VLM auto-prompt 'face' が face のみ描画を誘導、 "
     "キャラ拡張 (phase-e 風) にはならない"),
    ("C_face_with_neck_shift.png", "C_face_with_neck", "shift", 3, 2,
     "首+肩 hint を活かして 体ボリュームのある character を生成、 "
     "入力は左下に保持、 shift の合成として良好"),
    ("C_face_with_neck_align.png", "C_face_with_neck", "align", 2, 3,
     "入力構図 (顔+首) を忠実に保持して stylize、 VLM 'person' で全身要素はやや薄め"),
    ("D_stick_figure_shift.png",  "D_stick_figure",   "shift", 3, 2,
     "棒人間を「松本タッチで肉付けされた character」 に変換、 "
     "shift で空白地帯配置、 入力 stick は左下に小さく残存"),
    ("D_stick_figure_align.png",  "D_stick_figure",   "align", 3, 3,
     "棒人間構図を忠実に保持 + 松本タッチで肉付け、 align としてベスト出力に近い"),
    ("F_angry_face_shift.png",    "F_angry_face",     "shift", 3, 2,
     "怒り表情を保持 + 横に detailed character (24 strokes)、 shift 合成として良好"),
    ("F_angry_face_align.png",    "F_angry_face",     "align", 1, 3,
     "位置は保持されているが、 SDXL が body を描き足さず風景画的になった。 "
     "phase-e の F_angry_face とは大きく違う出力。 画風としては失敗"),
    # object 系 8 行
    ("house_shift.png",   "house",   "shift", 3, 2,
     "木造の家、 草・木のディテール + 屋根構造で松本タッチ良好、 入力 house は右下"),
    ("house_align.png",   "house",   "align", 2, 3,
     "シンプル手書き家を構図保持で stylize、 strokes 数 9 と控えめだが align らしい"),
    ("tree_shift.png",    "tree",    "shift", 1, 2,
     "tree 入力が smiley face と誤認 (○+線が単純すぎ)、 SDXL が tree と認識できず。 "
     "shift 失敗 (input が limit)"),
    ("tree_align.png",    "tree",    "align", 1, 3,
     "tree 入力が smiley face と誤認、 align でも同じ失敗。 位置は保持されている"),
    ("cat_shift.png",     "cat",     "shift", 3, 2,
     "可愛い松本風猫 (黒猫風)、 表情あり、 41 strokes / 1066 pts。 shift 合成として理想的"),
    ("cat_align.png",     "cat",     "align", 2, 3,
     "anthropomorphic 化 (立った猫キャラ、 棒人間風の体・脚)、 "
     "CHARACTER_TEMPLATE の 'manga style character' が誘導した副作用。 位置は保持"),
    ("car_shift.png",     "car",     "shift", 3, 2,
     "走る車、 速度線、 手書きライン、 88 strokes / 2189 pts。 shift で最も成功した object"),
    ("car_align.png",     "car",     "align", 3, 3,
     "構図保持の detailed 車、 windshield 等の細部追加。 align で最も成功した object"),
]

ASSERT_LEN = 16
assert len(ROWS) == ASSERT_LEN, f"行数異常: {len(ROWS)}"


def to_csv(out_path: Path) -> None:
    """Excel が日本語を正しく開けるよう UTF-8 BOM 付き CSV。"""
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([
            "filename", "sketch_id", "mode",
            "画風 (Claude)", "位置 (Claude)",
            "Claude コメント",
            "画風 (user)", "位置 (user)", "user コメント",
        ])
        for fn, sid, mode, style, pos, comment in ROWS:
            w.writerow([fn, sid, mode, style, pos, comment, "", "", ""])
    print(f"saved CSV: {out_path}")


def to_markdown(out_path: Path) -> None:
    """GitHub 上で読みやすい Markdown 表 (3 段階を ◎/○/△ で)。"""
    SYM = {3: "◎", 2: "○", 1: "△"}
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("# 16 組 評価表 (画風 / 位置 × 3 段階)\n\n")
        f.write("評価軸 (3 段階):\n")
        f.write("- ◎ (3): 良好 — 期待通り、 採用に値する\n")
        f.write("- ○ (2): 部分的 — 一部期待外れだが用途次第で使える\n")
        f.write("- △ (1): 失敗 — 期待から大きく外れる\n\n")
        f.write("**画風**: Matsumoto タッチ + 線の質。\n\n")
        f.write("**位置**: mode の期待挙動と一致しているか。\n")
        f.write("- shift では 「入力からずれて配置」 = ◎\n")
        f.write("- align では 「入力位置を保持」 = ◎\n\n")
        f.write("| filename | sketch_id | mode | 画風 | 位置 | コメント (Claude 視点) |\n")
        f.write("|---|---|---|---|---|---|\n")
        for fn, sid, mode, style, pos, comment in ROWS:
            f.write(f"| [{fn}]({fn}) | {sid} | {mode} | "
                    f"{SYM[style]} ({style}) | {SYM[pos]} ({pos}) | {comment} |\n")
        # 集計
        f.write("\n## モード別 集計 (画風 / 位置 平均)\n\n")
        f.write("| mode | 画風 平均 | 位置 平均 | 件数 |\n")
        f.write("|---|---|---|---|\n")
        for m in ("shift", "align"):
            sub = [r for r in ROWS if r[2] == m]
            avg_s = sum(r[3] for r in sub) / len(sub)
            avg_p = sum(r[4] for r in sub) / len(sub)
            f.write(f"| {m} | {avg_s:.2f} | {avg_p:.2f} | {len(sub)} |\n")
        # 入力種別 集計
        f.write("\n## 入力種別 × モード 集計 (画風 平均)\n\n")
        f.write("| 種別 | shift | align |\n")
        f.write("|---|---|---|\n")
        for kind, ids in (
            ("キャラ系 (B/C/D/F)",
             ("B_round_smiley", "C_face_with_neck", "D_stick_figure", "F_angry_face")),
            ("object 系 (h/t/c/c)", ("house", "tree", "cat", "car")),
        ):
            row = [kind]
            for m in ("shift", "align"):
                sub = [r for r in ROWS if r[1] in ids and r[2] == m]
                if sub:
                    row.append(f"{sum(r[3] for r in sub) / len(sub):.2f}")
                else:
                    row.append("-")
            f.write(f"| {row[0]} | {row[1]} | {row[2]} |\n")
        # 最も評価が高い / 低い
        f.write("\n## ハイライト\n\n")
        best_style = max(ROWS, key=lambda r: (r[3], r[4]))
        worst_style = min(ROWS, key=lambda r: (r[3], r[4]))
        f.write(f"- 最高評価 (画風 {best_style[3]} / 位置 {best_style[4]}): "
                f"**{best_style[0]}** — {best_style[5]}\n")
        f.write(f"- 最低評価 (画風 {worst_style[3]} / 位置 {worst_style[4]}): "
                f"**{worst_style[0]}** — {worst_style[5]}\n")
    print(f"saved Markdown: {out_path}")


def main() -> int:
    grid_dir = _ROOT / "sketch_variations/grid_16_20260528_191328"
    if not grid_dir.exists():
        print(f"ERROR: grid_dir not found: {grid_dir}")
        return 1
    to_csv(grid_dir / "evaluation_table.csv")
    to_markdown(grid_dir / "EVALUATION.md")
    print(f"\n出力先: {grid_dir.relative_to(_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
