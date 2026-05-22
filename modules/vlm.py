"""VLM intent prediction (Qwen2.5-VL-7B-Instruct, NF4 quantization).

Predicts user drawing intent from a captured sketch image, returning a
structured `IntentPrediction` with subject / missing parts / next likely
additions.

Used by the v0.4 turn-based drawing loop. See:
- docs/20260521_1757_drawing_system_v04_design.md
- docs/20260522_2250_vlm_vram_measurement.md  (VRAM ~5.5GB, 23.8 tok/s on RTX 2000 Ada)
"""

from __future__ import annotations

import gc
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

import numpy as np
import torch
from PIL import Image

ImageLike = Union[Image.Image, str, Path, np.ndarray]


@dataclass
class IntentPrediction:
    """Structured user intent extracted by the VLM.

    Fields are populated from the model's free-form response via regex parsing.
    `raw_text` is always preserved for debugging or fallback when parsing fails.
    """
    subject: str = ""              # 主題 (e.g. "猫の顔")
    missing: list[str] = field(default_factory=list)  # 未完成な要素 (e.g. ["右耳", "ひげ"])
    next_likely: str = ""          # 次に描き足されそうな部分
    raw_text: str = ""             # モデル生出力 (parse失敗時もこれは入る)
    infer_time_s: float = 0.0      # 推論時間 (sec)
    n_tokens: int = 0              # 生成トークン数

    def to_text(self) -> str:
        """Return a single-line summary for legacy `build_prompt(str)` callers.

        Falls back to `raw_text` if structured fields are empty.
        """
        if self.subject:
            parts = [f"主題: {self.subject}"]
            if self.missing:
                parts.append(f"未完成: {', '.join(self.missing)}")
            if self.next_likely:
                parts.append(f"次の追加: {self.next_likely}")
            return " / ".join(parts)
        return self.raw_text.strip().replace("\n", " ")

    def is_parsed(self) -> bool:
        return bool(self.subject)


# --- デフォルトプロンプト (設計v0.4の意図予測テンプレ) -----------------------

_DEFAULT_PROMPT = (
    "この画像はホワイトボードに人が描きかけのスケッチです。"
    "この人は何を描こうとしていますか？ "
    "主題、未完成な要素、次に描き足されそうな部分を答えてください。"
)

# --- 出力パース用の正規表現 -----------------------------------------------
# モデル出力例 (計測時の実観測):
#   1. **主題**: 人間の顔のスケッチ。
#   2. **未完成な要素**: 目と鼻が描かれていますが、口や顔の他の部分はまだ描かれていません。
#   3. **次に描き足されそうな部分**: 頭部全体、顔の輪郭、そして口や鼻の詳細な部分が追加されると考えられます。
#
# 番号 / 太字記号 / コロンの全角半角ゆらぎを許容。
_RE_SUBJECT = re.compile(
    r"(?:^|\n)\s*(?:\d+[.\)]\s*)?\**\s*主題\s*\**\s*[:：]\s*(.+?)(?=\n|$)",
    re.MULTILINE,
)
_RE_MISSING = re.compile(
    r"(?:^|\n)\s*(?:\d+[.\)]\s*)?\**\s*未完成(?:な要素|要素)?\s*\**\s*[:：]\s*(.+?)(?=\n|$)",
    re.MULTILINE,
)
_RE_NEXT = re.compile(
    r"(?:^|\n)\s*(?:\d+[.\)]\s*)?\**\s*次に描き足されそうな部分\s*\**\s*[:：]\s*(.+?)(?=\n|$)",
    re.MULTILINE,
)


def _split_missing(text: str) -> list[str]:
    """Split a free-form '未完成' description into individual items.

    Heuristic: split on common Japanese list separators, then clean.
    """
    # 「目、鼻、口」「右耳・左耳」「頭と胴」のような区切りを許容
    candidates = re.split(r"[、,\u3001・,]|\s+と\s+|\s+及び\s+|\s+や\s+", text)
    return [c.strip().rstrip("。．.") for c in candidates if c.strip()]


def _parse_response(text: str) -> tuple[str, list[str], str]:
    """Parse the model's free-form response into (subject, missing, next_likely)."""
    subject = ""
    missing: list[str] = []
    next_likely = ""

    if m := _RE_SUBJECT.search(text):
        subject = m.group(1).strip().rstrip("。．.")
    if m := _RE_MISSING.search(text):
        missing = _split_missing(m.group(1).strip().rstrip("。．."))
    if m := _RE_NEXT.search(text):
        next_likely = m.group(1).strip().rstrip("。．.")

    return subject, missing, next_likely


# --- 画像入力の正規化 -------------------------------------------------------

def _normalize_image(image: ImageLike) -> Image.Image:
    """Normalize various image inputs into a PIL.Image (RGB)."""
    if isinstance(image, Image.Image):
        return image.convert("RGB") if image.mode != "RGB" else image
    if isinstance(image, (str, Path)):
        return Image.open(image).convert("RGB")
    if isinstance(image, np.ndarray):
        # OpenCV はデフォルトBGRなので注意。呼び出し側でRGB変換済みの想定。
        # チャンネル数だけ吸収。
        if image.ndim == 2:
            return Image.fromarray(image).convert("RGB")
        if image.ndim == 3 and image.shape[2] in (3, 4):
            return Image.fromarray(image[..., :3]).convert("RGB")
        raise ValueError(f"unsupported ndarray shape: {image.shape}")
    raise TypeError(f"unsupported image type: {type(image)}")


# --- VLM 本体 ---------------------------------------------------------------

class VLM:
    """Qwen2.5-VL-7B based intent predictor.

    Heavy resources (the model and processor) are loaded on first `load()` call
    or implicitly on `predict_intent()`. Call `unload()` to free VRAM.

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
        prompt_text: str = _DEFAULT_PROMPT,
        quantization: str = "nf4",  # "nf4" | "fp16" | None
        verbose: bool = False,
    ):
        self.model_id = model_id
        self.device = device
        self.max_new_tokens = max_new_tokens
        self.prompt_text = prompt_text
        self.quantization = quantization
        self.verbose = verbose

        self._model = None
        self._processor = None

    # --- ライフサイクル -----------------------------------------------------

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        """Load model and processor onto GPU. Idempotent."""
        if self.is_loaded:
            return

        # transformers / bnb は import コストが大きいので遅延 import
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
        """Release VRAM. Safe to call multiple times."""
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

    # --- 推論 --------------------------------------------------------------

    def predict_intent(self, image: ImageLike) -> IntentPrediction:
        """Predict the user's drawing intent from a captured image."""
        if not self.is_loaded:
            self.load()

        from qwen_vl_utils import process_vision_info

        pil_image = _normalize_image(image)

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": pil_image},
                    {"type": "text", "text": self.prompt_text},
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

        torch.cuda.synchronize() if torch.cuda.is_available() else None
        t0 = time.time()
        with torch.inference_mode():
            output_ids = self._model.generate(
                **inputs, max_new_tokens=self.max_new_tokens, do_sample=False
            )
        torch.cuda.synchronize() if torch.cuda.is_available() else None
        infer_time = time.time() - t0

        generated = output_ids[:, inputs.input_ids.shape[1]:]
        n_tokens = int(generated.shape[1])
        raw_text = self._processor.batch_decode(
            generated, skip_special_tokens=True
        )[0]

        subject, missing, next_likely = _parse_response(raw_text)

        result = IntentPrediction(
            subject=subject,
            missing=missing,
            next_likely=next_likely,
            raw_text=raw_text,
            infer_time_s=infer_time,
            n_tokens=n_tokens,
        )
        if self.verbose:
            parsed_ok = "OK" if result.is_parsed() else "PARSE_FAIL"
            print(
                f"[vlm] inferred in {infer_time:.2f}s "
                f"({n_tokens} tok, {n_tokens / infer_time:.1f} tok/s) [{parsed_ok}]"
            )
        return result


# --- スモークテスト ---------------------------------------------------------

def _make_dummy_sketch(path: Path) -> None:
    """Create a simple face-like sketch for offline testing."""
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
        prediction = vlm.predict_intent(test_image)

        print("\n=== IntentPrediction ===")
        print(f"  parsed       : {prediction.is_parsed()}")
        print(f"  subject      : {prediction.subject!r}")
        print(f"  missing      : {prediction.missing!r}")
        print(f"  next_likely  : {prediction.next_likely!r}")
        print(f"  n_tokens     : {prediction.n_tokens}")
        print(f"  infer_time_s : {prediction.infer_time_s:.2f}")
        print("\n=== to_text() (legacy compat) ===")
        print(f"  {prediction.to_text()}")
        print("\n=== raw_text ===")
        print(prediction.raw_text)
