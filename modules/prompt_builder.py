"""SDXL Turbo prompt builder (v0.5).

TopicGuess (VLM の出力) を受け取り、SDXL Turbo + Lineart ControlNet 用の
英語プロンプトを構築する。

設計指針:
- 線画として安定生成しやすい語彙を使う
- TopicGuess が "不明" を含む / confidence が低い場合は中立的なフォールバック
- missing_elements は SDXL には渡さない (ControlNet が差分を見るため)

See: docs/20260522_2330_drawing_system_v05_design.md
"""

from __future__ import annotations

import re

from modules.topic import TopicGuess, UNKNOWN_SUBJECT


# 2026-05-27: 「中央クリーンな絵 + 周辺スクラッチ noise」 の生成画像が
# 多発する問題への対処。 旧版は "line art, minimal detail" が SDXL に
# 「全体を線で埋める」 と解釈されて背景にスクラッチを足してた。
# 新版は: 単一線描を明示 + 純白背景に分離 + ノイズ系を negative で抑制。

_BASE_TEMPLATE = (
    "a simple line drawing of {subject_en} {action_en} {location_en}, "
    "single continuous black line on plain white background, "
    "clean smooth strokes, minimalist illustration, "
    "isolated subject, no background, no texture, no shading, "
    "centered composition"
)

_FALLBACK_TEMPLATE = (
    "a simple line drawing on plain white background, "
    "single continuous black line, clean smooth strokes, "
    "minimalist illustration, no texture, no shading"
)


def build_prompt(guess: TopicGuess, confidence_threshold: float = 0.3) -> str:
    """TopicGuess を SDXL Turbo 用の英語プロンプトに変換。

    confidence が閾値以下、または subject が UNKNOWN の場合は
    中立的なフォールバックプロンプトを返す。
    """
    if guess.confidence < confidence_threshold:
        return _FALLBACK_TEMPLATE
    if guess.subject is UNKNOWN_SUBJECT:
        return _FALLBACK_TEMPLATE

    return _normalize_spaces(_BASE_TEMPLATE.format(
        subject_en=guess.subject.en,
        action_en=guess.action.en,
        location_en=guess.location.en,
    ))


def _normalize_spaces(s: str) -> str:
    """連続する空白を1つにまとめ、カンマ直前の空白も除去する。

    en が空 (UNKNOWN_LOCATION/UNKNOWN_ACTION) のとき
    "{subject_en}  , ..." のようにカンマ直前に空白が残るため、
    それを潰してから空白圧縮する。
    """
    s = re.sub(r"\s+,", ",", s)   # カンマ前の空白を除去
    return " ".join(s.split())    # 連続空白を 1 つに


# --- スモークテスト ---------------------------------------------------------

if __name__ == "__main__":
    from modules.topic import (
        find_subject, find_location, find_action,
        UNKNOWN_SUBJECT, UNKNOWN_LOCATION, UNKNOWN_ACTION,
    )

    print("=== 通常ケース (高信頼度) ===")
    g1 = TopicGuess(
        subject=find_subject("犬"),
        location=find_location("公園"),
        action=find_action("走っている"),
        confidence=0.85,
    )
    print(f"  input:  {g1.to_text()}")
    print(f"  prompt: {build_prompt(g1)}")

    print("\n=== confidence 低い → fallback ===")
    g2 = TopicGuess(
        subject=find_subject("猫"),
        location=find_location("海"),
        action=find_action("寝ている"),
        confidence=0.15,
    )
    print(f"  input:  {g2.to_text()}")
    print(f"  prompt: {build_prompt(g2)}")

    print("\n=== subject 不明 → fallback ===")
    g3 = TopicGuess(
        subject=UNKNOWN_SUBJECT,
        location=find_location("山"),
        action=find_action("飛んでいる"),
        confidence=0.5,
    )
    print(f"  input:  {g3.to_text()}")
    print(f"  prompt: {build_prompt(g3)}")

    print("\n=== location のみ不明 → location なしで生成 ===")
    g4 = TopicGuess(
        subject=find_subject("龍"),
        location=UNKNOWN_LOCATION,
        action=find_action("踊っている"),
        confidence=0.6,
    )
    print(f"  input:  {g4.to_text()}")
    print(f"  prompt: {build_prompt(g4)}")
