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
# argv: [1]=disp接尾(既定NEW3) [2]=combo sid(既定new_combo) [3]=push(1で commit/push)
SUF=sys.argv[1] if len(sys.argv)>1 else "NEW3"
COMBO_SID=sys.argv[2] if len(sys.argv)>2 else "new_combo"
DO_PUSH=len(sys.argv)>3 and sys.argv[3]=="1"
SRC=ROOT/f"sketch_variations/disp_2026-06-02-{SUF}"
OUT=ROOT/f"sketch_variations/disp_2026-06-02-{SUF}COMBO"
BR="claude/style-pool-rebalance-20260529"
# 元画像 IMG_4368 (1179x1347) 内の各下書き bbox (split_new3 の検出値)
OW,OH=1179,1347
# webapp 合成は candidate を 704x1472 に stretch / input は contain で描く。
# → candidate も 704x1472 フレームに、 元レイアウトを「input と同じ contain 写像」で配置。
PW,PH=704,1472
SCALE=min(PW/OW, PH/OH); XOFF=(PW-OW*SCALE)/2; YOFF=(PH-OH*SCALE)/2
BBOX={"new_tree_a":(96,13,452,647),
      "new_tree_b":(666,104,450,683),
      "new_car":(43,771,979,532)}
SEEDS=[0,1,2]
from scripts.gen_routed import _save_candidate
from modules.stroke_order import order_strokes_center_out
CENTER=(PW/2.0, PH/2.0)   # 合成キャンバス中心 (描画始点の基準)

def remap(strokes, bx,by,bw,bh):
    pts=[p for st in strokes for p in st]
    if not pts: return []
    xs=[p[0] for p in pts]; ys=[p[1] for p in pts]
    sx0,sy0,sx1,sy1=min(xs),min(ys),max(xs),max(ys)
    sw,sh=max(sx1-sx0,1),max(sy1-sy0,1)
    s=min(bw/sw, bh/sh)                       # contain
    ox=bx+(bw-sw*s)/2 - sx0*s; oy=by+(bh-sh*s)/2 - sy0*s
    return [[(x*s+ox, y*s+oy) for x,y in st] for st in strokes]

# 入力は元 IMG_4368 そのまま (webapp が contain 表示 → candidate の contain と一致)
shutil.copy(ROOT/"assets/IMG_4368.jpg", ROOT/"sketch_variations/_inputs/new_combo.png")

if OUT.exists(): shutil.rmtree(OUT)
for seed in SEEDS:
    obj_lists=[]   # オブジェクト単位の strokes (1つ描き切ってから次へ、 を保つ)
    for sid,(bx,by,bw,bh) in BBOX.items():
        cand=list(SRC.glob(f"{sid}/v*_seed{seed}"))
        if not cand:
            print("missing",sid,seed); continue
        st=json.load(open(cand[0]/"strokes.json"))["strokes"]
        # bbox を 704x1472 パネルフレームへ contain 写像
        tb=(bx*SCALE+XOFF, by*SCALE+YOFF, bw*SCALE, bh*SCALE)
        obj_lists.append(remap(st,*tb))
    # 中心→外側・オブジェクト単位・連続化した描画順に並べ替え (ロボット滑らか描画用)
    combined=order_strokes_center_out(obj_lists,CENTER)
    d0=(((combined[0][0][0]-CENTER[0])**2+(combined[0][0][1]-CENTER[1])**2)**0.5
        if combined else -1)
    outd=OUT/COMBO_SID/f"v{seed+1}_seed{seed}"
    _save_candidate(outd,combined,PW,PH,generated=None,meta={"sid":COMBO_SID,
        "route":"new3_recombine","variant":f"combo_seed{seed}","seed":seed,
        "source":str(SRC.name),"placed_at":"original_bbox_contain",
        "stroke_order":"center_out_per_object","center":[round(CENTER[0]),round(CENTER[1])],
        "start_dist_from_center_px":round(d0,1),
        "note":f"{SUF}の3生成を元レイアウトへ合成。 描画順=中心最寄りから外側へ(オブジェクト単位)"})
    print(f"combo seed{seed}: {len(combined)} strokes, start {d0:.0f}px from center -> {outd}",flush=True)
if DO_PUSH:
    subprocess.run(["./venv/bin/python","-m","scripts.build_selection_webapp"])
    subprocess.run(["git","add",str(OUT),str(SRC),"sketch_variations/_inputs/new_combo.png",
                    "scripts/build_selection_webapp.py","docs/selection/index.html"])
    subprocess.run(["git","commit","-q","-m",f"{SUF}: 3生成を元レイアウトへ合成 ({COMBO_SID})"])
    for _ in range(6):
        if subprocess.run(["git","push","origin",BR]).returncode==0: break
        time.sleep(12)
print("RECOMBINE_DONE",flush=True)
