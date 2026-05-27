#!/usr/bin/env python3
"""SDXL LoRA fine-tune (画風学習) wrapper.

diffusers 公式の `examples/text_to_image/train_text_to_image_lora_sdxl.py`
をサブプロセスで起動するラッパー。 学習スクリプト本体は重く maintainable
な実装なので自前で書かず、 我々のリポは「データ整形 + 起動引数」だけ持つ。

前提:
  - scripts/prepare_style_dataset.py で training/<name>/dataset/ が
    作成済み (画像 + 同名 .txt キャプション)
  - diffusers と accelerate が venv に入っている
    (requirements-imagegen.txt に列挙済み)
  - 学習時 VRAM 12-16GB 推奨 (rank=32, batch=1, gradient_checkpointing, bf16)

挙動:
  1. diffusers の examples を取得 (HF_DIFFUSERS_PATH が指す checkout、
     なければ ~/.cache/draw_piper/diffusers に shallow clone)
  2. dataset を <name>/metadata.jsonl に変換 (image+caption の jsonl)
  3. accelerate launch でトレーニング起動 → 出力 LoRA を
     training/lora/<name>.safetensors に保存

使用:
  python3 -m scripts.train_style_lora \\
      --dataset training/matsumoto_taiyo/dataset \\
      --name matsumoto_taiyo \\
      --base cagliostrolab/animagine-xl-3.1 \\
      --rank 32 --steps 1500
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_DIFFUSERS_CACHE = Path.home() / ".cache" / "draw_piper" / "diffusers"
DIFFUSERS_REPO = "https://github.com/huggingface/diffusers"
TRAIN_SCRIPT_REL = Path("examples") / "text_to_image" / "train_text_to_image_lora_sdxl.py"

# diffusers training 例が import する追加依存 (venv に最初から入ってない場合あり)
TRAINING_DEPS = ["datasets", "ftfy", "Jinja2", "tensorboard", "peft"]


def _check_training_deps() -> None:
    """学習スクリプトが import する追加 pip 依存を pre-flight チェック。

    欠けているものがあれば エラーで止め、 インストール手順を表示する。
    """
    import importlib
    missing = []
    for mod in TRAINING_DEPS:
        try:
            importlib.import_module(mod.lower())
        except ImportError:
            missing.append(mod)
    if missing:
        print(f"[train] ERROR: 学習に必要な pip パッケージが未インストール: "
              f"{', '.join(missing)}", file=sys.stderr)
        print(f"[train] 解決: pip install {' '.join(missing)}",
              file=sys.stderr)
        # diffusers 例の requirements_sdxl.txt があればそちらも案内
        req_sdxl = (DEFAULT_DIFFUSERS_CACHE / "examples"
                    / "text_to_image" / "requirements_sdxl.txt")
        if req_sdxl.exists():
            print(f"[train] または: pip install -r {req_sdxl}",
                  file=sys.stderr)
        raise SystemExit(2)


def _installed_diffusers_tag() -> Optional[str]:
    """pip でインストール済の diffusers のバージョンに対応する git tag。

    例: '0.38.0' → 'v0.38.0'。 dev 版 / 不明時は None。
    """
    try:
        import diffusers   # type: ignore
        v = diffusers.__version__
    except ImportError:
        return None
    # '0.38.0+cu118' / '0.38.0.dev0' などは tag が無いので落とす
    base = v.split('+')[0]
    if '.dev' in base or 'rc' in base:
        return None
    return f"v{base}"


def _resolve_diffusers_path() -> Path:
    """Locate a diffusers checkout (env var > project-cache); clone if missing.

    pip でインストール済 diffusers のバージョンに合致する git tag で clone。
    キャッシュが別バージョンなら再 clone する (check_min_version で死ぬのを防ぐ)。
    """
    env_path = os.environ.get("HF_DIFFUSERS_PATH")
    if env_path:
        p = Path(env_path).expanduser()
        if (p / TRAIN_SCRIPT_REL).exists():
            return p
        print(f"[train] HF_DIFFUSERS_PATH={p} but training script not found",
              file=sys.stderr)

    cache = DEFAULT_DIFFUSERS_CACHE
    target_tag = _installed_diffusers_tag()    # e.g. "v0.38.0"

    # キャッシュ存在 + tag 一致なら再利用
    if cache.exists() and (cache / TRAIN_SCRIPT_REL).exists():
        cached_tag = None
        try:
            cached_tag = subprocess.check_output(
                ["git", "-C", str(cache), "describe", "--tags",
                 "--exact-match", "HEAD"],
                stderr=subprocess.DEVNULL).decode().strip()
        except Exception:
            pass
        if target_tag is None or cached_tag == target_tag:
            return cache
        print(f"[train] cached diffusers is {cached_tag or 'unknown'}, "
              f"target is {target_tag} → re-cloning")
        shutil.rmtree(cache)

    cache.parent.mkdir(parents=True, exist_ok=True)
    clone_cmd = ["git", "clone", "--depth", "1"]
    if target_tag:
        clone_cmd += ["--branch", target_tag]
        print(f"[train] cloning diffusers (tag={target_tag}) -> {cache} ...")
    else:
        print(f"[train] cloning diffusers (HEAD) -> {cache} "
              f"— installed diffusers バージョンから tag 特定不可")
    clone_cmd += [DIFFUSERS_REPO, str(cache)]

    try:
        subprocess.run(clone_cmd, check=True)
    except subprocess.CalledProcessError:
        if target_tag:
            print(f"[train] tag {target_tag} clone 失敗 → HEAD で再試行",
                  file=sys.stderr)
            subprocess.run(["git", "clone", "--depth", "1",
                            DIFFUSERS_REPO, str(cache)], check=True)
        else:
            raise

    if not (cache / TRAIN_SCRIPT_REL).exists():
        raise RuntimeError(
            f"clone succeeded but {TRAIN_SCRIPT_REL} missing in {cache}")
    return cache


def _build_metadata_jsonl(dataset_dir: Path) -> Path:
    """Convert <image>.png + <image>.txt → metadata.jsonl in the same dir.

    Format expected by diffusers t2i example:
      {"file_name": "img1.png", "text": "caption..."}
    """
    img_paths = sorted(
        p for p in dataset_dir.iterdir()
        if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    )
    if not img_paths:
        raise RuntimeError(f"no images in {dataset_dir}")
    metadata = dataset_dir / "metadata.jsonl"
    with open(metadata, "w", encoding="utf-8") as f:
        for img in img_paths:
            txt = img.with_suffix(".txt")
            if not txt.exists():
                print(f"[train] WARN: no caption for {img.name}, skipping",
                      file=sys.stderr)
                continue
            caption = txt.read_text(encoding="utf-8").strip()
            f.write(json.dumps(
                {"file_name": img.name, "text": caption},
                ensure_ascii=False) + "\n")
    n = sum(1 for _ in open(metadata, encoding="utf-8"))
    print(f"[train] wrote {metadata}  ({n} entries)")
    return metadata


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", type=Path, required=True,
                    help="prepare_style_dataset.py の出力フォルダ")
    ap.add_argument("--name", type=str, required=True,
                    help="LoRA 名 (training/lora/<name>.safetensors に保存)")
    ap.add_argument("--base", type=str,
                    default="cagliostrolab/animagine-xl-3.1",
                    help="LoRA を載せる base SDXL モデル")
    ap.add_argument("--resolution", type=int, default=1024)
    ap.add_argument("--rank", type=int, default=32,
                    help="LoRA rank (16-64 推奨、 高いほど画風強く・サイズ大)")
    ap.add_argument("--steps", type=int, default=1500,
                    help="学習ステップ数 (1000-3000 推奨)")
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--batch", type=int, default=1,
                    help="VRAM ぎりぎりなら 1。 余裕あれば 2-4")
    ap.add_argument("--checkpointing", action="store_true", default=True,
                    help="gradient checkpointing (VRAM 節約・速度↓)")
    ap.add_argument("--mixed-precision", choices=["bf16", "fp16", "no"],
                    default="bf16")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--dry-run", action="store_true",
                    help="コマンドを表示するだけで実行しない")
    args = ap.parse_args()

    if not args.dataset.exists():
        print(f"[train] dataset not found: {args.dataset}", file=sys.stderr)
        return 2

    # 0. pre-flight: 学習スクリプト依存チェック (--dry-run も実行前に)
    _check_training_deps()

    # 1. metadata.jsonl 作成
    _build_metadata_jsonl(args.dataset)

    # 2. diffusers checkout 確保
    diffusers_dir = _resolve_diffusers_path()
    train_script = diffusers_dir / TRAIN_SCRIPT_REL
    print(f"[train] using {train_script}")

    # 3. 出力先
    out_dir = _ROOT / "training" / "lora_runs" / args.name
    out_dir.mkdir(parents=True, exist_ok=True)
    final_path = _ROOT / "training" / "lora" / f"{args.name}.safetensors"
    final_path.parent.mkdir(parents=True, exist_ok=True)

    # 4. accelerate launch コマンド組み立て
    cmd = [
        "accelerate", "launch", str(train_script),
        f"--pretrained_model_name_or_path={args.base}",
        f"--train_data_dir={args.dataset.resolve()}",
        f"--resolution={args.resolution}",
        f"--train_batch_size={args.batch}",
        f"--rank={args.rank}",
        f"--learning_rate={args.lr}",
        f"--lr_scheduler=cosine",
        f"--lr_warmup_steps=100",
        f"--max_train_steps={args.steps}",
        f"--checkpointing_steps={max(200, args.steps // 5)}",
        f"--output_dir={out_dir}",
        f"--mixed_precision={args.mixed_precision}",
        f"--seed={args.seed}",
        "--caption_column=text",
        "--image_column=file_name",
        "--center_crop",
        "--random_flip",
    ]
    if args.checkpointing:
        cmd.append("--gradient_checkpointing")
    # base モデルの variant 指定。 fp16 variant がある SDXL Turbo 等は
    # --variant=fp16 を渡して VRAM 節約。 Animagine は variant 無いので
    # 何も渡さない (デフォルト = fp32 weights をロード)
    if "sdxl-turbo" in args.base.lower():
        cmd.append("--variant=fp16")
    # Animagine / Lightning は変数指定なし (script デフォルト = None)

    print(f"[train] launching:\n  {' '.join(cmd)}")
    if args.dry_run:
        print("[train] dry-run, exiting")
        return 0

    # 5. 実行
    try:
        subprocess.run(cmd, check=True, cwd=_ROOT)
    except subprocess.CalledProcessError as e:
        print(f"[train] training failed with exit {e.returncode}",
              file=sys.stderr)
        return e.returncode

    # 6. LoRA weights を training/lora/<name>.safetensors にコピー
    candidates = list(out_dir.glob("pytorch_lora_weights.safetensors"))
    if not candidates:
        # fall back to any .safetensors in out_dir
        candidates = list(out_dir.glob("*.safetensors"))
    if candidates:
        src = candidates[0]
        shutil.copy(src, final_path)
        print(f"[train] LoRA saved -> {final_path}")
        size_mb = final_path.stat().st_size / (1024 * 1024)
        print(f"[train] size: {size_mb:.1f} MB")
    else:
        print(f"[train] WARN: no .safetensors in {out_dir}", file=sys.stderr)
        return 3

    # 7. 使用方法を表示
    print()
    print("[train] === next steps ===")
    print(f"  preset 'matsumoto_taiyo_animagine' で参照される LoRA path:")
    print(f"    training/lora/{args.name}.safetensors")
    print()
    print(f"  比較スクリプトで動作確認:")
    print(f"    python3 -m scripts.compare_imagegen_models \\")
    print(f"        --guide path/to/sketch.jpg --prompt '...' \\")
    print(f"        --presets matsumoto_taiyo_animagine animagine_xl_31_mistoline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
