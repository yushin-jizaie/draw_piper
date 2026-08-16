"""FLUX.1-schnell + ControlNet + style LoRA で ControlNet 強度を比較生成。
ねらい: 「大きさが合っていない」 → CN(canny) が入力線にロックして被写体が
frame いっぱいにならない。 CN を 0.2 / 0.5 で振って最適点を見る。
プロンプトは新・完成形ビジョン(VLM complete)、 LoRA@0.8 固定。 差は CN のみ。
VLM ビジョンは前回バッチの _vlm_visions.json を再利用 (無ければ計算)。
"""
import time, subprocess, gc, sys, traceback, json
from pathlib import Path
import numpy as np, cv2, torch
from PIL import Image
ROOT=Path("/home/jizaiedev2026/draw_piper"); sys.path.insert(0,str(ROOT))
import os; os.chdir(ROOT)
SIDS=["samp_IMG_4357","samp_IMG_4362","samp_IMG_4363"]
SEEDS=[0,1]
CN_SWEEP=[0.2,0.5]
LORA_DIR="models/flux_lora_winners"; LORA_STR=0.8
TRIGGER="tklineart"
REPO="chutesai/FLUX.1-schnell"; CN="Shakker-Labs/FLUX.1-dev-ControlNet-Union-Pro"
CW,CH,SIZE=704,1472,1024
BASE=ROOT/"sketch_variations/disp_2026-06-02-FLUXCN"; BR="claude/style-pool-rebalance-20260529"
VIS_SRC=ROOT/"sketch_variations/disp_2026-06-02-FLUXDESIGN/_vlm_visions.json"
SIMPLE=("bold simple cartoon line art, thick black outlines, one single large subject "
        "centered and filling the frame, minimal detail, few clean lines, white background, "
        "no fill, no shading, no background objects, no text")
def log(*a): print("[cnsweep]",*a,flush=True)
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

assert (ROOT/LORA_DIR/"pytorch_lora_weights.safetensors").exists(), "LoRA未完成"

# --- VLM ビジョン: 再利用 or 計算 -------------------------------------------
info={}
if VIS_SRC.exists():
    saved=json.loads(VIS_SRC.read_text())
    if all(s in saved and saved[s].get("vision") for s in SIDS):
        info=saved; log("reuse visions from", VIS_SRC.name)
if not info:
    from modules.vlm import VLM
    vlm=VLM(verbose=True)
    for sid in SIDS:
        inp=Image.open(f"sketch_variations/_inputs/{sid}.png").convert("RGB")
        lit=vlm.describe_literal(inp) or "subject"
        scene=vlm.describe_scene(inp) or lit
        vision=vlm.design_instruction(inp, subject=scene, mode="complete") or scene
        info[sid]={"lit":lit,"scene":scene,"vision":vision}
        log(sid,"vision:",vision)
    del vlm; gc.collect(); torch.cuda.empty_cache()
for sid in SIDS: log(sid,"vision:",info[sid]["vision"])

# --- FLUX + ControlNet + LoRA ------------------------------------------------
from diffusers import FluxControlNetModel, FluxControlNetPipeline, FluxTransformer2DModel, BitsAndBytesConfig as DBNB
from transformers import T5EncoderModel, BitsAndBytesConfig as TBNB
dnf4=DBNB(load_in_4bit=True,bnb_4bit_quant_type="nf4",bnb_4bit_compute_dtype=torch.bfloat16)
tnf4=TBNB(load_in_4bit=True,bnb_4bit_quant_type="nf4",bnb_4bit_compute_dtype=torch.bfloat16)
log("loading flux...")
tr=FluxTransformer2DModel.from_pretrained(REPO,subfolder="transformer",quantization_config=dnf4,torch_dtype=torch.bfloat16)
te2=T5EncoderModel.from_pretrained(REPO,subfolder="text_encoder_2",quantization_config=tnf4,torch_dtype=torch.bfloat16)
cnet=FluxControlNetModel.from_pretrained(CN,torch_dtype=torch.bfloat16)
pipe=FluxControlNetPipeline.from_pretrained(REPO,transformer=tr,text_encoder_2=te2,controlnet=cnet,torch_dtype=torch.bfloat16)
pipe.load_lora_weights(LORA_DIR,adapter_name="winners")
pipe.set_adapters(["winners"],[LORA_STR])
pipe.enable_model_cpu_offload(); log("flux+lora ready")
from modules.input_prep import square_pad
from modules.vectorizer import Vectorizer, load_binarize_config
from scripts.gen_routed import _save_candidate
vc=Vectorizer(gen_line_mode="binarize",**load_binarize_config())
import shutil
if BASE.exists(): shutil.rmtree(BASE)

ok=0; vi=0
for sid in SIDS:
    d=info[sid]
    prompt=f"{TRIGGER}, {d['vision']} {SIMPLE}"
    inp=Image.open(f"sketch_variations/_inputs/{sid}.png").convert("RGB")
    ctrl=canny_ctrl(square_pad(inp,SIZE))
    for cnv in CN_SWEEP:
        tag=f"cn{int(round(cnv*100)):02d}"
        for seed in SEEDS:
            vi+=1
            try:
                img=pipe(prompt=prompt,control_image=ctrl,control_mode=0,
                    controlnet_conditioning_scale=cnv,
                    width=SIZE,height=SIZE,num_inference_steps=4,guidance_scale=0.0,
                    generator=torch.Generator("cpu").manual_seed(seed)).images[0]
            except Exception as e: log("FAIL",sid,tag,seed,repr(e)); traceback.print_exc(); continue
            r=vc.vectorize(generated_image=hardboost(img),user_image=None)
            placed=place_fill(r.strokes)
            outd=BASE/sid/f"v{vi}_seed{seed}_{tag}"
            _save_candidate(outd,placed,CW,CH,generated=img,meta={"sid":sid,
                "route":"flux_cn_sweep","variant":tag,"cn":cnv,
                "lora":LORA_DIR,"lora_strength":LORA_STR,
                "preset":REPO,"controlnet":CN,"seed":seed,"prompt":prompt,
                "scene":d["scene"],"vision":d["vision"],
                "placed_at":"fill_board","input_fit":"contain",
                "vectorize":"binarize+hardboost250","min_feature_px":15,
                "note":"CN強度比較 0.2 vs 0.5 (大きさ合わせ) / VLM完成形 + LoRA0.8"})
            log(sid,tag,seed,len(placed),"strokes"); ok+=1
log("generated",ok)
if ok>0:
    subprocess.run(["./venv/bin/python","-m","scripts.build_selection_webapp"])
    subprocess.run(["git","add",str(BASE),"docs/selection/index.html"])
    subprocess.run(["git","commit","-q","-m","FLUX CN強度比較 0.2 vs 0.5 (大きさ合わせ) / VLM完成形+LoRA0.8"])
    for _ in range(6):
        if subprocess.run(["git","push","origin",BR]).returncode==0: break
        time.sleep(12)
    log("pushed")
print("CN_SWEEP_DONE",flush=True)
