"""SDXL Turbo + Lineart ControlNet VRAM 計測スクリプト

設計 v0.4 の Step C 単体計測。VLM (Qwen2.5-VL-7B INT4) との共存可否を
判定するための一次データを取る。

計測する観点:
  - baseline → model load → inputs → warmup → inference → cleanup の各時点で
    allocated / reserved / peak の VRAM を記録
  - 推論レイテンシ (1, 2, 4 step) と tok/s 相当の指標
  - 生成画像の品質目視確認

入力:
  scripts/test_sketch.jpg (VLM 計測時に生成済み)
    -> Lineart ControlNet のガイドとして使う (median 合成後のキャプチャ相当)

プロンプト:
  「VLM が予測した次の描画意図」を模した固定テキスト
    例: "a simple line drawing of a human face, head outline and ears"
"""

import gc
import logging
import time
from datetime import datetime
from pathlib import Path

import torch
from PIL import Image


# ----- ロギング設定 -----
LOG_DIR = Path.home() / "draw_piper" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / f"sdxl_vram_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


# ----- ヘルパー -----
def gpu_mem_snapshot(label: str) -> dict:
    """現時点の VRAM 使用量を allocated / reserved / peak で取得しログ出力"""
    if not torch.cuda.is_available():
        log.warning(f"[{label}] CUDA not available")
        return {}

    allocated = torch.cuda.memory_allocated() / 1024**3
    reserved = torch.cuda.memory_reserved() / 1024**3
    peak = torch.cuda.max_memory_allocated() / 1024**3
    log.info(
        f"[{label}] allocated={allocated:.2f}GB "
        f"reserved={reserved:.2f}GB peak={peak:.2f}GB"
    )
    return {
        "label": label,
        "allocated_gb": allocated,
        "reserved_gb": reserved,
        "peak_gb": peak,
    }


def reset_peak():
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()


# ----- 入力データ準備 -----
SCRIPT_DIR = Path(__file__).resolve().parent
TEST_SKETCH = SCRIPT_DIR / "test_sketch.jpg"

if not TEST_SKETCH.exists():
    log.error(f"Test sketch not found: {TEST_SKETCH}")
    log.error("Run measure_vlm_vram.py first to generate it, or create manually.")
    raise SystemExit(1)

# 設計 v0.4 の意図予測テンプレ的なプロンプト
# (本番では VLM 出力 → prompt_builder.py で組み立てる箇所)
PROMPT = (
    "a simple monochrome line drawing of a human face, "
    "showing the head outline, ears, and hair contour, "
    "minimalist sketch style, white background, black lines only"
)
NEGATIVE_PROMPT = "color, shading, photo, photorealistic, complex background"


def main():
    log.info("=" * 60)
    log.info("SDXL Turbo + Lineart ControlNet VRAM Measurement")
    log.info(f"Log file: {LOG_FILE}")
    log.info("=" * 60)

    if not torch.cuda.is_available():
        log.error("CUDA not available. Aborting.")
        raise SystemExit(1)

    log.info(f"GPU: {torch.cuda.get_device_name(0)}")
    log.info(f"Total VRAM: {torch.cuda.get_device_properties(0).total_memory / 1024**3:.2f}GB")
    log.info(f"torch: {torch.__version__}")

    # ----- baseline -----
    reset_peak()
    snapshots = [gpu_mem_snapshot("baseline")]

    # ----- import (遅延 import で baseline を汚さない) -----
    log.info("Importing diffusers...")
    from diffusers import (
        StableDiffusionXLControlNetPipeline,
        ControlNetModel,
        AutoencoderKL,
    )
    import diffusers
    log.info(f"diffusers: {diffusers.__version__}")

    # ----- ControlNet ロード -----
    log.info("Loading Lineart ControlNet (SDXL用)...")
    t0 = time.time()
    # SDXL 用 Lineart ControlNet
    # 候補1: xinsir/controlnet-scribble-sdxl-1.0 (scribble系、線画と相性良)
    # 候補2: TheMistoAI/MistoLine (SDXL専用Lineart、品質高め)
    # まずは MistoLine を試す。OOM なら scribble にフォールバック
    controlnet = ControlNetModel.from_pretrained(
        "TheMistoAI/MistoLine",
        torch_dtype=torch.float16,
        variant="fp16",
    )
    log.info(f"ControlNet loaded in {time.time()-t0:.1f}s")
    snapshots.append(gpu_mem_snapshot("after controlnet load (cpu)"))

    # ----- SDXL Turbo パイプライン ロード -----
    log.info("Loading SDXL Turbo pipeline with ControlNet...")
    t0 = time.time()
    pipe = StableDiffusionXLControlNetPipeline.from_pretrained(
        "stabilityai/sdxl-turbo",
        controlnet=controlnet,
        torch_dtype=torch.float16,
        variant="fp16",
        use_safetensors=True,
    )
    log.info(f"Pipeline loaded in {time.time()-t0:.1f}s")
    snapshots.append(gpu_mem_snapshot("after pipeline load (cpu)"))

    # GPU に送る
    log.info("Moving pipeline to CUDA...")
    t0 = time.time()
    pipe = pipe.to("cuda")
    torch.cuda.synchronize()
    log.info(f"Moved to CUDA in {time.time()-t0:.1f}s")
    snapshots.append(gpu_mem_snapshot("after pipeline to(cuda)"))

    # ----- 入力準備 -----
    log.info(f"Loading test sketch: {TEST_SKETCH}")
    guide_image = Image.open(TEST_SKETCH).convert("RGB")
    # SDXL は 1024x1024 が標準だが、512 でも動く。本番想定の解像度で測る。
    # まずは 1024x1024 (median合成キャプチャをリサイズ想定)
    guide_image = guide_image.resize((1024, 1024), Image.LANCZOS)
    snapshots.append(gpu_mem_snapshot("after inputs prepared"))

    # ----- warmup (1枚捨て生成) -----
    log.info("Warmup generation (1 step, discarded)...")
    t0 = time.time()
    with torch.no_grad():
        _ = pipe(
            prompt=PROMPT,
            negative_prompt=NEGATIVE_PROMPT,
            image=guide_image,
            num_inference_steps=1,
            guidance_scale=0.0,  # SDXL Turbo は guidance_scale=0 推奨
            controlnet_conditioning_scale=0.8,
        ).images[0]
    torch.cuda.synchronize()
    log.info(f"Warmup done in {time.time()-t0:.2f}s")
    snapshots.append(gpu_mem_snapshot("after warmup"))

    # ----- 本番計測 (1, 2, 4 step) -----
    results = []
    for steps in [1, 2, 4]:
        log.info(f"--- Inference with {steps} step(s) ---")
        reset_peak()
        t0 = time.time()
        with torch.no_grad():
            out_image = pipe(
                prompt=PROMPT,
                negative_prompt=NEGATIVE_PROMPT,
                image=guide_image,
                num_inference_steps=steps,
                guidance_scale=0.0,
                controlnet_conditioning_scale=0.8,
            ).images[0]
        torch.cuda.synchronize()
        elapsed = time.time() - t0
        snap = gpu_mem_snapshot(f"after inference ({steps} step)")
        snap["steps"] = steps
        snap["latency_s"] = elapsed
        results.append(snap)
        log.info(f"{steps}-step: {elapsed:.2f}s")

        # 画像を保存（品質目視確認用）
        out_path = LOG_DIR / f"sdxl_output_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{steps}step.png"
        out_image.save(out_path)
        log.info(f"Saved output to {out_path}")

    # ----- cleanup -----
    log.info("Cleanup...")
    del pipe
    del controlnet
    gc.collect()
    torch.cuda.empty_cache()
    snapshots.append(gpu_mem_snapshot("after cleanup"))

    # ----- サマリー -----
    log.info("=" * 60)
    log.info("SUMMARY")
    log.info("=" * 60)
    log.info(f"{'Label':<35} {'Alloc':>8} {'Resv':>8} {'Peak':>8}")
    for s in snapshots:
        if "allocated_gb" in s:
            log.info(
                f"{s['label']:<35} "
                f"{s['allocated_gb']:>7.2f}G "
                f"{s['reserved_gb']:>7.2f}G "
                f"{s['peak_gb']:>7.2f}G"
            )

    log.info("")
    log.info("Inference latency:")
    for r in results:
        log.info(f"  {r['steps']}-step: {r['latency_s']:.2f}s (peak={r['peak_gb']:.2f}GB)")

    log.info("")
    log.info("Done. Compare 'after pipeline to(cuda)' value with VLM measurement")
    log.info(f"  (VLM was 5.51GB allocated, 5.98GB peak)")
    log.info("  Sum for coexistence estimate:")
    if results:
        sdxl_peak = max(r["peak_gb"] for r in results)
        log.info(f"  VLM(5.98) + SDXL({sdxl_peak:.2f}) = {5.98 + sdxl_peak:.2f}GB")
        log.info(f"  16GB budget remaining: {16.0 - (5.98 + sdxl_peak):.2f}GB")


if __name__ == "__main__":
    main()
