"""新規手描き3枚(木A/木B/車, assets/IMG_4368 由来)を最良設定 strong_cn20 で生成。
DETAIL_STRONG プロンプト + CN0.2 + OpenCV線抽出 + winners LoRA、 3 seed ガチャ。
"""
import time, subprocess, gc, sys, traceback
from pathlib import Path
import numpy as np, cv2, torch
from PIL import Image
ROOT=Path("/home/jizaiedev2026/draw_piper"); sys.path.insert(0,str(ROOT))
import os; os.chdir(ROOT)
SIDS=["new_tree_a","new_tree_b","new_car"]
SEEDS=[0,1,2]
CN_SCALE=0.20; MINF=8
LORA_DIR="models/flux_lora_winners"; LORA_STR=0.6; TRIGGER="tklineart"
REPO="chutesai/FLUX.1-schnell"; CN="Shakker-Labs/FLUX.1-dev-ControlNet-Union-Pro"
CW,CH,SIZE=704,1472,1024
BASE=ROOT/"sketch_variations/disp_2026-06-02-NEW3M"; BR="claude/style-pool-rebalance-20260529"
# 2026-06-03: デフォルトのスタイル文を B ルートの manga 系に差し替え (ユーザー指定)。
DETAIL_STRONG=("manga style, clean bold ink lineart, white background, appealing design, multiple")
def log(*a): print("[new3]",*a,flush=True)
def canny_ctrl(img):
    g=np.array(img.convert("L")); e=cv2.dilate(cv2.Canny(g,80,160),np.ones((2,2),np.uint8))
    return Image.fromarray(cv2.cvtColor(e,cv2.COLOR_GRAY2RGB))
def place_fill(strokes, min_feat=MINF, margin=0.92):
    pts=[p for st in strokes for p in st]
    if not pts: return []
    xs=[p[0] for p in pts]; ys=[p[1] for p in pts]
    x0,y0,x1,y1=min(xs),min(ys),max(xs),max(ys); bw,bh=max(x1-x0,1),max(y1-y0,1)
    s=min(CW*margin/bw, CH*margin/bh); ox=(CW-bw*s)/2-x0*s; oy=(CH-bh*s)/2-y0*s
    out=[[(x*s+ox,y*s+oy) for x,y in st] for st in strokes]
    def md(st):
        X=[p[0] for p in st];Y=[p[1] for p in st];return max(max(X)-min(X),max(Y)-min(Y))
    return [st for st in out if len(st)>=2 and md(st)>=min_feat]

assert (ROOT/LORA_DIR/"pytorch_lora_weights.safetensors").exists(), "LoRA未完成"
from modules.vlm import VLM
info={}; vlm=VLM(verbose=True)
for sid in SIDS:
    inp=Image.open(f"sketch_variations/_inputs/{sid}.png").convert("RGB")
    scene=vlm.describe_scene(inp) or "subject"
    vision=vlm.design_instruction(inp,scene,mode="complete") or scene
    info[sid]={"scene":scene,"vision":vision}; log(sid,"scene:",scene); log(sid,"vision:",vision)
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

ok=0
for sid in SIDS:
    vision=info[sid]["vision"]; prompt=f"{TRIGGER}, {vision} {DETAIL_STRONG}"
    inp=Image.open(f"sketch_variations/_inputs/{sid}.png").convert("RGB")
    ctrl=canny_ctrl(square_pad(inp,SIZE))
    for i,seed in enumerate(SEEDS,1):
        try:
            img=pipe(prompt=prompt,control_image=ctrl,control_mode=0,
                controlnet_conditioning_scale=CN_SCALE,width=SIZE,height=SIZE,
                num_inference_steps=4,guidance_scale=0.0,
                generator=torch.Generator("cpu").manual_seed(seed)).images[0]
        except Exception as e: log("FAIL",sid,seed,repr(e)); traceback.print_exc(); continue
        lines=extract_lines(img)
        r=vc.vectorize(generated_image=lines,user_image=None)
        placed=place_fill(r.strokes)
        outd=BASE/sid/f"v{i}_seed{seed}"
        _save_candidate(outd,placed,CW,CH,generated=img,meta={"sid":sid,
            "route":"flux_new3","variant":f"strong_cn20_seed{seed}","cn":CN_SCALE,
            "min_feature_px":MINF,"lora":LORA_DIR,"lora_strength":LORA_STR,"seed":seed,
            "prompt":prompt,"vision":vision,"placed_at":"fill_board",
            "line_extract":"opencv_bgremove_adaptive","note":"新3枚 6/3最新ルート+manga默认スタイル ガチャ"})
        log(sid,seed,len(placed),"strokes"); ok+=1
log("generated",ok)
if ok>0:
    subprocess.run(["./venv/bin/python","-m","scripts.build_selection_webapp"])
    subprocess.run(["git","add",str(BASE),"sketch_variations/_inputs/new_tree_a.png",
                    "sketch_variations/_inputs/new_tree_b.png","sketch_variations/_inputs/new_car.png",
                    "docs/selection/index.html"])
    subprocess.run(["git","commit","-q","-m","新3枚を6/3最新ルート+mangaデフォルトスタイルで生成 (NEW3M)"])
    for _ in range(6):
        if subprocess.run(["git","push","origin",BR]).returncode==0: break
        time.sleep(12)
    log("pushed")
print("NEW3_DONE",flush=True)
