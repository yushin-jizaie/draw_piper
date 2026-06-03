"""元の絵は描き直さず、 装飾だけを書き足して累積 (2026-06-04 ユーザー)。
元の入力線を verbatim 固定し、 各周: 現在の絵(元線+累積装飾)を control に decorate 生成
→ 既存分(元線+累積装飾)を引いて「新しい装飾だけ」を抽出 → 累積に足す。
元線は一切再生成しない。 出力 full=元線+累積装飾 / deco=累積装飾のみ(ロボット描画分)。
"""
import time, subprocess, gc, sys, traceback
from pathlib import Path
import numpy as np, cv2, torch
from PIL import Image
ROOT=Path("/home/jizaiedev2026/draw_piper"); sys.path.insert(0,str(ROOT))
import os; os.chdir(ROOT)
SID_IN="assets/IMG_4368.jpg"; COMBO_SID="new_combo_decoaccum"
N_ROUNDS=4; SEED=0; CN=0.55   # 派手装飾: 新規余地を増やすため少し下げる
LORA_DIR="models/flux_lora_winners"; LORA_STR=0.6; TRIGGER="tklineart"
REPO="chutesai/FLUX.1-schnell"; CNREPO="Shakker-Labs/FLUX.1-dev-ControlNet-Union-Pro"
CW,CH,SIZE=704,1472,1024
BASE=ROOT/"sketch_variations/disp_2026-06-04-DECOACCUM"; BR="claude/style-pool-rebalance-20260529"
DEFAULT_STYLE="manga style, clean bold black ink lineart on white background"
SUBPX=14.0
def log(*a): print("[acc]",*a,flush=True)
def canny_ctrl(img):
    g=np.array(img.convert("L")); e=cv2.dilate(cv2.Canny(g,80,160),np.ones((2,2),np.uint8))
    return Image.fromarray(cv2.cvtColor(e,cv2.COLOR_GRAY2RGB))
def subtract(gen_str, existing, D=SUBPX):
    """gen_str から existing(元線+累積装飾)に重なるストロークを除き、 新規装飾だけ残す。"""
    cell=D; grid={}
    for st in existing:
        for x,y in st: grid[(int(x//cell),int(y//cell))]=1
    def near(x,y):
        cx,cy=int(x//cell),int(y//cell)
        return any((cx+dx,cy+dy) in grid for dx in(-1,0,1) for dy in(-1,0,1))
    return [st for st in gen_str if len(st)>=2 and sum(near(x,y) for x,y in st)/len(st)<0.5]

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
from scripts.gen_routed import _save_candidate, _place_input_aligned
vc=Vectorizer(gen_line_mode="binarize",**load_binarize_config())
import shutil
if BASE.exists(): shutil.rmtree(BASE)
shutil.copy(ROOT/SID_IN, ROOT/f"sketch_variations/_inputs/{COMBO_SID}.png")

# 派手強調: lavish/flamboyant/abundant を足して装飾を大量・華やかに。
prompt=f"{TRIGGER}, {vision} lavish flamboyant abundant ornate decoration everywhere, {DEFAULT_STYLE}"
_W,_H=inp0.size
sq=square_pad(inp0,SIZE)
base_str=vc.vectorize(generated_image=sq,user_image=None).strokes   # 元線 (verbatim, 固定)
deco_accum=[]                                                        # 累積装飾 (gen frame)
def place(strokes): return _place_input_aligned(strokes,_W,_H,SIZE,CW,CH)
ok=0
for r in range(1,N_ROUNDS+1):
    canvas=render_strokes_to_image(base_str+deco_accum,width=SIZE,height=SIZE,line_width=2)  # 現在の絵
    ctrl=canny_ctrl(canvas)
    try:
        img=pipe(prompt=prompt,control_image=ctrl,control_mode=0,controlnet_conditioning_scale=CN,
            width=SIZE,height=SIZE,num_inference_steps=4,guidance_scale=0.0,
            generator=torch.Generator("cpu").manual_seed(SEED+r)).images[0]   # 周ごとに seed 変え新規装飾を促す
    except Exception as e: log("FAIL",r,repr(e)); traceback.print_exc(); break
    gen_str=vc.vectorize(generated_image=extract_lines(img),user_image=None).strokes
    new_deco=subtract(gen_str, base_str+deco_accum)     # 既存(元線+累積)を引いた新規装飾だけ
    deco_accum=deco_accum+new_deco                      # 累積 (元線は触らない)
    log(f"round{r}: +{len(new_deco)} new deco, accum {len(deco_accum)}")
    for label,strokes in [("full",base_str+deco_accum),("deco",deco_accum)]:
        outd=BASE/COMBO_SID/f"v{(r-1)*2 + (1 if label=='full' else 2)}_seed{SEED}_round{r}_{label}"
        _save_candidate(outd,place(strokes),CW,CH,generated=img,meta={"sid":COMBO_SID,
            "route":"flux_decorate_accum","variant":f"round{r}_{label}","round":r,"layer":label,
            "cn":CN,"seed":SEED,"new_deco":len(new_deco),"accum_deco":len(deco_accum),
            "prompt":prompt,"vision":vision,
            "note":f"元線verbatim固定+装飾累積 {r}/{N_ROUNDS}周。 full=元線+累積装飾/deco=装飾のみ"})
        log(f"round{r}",label,len(place(strokes)),"strokes"); ok+=1
log("generated",ok)
if ok>0:
    subprocess.run(["./venv/bin/python","-m","scripts.build_selection_webapp"])
    subprocess.run(["git","add",str(BASE),f"sketch_variations/_inputs/{COMBO_SID}.png",
                    "scripts/build_selection_webapp.py","scripts/gen_decorate_accum.py","docs/selection/index.html"])
    subprocess.run(["git","commit","-q","-m","元線verbatim固定+装飾だけ累積(再生成しない) 4周 (DECOACCUM)"])
    for _ in range(6):
        if subprocess.run(["git","push","origin",BR]).returncode==0: break
        time.sleep(12)
    log("pushed")
print("DECOACCUM_DONE",flush=True)
