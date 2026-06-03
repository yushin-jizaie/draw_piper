"""ストローク描画順の可視化。 new_combo_m の strokes.json を描画順どおりに
色グラデーション(始め=赤→終わり=青)で描き、 中心十字 / ペン移動線 / START・END
マーカーを重ねる。 webapp(disp_2026-06-02-ORDERVIZ/new_combo_m)で確認。
"""
import sys, json, shutil, subprocess, time, colorsys
from pathlib import Path
from PIL import Image, ImageDraw
ROOT=Path("/home/jizaiedev2026/draw_piper"); sys.path.insert(0,str(ROOT))
import os; os.chdir(ROOT)
PW,PH=704,1472; CX,CY=PW/2,PH/2
BR="claude/style-pool-rebalance-20260529"
SRC=ROOT/"sketch_variations/disp_2026-06-02-NEW3MCOMBO/new_combo_m"
OUT=ROOT/"sketch_variations/disp_2026-06-02-ORDERVIZ/new_combo_m"
if OUT.parent.exists(): shutil.rmtree(OUT.parent)
def hsv(i,n):
    h=0.0+ (i/max(n-1,1))*0.66   # 0=赤 → 0.66=青
    r,g,b=colorsys.hsv_to_rgb(h,0.95,0.9); return (int(r*255),int(g*255),int(b*255))
for vdir in sorted(SRC.glob("v*_seed*")):
    strokes=json.load(open(vdir/"strokes.json"))["strokes"]
    im=Image.new("RGB",(PW,PH),(255,255,255)); d=ImageDraw.Draw(im)
    # 中心十字
    d.line([(CX-18,CY),(CX+18,CY)],fill=(170,170,170),width=2)
    d.line([(CX,CY-18),(CX,CY+18)],fill=(170,170,170),width=2)
    d.ellipse([CX-22,CY-22,CX+22,CY+22],outline=(170,170,170),width=1)
    n=len(strokes); prev=None
    for i,st in enumerate(strokes):
        col=hsv(i,n)
        if prev is not None:   # ペン移動 (前stroke終点→今stroke始点) を薄線で
            d.line([prev,tuple(st[0])],fill=(210,210,210),width=1)
        if len(st)>=2:
            d.line([tuple(p) for p in st],fill=col,width=2,joint="curve")
        prev=tuple(st[-1])
    # START / END
    s0=tuple(strokes[0][0]); e=tuple(strokes[-1][-1])
    d.ellipse([s0[0]-9,s0[1]-9,s0[0]+9,s0[1]+9],fill=(0,180,0),outline=(0,90,0),width=2)
    d.text((s0[0]+11,s0[1]-6),"START",fill=(0,120,0))
    d.ellipse([e[0]-8,e[1]-8,e[0]+8,e[1]+8],fill=(220,0,0),outline=(120,0,0),width=2)
    d.text((e[0]+10,e[1]-6),"END",fill=(170,0,0))
    d.text((8,8),f"draw order: {n} strokes  red->blue  (center-out, per-object)",fill=(60,60,60))
    od=OUT/vdir.name; od.mkdir(parents=True,exist_ok=True)
    im.save(od/"30_vectorized_strokes.png")
    shutil.copy(vdir/"strokes.json", od/"strokes.json")
    (od/"00_meta.json").write_text(json.dumps({"sid":"new_combo_m","route":"order_viz",
        "variant":f"order_{vdir.name}","note":"描画順可視化: 赤=始め→青=終わり, 中心十字, ペン移動=薄線"},ensure_ascii=False))
    print("viz",vdir.name,n,"strokes",flush=True)
subprocess.run(["./venv/bin/python","-m","scripts.build_selection_webapp"])
subprocess.run(["git","add","sketch_variations/disp_2026-06-02-ORDERVIZ","docs/selection/index.html","scripts/viz_stroke_order.py"])
subprocess.run(["git","commit","-q","-m","描画順の可視化(中心→外側, 色グラデ+START/END+ペン移動線) を webapp に追加"])
for _ in range(6):
    if subprocess.run(["git","push","origin",BR]).returncode==0: break
    time.sleep(12)
print("ORDERVIZ_DONE",flush=True)
