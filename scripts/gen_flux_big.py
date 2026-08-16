"""FLUX.1-schnell + ControlNet: マーカー描画向け「大きくシンプルな単一被写体」を全画像生成。
要素を減らし、 被写体をボード一杯に拡大配置。 薄線は hard-boost(cut250) で回収、
binarize で単一線、 2-3mm マーカー未満(15px)の細部は除去。 3 seed で変化。
"""
import time, subprocess, gc, sys, traceback
from pathlib import Path
import numpy as np, cv2, torch
from PIL import Image
ROOT=Path("/home/jizaiedev2026/draw_piper"); sys.path.insert(0,str(ROOT))
import os; os.chdir(ROOT)
SIDS=["samp_IMG_4357","samp_IMG_4358","samp_IMG_4359","samp_IMG_4360",
      "samp_IMG_4361","samp_IMG_4362","samp_IMG_4363"]
REPO="chutesai/FLUX.1-schnell"; CN="Shakker-Labs/FLUX.1-dev-ControlNet-Union-Pro"
CW,CH,SIZE=704,1472,1024
BASE=ROOT/"sketch_variations/disp_2026-06-02-FLUX"; BR="claude/style-pool-rebalance-20260529"
SIMPLE=("bold simple cartoon line art, thick black outlines, one single large subject "
        "centered and filling the frame, minimal detail, few clean lines, white background, "
        "no fill, no shading, no background objects, no text")
FLAVORS=["", "cute friendly", "bold graphic"]   # seed ごとの軽い変化
def log(*a): print("[big]",*a,flush=True)
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
pipe.enable_model_cpu_offload(); log("flux ready")
from modules.input_prep import square_pad
from modules.vectorizer import Vectorizer, load_binarize_config
from scripts.gen_routed import _save_candidate
vc=Vectorizer(gen_line_mode="binarize",**load_binarize_config())
import shutil
if BASE.exists(): shutil.rmtree(BASE)
ok=0
for sid in SIDS:
    inp=Image.open(f"sketch_variations/_inputs/{sid}.png").convert("RGB")
    ctrl=canny_ctrl(square_pad(inp,SIZE)); s=subj[sid]
    for i,seed in enumerate([0,1,2],1):
        fl=FLAVORS[i-1]; prompt=f"a {(fl+' ') if fl else ''}{s}, {SIMPLE}"
        try:
            img=pipe(prompt=prompt,control_image=ctrl,control_mode=0,controlnet_conditioning_scale=0.5,
                width=SIZE,height=SIZE,num_inference_steps=4,guidance_scale=0.0,
                generator=torch.Generator("cpu").manual_seed(seed)).images[0]
        except Exception as e: log("FAIL",sid,seed,repr(e)); traceback.print_exc(); continue
        r=vc.vectorize(generated_image=hardboost(img),user_image=None)
        placed=place_fill(r.strokes)
        d=BASE/sid/f"v{i}_seed{seed}_big"
        _save_candidate(d,placed,CW,CH,generated=img,meta={"sid":sid,"route":"flux_big","variant":"big",
            "preset":REPO,"controlnet":CN,"cn":0.5,"seed":seed,"prompt":prompt,"subject":s,
            "placed_at":"fill_board","input_fit":"contain","vectorize":"binarize+hardboost250",
            "min_feature_px":15,"note":"marker 2-3mm 向け 大きくシンプル"})
        log(sid,seed,len(placed),"strokes"); ok+=1
log("generated",ok)
if ok>0:
    subprocess.run(["./venv/bin/python","-m","scripts.build_selection_webapp"])
    subprocess.run(["git","add",str(BASE),"docs/selection/index.html"])
    subprocess.run(["git","commit","-q","-m","FLUX をマーカー描画向けに刷新: 大きくシンプルな単一被写体+fill-board+hardboost線抽出"])
    for _ in range(6):
        if subprocess.run(["git","push","origin",BR]).returncode==0: break
        time.sleep(12)
    log("pushed")
print("BIG_BATCH_DONE",flush=True)
