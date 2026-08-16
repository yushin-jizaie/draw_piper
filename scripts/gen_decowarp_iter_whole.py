"""warp_deco 方式 × IMG_4368 を1枚(分割なし) × フィードバック 3 周 (2026-06-04 ユーザー)。
各周: 現在の絵を control に decorate 低CN生成 → DIS flow で現在の絵へワープ →
その線画を次周の入力に戻す(蓄積)。 各周の full(絵全体) と deco(元の入力線を引いた装飾) を保存。
"""
import time, subprocess, gc, sys, traceback
from pathlib import Path
import numpy as np, cv2, torch
from PIL import Image
ROOT=Path("/home/jizaiedev2026/draw_piper"); sys.path.insert(0,str(ROOT))
import os; os.chdir(ROOT)
SID_IN="assets/IMG_4368.jpg"; COMBO_SID="new_combo_decoiter"
N_ROUNDS=3; SEED=0; CN_DESIGN=0.30
LORA_DIR="models/flux_lora_winners"; LORA_STR=0.6; TRIGGER="tklineart"
REPO="chutesai/FLUX.1-schnell"; CNREPO="Shakker-Labs/FLUX.1-dev-ControlNet-Union-Pro"
CW,CH,SIZE=704,1472,1024
BASE=ROOT/"sketch_variations/disp_2026-06-04-DECOITERW"; BR="claude/style-pool-rebalance-20260529"
DEFAULT_STYLE="manga style, clean bold black ink lineart on white background"
SUBPX=14.0
def log(*a): print("[diw]",*a,flush=True)
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
def subtract(gen_str, inp_str, D=SUBPX):
    cell=D; grid={}
    for st in inp_str:
        for x,y in st: grid[(int(x//cell),int(y//cell))]=1
    def near(x,y):
        cx,cy=int(x//cell),int(y//cell)
        return any((cx+dx,cy+dy) in grid for dx in(-1,0,1) for dy in(-1,0,1))
    return [st for st in gen_str if len(st)>=2 and sum(near(x,y) for x,y in st)/len(st)<0.5]
def place_fill(strokes, margin=0.92, min_feat=10):
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
vlm=VLM(verbose=True)
inp0=Image.open(SID_IN).convert("RGB")
scene=vlm.describe_scene(inp0) or "subject"
vision=vlm.design_instruction(inp0,scene,mode="decorate") or scene
log("deco vision:",vision)
del vlm; gc.collect(); torch.cuda.empty_cache()

from diffusers import FluxControlNetModel, FluxControlNetPipeline, FluxTransformer2DModel, BitsAndBytesConfig as DBNB
from transformers import T5EncoderModel, BitsAndBytesConfig as TBNB
dnf4=DBNB(load_in_4bit=True,bnb_4bit_quant_type="nf4",bnb_4bit_compute_dtype=torch.bfloat16)
tnf4=TBNB(load_in_4bit=True,bnb_4bit_quant_type="nf4",bnb_4bit_compute_dtype=torch.bfloat16)
log("loading flux..."); tr=FluxTransformer2DModel.from_pretrained(REPO,subfolder="transformer",quantization_config=dnf4,torch_dtype=torch.bfloat16)
te2=T5EncoderModel.from_pretrained(REPO,subfolder="text_encoder_2",quantization_config=tnf4,torch_dtype=torch.bfloat16)
cnet=FluxControlNetModel.from_pretrained(CNREPO,torch_dtype=torch.bfloat16)
pipe=FluxControlNetPipeline.from_pretrained(REPO,transformer=tr,text_encoder_2=te2,controlnet=cnet,torch_dtype=torch.bfloat16)
pipe.load_lora_weights(LORA_DIR,adapter_name="winners"); pipe.set_adapters(["winners"],[LORA_STR])
pipe.enable_model_cpu_offload(); log("flux+lora ready")
from modules.input_prep import square_pad
from modules.vectorizer import Vectorizer, load_binarize_config
from modules.gen_line_extract import extract_lines
from modules.stroke_render import render_strokes_to_image
from scripts.gen_routed import _save_candidate
vc=Vectorizer(gen_line_mode="binarize",**load_binarize_config())
import shutil, json
if BASE.exists(): shutil.rmtree(BASE)
shutil.copy(ROOT/SID_IN, ROOT/f"sketch_variations/_inputs/{COMBO_SID}.png")

prompt=f"{TRIGGER}, {vision} {DEFAULT_STYLE}"
current=square_pad(inp0,SIZE)                                  # 周回で更新される現在の絵 (gen frame)
orig_str=vc.vectorize(generated_image=current,user_image=None).strokes  # 元の入力線 (装飾抽出用, 固定)
ok=0
for r in range(1,N_ROUNDS+1):
    ctrl=canny_ctrl(current)
    try:
        img=pipe(prompt=prompt,control_image=ctrl,control_mode=0,controlnet_conditioning_scale=CN_DESIGN,
            width=SIZE,height=SIZE,num_inference_steps=4,guidance_scale=0.0,
            generator=torch.Generator("cpu").manual_seed(SEED)).images[0]
    except Exception as e: log("FAIL",r,repr(e)); traceback.print_exc(); break
    gen_b=extract_lines(img); gen_str=vc.vectorize(generated_image=gen_b,user_image=None).strokes
    try:
        flow=line_flow(gen_b,current); warped=warp_strokes(gen_str,flow); mag=float(np.mean(np.linalg.norm(flow,axis=2)))
    except Exception as e: log("FAIL flow",r,repr(e)); warped=gen_str; mag=-1.0
    deco=subtract(warped,orig_str)                            # 元の入力線を引いた累積装飾
    # 次周の入力 = この周の絵 (warped を描画)
    warped_img=render_strokes_to_image(warped,width=SIZE,height=SIZE,line_width=2)
    for label,strokes in [("full",warped),("deco",deco)]:
        outd=BASE/COMBO_SID/f"v{(r-1)*2 + (1 if label=='full' else 2)}_seed{SEED}_round{r}_{label}"
        _save_candidate(outd,place_fill(strokes),CW,CH,generated=img,meta={"sid":COMBO_SID,
            "route":"flux_decowarp_iter","variant":f"round{r}_{label}","round":r,"layer":label,
            "cn":CN_DESIGN,"seed":SEED,"flow_mean_px":round(mag,2),"prompt":prompt,"vision":vision,
            "note":f"warp_deco方式×1枚×フィードバック {r}/{N_ROUNDS}周目。 full=絵全体/deco=元入力線を引いた装飾"})
        log(f"round{r}",label,len(place_fill(strokes)),"strokes"); ok+=1
    log(f"round{r} flow_mean_px",round(mag,2))
    current=warped_img.convert("RGB")                          # フィードバック
log("generated",ok)
if ok>0:
    subprocess.run(["./venv/bin/python","-m","scripts.build_selection_webapp"])
    subprocess.run(["git","add",str(BASE),f"sketch_variations/_inputs/{COMBO_SID}.png",
                    "scripts/build_selection_webapp.py","scripts/gen_decowarp_iter_whole.py","docs/selection/index.html"])
    subprocess.run(["git","commit","-q","-m","warp_deco×1枚×フィードバック3周: 装飾を蓄積生成 (DECOITERW)"])
    for _ in range(6):
        if subprocess.run(["git","push","origin",BR]).returncode==0: break
        time.sleep(12)
    log("pushed")
print("DECOITERW_DONE",flush=True)
