"""元線verbatim固定 + 装飾だけ派手に累積 + フィードバック時に「元線+前回までの装飾」を
OpenCV で消してから VLM に入れる版 (2026-06-04 ユーザー)。
各周: 現在の絵を control に decorate 生成 → 既存(元線+累積)を引いて新規装飾抽出 → 累積。
次周の VLM 入力 = 生成画像から元線+累積を OpenCV で白く消した画像(=新規装飾だけ)。
→ VLM は新しい装飾だけを見て発展させる(主題に再アンカーされず派手化)。
GPU 制約: FLUX(cpu_offload)常駐、 VLM は各周 load→run→unload で swap。
"""
import time, subprocess, gc, sys, traceback
from pathlib import Path
import numpy as np, cv2, torch
from PIL import Image
ROOT=Path("/home/jizaiedev2026/draw_piper"); sys.path.insert(0,str(ROOT))
import os; os.chdir(ROOT)
SID_IN="assets/IMG_4368.jpg"; COMBO_SID="new_combo_decoaccum2"
N_ROUNDS=4; SEED=0; CN=0.55
LORA_DIR="models/flux_lora_winners"; LORA_STR=0.6; TRIGGER="tklineart"
REPO="chutesai/FLUX.1-schnell"; CNREPO="Shakker-Labs/FLUX.1-dev-ControlNet-Union-Pro"
CW,CH,SIZE=704,1472,1024
BASE=ROOT/"sketch_variations/disp_2026-06-04-DECOACCUM2"; BR="claude/style-pool-rebalance-20260529"
DEFAULT_STYLE="manga style, clean bold black ink lineart on white background"
SUBPX=14.0
def log(*a): print("[acc2]",*a,flush=True)
def canny_ctrl(img):
    g=np.array(img.convert("L")); e=cv2.dilate(cv2.Canny(g,80,160),np.ones((2,2),np.uint8))
    return Image.fromarray(cv2.cvtColor(e,cv2.COLOR_GRAY2RGB))
def subtract(gen_str, existing, D=SUBPX):
    cell=D; grid={}
    for st in existing:
        for x,y in st: grid[(int(x//cell),int(y//cell))]=1
    def near(x,y):
        cx,cy=int(x//cell),int(y//cell)
        return any((cx+dx,cy+dy) in grid for dx in(-1,0,1) for dy in(-1,0,1))
    return [st for st in gen_str if len(st)>=2 and sum(near(x,y) for x,y in st)/len(st)<0.5]
def erase_img(img, strokes, width=9):
    """img(線画)から strokes を白で塗って消す (OpenCV)。 元線+既存装飾を消して新規装飾だけ残す。"""
    a=np.array(img.convert("RGB"))
    for st in strokes:
        if len(st)<2: continue
        cv2.polylines(a,[np.array(st,np.int32)],False,(255,255,255),width)
    return Image.fromarray(a)

from modules.input_prep import square_pad
from modules.vectorizer import Vectorizer, load_binarize_config
from modules.gen_line_extract import extract_lines
from modules.stroke_render import render_strokes_to_image
from scripts.gen_routed import _save_candidate, _place_input_aligned
from modules.vlm import VLM
vc=Vectorizer(gen_line_mode="binarize",**load_binarize_config())
inp0=Image.open(SID_IN).convert("RGB"); _W,_H=inp0.size
sq=square_pad(inp0,SIZE)

def vlm_vision(img):
    """VLM を load→describe_scene+design_instruction(decorate)→unload して vision を返す。"""
    v=VLM(verbose=False); v.load()
    try:
        scene=v.describe_scene(img) or "subject"
        vision=v.design_instruction(img,scene,mode="decorate") or scene
    finally:
        v.unload(); del v; gc.collect(); torch.cuda.empty_cache()
    return vision

# FLUX をロード (cpu_offload → 呼び出し時のみ GPU、 間は VLM 用に空く)
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
import shutil
if BASE.exists(): shutil.rmtree(BASE)
shutil.copy(ROOT/SID_IN, ROOT/f"sketch_variations/_inputs/{COMBO_SID}.png")

base_str=vc.vectorize(generated_image=sq,user_image=None).strokes
deco_accum=[]
vlm_img=sq                                          # round1: 元画像。 以降: 新規装飾だけの画像
def place(strokes): return _place_input_aligned(strokes,_W,_H,SIZE,CW,CH)
ok=0
for r in range(1,N_ROUNDS+1):
    log(f"round{r}: VLM on {'original' if r==1 else 'new-deco-only (元線+前回装飾をOpenCVで消去済)'}")
    vision=vlm_vision(vlm_img)
    prompt=f"{TRIGGER}, {vision} lavish flamboyant abundant ornate decoration everywhere, {DEFAULT_STYLE}"
    log(f"round{r} vision:",vision[:90])
    canvas=render_strokes_to_image(base_str+deco_accum,width=SIZE,height=SIZE,line_width=2)
    ctrl=canny_ctrl(canvas)
    try:
        img=pipe(prompt=prompt,control_image=ctrl,control_mode=0,controlnet_conditioning_scale=CN,
            width=SIZE,height=SIZE,num_inference_steps=4,guidance_scale=0.0,
            generator=torch.Generator("cpu").manual_seed(SEED+r)).images[0]
    except Exception as e: log("FAIL",r,repr(e)); traceback.print_exc(); break
    gen_lines=extract_lines(img); gen_str=vc.vectorize(generated_image=gen_lines,user_image=None).strokes
    existing=base_str+deco_accum
    new_deco=subtract(gen_str,existing)
    vlm_img=erase_img(gen_lines, existing)          # 元線+前回までの装飾を OpenCV で消す→新規装飾だけ
    deco_accum=deco_accum+new_deco
    log(f"round{r}: +{len(new_deco)} new deco, accum {len(deco_accum)}")
    for label,strokes in [("full",base_str+deco_accum),("deco",deco_accum)]:
        outd=BASE/COMBO_SID/f"v{(r-1)*2 + (1 if label=='full' else 2)}_seed{SEED}_round{r}_{label}"
        _save_candidate(outd,place(strokes),CW,CH,generated=img,meta={"sid":COMBO_SID,
            "route":"flux_decorate_accum2","variant":f"round{r}_{label}","round":r,"layer":label,
            "cn":CN,"seed":SEED,"new_deco":len(new_deco),"accum_deco":len(deco_accum),"vision":vision,
            "note":f"元線verbatim+装飾累積+VLMにはOpenCVで元線/前回を消した新規装飾だけ入力 {r}/{N_ROUNDS}周"})
        log(f"round{r}",label,len(place(strokes)),"strokes"); ok+=1
log("generated",ok)
if ok>0:
    subprocess.run(["./venv/bin/python","-m","scripts.build_selection_webapp"])
    subprocess.run(["git","add",str(BASE),f"sketch_variations/_inputs/{COMBO_SID}.png",
                    "scripts/build_selection_webapp.py","scripts/gen_decorate_accum2.py","docs/selection/index.html"])
    subprocess.run(["git","commit","-q","-m","装飾累積+VLMにOpenCVで元線/前回を消した新規装飾だけ入力 4周 (DECOACCUM2)"])
    for _ in range(6):
        if subprocess.run(["git","push","origin",BR]).returncode==0: break
        time.sleep(12)
    log("pushed")
print("DECOACCUM2_DONE",flush=True)
