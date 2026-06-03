"""保存済み generated.png に対し OpenCV 線抽出(modules.gen_line_extract)を適用し、
再 vectorize → 同 variant dir を上書き更新 (生成は再利用、 GPU 不要)。
背景に色/トーンが乗った松本 LoRA 出力の線化を改善する。
"""
import sys, json, subprocess, time
from pathlib import Path
from PIL import Image
ROOT=Path("/home/jizaiedev2026/draw_piper"); sys.path.insert(0,str(ROOT))
import os; os.chdir(ROOT)
CW,CH=704,1472; BR="claude/style-pool-rebalance-20260529"
BASE=ROOT/"sketch_variations/disp_2026-06-02-MTLORA"
from modules.gen_line_extract import extract_lines
from modules.vectorizer import Vectorizer, load_binarize_config
from scripts.gen_routed import _save_candidate
import numpy as np
def place_fill(strokes, margin=0.92, min_feat=15):
    pts=[p for st in strokes for p in st]
    if not pts: return []
    xs=[p[0] for p in pts]; ys=[p[1] for p in pts]
    x0,y0,x1,y1=min(xs),min(ys),max(xs),max(ys); bw,bh=max(x1-x0,1),max(y1-y0,1)
    s=min(CW*margin/bw, CH*margin/bh); ox=(CW-bw*s)/2-x0*s; oy=(CH-bh*s)/2-y0*s
    out=[[(x*s+ox,y*s+oy) for x,y in st] for st in strokes]
    def md(st):
        X=[p[0] for p in st];Y=[p[1] for p in st];return max(max(X)-min(X),max(Y)-min(Y))
    return [st for st in out if len(st)>=2 and md(st)>=min_feat]
vc=Vectorizer(gen_line_mode="binarize",**load_binarize_config())
n=0
for vdir in sorted(BASE.glob("*/v*")):
    gp=vdir/"generated.png"
    if not gp.exists(): continue
    raster=Image.open(gp).convert("RGB")
    lines=extract_lines(raster)                       # OpenCV 線抽出
    r=vc.vectorize(generated_image=lines,user_image=None)
    placed=place_fill(r.strokes)
    meta={}
    mp=vdir/"00_meta.json"
    if mp.exists(): meta=json.loads(mp.read_text())
    meta["line_extract"]="opencv_bgremove_adaptive"
    meta["vectorize"]="binarize+opencv_line_extract"
    _save_candidate(vdir,placed,CW,CH,generated=raster,meta=meta)
    print(f"[reextract] {vdir.parent.name}/{vdir.name}: {len(placed)} strokes",flush=True)
    n+=1
print("reextracted",n,flush=True)
subprocess.run(["./venv/bin/python","-m","scripts.build_selection_webapp"])
subprocess.run(["git","add",str(BASE),"docs/selection/index.html"])
subprocess.run(["git","commit","-q","-m","MTLORA: OpenCV線抽出(背景色/トーン除去+適応二値化)で再vectorize"])
for _ in range(6):
    if subprocess.run(["git","push","origin",BR]).returncode==0: break
    time.sleep(12)
print("REEXTRACT_DONE",flush=True)
