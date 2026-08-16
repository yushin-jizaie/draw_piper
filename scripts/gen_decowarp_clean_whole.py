"""DECOWARP クリーン路線(良かった house warp_full と同方式)を IMG_4368 1枚(分割なし)で。
CN0.3 + decorate + DIS flow warp の単発。 クラックル/点描を抑えるクリーン強調プロンプト。
複数 seed。 warp_full(元線+装飾) と warp_deco(元線引いた装飾のみ) を出す。
"""
import time, subprocess, gc, sys, traceback
from pathlib import Path
import numpy as np, cv2, torch
from PIL import Image
ROOT=Path("/home/jizaiedev2026/draw_piper"); sys.path.insert(0,str(ROOT))
import os; os.chdir(ROOT)
SID_IN="assets/IMG_4368.jpg"; COMBO_SID="new_combo_decowclean"
SEEDS=[0,1,2]; CN_DESIGN=0.30
LORA_DIR="models/flux_lora_winners"; LORA_STR=0.6; TRIGGER="tklineart"
REPO="chutesai/FLUX.1-schnell"; CNREPO="Shakker-Labs/FLUX.1-dev-ControlNet-Union-Pro"
CW,CH,SIZE=704,1472,1024
BASE=ROOT/"sketch_variations/disp_2026-06-04-DECOWARPCLEAN"; BR="claude/style-pool-rebalance-20260529"
# クリーン強調: クラックル/点描/ハッチを抑え、 滑らかな細い線の軽い装飾に。
CLEAN=("clean smooth flowing thin line decoration, elegant sparse ornament, simple curved "
       "vines, no stippling, no crosshatch, no hatching, no dotted texture, no scribble, no fill")
DEFAULT_STYLE="manga style, clean bold black ink lineart on white background"
SUBPX=14.0
def log(*a): print("[dwc]",*a,flush=True)
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

from modules.vlm import VLM
vlm=VLM(verbose=True)
inp0=Image.open(SID_IN).convert("RGB"); _W,_H=inp0.size
scene=vlm.describe_scene(inp0) or "subject"
vision=vlm.design_instruction(inp0,scene,mode="decorate") or scene
log("deco vision:",vision)
del vlm; gc.collect(); torch.cuda.empty_cache()
prompt=f"{TRIGGER}, {vision} {CLEAN}, {DEFAULT_STYLE}"

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
from scripts.gen_routed import _save_candidate, _place_input_aligned
vc=Vectorizer(gen_line_mode="binarize",**load_binarize_config())
import shutil
if BASE.exists(): shutil.rmtree(BASE)
shutil.copy(ROOT/SID_IN, ROOT/f"sketch_variations/_inputs/{COMBO_SID}.png")
sq=square_pad(inp0,SIZE); ctrl=canny_ctrl(sq)
inp_str=vc.vectorize(generated_image=sq,user_image=None).strokes
ok=0; vi=0
for seed in SEEDS:
    try:
        img=pipe(prompt=prompt,control_image=ctrl,control_mode=0,controlnet_conditioning_scale=CN_DESIGN,
            width=SIZE,height=SIZE,num_inference_steps=4,guidance_scale=0.0,
            generator=torch.Generator("cpu").manual_seed(seed)).images[0]
    except Exception as e: log("FAIL",seed,repr(e)); traceback.print_exc(); continue
    gen_b=extract_lines(img); gen_str=vc.vectorize(generated_image=gen_b,user_image=None).strokes
    try:
        flow=line_flow(gen_b,sq); warped=warp_strokes(gen_str,flow); mag=float(np.mean(np.linalg.norm(flow,axis=2)))
    except Exception as e: log("FAIL flow",seed,repr(e)); warped=gen_str; mag=-1.0
    deco=subtract(warped,inp_str)
    for label,strokes in [("warp_full",warped),("warp_deco",deco)]:
        vi+=1; outd=BASE/COMBO_SID/f"v{vi}_seed{seed}_{label}"
        _save_candidate(outd,_place_input_aligned(strokes,_W,_H,SIZE,CW,CH),CW,CH,generated=img,meta={"sid":COMBO_SID,
            "route":"flux_decowarp_clean","variant":f"{label}_s{seed}","layer":label,"cn":CN_DESIGN,"seed":seed,
            "flow_mean_px":round(mag,2),"prompt":prompt,"vision":vision,
            "note":"DECOWARPクリーン路線(CN0.3+decorate+warp 単発, クラックル抑制)を1枚で"})
        log(seed,label,len(strokes),"strokes"); ok+=1
    log(seed,"flow_mean_px",round(mag,2))
log("generated",ok)
if ok>0:
    subprocess.run(["./venv/bin/python","-m","scripts.build_selection_webapp"])
    subprocess.run(["git","add",str(BASE),f"sketch_variations/_inputs/{COMBO_SID}.png",
                    "scripts/build_selection_webapp.py","scripts/gen_decowarp_clean_whole.py","docs/selection/index.html"])
    subprocess.run(["git","commit","-q","-m","DECOWARPクリーン路線を1枚(IMG_4368)で再生成(クラックル抑制, 3seed)"])
    for _ in range(6):
        if subprocess.run(["git","push","origin",BR]).returncode==0: break
        time.sleep(12)
    log("pushed")
print("DECOWCLEAN_DONE",flush=True)
