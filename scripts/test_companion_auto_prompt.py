#!/usr/bin/env python3
"""Companion mode --auto-prompt のロジック smoke test (mock VLM)。

実 VLM (Qwen2.5-VL-7B + transformers + torch) を起動せずに、
TopicGuess → COMPANION_TEMPLATE → companion prompt の生成経路だけ確認する。

実機 VLM テストは ユーザ側で:
  ./venv/bin/python -m scripts.test_companion_mode \\
      --user-sketch <sketch.png> --auto-prompt --output logs/companion_<ts>

ここでは prompt_builder の組み合わせだけ検証 (PIL/torch/transformers 不要)。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


def _expect(condition: bool, msg: str) -> None:
    if not condition:
        raise AssertionError(msg)


def test_high_confidence_cat() -> None:
    from modules.topic import (
        TopicGuess, find_subject, find_location, find_action,
    )
    from modules.prompt_builder import (
        build_prompt, COMPANION_TEMPLATE, COMPANION_FALLBACK_TEMPLATE,
    )
    g = TopicGuess(
        subject=find_subject("猫"),
        location=find_location("公園"),
        action=find_action("寝ている"),
        confidence=0.85,
    )
    p = build_prompt(
        g, confidence_threshold=0.3,
        base_template=COMPANION_TEMPLATE,
        fallback_template=COMPANION_FALLBACK_TEMPLATE,
    )
    _expect("Matsumoto-style cat" in p, f"missing 'Matsumoto-style cat': {p}")
    _expect("sleeping" in p, f"missing 'sleeping': {p}")
    _expect("in a park" in p, f"missing 'in a park': {p}")
    print(f"[smoke] high-conf companion prompt OK\n  {p}")


def test_subject_only_no_location_action() -> None:
    """v2 demo の典型ケース: subject だけ判定、 location/action UNKNOWN。"""
    from modules.topic import (
        TopicGuess, find_subject, UNKNOWN_LOCATION, UNKNOWN_ACTION,
    )
    from modules.prompt_builder import (
        build_prompt, COMPANION_TEMPLATE, COMPANION_FALLBACK_TEMPLATE,
    )
    g = TopicGuess(
        subject=find_subject("家"),
        location=UNKNOWN_LOCATION,
        action=UNKNOWN_ACTION,
        confidence=0.7,
    )
    p = build_prompt(
        g, confidence_threshold=0.3,
        base_template=COMPANION_TEMPLATE,
        fallback_template=COMPANION_FALLBACK_TEMPLATE,
    )
    _expect("Matsumoto-style house" in p, f"missing 'Matsumoto-style house': {p}")
    _expect("manga style" in p, f"missing 'manga style': {p}")
    # location/action の prefix が剥がれていることも確認
    _expect(",," not in p, f"double comma indicates empty slot leak: {p}")
    print(f"[smoke] subject-only companion prompt OK\n  {p}")


def test_low_confidence_fallback() -> None:
    from modules.topic import (
        TopicGuess, find_subject, UNKNOWN_LOCATION, UNKNOWN_ACTION,
    )
    from modules.prompt_builder import (
        build_prompt, COMPANION_TEMPLATE, COMPANION_FALLBACK_TEMPLATE,
    )
    g = TopicGuess(
        subject=find_subject("猫"),
        location=UNKNOWN_LOCATION,
        action=UNKNOWN_ACTION,
        confidence=0.1,
    )
    p = build_prompt(
        g, confidence_threshold=0.3,
        base_template=COMPANION_TEMPLATE,
        fallback_template=COMPANION_FALLBACK_TEMPLATE,
    )
    _expect("Matsumoto-style illustration" in p, f"missing fallback marker: {p}")
    _expect("cat" not in p, f"subject leaked into fallback: {p}")
    print(f"[smoke] low-conf companion fallback OK\n  {p}")


def test_unknown_subject_fallback() -> None:
    from modules.topic import (
        TopicGuess, UNKNOWN_SUBJECT, find_location, find_action,
    )
    from modules.prompt_builder import (
        build_prompt, COMPANION_TEMPLATE, COMPANION_FALLBACK_TEMPLATE,
    )
    g = TopicGuess(
        subject=UNKNOWN_SUBJECT,
        location=find_location("公園"),
        action=find_action("走っている"),
        confidence=0.7,
    )
    p = build_prompt(
        g, confidence_threshold=0.3,
        base_template=COMPANION_TEMPLATE,
        fallback_template=COMPANION_FALLBACK_TEMPLATE,
    )
    _expect("Matsumoto-style illustration" in p, f"missing fallback marker: {p}")
    # location/action はあるが subject 不明なので fallback
    _expect("in a park" not in p, f"location leaked into fallback: {p}")
    print(f"[smoke] unknown-subject fallback OK\n  {p}")


def main() -> int:
    tests = [
        test_high_confidence_cat,
        test_subject_only_no_location_action,
        test_low_confidence_fallback,
        test_unknown_subject_fallback,
    ]
    for t in tests:
        print(f"\n--- {t.__name__} ---")
        t()
    print(f"\n[smoke] all {len(tests)} PASS.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
