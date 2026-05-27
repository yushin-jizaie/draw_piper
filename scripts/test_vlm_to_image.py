"""VLM → prompt_builder → ImageGenerator → Vectorizer つなぎこみテスト

Step C/D の確定後、VLM (Qwen2.5-VL-7B) → SDXL Turbo + MistoLine →
Vectorizer の直列パイプラインを 1 サイクルとして実行する。

設計の選択:
  v0.4 設計書では「同時常駐 + ControlNet CPU offload」が想定されていたが、
  Step C 完了時 (2026-05-23 15:30) の実測で:
    - VLM peak 5.98GB
    - SDXL peak 12.94GB
    - 合計 18.92GB (16GB 予算を 2.92GB 超過)

  ターン制 (2分サイクル) の v0.4/v0.5 では VLM 推論と SDXL 推論が
  並列ではなく直列に走るため、段階的ロード/アンロードを採用する:

    0. (--use-camera 時) Camera.capture_median() で median 合成キャプチャ
    1. (--sketch 時) input_sketch を cycle_dir にコピー
    2. VLM.load()        → peak ~6GB
    3. VLM.predict_intent
    4. VLM.unload()       → 0GB に戻ることを確認
    5. ImageGenerator.load()  → peak ~9GB (warmup後 ~13GB)
    6. ImageGenerator.generate
    7. ImageGenerator.unload()
    8. Vectorizer.vectorize() (CPU、GPU 不使用)

使い方:
  cd ~/draw_piper
  source venv/bin/activate

  # 既存: 固定スケッチを入力
  python scripts/test_vlm_to_image.py [--sketch path/to/sketch.jpg] [--steps 4]

  # 新規: 実カメラの median 合成を入力
  python scripts/test_vlm_to_image.py --use-camera [--cycles 3]

出力:
  - 標準出力 + logs/vlm_to_image_YYYYMMDD_HHMMSS.log にトレース
  - logs/vlm_to_image_YYYYMMDD_HHMMSS/cycle_NN/ に各サイクルの中間ファイル
    (captured.png (use-camera 時のみ), vlm_raw.txt, topic_guess.json,
     prompt.txt, generated.png, strokes.json, vec_debug/, timing.json)
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

import cv2
import torch
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.vlm import VLM  # noqa: E402
from modules.prompt_builder import build_prompt  # noqa: E402
from modules.image_gen import ImageGenerator  # noqa: E402
from modules.topic import TopicGuess  # noqa: E402
from modules.vectorizer import Vectorizer  # noqa: E402
from modules.camera import Camera  # noqa: E402


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
    vectorizer: Vectorizer,
    sdxl_steps: int,
    seed,
    prompt_base_template: str = None,
    prompt_fallback_template: str = None,
    prompt_confidence_threshold: float = 0.3,
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
    prompt = build_prompt(
        guess,
        confidence_threshold=prompt_confidence_threshold,
        base_template=prompt_base_template,
        fallback_template=prompt_fallback_template)
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

    log.info("---- STAGE 9: Vectorizer (CPU, no GPU load) ----")
    t0 = time.time()
    vec_result = vectorizer.vectorize(
        generated_image=generated,
        user_image=in_copy,
        debug_dir=cycle_dir / "vec_debug",
    )
    timing["vectorize_s"] = time.time() - t0
    timing["snapshots"].append(gpu_mem_snapshot("after vectorize", log))

    log.info(f"  strokes        : {vec_result.n_strokes}")
    log.info(f"  points         : {vec_result.n_points}")
    log.info(f"  total_length_px: {vec_result.total_length_px:.1f}")
    log.info(f"  image_shape    : {vec_result.image_shape}")

    # ピクセル単位の strokes を JSON で保存 (mm 変換は Step B キャリブ後)
    strokes_payload = {
        "image_shape": list(vec_result.image_shape),
        "n_strokes": vec_result.n_strokes,
        "n_points": vec_result.n_points,
        "total_length_px": vec_result.total_length_px,
        "strokes": [
            [[float(x), float(y)] for (x, y) in stroke]
            for stroke in vec_result.strokes
        ],
        "diagnostics": Vectorizer._diagnostics_to_jsonable(vec_result.diagnostics),
    }
    (cycle_dir / "strokes.json").write_text(
        json.dumps(strokes_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info(f"  saved {cycle_dir / 'strokes.json'}")

    (cycle_dir / "timing.json").write_text(
        json.dumps(timing, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return timing


def main() -> int:
    parser = argparse.ArgumentParser(
        description="VLM + prompt_builder + ImageGenerator + Vectorizer 直列パイプライン統合テスト"
    )
    parser.add_argument(
        "--sketch",
        type=Path,
        default=ROOT / "scripts" / "test_sketch.jpg",
        help="入力スケッチ画像。デフォルトは scripts/test_sketch.jpg",
    )
    parser.add_argument("--steps", type=int, default=None,
                         help="SDXL の num_inference_steps (省略時 imagegen_config.yaml の値)")
    parser.add_argument("--cycles", type=int, default=1, help="サイクル数")
    parser.add_argument("--seed", type=int, default=None, help="SDXL のシード")
    parser.add_argument("--log-dir", type=Path, default=ROOT / "logs", help="ログ出力ディレクトリ")
    # ----- camera 関連 -----
    parser.add_argument(
        "--use-camera", action="store_true",
        help="Camera.capture_median() の出力を入力にする。--sketch は無視される",
    )
    parser.add_argument(
        "--camera-device", type=int, default=0,
        help="cv2.VideoCapture の device id (デフォルト: 0)",
    )
    parser.add_argument(
        "--camera-width", type=int, default=1280,
        help="カメラキャプチャ幅 (デフォルト: 1280)",
    )
    parser.add_argument(
        "--camera-height", type=int, default=720,
        help="カメラキャプチャ高さ (デフォルト: 720)",
    )
    parser.add_argument(
        "--camera-n-frames", type=int, default=10,
        help="median 合成のフレーム数 (デフォルト: 10)",
    )
    parser.add_argument(
        "--camera-interval", type=float, default=0.2,
        help="median 合成のフレーム間隔 (秒、デフォルト: 0.2)",
    )
    parser.add_argument(
        "--camera-countdown", type=int, default=3,
        help="--use-camera 時、各サイクルのキャプチャ前カウントダウン秒数 (0 で無効)",
    )
    args = parser.parse_args()

    log, log_file = setup_logging(args.log_dir)

    log.info("=" * 60)
    log.info("VLM → prompt_builder → ImageGenerator → Vectorizer つなぎこみテスト")
    log.info(f"Log: {log_file}")
    log.info("=" * 60)

    if not torch.cuda.is_available():
        log.error("CUDA not available. Aborting.")
        return 1

    log.info(f"GPU            : {torch.cuda.get_device_name(0)}")
    log.info(f"Total VRAM     : {torch.cuda.get_device_properties(0).total_memory / 1024**3:.2f}GB")
    log.info(f"torch          : {torch.__version__}")
    log.info(f"use_camera     : {args.use_camera}")
    if args.use_camera:
        log.info(f"camera         : device={args.camera_device} "
                 f"{args.camera_width}x{args.camera_height} "
                 f"n_frames={args.camera_n_frames} "
                 f"interval={args.camera_interval}s "
                 f"countdown={args.camera_countdown}s")
    else:
        log.info(f"sketch         : {args.sketch}")
    log.info(f"sdxl steps     : {args.steps}")
    log.info(f"cycles         : {args.cycles}")
    log.info(f"seed           : {args.seed}")

    if not args.use_camera:
        if not args.sketch.exists():
            log.error(f"sketch not found: {args.sketch}")
            return 1
    else:
        log.info("--use-camera: skipping sketch file check")

    reset_peak()
    gpu_mem_snapshot("baseline", log)

    vlm = VLM(verbose=True)
    # imagegen + prompt 設定を yaml から読み込み (GUI で編集可能)
    from modules.image_gen import load_imagegen_config
    ig_cfg = load_imagegen_config()
    log.info(
        "imagegen config: preset=%s steps=%d guidance=%.2f cn=%.2f",
        ig_cfg.get("preset") or "(none)",
        ig_cfg["num_inference_steps"], ig_cfg["guidance_scale"],
        ig_cfg["controlnet_conditioning_scale"])
    # --steps が指定されていれば yaml を上書き
    if args.steps is not None:
        ig_cfg["num_inference_steps"] = int(args.steps)
    steps_eff = ig_cfg["num_inference_steps"]
    from modules.image_gen import build_image_generator_from_config
    image_gen = build_image_generator_from_config(ig_cfg, verbose=True)
    # vectorizer の binarize 設定を yaml から読み込み (パイプライン GUI の
    # 「閾値キャリブ」 で保存される ~/draw_piper/calibration/vectorizer_config.yaml)
    from modules.vectorizer import load_binarize_config
    bin_cfg = load_binarize_config()
    log.info(
        "vectorizer.binarize : method=%s block=%d c=%d fixed=%d",
        bin_cfg["binarize_method"], bin_cfg["adaptive_block_size"],
        bin_cfg["adaptive_c"], bin_cfg["fixed_threshold"])
    vectorizer = Vectorizer(verbose=True, **bin_cfg)

    camera = None
    if args.use_camera:
        log.info(
            "initializing camera (device=%d, %dx%d)",
            args.camera_device, args.camera_width, args.camera_height,
        )
        camera = Camera(
            device_id=args.camera_device,
            width=args.camera_width,
            height=args.camera_height,
            verbose=True,
        )
        camera.open()

    run_root = args.log_dir / f"vlm_to_image_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_root.mkdir(parents=True, exist_ok=True)

    all_timings: list = []
    try:
        for i in range(args.cycles):
            log.info("")
            log.info("#" * 60)
            log.info(f"# CYCLE {i + 1} / {args.cycles}")
            log.info("#" * 60)
            cycle_dir = run_root / f"cycle_{i + 1:02d}"
            cycle_dir.mkdir(parents=True, exist_ok=True)

            capture_elapsed = None
            if args.use_camera:
                assert camera is not None
                # countdown (各サイクルごとにユーザに撮影開始タイミングを知らせる)
                if args.camera_countdown > 0:
                    log.info(
                        "STAGE 0: capture countdown %d seconds...",
                        args.camera_countdown,
                    )
                    for s in range(args.camera_countdown, 0, -1):
                        log.info("  %d...", s)
                        time.sleep(1)

                log.info(
                    "---- STAGE 0: Camera median capture (%d frames @ %.2fs) ----",
                    args.camera_n_frames, args.camera_interval,
                )
                t_cap = time.time()
                captured = camera.capture_median(
                    n_frames=args.camera_n_frames,
                    interval_s=args.camera_interval,
                )
                capture_elapsed = time.time() - t_cap

                captured_path = cycle_dir / "captured.png"
                cv2.imwrite(str(captured_path), captured)
                log.info(
                    "  saved %s shape=%s elapsed=%.2fs",
                    captured_path, captured.shape, capture_elapsed,
                )
                sketch_path_for_cycle = captured_path
            else:
                sketch_path_for_cycle = args.sketch

            timing = run_one_cycle(
                sketch_path=sketch_path_for_cycle,
                cycle_dir=cycle_dir,
                log=log,
                vlm=vlm,
                image_gen=image_gen,
                vectorizer=vectorizer,
                sdxl_steps=steps_eff,
                seed=args.seed,
                prompt_base_template=ig_cfg.get("base_template"),
                prompt_fallback_template=ig_cfg.get("fallback_template"),
                prompt_confidence_threshold=float(
                    ig_cfg.get("confidence_threshold", 0.3)),
            )
            if capture_elapsed is not None:
                timing["camera_capture_s"] = capture_elapsed
            all_timings.append(timing)
    finally:
        if camera is not None:
            camera.close()

    log.info("")
    log.info("=" * 60)
    log.info("SUMMARY")
    log.info("=" * 60)
    for i, t in enumerate(all_timings, 1):
        log.info(f"--- cycle {i} ---")
        for k in (
            "camera_capture_s",
            "vlm_load_s",
            "vlm_predict_s",
            "vlm_unload_s",
            "image_gen_load_s",
            "image_gen_warmup_s",
            "image_gen_generate_s",
            "image_gen_unload_s",
            "vectorize_s",
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
