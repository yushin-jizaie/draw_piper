"""
Qwen2.5-VL-7B-Instruct を bitsandbytes NF4 量子化でロードし、
VRAM 使用量と推論速度を実測する。
SDXL Turbo との共存可否判定のための一次データ収集。
"""
import gc
import time
from pathlib import Path

import torch
from PIL import Image, ImageDraw
from transformers import (
    Qwen2_5_VLForConditionalGeneration,
    AutoProcessor,
    BitsAndBytesConfig,
)
from qwen_vl_utils import process_vision_info


MODEL_ID = "Qwen/Qwen2.5-VL-7B-Instruct"
TEST_IMAGE = Path("scripts/test_sketch.jpg")


def gb(n: int) -> float:
    return n / 1024**3


def snapshot(label: str) -> None:
    torch.cuda.synchronize()
    alloc = torch.cuda.memory_allocated()
    reserved = torch.cuda.memory_reserved()
    peak = torch.cuda.max_memory_allocated()
    print(f"[{label:24s}] allocated={gb(alloc):5.2f}GB  "
          f"reserved={gb(reserved):5.2f}GB  peak={gb(peak):5.2f}GB")


def main() -> None:
    assert torch.cuda.is_available(), "CUDA unavailable"
    print(f"Device : {torch.cuda.get_device_name(0)}")
    print(f"Total  : {gb(torch.cuda.get_device_properties(0).total_memory):.2f}GB")
    print(f"Torch  : {torch.__version__}")
    print()

    torch.cuda.reset_peak_memory_stats()
    snapshot("baseline")

    # --- モデルロード (NF4 4bit) ---
    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_use_double_quant=True,
    )

    t0 = time.time()
    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        MODEL_ID,
        quantization_config=bnb_config,
        device_map="cuda:0",
    )
    processor = AutoProcessor.from_pretrained(MODEL_ID)
    load_time = time.time() - t0
    print(f"\nmodel load: {load_time:.1f}s")
    snapshot("after model load")

    # --- ダミー画像 (簡単な顔のスケッチ) ---
    TEST_IMAGE.parent.mkdir(parents=True, exist_ok=True)
    if not TEST_IMAGE.exists():
        img = Image.new("RGB", (1024, 768), (245, 245, 245))
        d = ImageDraw.Draw(img)
        d.ellipse([400, 250, 600, 450], outline=(30, 30, 30), width=4)  # 顔の輪郭
        d.ellipse([445, 320, 475, 350], fill=(30, 30, 30))              # 左目
        d.ellipse([525, 320, 555, 350], fill=(30, 30, 30))              # 右目
        img.save(TEST_IMAGE, quality=92)
        print(f"created dummy sketch: {TEST_IMAGE}")

    # --- メッセージ構築 ---
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image", "image": str(TEST_IMAGE.resolve())},
                {"type": "text", "text":
                    "この画像はホワイトボードに人が描きかけのスケッチです。"
                    "この人は何を描こうとしていますか？ "
                    "主題、未完成な要素、次に描き足されそうな部分を答えてください。"
                },
            ],
        }
    ]
    text = processor.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )
    image_inputs, video_inputs = process_vision_info(messages)
    inputs = processor(
        text=[text],
        images=image_inputs,
        videos=video_inputs,
        padding=True,
        return_tensors="pt",
    ).to("cuda:0")
    snapshot("after inputs prepared")

    # --- warmup ---
    with torch.inference_mode():
        _ = model.generate(**inputs, max_new_tokens=8)
    torch.cuda.synchronize()
    snapshot("after warmup")

    # --- 本番計測 ---
    torch.cuda.reset_peak_memory_stats()
    t0 = time.time()
    with torch.inference_mode():
        output_ids = model.generate(
            **inputs, max_new_tokens=256, do_sample=False
        )
    torch.cuda.synchronize()
    infer_time = time.time() - t0

    generated = output_ids[:, inputs.input_ids.shape[1]:]
    text_out = processor.batch_decode(generated, skip_special_tokens=True)[0]
    n_tokens = generated.shape[1]

    snapshot("after inference")
    print(f"\ninference: {infer_time:.2f}s  ({n_tokens} tokens, "
          f"{n_tokens / infer_time:.1f} tok/s)")
    print(f"\n--- model output ---\n{text_out}\n--------------------")

    # --- クリーンアップ ---
    del model, processor, inputs, output_ids
    gc.collect()
    torch.cuda.empty_cache()
    snapshot("after cleanup")


if __name__ == "__main__":
    main()
