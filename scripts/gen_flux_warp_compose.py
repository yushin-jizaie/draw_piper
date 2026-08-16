"""デザイン性×アライン性: 「2生成→特徴ワープ後合成」(方式③)。
同 seed/同 prompt/同 control で:
  - align 生成 (高CN=0.7): 入力位置/構造に忠実 (アライン芯)
  - design 生成 (低CN=0.2 + VLM完成形): 魅力的だが入力からドリフト
DIS optical flow で design→align の変位場を求め、 design ストロークをワープして
align(=入力位置) に乗せる → デザイン中身ごと入力にアラインした合成を得る。
各 (sid,seed) で align / design(ドリフト) / warp(合成) の3層を出して診断。
基盤: FLUX.1-schnell + ControlNet + style LoRA、 prompt は今日の complete ビジョン。
"""
import time, subprocess, gc, sys, traceback, json
from pathlib import Path
import numpy as np, cv2, torch
from PIL import Image
ROOT=Path("/home/jizaiedev2026/draw_piper"); sys.path.insert(0,str(ROOT))
import os; os.chdir(ROOT)
# 5/29 当日(2026-05-29 15:19)に追加された手描きテスト入力 8 枚。
SIDS=["B_round_smiley","car","cat","C_face_with_neck","D_stick_figure",
      "F_angry_face","house","tree"]
SEEDS=[0]
CN_ALIGN=0.7; CN_DESIGN=0.2
LORA_DIR="models/flux_lora_winners"; LORA_STR=0.8; TRIGGER="tklineart"
REPO="chutesai/FLUX.1-schnell"; CN="Shakker-Labs/FLUX.1-dev-ControlNet-Union-Pro"
CW,CH,SIZE=704,1472,1024
BASE=ROOT/"sketch_variations/disp_2026-06-02-WARP529"; BR="claude/style-pool-rebalance-20260529"
SIMPLE=("bold simple cartoon line art, thick black outlines, one single large subject "
        "centered and filling the frame, minimal detail, few clean lines, white background, "
        "no fill, no shading, no background objects, no text")
def log(*a): print("[warp]",*a,flush=True)
def hardboost(img, cut=250):
    a=np.array(img.convert("L")); m=(a<cut).astype(np.uint8)*255
    m=cv2.medianBlur(m,3)
    return Image.fromarray(255-m).convert("RGB")
def canny_ctrl(img):
    g=np.array(img.convert("L")); e=cv2.dilate(cv2.Canny(g,80,160),np.ones((2,2),np.uint8))
    return Image.fromarray(cv2.cvtColor(e,cv2.COLOR_GRAY2RGB))

# --- 線画用 DIS optical flow: design(I0)→align(I1) の変位場 (gen 正方フレーム) ---
def line_flow(design_img, align_img):
    def prep(im):
        g=np.array(im.convert("L")).astype(np.uint8)
        g=255-g                              # 線を明るく (フロー支持を作る)
        return cv2.GaussianBlur(g,(0,0),3.0) # 疎な線に勾配支持を与える
    I0,I1=prep(design_img),prep(align_img)
    dis=cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
    dis.setUseSpatialPropagation(True)
    flow=dis.calc(I0,I1,None)               # flow[y,x]=(dx,dy): design画素→align画素
    return flow
def warp_strokes(strokes, flow):
    H,W=flow.shape[:2]; out=[]
    for st in strokes:
        ws=[]
        for x,y in st:
            xi=min(max(int(round(x)),0),W-1); yi=min(max(int(round(y)),0),H-1)
            dx,dy=flow[yi,xi]
            ws.append((x+float(dx), y+float(dy)))
        out.append(ws)
    return out

assert (ROOT/LORA_DIR/"pytorch_lora_weights.safetensors").exists(), "LoRA未完成"
# VLM: 各入力から完成形ビジョンを計算 (describe_scene → design_instruction complete)。
# FLUX ロード前に計算して VRAM 解放 (GPU 排他)。
from modules.vlm import VLM
info={}; vlm=VLM(verbose=True)
for sid in SIDS:
    _inp=Image.open(f"sketch_variations/_inputs/{sid}.png").convert("RGB")
    _scene=vlm.describe_scene(_inp) or "subject"
    _vision=vlm.design_instruction(_inp,_scene,mode="complete") or _scene
    info[sid]={"scene":_scene,"vision":_vision}
    log(sid,"scene:",_scene); log(sid,"vision:",_vision)
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
from scripts.gen_routed import _save_candidate, _place_input_aligned
vc=Vectorizer(gen_line_mode="binarize",**load_binarize_config())
import shutil
if BASE.exists(): shutil.rmtree(BASE)

def gen(prompt, ctrl, cn, seed):
    return pipe(prompt=prompt,control_image=ctrl,control_mode=0,
        controlnet_conditioning_scale=cn,width=SIZE,height=SIZE,
        num_inference_steps=4,guidance_scale=0.0,
        generator=torch.Generator("cpu").manual_seed(seed)).images[0]

ok=0; vi=0
for sid in SIDS:
    d=info[sid]; prompt=f"{TRIGGER}, {d['vision']} {SIMPLE}"
    inp=Image.open(f"sketch_variations/_inputs/{sid}.png").convert("RGB")
    _W,_H=inp.size
    sq=square_pad(inp,SIZE)                       # 入力を gen 正方フレームへ (アライン芯)
    ctrl=canny_ctrl(sq)
    for seed in SEEDS:
        try:
            de_img=gen(prompt,ctrl,CN_DESIGN,seed)   # 生成は design (低CN) 1 本のみ
        except Exception as e: log("FAIL gen",sid,seed,repr(e)); traceback.print_exc(); continue
        de_b=hardboost(de_img)
        de_str=vc.vectorize(generated_image=de_b,user_image=None).strokes
        a_str=vc.vectorize(generated_image=sq,user_image=None).strokes   # 入力の生線 (=芯)
        try:
            flow=line_flow(de_b,sq)                  # design→入力 の変位場
            w_str=warp_strokes(de_str,flow)
            mag=float(np.mean(np.linalg.norm(flow,axis=2)))
        except Exception as e:
            log("FAIL flow",sid,seed,repr(e)); w_str=de_str; mag=-1.0
        # 3層を入力位置へ写像して保存 (align=入力生線, warp はそこへ寄る)
        layers=[("align_input",a_str,sq,0.0),("design",de_str,de_img,CN_DESIGN),
                ("warp",w_str,de_img,CN_DESIGN)]
        for label,strokes,raster,cn in layers:
            placed=_place_input_aligned(strokes,_W,_H,SIZE,CW,CH)
            vi+=1
            outd=BASE/sid/f"v{vi}_seed{seed}_{label}"
            _save_candidate(outd,placed,CW,CH,generated=raster,meta={"sid":sid,
                "route":"flux_warp_compose","variant":label,"cn":cn,
                "cn_align":CN_ALIGN,"cn_design":CN_DESIGN,"flow_mean_px":round(mag,2),
                "lora":LORA_DIR,"lora_strength":LORA_STR,"seed":seed,"prompt":prompt,
                "vision":d["vision"],"placed_at":"input_aligned","input_fit":"contain",
                "vectorize":"binarize+hardboost250","align_source":"input_lines",
                "note":"方式③ warp合成: align=入力生線/design=低CN完成形/warp=designをDIS flowで入力へ"})
            log(sid,seed,label,len(placed),"strokes")
        log(sid,seed,"flow_mean_px",round(mag,2)); ok+=1
log("pairs done",ok)
BASE.mkdir(parents=True,exist_ok=True)
(BASE/"_vlm_visions.json").write_text(json.dumps(info,ensure_ascii=False,indent=2))
if ok>0:
    subprocess.run(["./venv/bin/python","-m","scripts.build_selection_webapp"])
    subprocess.run(["git","add",str(BASE),"docs/selection/index.html"])
    subprocess.run(["git","commit","-q","-m","方式③ warp合成(アライン源=入力生線)を5/29入力8枚に適用 (WARP529)"])
    for _ in range(6):
        if subprocess.run(["git","push","origin",BR]).returncode==0: break
        time.sleep(12)
    log("pushed")
print("WARP_COMPOSE_DONE",flush=True)
