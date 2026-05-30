"""VLM intent prediction with topic catalog (v0.5).

Qwen2.5-VL-7B-Instruct (NF4 量子化) を使い、ホワイトボード上のスケッチから
ユーザの意図を「お題カタログ内の選択肢」として推測する。

ユーザは物理カードを各カテゴリから1枚ずつ引いてお題を決めるが、
VLM はその選択肢リスト (カタログ) は知っているものの、引かれた具体的な
お題は知らない。VLM はスケッチを見て、カタログの中から最も近いものを当てる。

VRAM 実測: 5.5GB load / 6.0GB peak / 23.8 tok/s on RTX 2000 Ada
See: docs/20260522_2250_vlm_vram_measurement.md
See: docs/20260522_2330_drawing_system_v05_design.md
"""

from __future__ import annotations

import gc
import time
from pathlib import Path
from typing import Union

import numpy as np
import torch
from PIL import Image

from modules.topic import (
    SUBJECTS, LOCATIONS, ACTIONS,
    TopicGuess,
    format_choices,
    parse_vlm_json,
)

ImageLike = Union[Image.Image, str, Path, np.ndarray]


# --- VLM プロンプト (選択肢付き、JSON 要求) -----------------------------------

def _build_prompt() -> str:
    """選択肢付き JSON 要求プロンプトを動的構築。"""
    return f"""あなたはユーザが何を描こうとしているかを推測するアシスタントです。

ユーザは「主体」「場所」「動作」のお題カードを各カテゴリから1枚ずつ引いて、
それを元にホワイトボードに絵を描こうとしています。お題そのものはあなたには
教えられません。あなたはスケッチを見て、以下の選択肢の中から最も近いものを
推測してください。

【主体の選択肢】
{format_choices(SUBJECTS)}

【場所の選択肢】
{format_choices(LOCATIONS)}

【動作の選択肢】
{format_choices(ACTIONS)}

スケッチが選択肢のどれにも当てはまらないと判断したカテゴリは "不明" としてください。

回答は以下の JSON 形式のみで返してください。前後に説明文や ```json などのマークダウンを入れないでください。

{{
  "subject_ja": "<選択肢の中から1つ、または '不明'>",
  "location_ja": "<選択肢の中から1つ、または '不明'>",
  "action_ja": "<選択肢の中から1つ、または '不明'>",
  "missing_elements": ["<まだ描かれていない要素を日本語で短く列挙>"],
  "confidence": <0.0 から 1.0 の数値。ほぼ白紙なら 0.1 程度、明確に判別できれば 0.8+>
}}
"""


# --- 画像入力の正規化 -------------------------------------------------------

def _normalize_image(image: ImageLike) -> Image.Image:
    """様々な画像入力形式を PIL.Image (RGB) に正規化。"""
    if isinstance(image, Image.Image):
        return image.convert("RGB") if image.mode != "RGB" else image
    if isinstance(image, (str, Path)):
        return Image.open(image).convert("RGB")
    if isinstance(image, np.ndarray):
        if image.ndim == 2:
            return Image.fromarray(image).convert("RGB")
        if image.ndim == 3 and image.shape[2] in (3, 4):
            return Image.fromarray(image[..., :3]).convert("RGB")
        raise ValueError(f"unsupported ndarray shape: {image.shape}")
    raise TypeError(f"unsupported image type: {type(image)}")


# --- VLM 本体 ---------------------------------------------------------------

class VLM:
    """Qwen2.5-VL-7B based topic guesser.

    Memory budget (measured 2026-05-22 on RTX 2000 Ada):
      - model load:    ~5.5GB
      - inference peak: ~6.0GB
      - speed:         ~24 tok/s (256 tokens / 6.4s)
    """

    DEFAULT_MODEL_ID = "Qwen/Qwen2.5-VL-7B-Instruct"

    def __init__(
        self,
        model_id: str = DEFAULT_MODEL_ID,
        device: str = "cuda:0",
        max_new_tokens: int = 256,
        quantization: str = "nf4",
        verbose: bool = False,
    ):
        self.model_id = model_id
        self.device = device
        self.max_new_tokens = max_new_tokens
        self.quantization = quantization
        self.verbose = verbose

        self._model = None
        self._processor = None
        self._prompt_text = _build_prompt()

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        if self.is_loaded:
            return

        from transformers import (
            Qwen2_5_VLForConditionalGeneration,
            AutoProcessor,
            BitsAndBytesConfig,
        )

        kwargs: dict = {"device_map": self.device}
        if self.quantization == "nf4":
            kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.float16,
                bnb_4bit_use_double_quant=True,
            )
        elif self.quantization == "fp16":
            kwargs["torch_dtype"] = torch.float16
        elif self.quantization is None:
            kwargs["torch_dtype"] = "auto"
        else:
            raise ValueError(f"unknown quantization: {self.quantization!r}")

        if self.verbose:
            print(f"[vlm] loading {self.model_id} ({self.quantization})...")
        t0 = time.time()
        self._model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            self.model_id, **kwargs
        )
        self._processor = AutoProcessor.from_pretrained(self.model_id)
        if self.verbose:
            print(f"[vlm] loaded in {time.time() - t0:.1f}s")

    def unload(self) -> None:
        if not self.is_loaded:
            return
        del self._model
        del self._processor
        self._model = None
        self._processor = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def __enter__(self) -> "VLM":
        self.load()
        return self

    def __exit__(self, *exc) -> None:
        self.unload()

    # 2026-05-29: shift モード用 companion subject 推論プロンプト 3 パターン。
    # 入力 sketch の主題に対し、 隣に配置すると自然な「別の」 主題を 1 語で提案。
    # v1 = 現状 (関係パターンを方向ヒント)
    # v2 = noun-only 強化 (形容詞 "angry" 失敗対策)
    # v3 = 「動詞 + 物」 物語性重視 (情景的なフレーズ可)

    _COMPANION_PROMPT_V2 = (
        "You see a simple line-art sketch.\n"
        "Look at it, then name ONE other concrete object/being that would "
        "naturally appear next to it in the same drawing.\n\n"
        "Strict rules — failure to follow voids the answer:\n"
        "1. Output MUST be a noun (a thing you can point at), NOT an adjective "
        "and NOT a feeling.\n"
        "   - Wrong: angry, happy, fast, dark, sleeping, broken, sad\n"
        "   - Right: bird, person, chair, cloud, hand, fish, lamp\n"
        "2. The noun must refer to a DIFFERENT KIND of thing from what's drawn.\n"
        "   - If the sketch is a face, do NOT propose another face.\n"
        "3. Pick something a line-art artist can easily draw.\n"
        "4. Lowercase. No article. No punctuation. Single word.\n\n"
        "Think first about what naturally accompanies the main subject "
        "(tools need users; vehicles need riders; plants need animals or "
        "weather; foods need eaters; weather affects objects; buildings "
        "anchor scenes). Then output one matching concrete noun.\n\n"
        "Output:"
    )

    _COMPANION_PROMPT_V3 = (
        "You see a simple line-art sketch.\n"
        "Identify the main subject. Then imagine a short story moment that "
        "completes the scene, and name what to draw NEXT TO the main subject "
        "to tell that story.\n\n"
        "The accompanying element should:\n"
        "- be a concrete drawable thing (object, animal, person, weather, etc.)\n"
        "- be DIFFERENT in kind from the main subject\n"
        "- evoke an action or cause-and-effect with the main subject\n\n"
        "Inspiration for the *kind of relationship* (these are pattern hints, "
        "not vocabulary — apply the idea to whatever you see):\n"
        "- a tool implies its user mid-action (scissors → cutting hand)\n"
        "- a vehicle implies motion (bicycle → rider leaning)\n"
        "- a plant implies a tiny visitor or weather (tree → bird flying, "
        "tree → falling leaves)\n"
        "- a container implies what fills it (bowl → steaming soup)\n"
        "- weather implies who reacts (rain → person under umbrella)\n"
        "- food implies eating (apple → bite mark)\n"
        "- a creature implies its prey, pet, or counterpart\n\n"
        "Output format: a short phrase 1-3 words (noun, optionally with a "
        "describing verb participle). Lowercase, no article, no period.\n"
        "Examples of acceptable phrase shapes (do not copy these literally):\n"
        "  'flying bird'   'falling leaf'   'cutting hand'   'curled cat'\n\n"
        "Output:"
    )

    # 互換: 既存 _COMPANION_PROMPT_TEXT は v1 として残す
    _COMPANION_PROMPT_TEXT = (
        "You are looking at a simple line-art sketch.\n"
        "Step 1: identify the main subject of the sketch silently in your head.\n"
        "Step 2: propose ONE different subject that would naturally accompany or "
        "complement the main subject, as if drawn next to it in the same scene.\n\n"
        "Guidelines:\n"
        "- The proposed companion must NOT be the same kind of object as the main subject.\n"
        "- Choose something that makes the scene feel richer, tells a small story, or "
        "shows a natural cause/effect relationship.\n"
        "- Pick a single, concrete, drawable thing — preferably one word.\n"
        "- Avoid abstract concepts (love, time, music). Prefer tangible things "
        "(animal, person, weather, object, plant, furniture, vehicle).\n\n"
        "Examples of the *shape* of relationship to use (do NOT copy these literally, "
        "just understand the pattern):\n"
        "- tool → its user or what it acts upon\n"
        "- vehicle → its rider, passenger, or the road\n"
        "- plant → an animal, weather, or season element near it\n"
        "- container → its content or what fills it\n"
        "- food → an eater, utensil, or table setting\n"
        "- weather → what it affects (umbrella, puddle, shivering person)\n"
        "- building → a person entering it, a vehicle near it, or a tree beside it\n\n"
        "Apply the same kind of associative thinking to whatever you see. "
        "If the main subject is unclear, pick any plausible companion that "
        "would form a coherent line-art scene.\n\n"
        "Output format: a single English noun in lowercase, no article, no "
        "punctuation, no explanation. Output ONLY the noun."
    )

    # 2026-05-30 (Phase 1): composition refinement プロンプト 正式化。
    # 入力 sketch の主題を「より魅力的な構図」 に翻訳。 orientation や pose、
    # angle、 motion などを含む。 ハードコード例なし、 方向ヒントで汎化。
    _COMPOSITION_PROMPT_TEXT = (
        "You see a simple line-art sketch.\n"
        "The user drew the main subject in a basic pose / viewpoint, but "
        "rendering it as-is in detail would feel static and boring.\n"
        "Propose a more interesting composition for the SAME subject "
        "(do not change what the subject is — only refine HOW it is posed/"
        "viewed/captured).\n\n"
        "Apply ONE of these kinds of refinement (or invent a similar one):\n"
        "- animals: face one way, body another (looking back, twisting, "
        "mid-jump, curled)\n"
        "- vehicles: 3/4 angle, motion blur, slight tilt, in motion\n"
        "- people: dynamic pose, mid-stride, action, leaning, gesture\n"
        "- buildings/structures: low-angle view, perspective, dramatic angle\n"
        "- plants: low-angle, weather element (wind-bent, with falling leaves)\n"
        "- static objects: tilted angle, partial occlusion, dramatic lighting hint\n\n"
        "These are *patterns* to apply — do not copy literally. Look at the "
        "actual sketch and invent a refinement that fits it.\n\n"
        "Output format: a short phrase (3-8 words) describing the refinement. "
        "Lowercase, no period, no quotes. Just the phrase.\n"
        "Examples of the *shape* of acceptable phrases (not vocabulary to "
        "copy):\n"
        "  'looking back over shoulder'   'in mid-stride from behind'\n"
        "  'three-quarter view with motion'   'low-angle dramatic'\n"
        "  'curled up sleeping pose'   'leaning with one foot raised'\n\n"
        "Output:"
    )

    def predict_composition_refinement(self, image: ImageLike) -> str:
        """sketch から「魅力的な構図」 refinement phrase を 1 つ返す。

        例: 正面の猫 → 'looking back over shoulder'、
        横向きの車 → '3/4 angle with motion lines' 等。
        """
        if not self.is_loaded:
            self.load()
        from qwen_vl_utils import process_vision_info
        pil_image = _normalize_image(image)
        messages = [{
            "role": "user",
            "content": [
                {"type": "image", "image": pil_image},
                {"type": "text", "text": self._COMPOSITION_PROMPT_TEXT},
            ],
        }]
        text_template = self._processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True)
        image_inputs, video_inputs = process_vision_info(messages)
        inputs = self._processor(
            text=[text_template],
            images=image_inputs, videos=video_inputs,
            padding=True, return_tensors="pt",
        ).to(self.device)
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        t0 = time.time()
        with torch.inference_mode():
            output_ids = self._model.generate(
                **inputs, max_new_tokens=32, do_sample=False)
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        infer_time = time.time() - t0
        generated = output_ids[:, inputs.input_ids.shape[1]:]
        raw_text = self._processor.batch_decode(
            generated, skip_special_tokens=True)[0].strip()
        import re
        cleaned = raw_text.replace("\n", " ").replace('"', "").replace("'", "")
        cleaned = re.sub(r"[.!?,;:]+$", "", cleaned).strip().lower()
        words = cleaned.split()
        phrase = " ".join(words[:8])
        if self.verbose:
            print(f"[vlm] composition '{phrase}' from '{raw_text}' "
                  f"({infer_time:.2f}s)")
        return phrase

    def predict_companion_subject(self, image: ImageLike,
                                    prompt_version: str = "v1") -> str:
        """スケッチ画像から companion subject (関連する別の subject) を 1 単語で返す。

        shift モード (位置ずらし) 用。 入力主題と「同じもの」 ではなく、 自然に
        組み合わさる別の subject を VLM に提案させる。 例えば:
          ハサミ → hand、 自転車 → rider、 木 → bird、 傘 → rain、 鍋 → soup
        例示は VLM に渡す prompt の「方向性ヒント」 として埋め込み済 (汎化を促す)。

        Returns
        -------
        英語の単数名詞 (lowercase、 article なし)。 例: "bird", "person", "umbrella"
        """
        if not self.is_loaded:
            self.load()
        from qwen_vl_utils import process_vision_info
        # prompt version 選択
        prompt_map = {
            "v1": self._COMPANION_PROMPT_TEXT,
            "v2": self._COMPANION_PROMPT_V2,
            "v3": self._COMPANION_PROMPT_V3,
        }
        prompt = prompt_map.get(prompt_version, self._COMPANION_PROMPT_TEXT)
        pil_image = _normalize_image(image)
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": pil_image},
                    {"type": "text", "text": prompt},
                ],
            }
        ]
        text_template = self._processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        image_inputs, video_inputs = process_vision_info(messages)
        inputs = self._processor(
            text=[text_template],
            images=image_inputs,
            videos=video_inputs,
            padding=True,
            return_tensors="pt",
        ).to(self.device)
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        t0 = time.time()
        with torch.inference_mode():
            output_ids = self._model.generate(
                **inputs, max_new_tokens=16, do_sample=False
            )
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        infer_time = time.time() - t0
        generated = output_ids[:, inputs.input_ids.shape[1]:]
        raw_text = self._processor.batch_decode(
            generated, skip_special_tokens=True
        )[0].strip()
        # 単語/フレーズ抽出 (改行 / 句読点 / 冠詞を除去)
        import re
        cleaned = re.sub(r"[^a-zA-Z\s-]", " ", raw_text).strip().lower()
        words = cleaned.split()
        # よくある article を除去
        articles = {"a", "an", "the"}
        words = [w for w in words if w not in articles]
        # v3 は phrase (1-3 単語) 許容、 v1/v2 は単一単語
        max_words = 3 if prompt_version == "v3" else 1
        companion = " ".join(words[:max_words]) if words else "person"
        if self.verbose:
            print(
                f"[vlm] companion ({prompt_version}) '{companion}' "
                f"from '{raw_text}' ({infer_time:.2f}s)"
            )
        return companion

    def pick_best_companion(self, image: ImageLike,
                              candidates: list) -> tuple:
        """3 候補から best を 1 つ選ぶ meta-judging。

        Parameters
        ----------
        image : ImageLike
            入力 sketch (companion を選ぶ context)
        candidates : list of (version, name) tuples or list of names
            候補 companion 名のリスト。 例: [("v1", "balloon"), ("v2", "cloud"),
            ("v3", "smiling face")]

        Returns
        -------
        (chosen_index, chosen_name, infer_time_s) : tuple
            chosen_index は候補リストの 0-indexed 位置。 不明な場合は 0。
        """
        if not self.is_loaded:
            self.load()
        from qwen_vl_utils import process_vision_info
        # candidates を正規化 ((version, name) → name のみのリスト)
        names = []
        labels = []
        for c in candidates:
            if isinstance(c, tuple) and len(c) >= 2:
                labels.append(str(c[0]))
                names.append(str(c[1]))
            else:
                labels.append(f"#{len(names)+1}")
                names.append(str(c))
        if not names:
            return (0, "person", 0.0)
        # judge prompt: 候補を提示して、 sketch との相性で 1 つ選ばせる
        cand_list_text = "\n".join(
            f"  {i+1}. {labels[i]}: {names[i]}"
            for i in range(len(names))
        )
        judge_prompt = (
            "You see a simple line-art sketch.\n\n"
            "Three different ideas have been proposed for what to draw NEXT TO "
            "the main subject as a scene companion:\n\n"
            f"{cand_list_text}\n\n"
            "Evaluate them against these criteria:\n"
            "1. Is it a concrete drawable noun (not an adjective like 'angry')?\n"
            "2. Is it DIFFERENT in kind from what's in the sketch (not the same "
            "type of object)?\n"
            "3. Does it form a natural, story-telling pair with the sketch?\n"
            "4. Would it look good as a simple ink-line drawing next to the sketch?\n\n"
            "Pick the SINGLE BEST candidate.\n"
            "Output format: only the chosen number (1, 2, or 3). No explanation, "
            "no punctuation, just one digit."
        )
        pil_image = _normalize_image(image)
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": pil_image},
                    {"type": "text", "text": judge_prompt},
                ],
            }
        ]
        text_template = self._processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        image_inputs, video_inputs = process_vision_info(messages)
        inputs = self._processor(
            text=[text_template],
            images=image_inputs,
            videos=video_inputs,
            padding=True,
            return_tensors="pt",
        ).to(self.device)
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        t0 = time.time()
        with torch.inference_mode():
            output_ids = self._model.generate(
                **inputs, max_new_tokens=8, do_sample=False
            )
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        infer_time = time.time() - t0
        generated = output_ids[:, inputs.input_ids.shape[1]:]
        raw_text = self._processor.batch_decode(
            generated, skip_special_tokens=True
        )[0].strip()
        # 1-3 の数字を抽出
        import re
        m = re.search(r"\b([123])\b", raw_text)
        if m:
            chosen_idx = int(m.group(1)) - 1
        else:
            chosen_idx = 0
        chosen_idx = max(0, min(chosen_idx, len(names) - 1))
        chosen_name = names[chosen_idx]
        if self.verbose:
            print(
                f"[vlm] judge picked #{chosen_idx + 1} "
                f"({labels[chosen_idx]}: '{chosen_name}') "
                f"from {names} ({infer_time:.2f}s) raw='{raw_text}'"
            )
        return (chosen_idx, chosen_name, infer_time)

    def predict_intent(self, image: ImageLike) -> TopicGuess:
        """スケッチ画像から TopicGuess を返す。例外は投げない。"""
        if not self.is_loaded:
            self.load()

        from qwen_vl_utils import process_vision_info

        pil_image = _normalize_image(image)

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": pil_image},
                    {"type": "text", "text": self._prompt_text},
                ],
            }
        ]

        text_template = self._processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        image_inputs, video_inputs = process_vision_info(messages)
        inputs = self._processor(
            text=[text_template],
            images=image_inputs,
            videos=video_inputs,
            padding=True,
            return_tensors="pt",
        ).to(self.device)

        if torch.cuda.is_available():
            torch.cuda.synchronize()
        t0 = time.time()
        with torch.inference_mode():
            output_ids = self._model.generate(
                **inputs, max_new_tokens=self.max_new_tokens, do_sample=False
            )
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        infer_time = time.time() - t0

        generated = output_ids[:, inputs.input_ids.shape[1]:]
        n_tokens = int(generated.shape[1])
        raw_text = self._processor.batch_decode(
            generated, skip_special_tokens=True
        )[0]

        subject, location, action, missing, confidence = parse_vlm_json(raw_text)

        result = TopicGuess(
            subject=subject,
            location=location,
            action=action,
            missing_elements=missing,
            confidence=confidence,
            raw_text=raw_text,
            infer_time_s=infer_time,
            n_tokens=n_tokens,
        )
        if self.verbose:
            status = "OK" if result.has_known_subject() else "UNKNOWN"
            print(
                f"[vlm] inferred in {infer_time:.2f}s "
                f"({n_tokens} tok, {n_tokens / infer_time:.1f} tok/s) "
                f"[{status}, conf={confidence:.2f}]"
            )
        return result


# --- スモークテスト ---------------------------------------------------------

def _make_dummy_sketch(path: Path) -> None:
    """簡単な顔っぽいスケッチをダミー入力として生成。"""
    from PIL import ImageDraw
    img = Image.new("RGB", (1024, 768), (245, 245, 245))
    d = ImageDraw.Draw(img)
    d.ellipse([400, 250, 600, 450], outline=(30, 30, 30), width=4)
    d.ellipse([445, 320, 475, 350], fill=(30, 30, 30))
    d.ellipse([525, 320, 555, 350], fill=(30, 30, 30))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, quality=92)


if __name__ == "__main__":
    test_image = Path("scripts/test_sketch.jpg")
    if not test_image.exists():
        _make_dummy_sketch(test_image)
        print(f"[vlm] created dummy sketch: {test_image}")

    with VLM(verbose=True) as vlm:
        guess = vlm.predict_intent(test_image)

        print("\n=== TopicGuess ===")
        print(f"  subject       : {guess.subject.ja} ({guess.subject.en})")
        print(f"  location      : {guess.location.ja} ({guess.location.en})")
        print(f"  action        : {guess.action.ja} ({guess.action.en})")
        print(f"  missing       : {guess.missing_elements}")
        print(f"  confidence    : {guess.confidence:.2f}")
        print(f"  is_certain    : {guess.is_certain()}")
        print(f"  has_known     : {guess.has_known_subject()}")
        print(f"  n_tokens      : {guess.n_tokens}")
        print(f"  infer_time_s  : {guess.infer_time_s:.2f}")
        print(f"\n=== to_text ===")
        print(f"  {guess.to_text()}")
        print(f"\n=== raw_text ===")
        print(guess.raw_text)
