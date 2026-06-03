"""FLUX.1-schnell + ControlNet で LoRA(models/flux_lora_winners) 有無を比較生成。
gen_flux_big.py と同じパイプライン。 各被写体について base(LoRA無) と LoRA@強度 を同一 seed で出し、
画風が「勝ちパターン168枚」を学べたか webapp で見比べる。
GPU は学習と排他 → 学習完了(pytorch_lora_weights.safetensors 生成)後に実行すること。
"""
import time, subprocess, gc, sys, traceback
from pathlib import Path
import numpy as np, cv2, torch
from PIL import Image
ROOT=Path("/home/jizaiedev2026/draw_piper"); sys.path.insert(0,str(ROOT))
import os; os.chdir(ROOT)
SIDS=["samp_IMG_4357","samp_IMG_4362","samp_IMG_4363"]   # 顔 / 椅子 / 犬
SEEDS=[0,1]
LORA_DIR="models/flux_lora_winners"
TRIGGER="tklineart"                                       # 学習時 instance_prompt の先頭語
# 条件: (ラベル, LoRA強度 or None=base)
CONDS=[("base", None), ("lora08", 0.8)]
REPO="chutesai/FLUX.1-schnell"; CN="Shakker-Labs/FLUX.1-dev-ControlNet-Union-Pro"
CW,CH,SIZE=704,1472,1024
BASE=ROOT/"sketch_variations/disp_2026-06-02-FLUXLORA"; BR="claude/style-pool-rebalance-20260529"
SIMPLE=("bold simple cartoon line art, thick black outlines, one single large subject "
        "centered and filling the frame, minimal detail, few clean lines, white background, "
        "no fill, no shading, no background objects, no text")
def log(*a): print("[loracmp]",*a,flush=True)
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

assert (ROOT/LORA_DIR/"pytorch_lora_weights.safetensors").exists(), \
    f"LoRA未完成: {LORA_DIR}/pytorch_lora_weights.safetensors が無い (学習完了待ち)"

from modules.vlm import VLM
subj={}; vlm=VLM(verbose=False)
for sid in SIDS:
    inp=Image.open(f"sketch_variations/_inputs/{sid}.png").convert("RGB")
    subj[sid]=vlm.describe_literal(inp) or "subject"
    log("subj",sid,subj[sid])
del vlm; gc.collect(); torch.cuda.empty_cache()

from diffusers import FluxControlNetModel, FluxControlNetPipeline, FluxTransformer2DModel, BitsAndBytesConfig as DBNB
from transformers import T5EncoderModel, BitsAndBytesConfig as TBNB
dnf4=DBNB(load_in_4bit=True,bnb_4bit_quant_type="nf4",bnb_4bit_compute_dtype=torch.bfloat16)
tnf4=TBNB(load_in_4bit=True,bnb_4bit_quant_type="nf4",bnb_4bit_compute_dtype=torch.bfloat16)
log("loading flux...")
tr=FluxTransformer2DModel.from_pretrained(REPO,subfolder="transformer",quantization_config=dnf4,torch_dtype=torch.bfloat16)
te2=T5EncoderModel.from_pretrained(REPO,subfolder="text_encoder_2",quantization_config=tnf4,torch_dtype=torch.bfloat16)
cnet=FluxControlNetModel.from_pretrained(CN,torch_dtype=torch.bfloat16)
pipe=FluxControlNetPipeline.from_pretrained(REPO,transformer=tr,text_encoder_2=te2,controlnet=cnet,torch_dtype=torch.bfloat16)
# LoRA を adapter "winners" として読み込み (cpu_offload 前に注入)。 base 条件は scale 0 で無効化。
pipe.load_lora_weights(LORA_DIR,adapter_name="winners")
pipe.enable_model_cpu_offload(); log("flux+lora ready")
from modules.input_prep import square_pad
from modules.vectorizer import Vectorizer, load_binarize_config
from scripts.gen_routed import _save_candidate
vc=Vectorizer(gen_line_mode="binarize",**load_binarize_config())
import shutil
if BASE.exists(): shutil.rmtree(BASE)
ok=0; vi=0
for sid in SIDS:
    inp=Image.open(f"sketch_variations/_inputs/{sid}.png").convert("RGB")
    ctrl=canny_ctrl(square_pad(inp,SIZE)); s=subj[sid]
    for label,strength in CONDS:
        # base は LoRA 無効(scale 0)、 LoRA 条件は trigger 語を prompt 先頭に付与
        if strength is None:
            pipe.set_adapters(["winners"],[0.0]); pre=""
        else:
            pipe.set_adapters(["winners"],[strength]); pre=f"{TRIGGER}, "
        prompt=f"{pre}a {s}, {SIMPLE}"
        for seed in SEEDS:
            vi+=1
            try:
                img=pipe(prompt=prompt,control_image=ctrl,control_mode=0,controlnet_conditioning_scale=0.5,
                    width=SIZE,height=SIZE,num_inference_steps=4,guidance_scale=0.0,
                    generator=torch.Generator("cpu").manual_seed(seed)).images[0]
            except Exception as e: log("FAIL",sid,label,seed,repr(e)); traceback.print_exc(); continue
            r=vc.vectorize(generated_image=hardboost(img),user_image=None)
            placed=place_fill(r.strokes)
            d=BASE/sid/f"v{vi}_seed{seed}_{label}"
            _save_candidate(d,placed,CW,CH,generated=img,meta={"sid":sid,"route":"flux_lora_compare",
                "variant":label,"lora":(None if strength is None else LORA_DIR),"lora_strength":strength,
                "preset":REPO,"controlnet":CN,"cn":0.5,"seed":seed,"prompt":prompt,"subject":s,
                "placed_at":"fill_board","input_fit":"contain","vectorize":"binarize+hardboost250",
                "min_feature_px":15,"note":"LoRA有無比較 (勝ちパターン168枚 style LoRA)"})
            log(sid,label,seed,len(placed),"strokes"); ok+=1
log("generated",ok)
if ok>0:
    subprocess.run(["./venv/bin/python","-m","scripts.build_selection_webapp"])
    subprocess.run(["git","add",str(BASE),"docs/selection/index.html"])
    subprocess.run(["git","commit","-q","-m","FLUX style LoRA(勝ち168枚) 有無比較バッチ生成"])
    for _ in range(6):
        if subprocess.run(["git","push","origin",BR]).returncode==0: break
        time.sleep(12)
    log("pushed")
print("LORA_COMPARE_DONE",flush=True)
