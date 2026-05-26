"""Image generation with SDXL Turbo + Lineart ControlNet (v0.4.1).

設計v0.4.1 (Step C 完了時) で確定したパイプライン:
  prompt (build_prompt の出力) + guide_image (median 合成キャプチャ)
    -> SDXL Turbo + MistoLine ControlNet
    -> 生成画像 (写実的鉛筆画スタイル)
    -> [後段: vectorizer.py で Canny strong_blur + 差分 + ベクトル化]

VRAM 実測 (2026-05-23 RTX 2000 Ada):
  - pipeline to(cuda) 後: 8.96GB
  - 推論ピーク (4-step):  12.94GB
  - レイテンシ:            1-step 1.49s / 2-step 1.87s / 4-step 2.71s

VLM (5.98GB peak) との同時常駐は予算超過 (18.92GB)、ターン制の v0.4 では
VLM unload → ImageGenerator load の段階的スワップで運用する。

See:
  docs/20260523_1530_step_c_completion.md
  scripts/measure_sdxl_vram.py
"""

from __future__ import annotations

import gc
import time
from pathlib import Path
from typing import Optional, Union

import numpy as np
import torch
from PIL import Image


ImageLike = Union[Image.Image, str, Path, np.ndarray]


# --- デフォルトパラメータ (Step C 完了時の確定値) ---------------------------

DEFAULT_BASE_MODEL = "stabilityai/sdxl-turbo"
DEFAULT_CONTROLNET = "TheMistoAI/MistoLine"

DEFAULT_NUM_INFERENCE_STEPS = 4
DEFAULT_GUIDANCE_SCALE = 0.0
# 2026-05-27: 0.8 → 1.0 に。 ユーザ入力 (顔の輪郭・目・口) の位置を
# 厳格に保持し、 生成画像が re-position するのを防ぐ。 既存パーツが
# ずれず、 追加要素 (髪・体) だけ周囲に生えるように。
DEFAULT_CONTROLNET_SCALE = 1.0
# 2026-05-27: 「中央クリーンな絵 + 周辺スクラッチ noise」 への対処として
# noise / hatching / scribble 系を強化。
DEFAULT_NEGATIVE_PROMPT = (
    "color, shading, photo, photorealistic, complex background, "
    "scribble, sketchy, crosshatch, hatching, pencil texture, "
    "scratch marks, noise, multiple overlapping lines, "
    "duplicate strokes, dirty background, paper grain, "
    "fabric texture, smudge, blurry, watermark, signature, "
    "text, frame, border"
)
DEFAULT_RESOLUTION = 1024


def _normalize_image(image: ImageLike, size: Optional[int] = None) -> Image.Image:
    if isinstance(image, Image.Image):
        img = image.convert("RGB") if image.mode != "RGB" else image
    elif isinstance(image, (str, Path)):
        img = Image.open(image).convert("RGB")
    elif isinstance(image, np.ndarray):
        if image.ndim == 2:
            img = Image.fromarray(image).convert("RGB")
        elif image.ndim == 3 and image.shape[2] in (3, 4):
            img = Image.fromarray(image[..., :3]).convert("RGB")
        else:
            raise ValueError(f"unsupported ndarray shape: {image.shape}")
    else:
        raise TypeError(f"unsupported image type: {type(image)}")

    if size is not None and img.size != (size, size):
        img = img.resize((size, size), Image.LANCZOS)
    return img


class ImageGenerator:
    """SDXL Turbo + MistoLine ControlNet ラッパー。

    `vlm.VLM` と同じ流儀で `load() / unload()` + context manager を提供する。
    オーケストレータが VLM を unload してから ImageGenerator を load する
    段階的スワップ運用を前提とする。
    """

    def __init__(
        self,
        base_model_id: str = DEFAULT_BASE_MODEL,
        controlnet_id: str = DEFAULT_CONTROLNET,
        device: str = "cuda",
        torch_dtype: torch.dtype = torch.float16,
        num_inference_steps: int = DEFAULT_NUM_INFERENCE_STEPS,
        guidance_scale: float = DEFAULT_GUIDANCE_SCALE,
        controlnet_conditioning_scale: float = DEFAULT_CONTROLNET_SCALE,
        negative_prompt: str = DEFAULT_NEGATIVE_PROMPT,
        resolution: int = DEFAULT_RESOLUTION,
        verbose: bool = False,
    ):
        self.base_model_id = base_model_id
        self.controlnet_id = controlnet_id
        self.device = device
        self.torch_dtype = torch_dtype
        self.num_inference_steps = num_inference_steps
        self.guidance_scale = guidance_scale
        self.controlnet_conditioning_scale = controlnet_conditioning_scale
        self.negative_prompt = negative_prompt
        self.resolution = resolution
        self.verbose = verbose

        self._pipe = None
        self._controlnet = None

    @property
    def is_loaded(self) -> bool:
        return self._pipe is not None

    def load(self) -> None:
        if self.is_loaded:
            return

        from diffusers import (
            StableDiffusionXLControlNetPipeline,
            ControlNetModel,
        )

        if self.verbose:
            print(f"[image_gen] loading {self.controlnet_id} ...")
        t0 = time.time()
        self._controlnet = ControlNetModel.from_pretrained(
            self.controlnet_id,
            torch_dtype=self.torch_dtype,
            variant="fp16",
        )
        if self.verbose:
            print(f"[image_gen] controlnet loaded in {time.time() - t0:.1f}s")

        if self.verbose:
            print(f"[image_gen] loading {self.base_model_id} ...")
        t0 = time.time()
        self._pipe = StableDiffusionXLControlNetPipeline.from_pretrained(
            self.base_model_id,
            controlnet=self._controlnet,
            torch_dtype=self.torch_dtype,
            variant="fp16",
            use_safetensors=True,
        )
        if self.verbose:
            print(f"[image_gen] pipeline loaded in {time.time() - t0:.1f}s")

        t0 = time.time()
        self._pipe = self._pipe.to(self.device)
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        if self.verbose:
            print(f"[image_gen] moved to {self.device} in {time.time() - t0:.1f}s")

    def unload(self) -> None:
        if not self.is_loaded:
            return
        del self._pipe
        del self._controlnet
        self._pipe = None
        self._controlnet = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def warmup(self, guide_image: Optional[ImageLike] = None) -> None:
        if not self.is_loaded:
            self.load()
        if guide_image is None:
            guide_image = Image.new("RGB", (self.resolution, self.resolution), (255, 255, 255))
        if self.verbose:
            print("[image_gen] warmup (1-step, discarded) ...")
        t0 = time.time()
        _ = self.generate(
            prompt="line art, white background",
            guide_image=guide_image,
            num_inference_steps=1,
        )
        if self.verbose:
            print(f"[image_gen] warmup done in {time.time() - t0:.2f}s")

    def __enter__(self) -> "ImageGenerator":
        self.load()
        return self

    def __exit__(self, *exc) -> None:
        self.unload()

    def generate(
        self,
        prompt: str,
        guide_image: ImageLike,
        *,
        num_inference_steps: Optional[int] = None,
        guidance_scale: Optional[float] = None,
        controlnet_conditioning_scale: Optional[float] = None,
        negative_prompt: Optional[str] = None,
        seed: Optional[int] = None,
    ) -> Image.Image:
        if not self.is_loaded:
            self.load()

        steps = num_inference_steps if num_inference_steps is not None else self.num_inference_steps
        gs = guidance_scale if guidance_scale is not None else self.guidance_scale
        cn = (
            controlnet_conditioning_scale
            if controlnet_conditioning_scale is not None
            else self.controlnet_conditioning_scale
        )
        neg = negative_prompt if negative_prompt is not None else self.negative_prompt

        pil_guide = _normalize_image(guide_image, size=self.resolution)

        generator = None
        if seed is not None:
            generator = torch.Generator(device=self.device).manual_seed(int(seed))

        if torch.cuda.is_available():
            torch.cuda.synchronize()
        t0 = time.time()
        with torch.no_grad():
            out = self._pipe(
                prompt=prompt,
                negative_prompt=neg,
                image=pil_guide,
                num_inference_steps=steps,
                guidance_scale=gs,
                controlnet_conditioning_scale=cn,
                generator=generator,
            ).images[0]
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        elapsed = time.time() - t0

        if self.verbose:
            print(f"[image_gen] generated in {elapsed:.2f}s ({steps} step)")
        return out


if __name__ == "__main__":
    test_sketch = Path("scripts/test_sketch.jpg")
    if not test_sketch.exists():
        print(f"[image_gen] test sketch not found at {test_sketch}")
        test_sketch_img = Image.new("RGB", (1024, 1024), (245, 245, 245))
    else:
        test_sketch_img = Image.open(test_sketch)

    test_prompt = (
        "dog running in a park, "
        "line art, black ink on white, simple, clean lines, "
        "minimal detail, no shading, white background"
    )

    with ImageGenerator(verbose=True) as gen:
        gen.warmup(test_sketch_img)
        for steps in [1, 2, 4]:
            out = gen.generate(test_prompt, test_sketch_img, num_inference_steps=steps)
            out_path = Path(f"/tmp/image_gen_smoke_{steps}step.png")
            out.save(out_path)
            print(f"[image_gen] saved {out_path}")
