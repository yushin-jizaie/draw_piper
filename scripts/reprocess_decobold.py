"""DECOACCUM の生成画像(round*_full)を「太い線・塗りなし」(extract_lines_bold)に整えて
線化し直す (2026-06-04 ユーザー: 生成画像は良いが線が微妙→太線・塗りなしに)。 GPU 不要。
generated.png = bold-no-fill 画像、 30_vectorized = その vectorize。 webapp で比較。
"""
import sys, json, shutil, subprocess, time
from pathlib import Path
from PIL import Image
ROOT=Path("/home/jizaiedev2026/draw_piper"); sys.path.insert(0,str(ROOT))
import os; os.chdir(ROOT)
CW,CH=704,1472; BR="claude/style-pool-rebalance-20260529"
SRC=ROOT/"sketch_variations/disp_2026-06-04-DECOACCUM/new_combo_decoaccum"
OUT=ROOT/"sketch_variations/disp_2026-06-04-DECOBOLD/new_combo_decobold"
from modules.gen_line_extract import extract_lines_bold
from modules.vectorizer import Vectorizer, load_binarize_config
from scripts.gen_routed import _save_candidate
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
vc=Vectorizer(gen_line_mode="canny_centerline",**load_binarize_config())
if OUT.parent.exists(): shutil.rmtree(OUT.parent)
i=0
for vdir in sorted(SRC.glob("v*_round*_full")):
    gp=vdir/"generated.png"
    if not gp.exists(): continue
    raster=Image.open(gp).convert("RGB")
    bold=extract_lines_bold(raster, bold=3)            # 太い線・塗りなし
    r=vc.vectorize(generated_image=bold,user_image=None)
    placed=place_fill(r.strokes)
    rnd=vdir.name.split("_round")[1].split("_")[0]
    i+=1; outd=OUT/f"v{i}_seed0_round{rnd}_bold"
    outd.mkdir(parents=True,exist_ok=True)
    bold.save(outd/"generated.png")                    # bold画像を generated として表示
    _save_candidate(outd,placed,CW,CH,generated=bold,meta={"sid":"new_combo_decobold",
        "route":"decobold","variant":f"round{rnd}_bold","round":int(rnd),
        "source":vdir.name,"line_extract":"extract_lines_bold(太い線・塗りなし)",
        "note":"DECOACCUM生成画像を極性反転+塗り輪郭化+太線化してから線化"})
    print(f"round{rnd}: {len(placed)} strokes -> {outd}",flush=True)
print("reprocessed",i,flush=True)
subprocess.run(["./venv/bin/python","-m","scripts.build_selection_webapp"])
subprocess.run(["git","add",str(OUT.parent),"modules/gen_line_extract.py",
                "scripts/build_selection_webapp.py","scripts/reprocess_decobold.py","docs/selection/index.html"])
subprocess.run(["git","commit","-q","-m","DECOACCUM生成画像を太い線・塗りなしに整えて線化 (extract_lines_bold, DECOBOLD)"])
for _ in range(6):
    if subprocess.run(["git","push","origin",BR]).returncode==0: break
    time.sleep(12)
print("DECOBOLD_DONE",flush=True)
