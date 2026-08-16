"""B ルート(scatter/direct)で個別生成した3被写体を、 元IMG_4368レイアウトへ合成。
direct variant (manga シート丸ごと線画) を各 bbox へ contain 配置。 seed 123/7/555。
webapp(new_combo_b) に出して push。
"""
import sys, json, shutil, subprocess, time
from pathlib import Path
from PIL import Image
ROOT=Path("/home/jizaiedev2026/draw_piper"); sys.path.insert(0,str(ROOT))
import os; os.chdir(ROOT)
SRC=ROOT/"sketch_variations/disp_2026-06-02-BROUTE"
OUT=ROOT/"sketch_variations/disp_2026-06-02-BROUTECOMBO"
BR="claude/style-pool-rebalance-20260529"
OW,OH=1179,1347; PW,PH=704,1472
SCALE=min(PW/OW, PH/OH); XOFF=(PW-OW*SCALE)/2; YOFF=(PH-OH*SCALE)/2
BBOX={"new_tree_a":(96,13,452,647),"new_tree_b":(666,104,450,683),"new_car":(43,771,979,532)}
SEEDS=[123,7,555]            # gen_routed DEFAULT_SEEDS (B と同じ)
from scripts.gen_routed import _save_candidate
def remap(strokes, bx,by,bw,bh):
    pts=[p for st in strokes for p in st]
    if not pts: return []
    xs=[p[0] for p in pts]; ys=[p[1] for p in pts]
    sx0,sy0,sx1,sy1=min(xs),min(ys),max(xs),max(ys); sw,sh=max(sx1-sx0,1),max(sy1-sy0,1)
    s=min(bw/sw, bh/sh); ox=bx+(bw-sw*s)/2-sx0*s; oy=by+(bh-sh*s)/2-sy0*s
    return [[(x*s+ox,y*s+oy) for x,y in st] for st in strokes]

shutil.copy(ROOT/"assets/IMG_4368.jpg", ROOT/"sketch_variations/_inputs/new_combo.png")
if OUT.exists(): shutil.rmtree(OUT)
for i,seed in enumerate(SEEDS,1):
    combined=[]
    for sid,(bx,by,bw,bh) in BBOX.items():
        cand=list(SRC.glob(f"{sid}/v*_seed{seed}_direct")) or list(SRC.glob(f"{sid}/v*_seed{seed}*"))
        if not cand: print("missing",sid,seed); continue
        st=json.load(open(cand[0]/"strokes.json"))["strokes"]
        tb=(bx*SCALE+XOFF, by*SCALE+YOFF, bw*SCALE, bh*SCALE)
        combined+=remap(st,*tb)
    outd=OUT/"new_combo_b"/f"v{i}_seed{seed}"
    _save_candidate(outd,combined,PW,PH,generated=None,meta={"sid":"new_combo_b",
        "route":"broute_recombine","variant":f"combo_seed{seed}","seed":seed,
        "source":"disp_2026-06-02-BROUTE (scatter/direct manga)","placed_at":"original_bbox_contain",
        "note":"Bルート(manga multiple→direct)で個別生成→元IMG_4368レイアウトへ合成"})
    print(f"combo_b seed{seed}: {len(combined)} strokes -> {outd}",flush=True)
subprocess.run(["./venv/bin/python","-m","scripts.build_selection_webapp"])
subprocess.run(["git","add","sketch_variations/disp_2026-06-02-BROUTE","sketch_variations/disp_2026-06-02-BROUTECOMBO",
                "sketch_variations/_inputs/new_combo.png","scripts/build_selection_webapp.py",
                "scripts/recombine_broute.py","docs/selection/index.html"])
subprocess.run(["git","commit","-q","-m","Bルート(scatter/direct manga)で新3枚を個別生成→元レイアウト合成"])
for _ in range(6):
    if subprocess.run(["git","push","origin",BR]).returncode==0: break
    time.sleep(12)
print("BROUTE_RECOMBINE_DONE",flush=True)
