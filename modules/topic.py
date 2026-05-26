"""お題カタログ (Topic Catalog).

ユーザは「主体」「場所」「動作」のカードを物理的に各カテゴリから1枚ずつ引く。
システム (VLM) は引かれたカード自体は知らないが、選択肢のリストは知っている。
VLM はスケッチを見て、選択肢の中から最も近いものを推測する。

See: docs/20260522_2330_drawing_system_v05_design.md
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from typing import Optional


# --- TopicEntry: カタログの1要素 ---------------------------------------------

@dataclass(frozen=True)
class TopicEntry:
    """お題カタログの1エントリ。日英ペアで管理。"""
    ja: str
    en: str
    sdxl_hint: str = ""

    def __str__(self) -> str:
        return self.ja


# --- カテゴリごとのカタログ (MVP: 5x5x5 = 125通り) -----------------------------

SUBJECTS: list[TopicEntry] = [
    TopicEntry("犬", "dog"),
    TopicEntry("猫", "cat"),
    TopicEntry("鳥", "bird"),
    TopicEntry("ロボット", "robot"),
    TopicEntry("龍", "dragon"),
    # 2026-05-27 拡張: 子供のラクガキでよく出る対象を追加。 旧 5 種類だけ
    # だと スマイリーフェイス等 が VLM の消去法で 「ロボット」 になる
    # 問題への対処。
    TopicEntry("顔", "face"),
    TopicEntry("人", "person"),
    TopicEntry("家", "house"),
    TopicEntry("車", "car"),
    TopicEntry("花", "flower"),
    TopicEntry("木", "tree"),
    TopicEntry("魚", "fish"),
    TopicEntry("太陽", "sun"),
    TopicEntry("星", "star"),
]

LOCATIONS: list[TopicEntry] = [
    TopicEntry("公園", "in a park"),
    TopicEntry("海", "by the sea"),
    TopicEntry("山", "in the mountains"),
    TopicEntry("宇宙", "in space"),
    TopicEntry("森", "in a forest"),
]

ACTIONS: list[TopicEntry] = [
    TopicEntry("走っている", "running"),
    TopicEntry("寝ている", "sleeping"),
    TopicEntry("飛んでいる", "flying"),
    TopicEntry("食べている", "eating"),
    TopicEntry("踊っている", "dancing"),
]


# --- "Unknown" 定数 ---------------------------------------------------------

UNKNOWN_SUBJECT = TopicEntry("不明", "abstract shape")
UNKNOWN_LOCATION = TopicEntry("不明", "")
UNKNOWN_ACTION = TopicEntry("不明", "")


# --- TopicGuess: VLM の出力 -------------------------------------------------

@dataclass
class TopicGuess:
    """カタログ内分類によるユーザ意図推測。VLM.predict() の戻り値。"""
    subject: TopicEntry = UNKNOWN_SUBJECT
    location: TopicEntry = UNKNOWN_LOCATION
    action: TopicEntry = UNKNOWN_ACTION
    missing_elements: list[str] = field(default_factory=list)
    confidence: float = 0.0
    raw_text: str = ""
    infer_time_s: float = 0.0
    n_tokens: int = 0

    def is_certain(self, threshold: float = 0.3) -> bool:
        return self.confidence >= threshold

    def has_known_subject(self) -> bool:
        return self.subject is not UNKNOWN_SUBJECT

    def to_text(self) -> str:
        return (
            f"{self.subject}が{self.location}で{self.action} "
            f"(confidence={self.confidence:.2f})"
        )


# --- カタログ操作ユーティリティ ----------------------------------------------

def find_subject(ja_label: str) -> TopicEntry:
    return _find(SUBJECTS, ja_label, UNKNOWN_SUBJECT)


def find_location(ja_label: str) -> TopicEntry:
    return _find(LOCATIONS, ja_label, UNKNOWN_LOCATION)


def find_action(ja_label: str) -> TopicEntry:
    return _find(ACTIONS, ja_label, UNKNOWN_ACTION)


def _find(catalog: list[TopicEntry], ja_label: str, default: TopicEntry) -> TopicEntry:
    ja_label = (ja_label or "").strip()
    if not ja_label or ja_label == "不明":
        return default
    for entry in catalog:
        if entry.ja == ja_label:
            return entry
    return default


def format_choices(catalog: list[TopicEntry]) -> str:
    return "\n".join(f"- {e.ja}" for e in catalog)


# --- JSON パース ------------------------------------------------------------

def parse_vlm_json(raw_text: str) -> tuple[TopicEntry, TopicEntry, TopicEntry, list[str], float]:
    """VLM の JSON 応答をパースしてカタログエントリに解決する。

    パース失敗時は全フィールドを安全な default で埋めて返す。例外は投げない。
    """
    cleaned = _strip_fences(raw_text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return UNKNOWN_SUBJECT, UNKNOWN_LOCATION, UNKNOWN_ACTION, [], 0.0

    subject = find_subject(data.get("subject_ja", ""))
    location = find_location(data.get("location_ja", ""))
    action = find_action(data.get("action_ja", ""))

    missing = data.get("missing_elements", [])
    if not isinstance(missing, list):
        missing = []
    missing = [str(x).strip() for x in missing if str(x).strip()]

    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    return subject, location, action, missing, confidence


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


# --- ローカルテスト用 -------------------------------------------------------

def random_topic_for_test(seed: Optional[int] = None) -> tuple[TopicEntry, TopicEntry, TopicEntry]:
    rng = random.Random(seed)
    return (
        rng.choice(SUBJECTS),
        rng.choice(LOCATIONS),
        rng.choice(ACTIONS),
    )


# --- スモークテスト ---------------------------------------------------------

if __name__ == "__main__":
    print("=== カタログ ===")
    print(f"SUBJECTS ({len(SUBJECTS)}): {[e.ja for e in SUBJECTS]}")
    print(f"LOCATIONS ({len(LOCATIONS)}): {[e.ja for e in LOCATIONS]}")
    print(f"ACTIONS ({len(ACTIONS)}): {[e.ja for e in ACTIONS]}")
    print(f"組み合わせ数: {len(SUBJECTS) * len(LOCATIONS) * len(ACTIONS)}")

    print("\n=== format_choices ===")
    print(format_choices(SUBJECTS))

    print("\n=== ランダム例 ===")
    for i in range(3):
        s, l, a = random_topic_for_test()
        print(f"  {i+1}: {s} / {l} / {a}  (en: '{s.en} {a.en} {l.en}')")

    print("\n=== JSON パーステスト ===")

    test1 = '{"subject_ja": "犬", "location_ja": "公園", "action_ja": "走っている", "missing_elements": ["胴体", "脚"], "confidence": 0.85}'
    s, l, a, m, c = parse_vlm_json(test1)
    print(f"  case1: subject={s.ja}({s.en}) location={l.ja} action={a.ja} missing={m} conf={c}")

    test2 = '```json\n{"subject_ja": "猫", "location_ja": "不明", "action_ja": "寝ている", "missing_elements": [], "confidence": 0.5}\n```'
    s, l, a, m, c = parse_vlm_json(test2)
    print(f"  case2: subject={s.ja} location={l.ja} action={a.ja} conf={c}")

    test3 = '{"subject_ja": "恐竜", "location_ja": "海", "action_ja": "走っている", "missing_elements": [], "confidence": 0.6}'
    s, l, a, m, c = parse_vlm_json(test3)
    print(f"  case3 (recovers UNKNOWN for unknown subject): subject={s.ja}({s.en}) location={l.ja}")

    test4 = 'これは JSON ではない'
    s, l, a, m, c = parse_vlm_json(test4)
    print(f"  case4 (broken): subject={s.ja} location={l.ja} conf={c}")

    print("\n=== TopicGuess ===")
    guess = TopicGuess(
        subject=find_subject("犬"),
        location=find_location("公園"),
        action=find_action("走っている"),
        missing_elements=["胴体", "脚"],
        confidence=0.85,
    )
    print(f"  to_text: {guess.to_text()}")
    print(f"  is_certain(0.3): {guess.is_certain(0.3)}")
    print(f"  has_known_subject: {guess.has_known_subject()}")
