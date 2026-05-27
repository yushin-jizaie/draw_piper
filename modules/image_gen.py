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
    # 色味・写実系
    "color, shading, photo, photorealistic, gradient, "
    # ロボットアーム描画前提: ペン 1 本で描けない要素は全部 NG。
    # Vectorizer が strokes に変換する時にノイズと誤認するので、
    # 紙質感・ハッチング・塗りつぶし・グレー塗り は **絶対 NG**。
    "scribble, sketchy, crosshatch, hatching, pencil texture, "
    "scratch marks, noise, multiple overlapping lines, duplicate strokes, "
    "paper grain, paper texture, sepia tone, aged paper, "
    "brown background, beige background, "
    "gray background, dark background, filled background, busy background, "
    "background pattern, background texture, ink splatter, "
    "halftone, screentone, dot pattern, "
    "fabric texture, smudge, blurry, "
    # 商業データ由来のノイズ
    "watermark, signature, text, frame, border, "
    "manga panel border, page number, "
    "jpeg artifacts"
)
DEFAULT_RESOLUTION = 1024


# ----- モデルプリセット (画風比較用) ---------------------------------------
#
# 各プリセットは ImageGenerator.from_preset(name) で読める。
# 追加・調整は MODEL_PRESETS dict を編集するだけ。
#
# 注意:
#   - controlnet は SDXL 系で共通利用 (MistoLine) を想定
#   - num_inference_steps / guidance_scale は base モデルの推奨に合わせる
#     SDXL Turbo 系: 1-4 step, CFG 0
#     通常 SDXL / Animagine: 20-30 step, CFG 5-7
#   - variant=None なら HF からのデフォルト (fp32 weights) を取りに行く
#     → fp16 variant が無いモデル (Animagine 等) はこちらを使う

MODEL_PRESETS: dict[str, dict] = {
    # 現状のベースライン (写実寄り)
    "sdxl_turbo_mistoline": {
        "base_model_id": "stabilityai/sdxl-turbo",
        "controlnet_id": "TheMistoAI/MistoLine",
        "variant": "fp16",
        "num_inference_steps": 6,            # img2img だと effective steps が
        "guidance_scale": 0.0,                # strength で減るので 4 → 6 に増やす
        "controlnet_conditioning_scale": 1.0,
        "style_hint": (
            "line art, black ink on white, clean lines, "
            "white background, small character, lots of white space"
        ),
        "guide_dilate_ksize": 5,
        # img2img + ControlNet で空間レイアウト維持。 0.85 = 強めに denoise
        # (Turbo は steps 少ないので strength 高めにしないと変化が出にくい)
        "img2img_strength": 0.85,
    },
    # アニメ線画ベース (cagliostrolab/animagine-xl-3.1)
    # 線が太く clean、 影が少なめ。 ストローク描画と相性◎
    "animagine_xl_31_mistoline": {
        "base_model_id": "cagliostrolab/animagine-xl-3.1",
        "controlnet_id": "TheMistoAI/MistoLine",
        "variant": None,    # Animagine は fp16 variant なし
        "num_inference_steps": 30,
        "guidance_scale": 6.5,
        "controlnet_conditioning_scale": 0.9,
        "style_hint": (
            # Animagine 推奨の quality タグ + 線画指示 (短く)
            "masterpiece, monochrome lineart, thick clean lines, "
            "white background, small character, lots of white space"
        ),
        "guide_dilate_ksize": 5,
        # img2img: 0.7 で init 画像(白背景含む) と SDXL 生成の中間
        "img2img_strength": 0.7,
    },
    # 松本大洋風 LoRA (Animagine ベース + 自前学習 LoRA)
    # 学習: scripts/train_style_lora.py (training/matsumoto_taiyo/ の画像から)
    # LoRA 未学習時は preset 選択で FileNotFoundError → 先に学習する
    "matsumoto_taiyo_animagine": {
        "base_model_id": "cagliostrolab/animagine-xl-3.1",
        "controlnet_id": "TheMistoAI/MistoLine",
        "variant": None,
        "num_inference_steps": 32,
        "guidance_scale": 6.5,
        "controlnet_conditioning_scale": 0.85,
        # trigger word は学習時に caption へ挿入したものを使う
        # 短く保つ (CLIP 77 token 制限、 prompt と合わせて余裕持たせる)
        "style_hint": (
            "mt_taiyo_style, rough ink lineart, expressive face, "
            "monochrome, white background, lots of white space"
        ),
        # 学習結果。 path は project_root 相対 (相対パスは load() 時解決)
        "lora_path": "training/lora/matsumoto_taiyo.safetensors",
        # 2026-05-27 確定値 (方針 B: 白背景キープ + 顔ディテール追加):
        # - lora_scale 1.3: 線質・表情が松本タッチに乗る最低ライン
        # - img2img_strength 0.85: init 寄与 15% で oval スケールを残しつつ
        #   LoRA が紙質感や追加 stroke を出せる余地を確保
        "lora_scale": 1.3,
        "guide_dilate_ksize": 5,
        "img2img_strength": 0.85,
    },
    # matsumoto LoRA inpaint preset。
    # 方針: ユーザの黒線 (顔輪郭・目) は 100% 保持、 周辺の白部分には
    # 松本タッチの **線で** 体・髪・服 を描き足す。
    #
    # 重要な制約: 出力はロボットアームで ホワイトボードに描く。
    # → 紙質感・ハッチング・塗りつぶし・グレー塗り は 絶対 NG
    # → 「clean な黒線のみ」 を強制 (DEFAULT_NEGATIVE で押し込み済)
    # → LoRA は漫画パネル中心の学習なので背景埋め暴発しやすい。
    #    lora_scale を控えめにし、 prompt で「描く対象 = 体/髪/服 だけ」
    #    に絞って LoRA を線画モードに寄せる。
    "matsumoto_taiyo_inpaint": {
        "base_model_id": "cagliostrolab/animagine-xl-3.1",
        "controlnet_id": "TheMistoAI/MistoLine",
        "variant": None,
        "num_inference_steps": 32,
        "guidance_scale": 7.0,
        "controlnet_conditioning_scale": 0.85,
        # 線画 LoRA で学習済の trigger + 描く対象。 短く (77 token 内)。
        # 「white background」 は DEFAULT_NEGATIVE 側に既にある類語で抑制
        # 済なので positive で重ねず、 描く対象に集中
        "style_hint": (
            "mt_taiyo_style, character with body, messy hair, ink line art"
        ),
        "lora_path": "training/lora/matsumoto_taiyo.safetensors",
        "lora_scale": 1.4,                # 1.0 → 1.4 (lineart LoRA は控えめ気味)
        "guide_dilate_ksize": 5,
        "inpaint_mode": True,
        "inpaint_line_threshold": 200,
        "inpaint_keep_dilate": 4,
        "inpaint_strength": 1.0,          # 0.9 → 1.0 (mask 内は完全再生成、
                                          #   init の白背景 prior を捨てる)
    },
    # アニメ線画 + 速度寄り (SDXL Lightning + MistoLine)
    # 4-step 推論で SDXL Turbo より画質高め。 比較用
    "sdxl_lightning_4step_mistoline": {
        "base_model_id": "ByteDance/SDXL-Lightning",
        "controlnet_id": "TheMistoAI/MistoLine",
        "variant": None,
        "num_inference_steps": 4,
        "guidance_scale": 1.0,
        "controlnet_conditioning_scale": 1.0,
        "style_hint": (
            "line art, clean black lines on white background, "
            "no shading, simple"
        ),
        # NOTE: SDXL-Lightning は通常 unet weights を別 repo から差し替える
        # 必要がある (4step 等の variant) — Step C 実装の段階では未対応。
        # 動作確認は安定版 (sdxl-base + Lightning LoRA) でやり直す可能性あり
    },
}


# ----- imagegen_config.yaml 読み書き (GUI からの編集用) -----------------

from pathlib import Path as _Path

DEFAULT_IMAGEGEN_CONFIG_PATH = (
    _Path(__file__).resolve().parent.parent
    / "calibration" / "imagegen_config.yaml"
)


def load_imagegen_config(path: Optional[_Path] = None) -> dict:
    """imagegen_config.yaml から SDXL + prompt 設定を読み込む。
    戻り値は ImageGenerator.__init__ / VLM / prompt_builder に渡せる dict。
    ファイル無し / 失敗時は組込み既定値。
    """
    cfg_path = _Path(path) if path else DEFAULT_IMAGEGEN_CONFIG_PATH
    defaults = {
        "preset": None,    # None = preset を使わず numeric だけで構築
        "num_inference_steps": DEFAULT_NUM_INFERENCE_STEPS,
        "guidance_scale": DEFAULT_GUIDANCE_SCALE,
        "controlnet_conditioning_scale": DEFAULT_CONTROLNET_SCALE,
        "negative_prompt": DEFAULT_NEGATIVE_PROMPT,
        "base_template": None,    # None = prompt_builder の既定を使う
        "fallback_template": None,
        "confidence_threshold": 0.3,
    }
    if not cfg_path.exists():
        return defaults
    try:
        import yaml as _yaml
        with open(cfg_path) as f:
            data = _yaml.safe_load(f) or {}
    except Exception:
        return defaults
    ig = data.get("imagegen") or {}
    pr = data.get("prompt") or {}
    out = dict(defaults)
    if "preset" in ig:
        v = ig["preset"]
        out["preset"] = str(v) if v else None
    for k in ("num_inference_steps", "guidance_scale",
              "controlnet_conditioning_scale", "negative_prompt"):
        if k in ig:
            out[k] = ig[k]
    if "base_template" in pr:
        out["base_template"] = pr["base_template"] or None
    if "fallback_template" in pr:
        out["fallback_template"] = pr["fallback_template"] or None
    if "confidence_threshold" in pr:
        try:
            out["confidence_threshold"] = float(pr["confidence_threshold"])
        except Exception:
            pass
    return out


def save_imagegen_config(
    num_inference_steps: int,
    guidance_scale: float,
    controlnet_conditioning_scale: float,
    negative_prompt: str,
    base_template: Optional[str] = None,
    fallback_template: Optional[str] = None,
    confidence_threshold: float = 0.3,
    preset: Optional[str] = None,
    path: Optional[_Path] = None,
) -> _Path:
    """imagegen_config.yaml に書き出し。"""
    cfg_path = _Path(path) if path else DEFAULT_IMAGEGEN_CONFIG_PATH
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    import yaml as _yaml
    data = {
        "imagegen": {
            "preset": preset or "",
            "num_inference_steps": int(num_inference_steps),
            "guidance_scale": float(guidance_scale),
            "controlnet_conditioning_scale": float(controlnet_conditioning_scale),
            "negative_prompt": str(negative_prompt),
        },
        "prompt": {
            "base_template": base_template or "",
            "fallback_template": fallback_template or "",
            "confidence_threshold": float(confidence_threshold),
        },
    }
    with open(cfg_path, "w") as f:
        _yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True,
                          default_flow_style=False)
    return cfg_path


def build_image_generator_from_config(cfg: dict, *, verbose: bool = True) -> "ImageGenerator":
    """imagegen_config から ImageGenerator を組み立てる。
    preset が指定されていれば from_preset() で base + controlnet + LoRA を取り、
    numeric (steps / guidance / cn_scale) と negative_prompt は yaml の値で
    上書きする。
    """
    preset = cfg.get("preset")
    overrides = {
        "verbose": verbose,
        "num_inference_steps": int(cfg["num_inference_steps"]),
        "guidance_scale": float(cfg["guidance_scale"]),
        "controlnet_conditioning_scale": float(
            cfg["controlnet_conditioning_scale"]),
        "negative_prompt": str(cfg["negative_prompt"]),
    }
    if preset and preset in MODEL_PRESETS:
        return ImageGenerator.from_preset(preset, **overrides)
    if preset:
        # 不明な preset 名 — 警告して既定にフォールバック
        print(f"[image_gen] WARN: unknown preset '{preset}', "
              f"falling back to default. available: {list(MODEL_PRESETS)}")
    return ImageGenerator(**overrides)


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


def _dilate_guide_lines(img: Image.Image,
                         ksize: int,
                         line_threshold: int = 128) -> Image.Image:
    """Lineart guide の線を太らせる。 sparse / 細い入力で ControlNet が
    空間情報を保持できず SDXL が「被写体は全画面」 prior に支配されて
    顔が画面はみ出す問題への対処。

    Parameters
    ----------
    img : PIL.Image
        guide image (黒線 on 白背景 想定、 RGB)
    ksize : int
        cv2.dilate のカーネルサイズ (1 以下なら no-op)。 推奨 3-7。
    line_threshold : int
        線とみなす階調 (< threshold = 線、 0-255)

    Returns
    -------
    PIL.Image (RGB)
    """
    if ksize <= 1:
        return img
    try:
        import cv2 as _cv2
    except ImportError:
        return img
    arr = np.array(img.convert("L"))
    line_mask = (arr < int(line_threshold)).astype(np.uint8) * 255
    kernel = np.ones((int(ksize), int(ksize)), np.uint8)
    dilated = _cv2.dilate(line_mask, kernel, iterations=1)
    result = 255 - dilated
    return Image.fromarray(result).convert("RGB")


def _make_inpaint_mask(img: Image.Image,
                        line_threshold: int = 200,
                        keep_dilate: int = 3) -> Image.Image:
    """Inpaint mask を生成。

    入力: 黒線 on 白背景 の sketch (RGB)
    出力: 黒(0) = 元画素を保持 / 白(255) = SDXL に再生成させる、 の白黒 mask

    Parameters
    ----------
    img : PIL.Image
        guide image (RGB)
    line_threshold : int
        この値以上の輝度を「白背景=生成領域」 とみなす (0-255)。
        200 だとアンチエイリアスの薄いグレーも 保持側に倒れる
    keep_dilate : int
        keep 領域(=線) を膨らませる pixel 数。 ユーザの線の周辺数 px は
        触らないようにすると 線が SDXL に塗りつぶされるのを防げる。
        0 で off, 推奨 3-5

    Returns
    -------
    PIL.Image (mode='L')
        白 = generate / 黒 = keep
    """
    arr = np.array(img.convert("L"))
    # 白(>=threshold) を生成領域(=255)、 黒(<threshold) を keep(=0)
    mask = (arr >= int(line_threshold)).astype(np.uint8) * 255
    if keep_dilate >= 1:
        try:
            import cv2 as _cv2
        except ImportError:
            return Image.fromarray(mask)
        # generate 領域を erode = keep 領域を膨らます
        kernel = np.ones((int(keep_dilate), int(keep_dilate)), np.uint8)
        mask = _cv2.erode(mask, kernel, iterations=1)
    return Image.fromarray(mask)


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
        variant: Optional[str] = "fp16",
        style_hint: str = "",
        lora_path: Optional[str] = None,
        lora_scale: float = 1.0,
        guide_dilate_ksize: int = 0,
        img2img_strength: float = 0.0,
        inpaint_mode: bool = False,
        inpaint_line_threshold: int = 200,
        inpaint_keep_dilate: int = 3,
        inpaint_strength: float = 1.0,
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
        # HF variant ("fp16" / "fp32" / None)。 Animagine 等 fp16 variant
        # 無いモデルは None。 ControlNet 側にも同じ variant を試す。
        self.variant = variant
        # caller (prompt_builder 等) が prompt に追加するスタイル指示
        self.style_hint = style_hint
        # LoRA weights を base に焼き込まずに ロード (推論時に lora_scale で混合)。
        # 相対パスは project_root 起点で解決される (load() で resolve)。
        self.lora_path = lora_path
        self.lora_scale = float(lora_scale)
        # 入力線が細すぎて ControlNet が空間保持できない問題への対処。
        # 1 以下 = 無効、 推奨 3-7 (5 が標準)
        self.guide_dilate_ksize = int(guide_dilate_ksize)
        # img2img + ControlNet モード。 0.0 = text2img (従来)、
        # 0.5-0.7 = init_image (ユーザのスケッチ) の白背景 prior を残しつつ
        # ControlNet で線位置 guide。 SDXL の "subject fills frame" prior を抑制し、
        # ユーザの描いた小さい oval を そのスケールのまま 残せる
        self.img2img_strength = float(img2img_strength)
        # Inpaint mode (img2img_strength より優先)。 ユーザの線(黒画素) は
        # 100% 保持、 白背景部分は SDXL + LoRA に完全自由 で再描画させる。
        # → img2img の「init を一律 N% 残す」 では出来ない 「線だけ exact 保持
        # + 周辺は LoRA で大胆に描画」 が可能
        self.inpaint_mode = bool(inpaint_mode)
        self.inpaint_line_threshold = int(inpaint_line_threshold)
        self.inpaint_keep_dilate = int(inpaint_keep_dilate)
        # inpaint denoising strength。 1.0 = mask 領域は完全再生成 (init 無視)。
        # 0.6-0.9 = mask 領域に init pixel (白背景) の prior を 部分的に残す
        # → LoRA の「背景パターンで埋める」 prior を白背景プリミティブで抑制
        self.inpaint_strength = float(inpaint_strength)
        self.verbose = verbose

        self._pipe = None
        self._controlnet = None

    @classmethod
    def from_preset(cls, name: str, **overrides) -> "ImageGenerator":
        """Build an ImageGenerator from MODEL_PRESETS[name]. overrides は
        プリセットの個別フィールドを上書きする (verbose 等を渡す用)。
        """
        if name not in MODEL_PRESETS:
            raise KeyError(
                f"unknown preset '{name}'. available: {list(MODEL_PRESETS)}")
        cfg = dict(MODEL_PRESETS[name])
        cfg.update(overrides)
        return cls(**cfg)

    @property
    def is_loaded(self) -> bool:
        return self._pipe is not None

    def load(self) -> None:
        if self.is_loaded:
            return

        # mode 選択: inpaint > img2img > text2img の優先順
        use_inpaint = self.inpaint_mode
        use_img2img = (not use_inpaint) and self.img2img_strength > 0.0
        from diffusers import ControlNetModel
        if use_inpaint:
            from diffusers import (
                StableDiffusionXLControlNetInpaintPipeline as PipeClass,
            )
        elif use_img2img:
            from diffusers import (
                StableDiffusionXLControlNetImg2ImgPipeline as PipeClass,
            )
        else:
            from diffusers import (
                StableDiffusionXLControlNetPipeline as PipeClass,
            )

        if self.verbose:
            print(f"[image_gen] loading {self.controlnet_id} ...")
        t0 = time.time()
        # ControlNet の variant は base model とは独立。 MistoLine は
        # fp16 variant のみ提供 (.fp16.safetensors) なので、 base が
        # Animagine (variant=None) でも ControlNet 側は fp16 で読む。
        # 段階的に fp16 -> None -> .bin 形式 と試行錯誤
        cn_load_attempts = ["fp16", None]
        last_err = None
        self._controlnet = None
        for cn_variant in cn_load_attempts:
            cn_kwargs = {"torch_dtype": self.torch_dtype,
                         "use_safetensors": True}
            if cn_variant:
                cn_kwargs["variant"] = cn_variant
            try:
                self._controlnet = ControlNetModel.from_pretrained(
                    self.controlnet_id, **cn_kwargs,
                )
                if self.verbose and cn_variant != "fp16":
                    print(f"[image_gen] controlnet loaded with "
                          f"variant={cn_variant}")
                break
            except (OSError, ValueError) as e:
                last_err = e
                if self.verbose:
                    print(f"[image_gen] controlnet variant={cn_variant} "
                          f"not found, trying next: {e}")
        if self._controlnet is None:
            # 最後の手段: use_safetensors を外して .bin も許可
            try:
                self._controlnet = ControlNetModel.from_pretrained(
                    self.controlnet_id, torch_dtype=self.torch_dtype,
                )
                if self.verbose:
                    print(f"[image_gen] controlnet loaded as .bin fallback")
            except Exception:
                raise last_err
        if self.verbose:
            print(f"[image_gen] controlnet loaded in {time.time() - t0:.1f}s")

        if self.verbose:
            print(f"[image_gen] loading {self.base_model_id} ...")
        t0 = time.time()
        base_kwargs = {
            "controlnet": self._controlnet,
            "torch_dtype": self.torch_dtype,
            "use_safetensors": True,
        }
        if self.variant:
            base_kwargs["variant"] = self.variant
        try:
            self._pipe = PipeClass.from_pretrained(
                self.base_model_id, **base_kwargs,
            )
        except (OSError, ValueError) as e:
            if self.variant and "variant" in base_kwargs:
                if self.verbose:
                    print(f"[image_gen] base variant={self.variant} not "
                          f"found, retrying without variant: {e}")
                base_kwargs.pop("variant", None)
                self._pipe = PipeClass.from_pretrained(
                    self.base_model_id, **base_kwargs,
                )
            else:
                raise
        if self.verbose:
            if use_inpaint:
                mode = "inpaint"
            elif use_img2img:
                mode = "img2img"
            else:
                mode = "text2img"
            print(f"[image_gen] pipeline loaded ({mode}) in "
                  f"{time.time() - t0:.1f}s")

        t0 = time.time()
        self._pipe = self._pipe.to(self.device)
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        if self.verbose:
            print(f"[image_gen] moved to {self.device} in {time.time() - t0:.1f}s")

        # LoRA load (optional)
        if self.lora_path:
            lora_path = Path(self.lora_path)
            if not lora_path.is_absolute():
                # resolve relative to project_root (parent of modules/)
                lora_path = (
                    Path(__file__).resolve().parent.parent / lora_path
                )
            if not lora_path.exists():
                raise FileNotFoundError(
                    f"LoRA weights not found: {lora_path}. "
                    f"Train via scripts/train_style_lora.py first."
                )
            if self.verbose:
                print(f"[image_gen] loading LoRA {lora_path} (scale={self.lora_scale}) ...")
            t0 = time.time()
            self._pipe.load_lora_weights(str(lora_path))
            if self.verbose:
                print(f"[image_gen] LoRA loaded in {time.time() - t0:.1f}s")

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
        if self.guide_dilate_ksize > 1:
            pil_guide = _dilate_guide_lines(pil_guide,
                                              ksize=self.guide_dilate_ksize)
            if self.verbose:
                print(f"[image_gen] guide dilated (ksize="
                      f"{self.guide_dilate_ksize})")

        generator = None
        if seed is not None:
            generator = torch.Generator(device=self.device).manual_seed(int(seed))

        if torch.cuda.is_available():
            torch.cuda.synchronize()
        t0 = time.time()
        # mode で渡す引数構造が変わる:
        #   text2img : image=control
        #   img2img  : image=init, control_image=control, strength=...
        #   inpaint  : image=init, mask_image=mask, control_image=control,
        #              strength=1.0 (mask 内を完全 regenerate)
        use_inpaint = self.inpaint_mode
        use_img2img = (not use_inpaint) and self.img2img_strength > 0.0
        if use_inpaint:
            init_image = _normalize_image(guide_image, size=self.resolution)
            mask_image = _make_inpaint_mask(
                init_image,
                line_threshold=self.inpaint_line_threshold,
                keep_dilate=self.inpaint_keep_dilate,
            )
            if self.verbose:
                m_arr = np.array(mask_image)
                gen_pct = (m_arr > 127).mean() * 100
                print(f"[image_gen] inpaint mask: {gen_pct:.1f}% generate, "
                      f"{100 - gen_pct:.1f}% keep "
                      f"(thresh={self.inpaint_line_threshold}, "
                      f"keep_dilate={self.inpaint_keep_dilate})")
            pipe_kwargs = {
                "prompt": prompt,
                "negative_prompt": neg,
                "image": init_image,
                "mask_image": mask_image,
                "control_image": pil_guide,
                "strength": self.inpaint_strength,
                "num_inference_steps": steps,
                "guidance_scale": gs,
                "controlnet_conditioning_scale": cn,
                "generator": generator,
            }
        elif use_img2img:
            # init_image は元のユーザ画像 (dilate 前の生画像)。 白背景の
            # prior を残すため。 control_image は dilate 後の lineart
            init_image = _normalize_image(guide_image, size=self.resolution)
            pipe_kwargs = {
                "prompt": prompt,
                "negative_prompt": neg,
                "image": init_image,
                "control_image": pil_guide,
                "strength": self.img2img_strength,
                "num_inference_steps": steps,
                "guidance_scale": gs,
                "controlnet_conditioning_scale": cn,
                "generator": generator,
            }
        else:
            pipe_kwargs = {
                "prompt": prompt,
                "negative_prompt": neg,
                "image": pil_guide,
                "num_inference_steps": steps,
                "guidance_scale": gs,
                "controlnet_conditioning_scale": cn,
                "generator": generator,
            }
        # LoRA を有効にする場合は cross_attention_kwargs で scale を渡す
        # (load_lora_weights だけでは fuse されないので、 推論毎に指定が必要)
        if self.lora_path:
            pipe_kwargs["cross_attention_kwargs"] = {"scale": self.lora_scale}
        with torch.no_grad():
            out = self._pipe(**pipe_kwargs).images[0]
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
