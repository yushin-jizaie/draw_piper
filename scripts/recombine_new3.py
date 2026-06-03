"""NEW3 の生成結果(木A→象/木B→花/車→トラック)を、 元画像 IMG_4368 の各下書き位置
(bbox)へ戻して1枚に再構成。 seed ごとに 1 枚 (3 被写体同 seed を合成)。
webapp に new_combo として表示。
"""
import sys, json, shutil, subprocess, time
from pathlib import Path
import cv2
from PIL import Image
ROOT=Path("/home/jizaiedev2026/draw_piper"); sys.path.insert(0,str(ROOT))
import os; os.chdir(ROOT)
SRC=ROOT/"sketch_variations/disp_2026-06-02-NEW3"
OUT=ROOT/"sketch_variations/disp_2026-06-02-NEW3COMBO"
BR="claude/style-pool-rebalance-20260529"
# 元画像 IMG_4368 (1179x1347) 内の各下書き bbox (split_new3 の検出値)
OW,OH=1179,1347
# webapp は全プレビューを正方セルに強制するため、 正方キャンバスに揃えて無歪み表示。
SQ=max(OW,OH); XP=(SQ-OW)//2; YP=(SQ-OH)//2
BBOX={"new_tree_a":(96,13,452,647),
      "new_tree_b":(666,104,450,683),
      "new_car":(43,771,979,532)}
SEEDS=[0,1,2]
from scripts.gen_routed import _save_candidate

def remap(strokes, bx,by,bw,bh):
    pts=[p for st in strokes for p in st]
    if not pts: return []
    xs=[p[0] for p in pts]; ys=[p[1] for p in pts]
    sx0,sy0,sx1,sy1=min(xs),min(ys),max(xs),max(ys)
    sw,sh=max(sx1-sx0,1),max(sy1-sy0,1)
    s=min(bw/sw, bh/sh)                       # contain
    ox=bx+(bw-sw*s)/2 - sx0*s; oy=by+(bh-sh*s)/2 - sy0*s
    return [[(x*s+ox, y*s+oy) for x,y in st] for st in strokes]

# 元画像を正方パディングして webapp 入力に (combo と同じ正方フレーム)
orig=Image.open(ROOT/"assets/IMG_4368.jpg").convert("RGB")
sqimg=Image.new("RGB",(SQ,SQ),(255,255,255)); sqimg.paste(orig,(XP,YP))
sqimg.save(ROOT/"sketch_variations/_inputs/new_combo.png")

if OUT.exists(): shutil.rmtree(OUT)
for seed in SEEDS:
    combined=[]
    for sid,(bx,by,bw,bh) in BBOX.items():
        cand=list(SRC.glob(f"{sid}/v*_seed{seed}"))
        if not cand:
            print("missing",sid,seed); continue
        st=json.load(open(cand[0]/"strokes.json"))["strokes"]
        combined+=remap(st,bx+XP,by+YP,bw,bh)   # 正方フレームへシフト
    outd=OUT/"new_combo"/f"v{seed+1}_seed{seed}"
    _save_candidate(outd,combined,SQ,SQ,generated=None,meta={"sid":"new_combo",
        "route":"new3_recombine","variant":f"combo_seed{seed}","seed":seed,
        "source":"disp_2026-06-02-NEW3","placed_at":"original_bbox",
        "note":"NEW3の3生成を元IMG_4368レイアウト(各bbox)へ戻して1枚に再構成"})
    print(f"combo seed{seed}: {len(combined)} strokes -> {outd}",flush=True)
print("RECOMBINE_DONE",flush=True)
