"""デザイン性(細部の豊かさ)を上げる比較。 「大きくシンプル」抑制を外し、 detailed 線画を
促すプロンプト + CN高め + min_feature を下げて 参照(object route の detailed 車)レベルを狙う。
simple(旧) vs rich(detail) 2段(CN0.35/0.55) を比較。 OpenCV線抽出 + winners LoRA。
"""
import time, subprocess, gc, sys, traceback, json
from pathlib import Path
import numpy as np, cv2, torch
from PIL import Image
ROOT=Path("/home/jizaiedev2026/draw_piper"); sys.path.insert(0,str(ROOT))
import os; os.chdir(ROOT)
SIDS=["car","cat","house"]
SEED=0
LORA_DIR="models/flux_lora_winners"; LORA_STR=0.6; TRIGGER="tklineart"
REPO="chutesai/FLUX.1-schnell"; CN="Shakker-Labs/FLUX.1-dev-ControlNet-Union-Pro"
CW,CH,SIZE=704,1472,1024
BASE=ROOT/"sketch_variations/disp_2026-06-02-RICH2"; BR="claude/style-pool-rebalance-20260529"
# detailed 線画 (前回)
DETAIL=("detailed clean black line art, bold confident outlines plus fine interior detail "
        "lines, accurate structure and proportions, intricate linework, one main subject "
        "filling the frame, white background, no fill, no shading, no color, no text")
# 強化版: より多くの細部/メカ的ディテールを明示 (参照=detailed 車レベルを狙う)
DETAIL_STRONG=("highly detailed clean black line art, bold confident outlines with many fine "
        "interior detail lines, mechanical and structural detail, panel lines, intricate "
        "accurate linework, rich detailing throughout, one main subject filling the frame, "
        "white background, no fill, no shading, no color, no text")
# 条件: (ラベル, style, CN, min_feature_px) — 「両方」: 低CN + プロンプト強化
CONDS=[("detail_cn35",DETAIL,0.35,8),
       ("strong_cn35",DETAIL_STRONG,0.35,8),
       ("strong_cn20",DETAIL_STRONG,0.20,8)]
def log(*a): print("[rich]",*a,flush=True)
def canny_ctrl(img):
    g=np.array(img.convert("L")); e=cv2.dilate(cv2.Canny(g,80,160),np.ones((2,2),np.uint8))
    return Image.fromarray(cv2.cvtColor(e,cv2.COLOR_GRAY2RGB))
def place_fill(strokes, min_feat, margin=0.92):
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
    info[sid]={"scene":scene,"vision":vision}; log(sid,"vision:",vision)
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
    vision=info[sid]["vision"]
    inp=Image.open(f"sketch_variations/_inputs/{sid}.png").convert("RGB")
    ctrl=canny_ctrl(square_pad(inp,SIZE))
    for label,style,cn,minf in CONDS:
        prompt=f"{TRIGGER}, {vision} {style}"
        vi+=1
        try:
            img=pipe(prompt=prompt,control_image=ctrl,control_mode=0,
                controlnet_conditioning_scale=cn,width=SIZE,height=SIZE,
                num_inference_steps=4,guidance_scale=0.0,
                generator=torch.Generator("cpu").manual_seed(SEED)).images[0]
        except Exception as e: log("FAIL",sid,label,repr(e)); traceback.print_exc(); continue
        lines=extract_lines(img)                       # OpenCV 線抽出 (背景クリーン)
        r=vc.vectorize(generated_image=lines,user_image=None)
        placed=place_fill(r.strokes,minf)
        outd=BASE/sid/f"v{vi}_seed{SEED}_{label}"
        _save_candidate(outd,placed,CW,CH,generated=img,meta={"sid":sid,
            "route":"flux_richness","variant":label,"style":("strong" if style is DETAIL_STRONG else "detail"),
            "cn":cn,"min_feature_px":minf,"lora":LORA_DIR,"lora_strength":LORA_STR,
            "seed":SEED,"prompt":prompt,"vision":vision,"placed_at":"fill_board",
            "line_extract":"opencv_bgremove_adaptive","note":"デザイン性(細部)比較 simple vs detailed"})
        log(sid,label,len(placed),"strokes"); ok+=1
log("generated",ok)
if ok>0:
    subprocess.run(["./venv/bin/python","-m","scripts.build_selection_webapp"])
    subprocess.run(["git","add",str(BASE),"docs/selection/index.html"])
    subprocess.run(["git","commit","-q","-m","デザイン性追い込み(両方): プロンプト強化+低CN0.2 (RICH2)"])
    for _ in range(6):
        if subprocess.run(["git","push","origin",BR]).returncode==0: break
        time.sleep(12)
    log("pushed")
print("RICH_DONE",flush=True)
