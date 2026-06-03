"""装飾フィードバックループ: decorate 出力を入力に戻して再装飾、を N 回繰り返す。
各パスで「今ある線に絡む装飾」を足すので、 回すほど装飾が層状に蓄積する。
(2026-06-04 ユーザー: DECORATE を何度も入れては描いてを繰り返したら?)
各 iter の線画を保存して蓄積の様子を見る。 CN は前パスを保ちつつ足せる中CN。
"""
import time, subprocess, gc, sys, traceback
from pathlib import Path
import numpy as np, cv2, torch
from PIL import Image
ROOT=Path("/home/jizaiedev2026/draw_piper"); sys.path.insert(0,str(ROOT))
import os; os.chdir(ROOT)
SIDS=["new_car","B_round_smiley"]
N_ITER=4; SEED=0; CN=0.65
LORA_DIR="models/flux_lora_winners"; LORA_STR=0.6; TRIGGER="tklineart"
REPO="chutesai/FLUX.1-schnell"; CNREPO="Shakker-Labs/FLUX.1-dev-ControlNet-Union-Pro"
CW,CH,SIZE=704,1472,1024
BASE=ROOT/"sketch_variations/disp_2026-06-04-DECOITER"; BR="claude/style-pool-rebalance-20260529"
DEFAULT_STYLE="manga style, clean bold black ink lineart on white background"
def log(*a): print("[decoiter]",*a,flush=True)
def canny_ctrl(img):
    g=np.array(img.convert("L")); e=cv2.dilate(cv2.Canny(g,80,160),np.ones((2,2),np.uint8))
    return Image.fromarray(cv2.cvtColor(e,cv2.COLOR_GRAY2RGB))
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
cnet=FluxControlNetModel.from_pretrained(CNREPO,torch_dtype=torch.bfloat16)
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
    vision=info[sid]["vision"]; prompt=f"{TRIGGER}, {vision} {DEFAULT_STYLE}"
    current=square_pad(Image.open(f"sketch_variations/_inputs/{sid}.png").convert("RGB"),SIZE)
    for it in range(1,N_ITER+1):
        ctrl=canny_ctrl(current)
        try:
            img=pipe(prompt=prompt,control_image=ctrl,control_mode=0,controlnet_conditioning_scale=CN,
                width=SIZE,height=SIZE,num_inference_steps=4,guidance_scale=0.0,
                generator=torch.Generator("cpu").manual_seed(SEED)).images[0]
        except Exception as e: log("FAIL",sid,it,repr(e)); traceback.print_exc(); break
        lines=extract_lines(img); strokes=vc.vectorize(generated_image=lines,user_image=None).strokes
        placed=place_fill(strokes)
        outd=BASE/sid/f"v{it}_seed{SEED}_iter{it}"
        _save_candidate(outd,placed,CW,CH,generated=img,meta={"sid":sid,"route":"flux_decorate_iter",
            "variant":f"iter{it}","iteration":it,"cn":CN,"seed":SEED,"prompt":prompt,"vision":vision,
            "lora":LORA_DIR,"lora_strength":LORA_STR,
            "note":f"装飾フィードバック {it}/{N_ITER} 回目: 前パス出力を入力に戻して再装飾(蓄積)"})
        log(sid,f"iter{it}",len(placed),"strokes"); ok+=1
        current=lines.resize((SIZE,SIZE))   # 次パスの入力 = 今の線画
log("generated",ok)
if ok>0:
    subprocess.run(["./venv/bin/python","-m","scripts.build_selection_webapp"])
    subprocess.run(["git","add",str(BASE),"docs/selection/index.html","scripts/gen_decorate_iter.py"])
    subprocess.run(["git","commit","-q","-m","装飾フィードバックループ: 出力を入力に戻して再装飾をN回(蓄積)"])
    for _ in range(6):
        if subprocess.run(["git","push","origin",BR]).returncode==0: break
        time.sleep(12)
    log("pushed")
print("DECOITER_DONE",flush=True)
