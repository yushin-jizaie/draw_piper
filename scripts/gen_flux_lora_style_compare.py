"""今の FLUX 出力に「松本スタイル」を乗せるとどうなるか: winners LoRA vs 松本 LoRA 比較。
同 prompt(VLM完成形ビジョン)/seed/CN で adapter を切替えて生成し、 webapp で画風差を見る。
"""
import time, subprocess, gc, sys, traceback, json
from pathlib import Path
import numpy as np, cv2, torch
from PIL import Image
ROOT=Path("/home/jizaiedev2026/draw_piper"); sys.path.insert(0,str(ROOT))
import os; os.chdir(ROOT)
SIDS=["car","cat","house","B_round_smiley"]
SEED=0; CN_SCALE=0.4
LW="models/flux_lora_winners"; LM="models/flux_lora_matsumoto"
# 条件: (ラベル, adapter, 強度, トリガー語)
CONDS=[("winners08","winners",0.8,"tklineart"),
       ("mttaiyo08","mttaiyo",0.8,"mttaiyo"),
       ("mttaiyo10","mttaiyo",1.0,"mttaiyo")]
REPO="chutesai/FLUX.1-schnell"; CN="Shakker-Labs/FLUX.1-dev-ControlNet-Union-Pro"
CW,CH,SIZE=704,1472,1024
BASE=ROOT/"sketch_variations/disp_2026-06-02-MTLORA"; BR="claude/style-pool-rebalance-20260529"
VIS=ROOT/"sketch_variations/disp_2026-06-02-WARP529/_vlm_visions.json"
SIMPLE=("bold simple cartoon line art, thick black outlines, one single large subject "
        "centered and filling the frame, minimal detail, few clean lines, white background, "
        "no fill, no shading, no background objects, no text")
def log(*a): print("[mtlora]",*a,flush=True)
def hardboost(img, cut=250):
    a=np.array(img.convert("L")); m=(a<cut).astype(np.uint8)*255
    m=cv2.medianBlur(m,3); return Image.fromarray(255-m).convert("RGB")
def canny_ctrl(img):
    g=np.array(img.convert("L")); e=cv2.dilate(cv2.Canny(g,80,160),np.ones((2,2),np.uint8))
    return Image.fromarray(cv2.cvtColor(e,cv2.COLOR_GRAY2RGB))
def place_fill(strokes, margin=0.92, min_feat=15):
    pts=[p for st in strokes for p in st]
    if not pts: return []
    xs=[p[0] for p in pts]; ys=[p[1] for p in pts]
    x0,y0,x1,y1=min(xs),min(ys),max(xs),max(ys); bw,bh=max(x1-x0,1),max(y1-y0,1)
    s=min(CW*margin/bw, CH*margin/bh); ox=(CW-bw*s)/2 - x0*s; oy=(CH-bh*s)/2 - y0*s
    out=[[(x*s+ox,y*s+oy) for x,y in st] for st in strokes]
    def md(st):
        X=[p[0] for p in st];Y=[p[1] for p in st];return max(max(X)-min(X),max(Y)-min(Y))
    return [st for st in out if len(st)>=2 and md(st)>=min_feat]

assert (ROOT/LM/"pytorch_lora_weights.safetensors").exists(), "松本LoRA未完成"
info=json.loads(VIS.read_text())

from diffusers import FluxControlNetModel, FluxControlNetPipeline, FluxTransformer2DModel, BitsAndBytesConfig as DBNB
from transformers import T5EncoderModel, BitsAndBytesConfig as TBNB
dnf4=DBNB(load_in_4bit=True,bnb_4bit_quant_type="nf4",bnb_4bit_compute_dtype=torch.bfloat16)
tnf4=TBNB(load_in_4bit=True,bnb_4bit_quant_type="nf4",bnb_4bit_compute_dtype=torch.bfloat16)
log("loading flux...")
tr=FluxTransformer2DModel.from_pretrained(REPO,subfolder="transformer",quantization_config=dnf4,torch_dtype=torch.bfloat16)
te2=T5EncoderModel.from_pretrained(REPO,subfolder="text_encoder_2",quantization_config=tnf4,torch_dtype=torch.bfloat16)
cnet=FluxControlNetModel.from_pretrained(CN,torch_dtype=torch.bfloat16)
pipe=FluxControlNetPipeline.from_pretrained(REPO,transformer=tr,text_encoder_2=te2,controlnet=cnet,torch_dtype=torch.bfloat16)
pipe.load_lora_weights(LW,adapter_name="winners")
pipe.load_lora_weights(LM,adapter_name="mttaiyo")
pipe.enable_model_cpu_offload(); log("flux+2 loras ready")
from modules.input_prep import square_pad
from modules.vectorizer import Vectorizer, load_binarize_config
from scripts.gen_routed import _save_candidate
vc=Vectorizer(gen_line_mode="binarize",**load_binarize_config())
import shutil
if BASE.exists(): shutil.rmtree(BASE)

ok=0; vi=0
for sid in SIDS:
    vision=info[sid]["vision"]
    inp=Image.open(f"sketch_variations/_inputs/{sid}.png").convert("RGB")
    ctrl=canny_ctrl(square_pad(inp,SIZE))
    for label,adapter,strength,trig in CONDS:
        pipe.set_adapters([adapter],[strength])
        prompt=f"{trig}, {vision} {SIMPLE}"
        vi+=1
        try:
            img=pipe(prompt=prompt,control_image=ctrl,control_mode=0,
                controlnet_conditioning_scale=CN_SCALE,width=SIZE,height=SIZE,
                num_inference_steps=4,guidance_scale=0.0,
                generator=torch.Generator("cpu").manual_seed(SEED)).images[0]
        except Exception as e: log("FAIL",sid,label,repr(e)); traceback.print_exc(); continue
        r=vc.vectorize(generated_image=hardboost(img),user_image=None)
        placed=place_fill(r.strokes)
        outd=BASE/sid/f"v{vi}_seed{SEED}_{label}"
        _save_candidate(outd,placed,CW,CH,generated=img,meta={"sid":sid,
            "route":"flux_lora_style_compare","variant":label,"adapter":adapter,
            "lora_strength":strength,"trigger":trig,"cn":CN_SCALE,"seed":SEED,
            "prompt":prompt,"vision":vision,"placed_at":"fill_board",
            "vectorize":"binarize+hardboost250","note":"winners vs 松本 LoRA 画風比較"})
        log(sid,label,len(placed),"strokes"); ok+=1
log("generated",ok)
if ok>0:
    subprocess.run(["./venv/bin/python","-m","scripts.build_selection_webapp"])
    subprocess.run(["git","add",str(BASE),"docs/selection/index.html"])
    subprocess.run(["git","commit","-q","-m","松本FLUX LoRA vs winners 画風比較 (mttaiyo trigger, CN0.4)"])
    for _ in range(6):
        if subprocess.run(["git","push","origin",BR]).returncode==0: break
        time.sleep(12)
    log("pushed")
print("MTLORA_DONE",flush=True)
