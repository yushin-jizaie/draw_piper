"""装飾を低CNでリッチに生成 → DIS flow で入力線へワープして「元線に絡みつかせる」
→ 元線を差し引いて装飾のみ抽出 (方式③ warp の装飾応用, 2026-06-04)。
低CNで装飾量を確保しつつ、 ワープで元線に密着させるのが狙い。
各被写体で design(ワープ前) / warp_full(入力へワープ) / warp_deco(元線引いた装飾のみ) を出す。
"""
import time, subprocess, gc, sys, traceback
from pathlib import Path
import numpy as np, cv2, torch
from PIL import Image
ROOT=Path("/home/jizaiedev2026/draw_piper"); sys.path.insert(0,str(ROOT))
import os; os.chdir(ROOT)
SIDS=["new_car","house","B_round_smiley"]
SEED=0; CN_DESIGN=0.30
LORA_DIR="models/flux_lora_winners"; LORA_STR=0.6; TRIGGER="tklineart"
REPO="chutesai/FLUX.1-schnell"; CN="Shakker-Labs/FLUX.1-dev-ControlNet-Union-Pro"
CW,CH,SIZE=704,1472,1024
BASE=ROOT/"sketch_variations/disp_2026-06-04-DECOWARP"; BR="claude/style-pool-rebalance-20260529"
DEFAULT_STYLE="manga style, clean bold black ink lineart on white background"
SUBPX=14.0
def log(*a): print("[decowarp]",*a,flush=True)
def hardboost(img,cut=250):
    a=np.array(img.convert("L")); m=(a<cut).astype(np.uint8)*255; m=cv2.medianBlur(m,3)
    return Image.fromarray(255-m).convert("RGB")
def canny_ctrl(img):
    g=np.array(img.convert("L")); e=cv2.dilate(cv2.Canny(g,80,160),np.ones((2,2),np.uint8))
    return Image.fromarray(cv2.cvtColor(e,cv2.COLOR_GRAY2RGB))
def line_flow(design_img, align_img):
    def prep(im):
        g=np.array(im.convert("L")).astype(np.uint8); g=255-g
        return cv2.GaussianBlur(g,(0,0),3.0)
    dis=cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM); dis.setUseSpatialPropagation(True)
    return dis.calc(prep(design_img),prep(align_img),None)
def warp_strokes(strokes, flow):
    H,W=flow.shape[:2]; out=[]
    for st in strokes:
        ws=[]
        for x,y in st:
            xi=min(max(int(round(x)),0),W-1); yi=min(max(int(round(y)),0),H-1)
            dx,dy=flow[yi,xi]; ws.append((x+float(dx),y+float(dy)))
        out.append(ws)
    return out
def subtract_input(gen_str, inp_str, D=SUBPX):
    cell=D; grid={}
    for st in inp_str:
        for x,y in st: grid[(int(x//cell),int(y//cell))]=1
    def near(x,y):
        cx,cy=int(x//cell),int(y//cell)
        return any((cx+dx,cy+dy) in grid for dx in(-1,0,1) for dy in(-1,0,1))
    return [st for st in gen_str if len(st)>=2 and sum(near(x,y) for x,y in st)/len(st)<0.5]
def place_fill(strokes, margin=0.92, min_feat=12):
    pts=[p for st in strokes for p in st]
    if not pts: return []
    xs=[p[0] for p in pts]; ys=[p[1] for p in pts]
    x0,y0,x1,y1=min(xs),min(ys),max(xs),max(ys); bw,bh=max(x1-x0,1),max(y1-y0,1)
    s=min(CW*margin/bw, CH*margin/bh); ox=(CW-bw*s)/2-x0*s; oy=(CH-bh*s)/2-y0*s
    out=[[(x*s+ox,y*s+oy) for x,y in st] for st in strokes]
    def md(st):
        X=[p[0] for p in st];Y=[p[1] for p in st];return max(max(X)-min(X),max(Y)-min(Y))
    return [st for st in out if len(st)>=2 and md(st)>=min_feat]

from modules.vlm import VLM
info={}; vlm=VLM(verbose=True)
for sid in SIDS:
    inp=Image.open(f"sketch_variations/_inputs/{sid}.png").convert("RGB")
    scene=vlm.describe_scene(inp) or "subject"
    vision=vlm.design_instruction(inp,scene,mode="decorate") or scene
    info[sid]={"scene":scene,"vision":vision}; log(sid,"deco vision:",vision)
del vlm; gc.collect(); torch.cuda.empty_cache()

from diffusers import FluxControlNetModel, FluxControlNetPipeline, FluxTransformer2DModel, BitsAndBytesConfig as DBNB
from transformers import T5EncoderModel, BitsAndBytesConfig as TBNB
dnf4=DBNB(load_in_4bit=True,bnb_4bit_quant_type="nf4",bnb_4bit_compute_dtype=torch.bfloat16)
tnf4=TBNB(load_in_4bit=True,bnb_4bit_quant_type="nf4",bnb_4bit_compute_dtype=torch.bfloat16)
log("loading flux..."); tr=FluxTransformer2DModel.from_pretrained(REPO,subfolder="transformer",quantization_config=dnf4,torch_dtype=torch.bfloat16)
te2=T5EncoderModel.from_pretrained(REPO,subfolder="text_encoder_2",quantization_config=tnf4,torch_dtype=torch.bfloat16)
cnet=FluxControlNetModel.from_pretrained(CN,torch_dtype=torch.bfloat16)
pipe=FluxControlNetPipeline.from_pretrained(REPO,transformer=tr,text_encoder_2=te2,controlnet=cnet,torch_dtype=torch.bfloat16)
pipe.load_lora_weights(LORA_DIR,adapter_name="winners"); pipe.set_adapters(["winners"],[LORA_STR])
pipe.enable_model_cpu_offload(); log("flux+lora ready")
from modules.input_prep import square_pad
from modules.vectorizer import Vectorizer, load_binarize_config
from modules.gen_line_extract import extract_lines
from scripts.gen_routed import _save_candidate, _place_input_aligned
vc=Vectorizer(gen_line_mode="binarize",**load_binarize_config())
import shutil
if BASE.exists(): shutil.rmtree(BASE)

ok=0; vi=0
for sid in SIDS:
    vision=info[sid]["vision"]; prompt=f"{TRIGGER}, {vision} {DEFAULT_STYLE}"
    inp=Image.open(f"sketch_variations/_inputs/{sid}.png").convert("RGB"); _W,_H=inp.size
    sq=square_pad(inp,SIZE); ctrl=canny_ctrl(sq)
    inp_str=vc.vectorize(generated_image=sq,user_image=None).strokes
    try:
        img=pipe(prompt=prompt,control_image=ctrl,control_mode=0,controlnet_conditioning_scale=CN_DESIGN,
            width=SIZE,height=SIZE,num_inference_steps=4,guidance_scale=0.0,
            generator=torch.Generator("cpu").manual_seed(SEED)).images[0]
    except Exception as e: log("FAIL",sid,repr(e)); traceback.print_exc(); continue
    gen_b=extract_lines(img); gen_str=vc.vectorize(generated_image=gen_b,user_image=None).strokes
    try:
        flow=line_flow(gen_b,sq); warped=warp_strokes(gen_str,flow)
        mag=float(np.mean(np.linalg.norm(flow,axis=2)))
    except Exception as e: log("FAIL flow",sid,repr(e)); warped=gen_str; mag=-1.0
    deco=subtract_input(warped,inp_str)
    layers=[("design",place_fill(gen_str)),                              # ワープ前(参考)
            ("warp_full",_place_input_aligned(warped,_W,_H,SIZE,CW,CH)), # 入力へワープ(元線+装飾)
            ("warp_deco",_place_input_aligned(deco,_W,_H,SIZE,CW,CH))]   # 元線引いた装飾のみ
    for label,placed in layers:
        vi+=1; outd=BASE/sid/f"v{vi}_seed{SEED}_{label}"
        _save_candidate(outd,placed,CW,CH,generated=img,meta={"sid":sid,"route":"flux_decorate_warp",
            "variant":label,"layer":label,"cn":CN_DESIGN,"seed":SEED,"flow_mean_px":round(mag,2),
            "prompt":prompt,"vision":vision,"lora":LORA_DIR,"lora_strength":LORA_STR,
            "note":"装飾を低CN生成→DIS flowで入力線へワープ→元線引いて装飾のみ"})
        log(sid,label,len(placed),"strokes"); ok+=1
    log(sid,"flow_mean_px",round(mag,2))
log("generated",ok)
if ok>0:
    subprocess.run(["./venv/bin/python","-m","scripts.build_selection_webapp"])
    subprocess.run(["git","add",str(BASE),"docs/selection/index.html","scripts/gen_decorate_warp.py"])
    subprocess.run(["git","commit","-q","-m","装飾×ワープ: 低CNで装飾生成→DIS flowで入力線へ寄せ→元線引いて装飾のみ(方式③応用)"])
    for _ in range(6):
        if subprocess.run(["git","push","origin",BR]).returncode==0: break
        time.sleep(12)
    log("pushed")
print("DECOWARP_DONE",flush=True)
