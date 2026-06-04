"""ルート別の生成バックエンド (2026-06-04)。

各 backend は「crop画像 + prompt + seed → 生成PIL画像」だけを実装する。
下流 (線抽出・vectorize・曲率制約・stroke順・warp・出力) は modules/route_driver が担う。
diffusers などの重い import は各 backend の load() 内で遅延する (VRAM/起動時間配慮)。

make_backend(route_id, args) が 1 つだけ生成する (16GB VRAM・推論排他のため同時生成しない)。
"""
from __future__ import annotations
import gc
import torch
from PIL import Image

from modules.route_driver import CW, CH, SIZE, canny_ctrl, log

# --- FLUX-decorate (現行ルート) 定数 ---
LORA_DIR = "models/flux_lora_winners"; LORA_STR = 0.6; TRIGGER = "tklineart"
REPO = "chutesai/FLUX.1-schnell"; CN = "Shakker-Labs/FLUX.1-dev-ControlNet-Union-Pro"
CN_SCALE = 0.55
# ロボットが物理的に描ける形に誘導: 太く大胆・滑らかな大曲線・微小ディテール/渦巻き/小円なし。
STYLE = ("manga style, clean bold black ink lineart on white background, "
         "thick smooth confident strokes, large gentle curves, simple bold shapes, "
         "no tiny details, no fine hatching, no spirals, no small concentric circles, "
         "no intricate texture")


def _cn_steps_from_config(args, default_cn):
    """imagegen_config.yaml から controlnet_conditioning_scale と num_inference_steps を読む。"""
    cn_scale = default_cn; steps = max(1, int(args.steps))
    try:
        from modules.image_gen import load_imagegen_config
        ig = load_imagegen_config()
        if ig.get("controlnet_conditioning_scale") is not None:
            cn_scale = float(ig["controlnet_conditioning_scale"])
        if ig.get("num_inference_steps"):
            steps = max(1, min(50, int(ig["num_inference_steps"])))
    except Exception as e:
        log(f"imagegen_config 読込スキップ ({e})")
    return cn_scale, steps


class FluxDecorateBackend:
    """現行 DECORATE ルート: FLUX.1-schnell + winners LoRA@0.6 + ControlNet Union(canny)。"""
    name = "flux_decorate"
    route_label = "DECORATE (FLUX+winnersLoRA+decorate+CN0.55+manga+opencv+center-out)"
    multi_object = True
    uses_vlm = True

    def __init__(self, args):
        self.args = args
        self.cn_scale, self.steps = _cn_steps_from_config(args, CN_SCALE)
        log(f"flux_decorate: CN_scale={self.cn_scale:.2f} steps={self.steps} "
            f"(preset/guidance/negative は schnell では無効)")
        self.pipe = None

    def build_prompt(self, vision):
        return f"{TRIGGER}, {vision.get('vision') or vision.get('scene') or 'subject'} {STYLE}"

    def load(self):
        log("FLUX/imagegen load")
        from diffusers import (FluxControlNetModel, FluxControlNetPipeline,
                               FluxTransformer2DModel, BitsAndBytesConfig as DBNB)
        from transformers import T5EncoderModel, BitsAndBytesConfig as TBNB
        dnf4 = DBNB(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.bfloat16)
        tnf4 = TBNB(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.bfloat16)
        tr = FluxTransformer2DModel.from_pretrained(REPO, subfolder="transformer", quantization_config=dnf4, torch_dtype=torch.bfloat16)
        te2 = T5EncoderModel.from_pretrained(REPO, subfolder="text_encoder_2", quantization_config=tnf4, torch_dtype=torch.bfloat16)
        cnet = FluxControlNetModel.from_pretrained(CN, torch_dtype=torch.bfloat16)
        self.pipe = FluxControlNetPipeline.from_pretrained(
            REPO, transformer=tr, text_encoder_2=te2, controlnet=cnet, torch_dtype=torch.bfloat16)
        self.pipe.load_lora_weights(LORA_DIR, adapter_name="winners")
        self.pipe.set_adapters(["winners"], [LORA_STR])
        self.pipe.enable_model_cpu_offload()
        log("FLUX ready")

    def generate_object_image(self, crop, prompt, seed):
        from modules.input_prep import square_pad
        ctrl = canny_ctrl(square_pad(crop, SIZE))
        return self.pipe(prompt=prompt, control_image=ctrl, control_mode=0,
                         controlnet_conditioning_scale=self.cn_scale, width=SIZE, height=SIZE,
                         num_inference_steps=self.steps, guidance_scale=0.0,
                         generator=torch.Generator("cpu").manual_seed(seed)).images[0]

    def teardown(self):
        self.pipe = None; gc.collect(); torch.cuda.empty_cache()


class SdxlRoutedBackend:
    """SDXL + ControlNet(MistoLine) + 占有率ルート判定 (gen_routed 相当)。

    preset は imagegen_config.yaml (= GUI の SDXL/プロンプト設定ダイアログ) から。
    decide_route で framed(square_pad) / stylize(resize) のガイドを切替。
    SDXL は negative_prompt も有効 (preset 既定を使う)。
    """
    name = "sdxl_routed"
    route_label = "SDXL routed (preset+ControlNet+occupancy route)"
    multi_object = True
    uses_vlm = True
    PRESET_OVERRIDE = None      # None = config の preset を使う

    def __init__(self, args):
        self.args = args
        self.cn_scale, self.steps = _cn_steps_from_config(args, 0.55)
        preset = self.PRESET_OVERRIDE
        if preset is None:
            try:
                from modules.image_gen import load_imagegen_config
                preset = load_imagegen_config().get("preset") or "illustrious_v2_lineart_char"
            except Exception:
                preset = "illustrious_v2_lineart_char"
        self.preset = preset
        log(f"{self.name}: preset={self.preset} CN={self.cn_scale:.2f} steps={self.steps}")
        self.gen = None

    def build_prompt(self, vision):
        # design_instruction 本文をプロンプト本体に (style は preset の style_suffix が付与)。
        return vision.get("vision") or vision.get("scene") or "a subject"

    def load(self):
        log(f"SDXL/imagegen load (preset={self.preset})")
        from modules.image_gen import ImageGenerator
        self.gen = ImageGenerator.from_preset(self.preset, resolution=(CW, CH), verbose=False)
        self.gen.load()
        log("SDXL ready")

    def generate_object_image(self, crop, prompt, seed):
        from modules.input_router import decide_route
        from modules.input_prep import square_pad
        try:
            route = decide_route(crop).route
        except Exception:
            route = "stylize"
        if route == "framed":
            guide = square_pad(crop, CW)        # コンパクト被写体: 正方パッドで縦横比保持
        else:
            guide = crop.resize((CW, CH))       # stylize/companion: 全体リサイズ
        return self.gen.generate(
            prompt=prompt, guide_image=guide, seed=seed,
            controlnet_conditioning_scale=self.cn_scale, num_inference_steps=self.steps)

    def teardown(self):
        self.gen = None; gc.collect(); torch.cuda.empty_cache()


class SdxlText2ImgBackend(SdxlRoutedBackend):
    """SDXL text2img (線ヒントのみ・prompt駆動 detailed lineart, M16 object route)。

    preset を illustrious_v2_text2img に固定 (config 上書き不可 = ルートの同一性を保つ)。
    """
    name = "sdxl_text2img"
    route_label = "SDXL text2img (prompt-driven, line hint only)"
    PRESET_OVERRIDE = "illustrious_v2_text2img"


class IpMatsumotoBackend:
    """IP-Adapter 松本画風 two-stage (M15)。 全体1枚で生成 (分割しない)。

    実証済みプリセット = gacha_character_autoprompt_C_face_with_neck_20260528_152755:
      stage1=illustrious_v2_inpaint(character), stage2_strength=0.45, ip_scale=0.6,
      stage1_prompt = 被写体 + character テンプレ(5/28版)。 これを焼き込んで再現する。
    """
    name = "ip_matsumoto"
    route_label = "IP-Adapter matsumoto two-stage (gacha 20260528 character preset)"
    multi_object = False
    uses_vlm = True
    STAGE2_STRENGTH = 0.45
    IP_SCALE = 0.6
    # 5/28 gacha の character テンプレ (00_auto_prompt.txt から、 被写体に続く suffix)。
    CHAR_SUFFIX = ("manga style character, dynamic pose, expressive ink lines, "
                   "detailed lineart, single continuous black line on plain white background, "
                   "clean smooth strokes, no shading")

    def __init__(self, args):
        self.args = args
        self.category = getattr(args, "category", "character") or "character"
        self.style_ref = getattr(args, "style_ref", None)
        log(f"{self.name}: category={self.category} style_ref={self.style_ref or '(auto)'} "
            f"(stage2_str={self.STAGE2_STRENGTH} ip={self.IP_SCALE})")

    def build_prompt(self, vision):
        subj = (vision.get("scene") or vision.get("vision") or "person").strip()
        if self.category == "object":
            return subj                          # object は describe をそのまま (companion 相当)
        return f"{subj}, {self.CHAR_SUFFIX}"      # character: 5/28 実証テンプレを付与

    def load(self):
        # two_stage_generate 内で stage2 の SDXL+IP-Adapter を都度ロードする (関数側に委譲)。
        log("IP-Adapter/imagegen load (two-stage, lazy)")

    def generate_object_image(self, crop, prompt, seed):
        from scripts.test_ip_adapter_two_stage import two_stage_generate
        return two_stage_generate(
            crop, category=self.category, style_ref=self.style_ref,
            stage1_prompt=prompt, stage2_strength=self.STAGE2_STRENGTH,
            ip_scale=self.IP_SCALE, seed=seed, resolution=(CW, CH))

    def teardown(self):
        gc.collect(); torch.cuda.empty_cache()


_BACKENDS = {
    "flux_decorate": FluxDecorateBackend,
    "sdxl_routed": SdxlRoutedBackend,
    "sdxl_text2img": SdxlText2ImgBackend,
    "ip_matsumoto": IpMatsumotoBackend,
}


def make_backend(route_id, args):
    cls = _BACKENDS.get(route_id)
    if cls is None:
        log(f"unknown route '{route_id}' — flux_decorate にフォールバック")
        cls = FluxDecorateBackend
    return cls(args)
