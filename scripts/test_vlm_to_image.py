"""VLM → prompt_builder → ImageGenerator つなぎこみテスト

Step C の最後の未解決事項 「VLM + SDXL Turbo の組み合わせを実機で通す」を、
段階的ロード/アンロード方式で実行可能にする統合スクリプト。

設計の選択:
  v0.4 設計書では「同時常駐 + ControlNet CPU offload」が想定されていたが、
  Step C 完了時 (2026-05-23 15:30) の実測で:
    - VLM peak 5.98GB
    - SDXL peak 12.94GB
    - 合計 18.92GB (16GB 予算を 2.92GB 超過)

  ターン制 (2分サイクル) の v0.4/v0.5 では VLM 推論と SDXL 推論が
  並列ではなく直列に走るため、段階的ロード/アンロードを採用する:

    1. capture (median 合成, 2s)
    2. VLM.load()        → peak ~6GB
    3. VLM.predict_intent
    4. VLM.unload()       → 0GB に戻ることを確認
    5. ImageGenerator.load()  → peak ~9GB (warmup後 ~13GB)
    6. ImageGenerator.generate
    7. ImageGenerator.unload()
    8. [後続: vectorize + 軌道変換]

使い方:
  cd ~/draw_piper
  source venv/bin/activate
  python scripts/test_vlm_to_image.py [--sketch path/to/sketch.jpg] [--steps 4]

出力:
  - 標準出力 + logs/vlm_to_image_YYYYMMDD_HHMMSS.log にトレース
  - logs/vlm_to_image_YYYYMMDD_HHMMSS/cycle_NN/ に各サイクルの中間ファイル
"""

from __future__ import annotations

import argparse
import gc
import json
import logging
import shutil
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import torch
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.vlm import VLM  # noqa: E402
from modules.prompt_builder import build_prompt  # noqa: E402
from modules.image_gen import ImageGenerator  # noqa: E402
from modules.topic import TopicGuess  # noqa: E402


def setup_logging(log_dir: Path) -> tuple[logging.Logger, Path]:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"vlm_to_image_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    handlers = [logging.FileHandler(log_file), logging.StreamHandler()]
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
        force=True,
    )
    return logging.getLogger("vlm_to_image"), log_file


def gpu_mem_snapshot(label: str, log: logging.Logger) -> dict:
    if not torch.cuda.is_available():
        log.warning(f"[{label}] CUDA not available")
        return {"label": label}
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


def reset_peak() -> None:
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()


def force_gc_and_empty_cache() -> None:
    for _ in range(3):
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def topic_guess_to_dict(guess: TopicGuess) -> dict:
    d = asdict(guess)
    for k in ("subject", "location", "action"):
        v = d[k]
        d[k] = {"ja": v["ja"], "en": v["en"]}
    return d


def run_one_cycle(
    sketch_path: Path,
    cycle_dir: Path,
    *,
    log: logging.Logger,
    vlm: VLM,
    image_gen: ImageGenerator,
    sdxl_steps: int,
    seed,
) -> dict:
    cycle_dir.mkdir(parents=True, exist_ok=True)
    timing: dict = {"cycle_dir": str(cycle_dir), "snapshots": []}

    in_copy = cycle_dir / "input_sketch.jpg"
    if sketch_path.suffix.lower() in (".jpg", ".jpeg"):
        shutil.copyfile(sketch_path, in_copy)
    else:
        Image.open(sketch_path).convert("RGB").save(in_copy, quality=92)

    log.info("---- STAGE 1: VLM load ----")
    reset_peak()
    timing["snapshots"].append(gpu_mem_snapshot("before vlm load", log))

    t0 = time.time()
    vlm.load()
    timing["vlm_load_s"] = time.time() - t0
    timing["snapshots"].append(gpu_mem_snapshot("after vlm load", log))

    log.info("---- STAGE 2: VLM predict_intent ----")
    t0 = time.time()
    guess = vlm.predict_intent(in_copy)
    timing["vlm_predict_s"] = time.time() - t0
    timing["snapshots"].append(gpu_mem_snapshot("after vlm predict", log))

    log.info(f"  subject       : {guess.subject.ja} ({guess.subject.en})")
    log.info(f"  location      : {guess.location.ja} ({guess.location.en})")
    log.info(f"  action        : {guess.action.ja} ({guess.action.en})")
    log.info(f"  missing       : {guess.missing_elements}")
    log.info(f"  confidence    : {guess.confidence:.2f}")
    log.info(f"  is_certain    : {guess.is_certain()}")
    log.info(f"  n_tokens      : {guess.n_tokens}")
    log.info(f"  infer_time_s  : {guess.infer_time_s:.2f}")

    (cycle_dir / "vlm_raw.txt").write_text(guess.raw_text, encoding="utf-8")
    (cycle_dir / "topic_guess.json").write_text(
        json.dumps(topic_guess_to_dict(guess), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    log.info("---- STAGE 3: VLM unload ----")
    t0 = time.time()
    vlm.unload()
    force_gc_and_empty_cache()
    timing["vlm_unload_s"] = time.time() - t0
    timing["snapshots"].append(gpu_mem_snapshot("after vlm unload", log))

    log.info("---- STAGE 4: prompt_builder ----")
    prompt = build_prompt(guess)
    log.info(f"  prompt: {prompt}")
    (cycle_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    timing["prompt"] = prompt

    log.info("---- STAGE 5: ImageGenerator load ----")
    reset_peak()
    timing["snapshots"].append(gpu_mem_snapshot("before image_gen load", log))

    t0 = time.time()
    image_gen.load()
    timing["image_gen_load_s"] = time.time() - t0
    timing["snapshots"].append(gpu_mem_snapshot("after image_gen load", log))

    log.info("---- STAGE 6: ImageGenerator warmup (1-step discard) ----")
    t0 = time.time()
    image_gen.warmup(in_copy)
    timing["image_gen_warmup_s"] = time.time() - t0
    timing["snapshots"].append(gpu_mem_snapshot("after warmup", log))

    log.info(f"---- STAGE 7: ImageGenerator generate ({sdxl_steps} step) ----")
    reset_peak()
    t0 = time.time()
    generated = image_gen.generate(
        prompt=prompt,
        guide_image=in_copy,
        num_inference_steps=sdxl_steps,
        seed=seed,
    )
    timing["image_gen_generate_s"] = time.time() - t0
    timing["snapshots"].append(gpu_mem_snapshot(f"after generate ({sdxl_steps} step)", log))

    generated_path = cycle_dir / "generated.png"
    generated.save(generated_path)
    log.info(f"  saved {generated_path}")

    log.info("---- STAGE 8: ImageGenerator unload ----")
    t0 = time.time()
    image_gen.unload()
    force_gc_and_empty_cache()
    timing["image_gen_unload_s"] = time.time() - t0
    timing["snapshots"].append(gpu_mem_snapshot("after image_gen unload", log))

    (cycle_dir / "timing.json").write_text(
        json.dumps(timing, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return timing


def main() -> int:
    parser = argparse.ArgumentParser(
        description="VLM + prompt_builder + ImageGenerator 直列パイプライン統合テスト"
    )
    parser.add_argument(
        "--sketch",
        type=Path,
        default=ROOT / "scripts" / "test_sketch.jpg",
        help="入力スケッチ画像。デフォルトは scripts/test_sketch.jpg",
    )
    parser.add_argument("--steps", type=int, default=4, help="SDXL の num_inference_steps")
    parser.add_argument("--cycles", type=int, default=1, help="サイクル数")
    parser.add_argument("--seed", type=int, default=None, help="SDXL のシード")
    parser.add_argument("--log-dir", type=Path, default=ROOT / "logs", help="ログ出力ディレクトリ")
    args = parser.parse_args()

    log, log_file = setup_logging(args.log_dir)

    log.info("=" * 60)
    log.info("VLM → prompt_builder → ImageGenerator つなぎこみテスト")
    log.info(f"Log: {log_file}")
    log.info("=" * 60)

    if not torch.cuda.is_available():
        log.error("CUDA not available. Aborting.")
        return 1

    log.info(f"GPU            : {torch.cuda.get_device_name(0)}")
    log.info(f"Total VRAM     : {torch.cuda.get_device_properties(0).total_memory / 1024**3:.2f}GB")
    log.info(f"torch          : {torch.__version__}")
    log.info(f"sketch         : {args.sketch}")
    log.info(f"sdxl steps     : {args.steps}")
    log.info(f"cycles         : {args.cycles}")
    log.info(f"seed           : {args.seed}")

    if not args.sketch.exists():
        log.error(f"sketch not found: {args.sketch}")
        return 1

    reset_peak()
    gpu_mem_snapshot("baseline", log)

    vlm = VLM(verbose=True)
    image_gen = ImageGenerator(verbose=True, num_inference_steps=args.steps)

    run_root = args.log_dir / f"vlm_to_image_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_root.mkdir(parents=True, exist_ok=True)

    all_timings: list = []
    for i in range(args.cycles):
        log.info("")
        log.info("#" * 60)
        log.info(f"# CYCLE {i + 1} / {args.cycles}")
        log.info("#" * 60)
        cycle_dir = run_root / f"cycle_{i + 1:02d}"
        timing = run_one_cycle(
            sketch_path=args.sketch,
            cycle_dir=cycle_dir,
            log=log,
            vlm=vlm,
            image_gen=image_gen,
            sdxl_steps=args.steps,
            seed=args.seed,
        )
        all_timings.append(timing)

    log.info("")
    log.info("=" * 60)
    log.info("SUMMARY")
    log.info("=" * 60)
    for i, t in enumerate(all_timings, 1):
        log.info(f"--- cycle {i} ---")
        for k in (
            "vlm_load_s",
            "vlm_predict_s",
            "vlm_unload_s",
            "image_gen_load_s",
            "image_gen_warmup_s",
            "image_gen_generate_s",
            "image_gen_unload_s",
        ):
            if k in t:
                log.info(f"  {k:<24}: {t[k]:.2f} s")

    (run_root / "all_timings.json").write_text(
        json.dumps(all_timings, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info(f"\nrun artifacts: {run_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
