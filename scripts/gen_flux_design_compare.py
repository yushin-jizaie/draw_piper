"""FLUX.1-schnell + ControlNet + style LoRA で「プロンプト方式」を比較生成。
ねらい: 旧 describe_literal(主題1-2語) は SIMPLE スタイル語に埋もれて先祖返り
→ 入力との差が出ない。 新方式は VLM が入力(途中の下書き)から「未来の完成形」を
積極補完して記述し、 SIMPLE はあくまでデフォルトのマーカー描画スタイルとして付与。
ControlNet=0.4 (緩めて完成形が入力から離れられる)、 LoRA@0.8、 2 prompt 条件を比較。
GPU は学習と排他 → 学習完了後に実行。
"""
import time, subprocess, gc, sys, traceback, json
from pathlib import Path
import numpy as np, cv2, torch
from PIL import Image
ROOT=Path("/home/jizaiedev2026/draw_piper"); sys.path.insert(0,str(ROOT))
import os; os.chdir(ROOT)
SIDS=["samp_IMG_4357","samp_IMG_4362","samp_IMG_4363"]   # 顔 / 椅子 / 犬
SEEDS=[0,1]
LORA_DIR="models/flux_lora_winners"; LORA_STR=0.8
TRIGGER="tklineart"
CN_SCALE=0.4
REPO="chutesai/FLUX.1-schnell"; CN="Shakker-Labs/FLUX.1-dev-ControlNet-Union-Pro"
CW,CH,SIZE=704,1472,1024
BASE=ROOT/"sketch_variations/disp_2026-06-02-FLUXDESIGN"; BR="claude/style-pool-rebalance-20260529"
# 画像生成 AI 側の「デフォルトスタイル」(マーカー描画向け)。 デザイン内容は VLM が出す。
SIMPLE=("bold simple cartoon line art, thick black outlines, one single large subject "
        "centered and filling the frame, minimal detail, few clean lines, white background, "
        "no fill, no shading, no background objects, no text")
def log(*a): print("[design]",*a,flush=True)
def hardboost(img, cut=250):
    a=np.array(img.convert("L")); m=(a<cut).astype(np.uint8)*255
    m=cv2.medianBlur(m,3)
    return Image.fromarray(255-m).convert("RGB")
def canny_ctrl(img):
    g=np.array(img.convert("L")); e=cv2.dilate(cv2.Canny(g,80,160),np.ones((2,2),np.uint8))
    return Image.fromarray(cv2.cvtColor(e,cv2.COLOR_GRAY2RGB))
def place_fill(strokes, margin=0.92, min_feat=15):
    pts=[p for st in strokes for p in st]
    if not pts: return []
    xs=[p[0] for p in pts]; ys=[p[1] for p in pts]
    x0,y0,x1,y1=min(xs),min(ys),max(xs),max(ys); bw,bh=max(x1-x0,1),max(y1-y0,1)
    s=min(CW*margin/bw, CH*margin/bh)
    ox=(CW-bw*s)/2 - x0*s; oy=(CH-bh*s)/2 - y0*s
    out=[[(x*s+ox,y*s+oy) for x,y in st] for st in strokes]
    def md(st):
        X=[p[0] for p in st];Y=[p[1] for p in st];return max(max(X)-min(X),max(Y)-min(Y))
    return [st for st in out if len(st)>=2 and md(st)>=min_feat]

assert (ROOT/LORA_DIR/"pytorch_lora_weights.safetensors").exists(), "LoRA未完成"

# --- 1) VLM: 各入力から literal(旧) と scene→completion vision(新) を取得 -----
from modules.vlm import VLM
info={}; vlm=VLM(verbose=True)
for sid in SIDS:
    inp=Image.open(f"sketch_variations/_inputs/{sid}.png").convert("RGB")
    lit=vlm.describe_literal(inp) or "subject"
    scene=vlm.describe_scene(inp) or lit
    vision=vlm.design_instruction(inp, subject=scene, mode="complete") or scene
    info[sid]={"lit":lit,"scene":scene,"vision":vision}
    log(sid,"| lit:",lit,"| scene:",scene)
    log(sid,"| vision:",vision)
del vlm; gc.collect(); torch.cuda.empty_cache()

# --- 2) FLUX + ControlNet + LoRA ---------------------------------------------
from diffusers import FluxControlNetModel, FluxControlNetPipeline, FluxTransformer2DModel, BitsAndBytesConfig as DBNB
from transformers import T5EncoderModel, BitsAndBytesConfig as TBNB
dnf4=DBNB(load_in_4bit=True,bnb_4bit_quant_type="nf4",bnb_4bit_compute_dtype=torch.bfloat16)
tnf4=TBNB(load_in_4bit=True,bnb_4bit_quant_type="nf4",bnb_4bit_compute_dtype=torch.bfloat16)
log("loading flux...")
tr=FluxTransformer2DModel.from_pretrained(REPO,subfolder="transformer",quantization_config=dnf4,torch_dtype=torch.bfloat16)
te2=T5EncoderModel.from_pretrained(REPO,subfolder="text_encoder_2",quantization_config=tnf4,torch_dtype=torch.bfloat16)
cnet=FluxControlNetModel.from_pretrained(CN,torch_dtype=torch.bfloat16)
pipe=FluxControlNetPipeline.from_pretrained(REPO,transformer=tr,text_encoder_2=te2,controlnet=cnet,torch_dtype=torch.bfloat16)
pipe.load_lora_weights(LORA_DIR,adapter_name="winners")
pipe.set_adapters(["winners"],[LORA_STR])   # 全条件 LoRA 同一 → 差は prompt のみ
pipe.enable_model_cpu_offload(); log("flux+lora ready")
from modules.input_prep import square_pad
from modules.vectorizer import Vectorizer, load_binarize_config
from scripts.gen_routed import _save_candidate
vc=Vectorizer(gen_line_mode="binarize",**load_binarize_config())
import shutil
if BASE.exists(): shutil.rmtree(BASE)

def build_prompt(kind, d):
    if kind=="oldlit":   # 旧: 主題1-2語 + SIMPLE (先祖返り)
        return f"{TRIGGER}, a {d['lit']}, {SIMPLE}"
    # 新: VLM 完成形ビジョン + SIMPLE(デフォルトスタイル)
    return f"{TRIGGER}, {d['vision']} {SIMPLE}"
CONDS=["oldlit","design"]

ok=0; vi=0
for sid in SIDS:
    d=info[sid]
    inp=Image.open(f"sketch_variations/_inputs/{sid}.png").convert("RGB")
    ctrl=canny_ctrl(square_pad(inp,SIZE))
    for kind in CONDS:
        prompt=build_prompt(kind,d)
        for seed in SEEDS:
            vi+=1
            try:
                img=pipe(prompt=prompt,control_image=ctrl,control_mode=0,
                    controlnet_conditioning_scale=CN_SCALE,
                    width=SIZE,height=SIZE,num_inference_steps=4,guidance_scale=0.0,
                    generator=torch.Generator("cpu").manual_seed(seed)).images[0]
            except Exception as e: log("FAIL",sid,kind,seed,repr(e)); traceback.print_exc(); continue
            r=vc.vectorize(generated_image=hardboost(img),user_image=None)
            placed=place_fill(r.strokes)
            outd=BASE/sid/f"v{vi}_seed{seed}_{kind}"
            _save_candidate(outd,placed,CW,CH,generated=img,meta={"sid":sid,
                "route":"flux_design_compare","variant":kind,"prompt_mode":kind,
                "lora":LORA_DIR,"lora_strength":LORA_STR,"cn":CN_SCALE,
                "preset":REPO,"controlnet":CN,"seed":seed,"prompt":prompt,
                "literal":d["lit"],"scene":d["scene"],"vision":d["vision"],
                "placed_at":"fill_board","input_fit":"contain",
                "vectorize":"binarize+hardboost250","min_feature_px":15,
                "note":"prompt方式比較 oldlit(主題1-2語) vs design(VLM完成形補完) @CN0.4 LoRA0.8"})
            log(sid,kind,seed,len(placed),"strokes"); ok+=1
log("generated",ok)
# VLM テキストは meta にも保存。 サマリも書き出して目視できるように。
(BASE/"_vlm_visions.json").write_text(json.dumps(info,ensure_ascii=False,indent=2))
if ok>0:
    subprocess.run(["./venv/bin/python","-m","scripts.build_selection_webapp"])
    subprocess.run(["git","add",str(BASE),"docs/selection/index.html"])
    subprocess.run(["git","commit","-q","-m","FLUX prompt方式比較: VLM完成形補完 vs 旧主題語 @CN0.4 LoRA0.8"])
    for _ in range(6):
        if subprocess.run(["git","push","origin",BR]).returncode==0: break
        time.sleep(12)
    log("pushed")
print("DESIGN_COMPARE_DONE",flush=True)
