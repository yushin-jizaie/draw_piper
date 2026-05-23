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
