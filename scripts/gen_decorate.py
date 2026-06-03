"""元の線を骨格として活かし、 その線に絡む装飾を増やす生成 (2026-06-04 方向)。
ControlNet 中〜高CN で元線を追従しつつ design_instruction(mode=decorate) で線に絡む装飾を足す。
各被写体で full(元線追従+装飾) と deco(元線を差し引いた装飾のみ=ロボットが描く分) を出す。
CN 0.55/0.75 を比較。 FLUX + winners LoRA + OpenCV線抽出。
"""
import time, subprocess, gc, sys, traceback
from pathlib import Path
import numpy as np, cv2, torch
from PIL import Image
ROOT=Path("/home/jizaiedev2026/draw_piper"); sys.path.insert(0,str(ROOT))
import os; os.chdir(ROOT)
SIDS=["B_round_smiley","new_car","house"]
SEED=0; CNS=[0.55,0.75]
LORA_DIR="models/flux_lora_winners"; LORA_STR=0.6; TRIGGER="tklineart"
REPO="chutesai/FLUX.1-schnell"; CN="Shakker-Labs/FLUX.1-dev-ControlNet-Union-Pro"
CW,CH,SIZE=704,1472,1024
BASE=ROOT/"sketch_variations/disp_2026-06-04-DECORATE"; BR="claude/style-pool-rebalance-20260529"
DEFAULT_STYLE="manga style, clean bold black ink lineart on white background"
SUBPX=14.0   # 元線とみなす近接距離(px, gen 1024 frame)
def log(*a): print("[deco]",*a,flush=True)
def canny_ctrl(img):
    g=np.array(img.convert("L")); e=cv2.dilate(cv2.Canny(g,80,160),np.ones((2,2),np.uint8))
    return Image.fromarray(cv2.cvtColor(e,cv2.COLOR_GRAY2RGB))
def fill_tf(strokes, margin=0.92):
    pts=[p for st in strokes for p in st]
    if not pts: return None
    xs=[p[0] for p in pts]; ys=[p[1] for p in pts]
    x0,y0,x1,y1=min(xs),min(ys),max(xs),max(ys); bw,bh=max(x1-x0,1),max(y1-y0,1)
    s=min(CW*margin/bw, CH*margin/bh); return (s,(CW-bw*s)/2-x0*s,(CH-bh*s)/2-y0*s)
def apply_tf(strokes, tf, min_feat=12):
    if tf is None: return []
    s,ox,oy=tf; out=[[(x*s+ox,y*s+oy) for x,y in st] for st in strokes]
    def md(st):
        X=[p[0] for p in st];Y=[p[1] for p in st];return max(max(X)-min(X),max(Y)-min(Y))
    return [st for st in out if len(st)>=2 and md(st)>=min_feat]
def subtract_input(gen_str, inp_str, D=SUBPX):
    """gen_str から inp_str(元線)に重なるストロークを除き、 装飾だけ残す。"""
    cell=D; grid={}
    for st in inp_str:
        for x,y in st: grid.setdefault((int(x//cell),int(y//cell)),1)
    def near(x,y):
        cx,cy=int(x//cell),int(y//cell)
        for dx in(-1,0,1):
            for dy in(-1,0,1):
                if (cx+dx,cy+dy) in grid: return True
        return False
    deco=[]
    for st in gen_str:
        if len(st)<2: continue
        hit=sum(1 for x,y in st if near(x,y))
        if hit/len(st) < 0.5: deco.append(st)   # 元線重なりが半分未満=装飾
    return deco

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
log("loading flux...")
tr=FluxTransformer2DModel.from_pretrained(REPO,subfolder="transformer",quantization_config=dnf4,torch_dtype=torch.bfloat16)
te2=T5EncoderModel.from_pretrained(REPO,subfolder="text_encoder_2",quantization_config=tnf4,torch_dtype=torch.bfloat16)
cnet=FluxControlNetModel.from_pretrained(CN,torch_dtype=torch.bfloat16)
pipe=FluxControlNetPipeline.from_pretrained(REPO,transformer=tr,text_encoder_2=te2,controlnet=cnet,torch_dtype=torch.bfloat16)
pipe.load_lora_weights(LORA_DIR,adapter_name="winners"); pipe.set_adapters(["winners"],[LORA_STR])
pipe.enable_model_cpu_offload(); log("flux+lora ready")
from modules.input_prep import square_pad
from modules.vectorizer import Vectorizer, load_binarize_config
from modules.gen_line_extract import extract_lines
from scripts.gen_routed import _save_candidate
vc=Vectorizer(gen_line_mode="binarize",**load_binarize_config())
import shutil
if BASE.exists(): shutil.rmtree(BASE)

ok=0; vi=0
for sid in SIDS:
    vision=info[sid]["vision"]; prompt=f"{TRIGGER}, {vision} {DEFAULT_STYLE}"
    inp=Image.open(f"sketch_variations/_inputs/{sid}.png").convert("RGB")
    sq=square_pad(inp,SIZE); ctrl=canny_ctrl(sq)
    inp_str=vc.vectorize(generated_image=sq,user_image=None).strokes   # 元線 (gen frame)
    for cn in CNS:
        try:
            img=pipe(prompt=prompt,control_image=ctrl,control_mode=0,controlnet_conditioning_scale=cn,
                width=SIZE,height=SIZE,num_inference_steps=4,guidance_scale=0.0,
                generator=torch.Generator("cpu").manual_seed(SEED)).images[0]
        except Exception as e: log("FAIL",sid,cn,repr(e)); traceback.print_exc(); continue
        gen_str=vc.vectorize(generated_image=extract_lines(img),user_image=None).strokes
        tf=fill_tf(gen_str)
        deco_str=subtract_input(gen_str,inp_str)
        tag=f"cn{int(round(cn*100)):02d}"
        for label,strokes in [("full",gen_str),("deco",deco_str)]:
            placed=apply_tf(strokes,tf); vi+=1
            outd=BASE/sid/f"v{vi}_seed{SEED}_{tag}_{label}"
            _save_candidate(outd,placed,CW,CH,generated=img,meta={"sid":sid,
                "route":"flux_decorate","variant":f"{tag}_{label}","cn":cn,"seed":SEED,
                "layer":label,"prompt":prompt,"vision":vision,
                "lora":LORA_DIR,"lora_strength":LORA_STR,"line_extract":"opencv_bgremove_adaptive",
                "note":"元線を活かし線に絡む装飾。full=元線+装飾 / deco=元線を引いた装飾のみ(ロボット描画分)"})
            log(sid,tag,label,len(placed),"strokes"); ok+=1
log("generated",ok)
if ok>0:
    subprocess.run(["./venv/bin/python","-m","scripts.build_selection_webapp"])
    subprocess.run(["git","add",str(BASE),"docs/selection/index.html","scripts/gen_decorate.py","modules/vlm.py"])
    subprocess.run(["git","commit","-q","-m","装飾モード: 元線を活かし線に絡む装飾を生成(decorate, CN0.55/0.75, full/deco層)"])
    for _ in range(6):
        if subprocess.run(["git","push","origin",BR]).returncode==0: break
        time.sleep(12)
    log("pushed")
print("DECORATE_DONE",flush=True)
