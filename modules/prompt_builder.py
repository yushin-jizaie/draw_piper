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
    "no background, no texture, no shading, "
    "preserve original line positions and scale exactly, "
    "do not redraw existing lines, only add minimal new details"
)
# 「centered composition」 / 「isolated subject」 は 2026-05-27 に削除。
# これらが SDXL に 「フレーム中央に再配置」 を促し、 ユーザ入力 (顔等)
# の位置と生成画像のパーツ位置がズレる原因だった。 ControlNet
# conditioning_scale を 1.0 に上げて入力位置を厳守させる。

_FALLBACK_TEMPLATE = (
    "a simple line drawing on plain white background, "
    "single continuous black line, clean smooth strokes, "
    "minimalist illustration, no texture, no shading"
)


COMPANION_TEMPLATE = (
    "a stylish Matsumoto-style {subject_en} {action_en} {location_en}, "
    # 2026-05-31: 線の細密さより「構図・デザイン性」 を重視。 簡潔な線でも
    # 魅力的に見えるよう、 dynamic な構図 / 大きな主題 / 大胆なデザインを明示。
    "dynamic striking composition, appealing bold design, "
    "large subject filling the frame, interesting angle, "
    "manga style, bold confident ink lines, "
    "single continuous black line on plain white background, "
    "clean smooth strokes, illustrative, "
    # Frida 適合: discrete contours + ハッチング禁止 + 20-40 stroke 目安
    # (docs/frida_stroke_guideline.md)
    "discrete clean contours per element, no shading, "
    "no hatching, no cross-hatching, approximately 20 to 40 separate strokes"
)

COMPANION_FALLBACK_TEMPLATE = (
    "a stylish Matsumoto-style illustration, "
    "appealing bold design, dynamic composition, "
    "manga style, expressive ink lines, "
    "single continuous black line on plain white background, "
    "clean smooth strokes, discrete clean contours per element, "
    "no shading, no hatching, no cross-hatching"
)


# 重ね合わせモード (M15/M16 character) 用。 IP-Adapter two-stage の
# Stage 1 prompt として使う想定。 VLM の subject を 主役にしつつ、
# style ref で 松本タッチを後段の Stage 2 で重ねる。
CHARACTER_TEMPLATE = (
    "{subject_en} {action_en} {location_en}, "
    # 2026-05-31: 構図・デザイン性重視。 簡潔な線でも魅力的に。
    "manga style character, dynamic striking pose, appealing bold design, "
    "interesting angle, bold confident ink lines, clean lineart, "
    "single continuous black line on plain white background, "
    "clean smooth strokes, "
    # Frida 適合 (docs/frida_stroke_guideline.md)
    "discrete clean contours per element, no shading, "
    "no hatching, no cross-hatching, approximately 20 to 40 separate strokes"
)

CHARACTER_FALLBACK_TEMPLATE = (
    "1boy, solo, young boy with full body, messy hair, simple t-shirt, "
    "manga style character, dynamic pose, expressive ink lines, "
    "bold confident lineart, single continuous black line on plain white background, "
    "appealing bold design, clean smooth strokes, discrete clean contours per element, "
    "no shading, no hatching, no cross-hatching"
)


def build_prompt(guess: TopicGuess, confidence_threshold: float = 0.3,
                  base_template: str = None,
                  fallback_template: str = None) -> str:
    """TopicGuess を SDXL Turbo 用の英語プロンプトに変換。

    confidence が閾値以下、または subject が UNKNOWN の場合は
    中立的なフォールバックプロンプトを返す。

    base_template / fallback_template を渡すと、 組込み既定を上書き
    可能 (GUI からのプロンプト編集用)。 None の時は組込み既定。
    """
    base = base_template if base_template else _BASE_TEMPLATE
    fallback = fallback_template if fallback_template else _FALLBACK_TEMPLATE

    if guess.confidence < confidence_threshold or guess.subject is UNKNOWN_SUBJECT:
        # カード分類が低 confidence / 不明。 リテラル記述 (例: "circle") が
        # あれば、 それを subject として base テンプレに埋める (汎用フォールバック
        # は入力を完全に無視するため、 リテラル記述の方が入力に即した絵になる)。
        literal = (getattr(guess, "literal_en", "") or "").strip()
        if literal:
            return _normalize_spaces(base.format(
                subject_en=literal, action_en="", location_en=""))
        return _normalize_spaces(fallback)

    return _normalize_spaces(base.format(
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

    print("\n=== companion mode (Matsumoto style) ===")
    g5 = TopicGuess(
        subject=find_subject("猫"),
        location=UNKNOWN_LOCATION,
        action=UNKNOWN_ACTION,
        confidence=0.7,
    )
    print(f"  input:  {g5.to_text()}")
    print(f"  prompt: {build_prompt(g5, base_template=COMPANION_TEMPLATE, fallback_template=COMPANION_FALLBACK_TEMPLATE)}")

    print("\n=== companion fallback (low confidence) ===")
    g6 = TopicGuess(
        subject=find_subject("猫"),
        location=UNKNOWN_LOCATION,
        action=UNKNOWN_ACTION,
        confidence=0.1,
    )
    print(f"  input:  {g6.to_text()}")
    print(f"  prompt: {build_prompt(g6, base_template=COMPANION_TEMPLATE, fallback_template=COMPANION_FALLBACK_TEMPLATE)}")

    print("\n=== character mode (Stage 1 prompt) ===")
    g7 = TopicGuess(
        subject=find_subject("人"),
        location=find_location("公園"),
        action=find_action("走っている"),
        confidence=0.8,
    )
    print(f"  input:  {g7.to_text()}")
    print(f"  prompt: {build_prompt(g7, base_template=CHARACTER_TEMPLATE, fallback_template=CHARACTER_FALLBACK_TEMPLATE)}")

    print("\n=== character fallback (low conf → 1boy default) ===")
    g8 = TopicGuess(
        subject=UNKNOWN_SUBJECT,
        location=UNKNOWN_LOCATION,
        action=UNKNOWN_ACTION,
        confidence=0.1,
    )
    print(f"  input:  {g8.to_text()}")
    print(f"  prompt: {build_prompt(g8, base_template=CHARACTER_TEMPLATE, fallback_template=CHARACTER_FALLBACK_TEMPLATE)}")
