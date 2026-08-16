import json, numpy as np
from pathlib import Path
from PIL import Image
ROOT=Path("/home/jizaiedev2026/draw_piper")
OUT=ROOT/"sketch_variations/lora_winners_dataset"; OUT.mkdir(parents=True, exist_ok=True)
data=json.load(open("/tmp/sel.json"))
SIZE=768; n=0
seen=set()
for subj,d in data.items():
    for s in d["selections"]:
        rel=s["strokes_png_rel"]; p=ROOT/rel
        if not p.exists() or rel in seen: continue
        seen.add(rel)
        img=Image.open(p).convert("L"); a=np.array(img)
        ys,xs=np.where(a<128)
        if len(xs)<5: continue
        x0,y0,x1,y1=xs.min(),ys.min(),xs.max(),ys.max()
        bw,bh=x1-x0,y1-y0
        m=int(max(bw,bh)*0.08)
        x0,y0=max(0,x0-m),max(0,y0-m); x1,y1=min(a.shape[1],x1+m),min(a.shape[0],y1+m)
        crop=img.crop((x0,y0,x1,y1))
        cw,ch=crop.size; sc=SIZE*0.9/max(cw,ch)
        nw,nh=max(1,int(cw*sc)),max(1,int(ch*sc))
        crop=crop.resize((nw,nh), Image.LANCZOS)
        canvas=Image.new("L",(SIZE,SIZE),255)
        canvas.paste(crop,((SIZE-nw)//2,(SIZE-nh)//2))
        canvas.convert("RGB").save(OUT/f"win_{n:03d}.png"); n+=1
print("dataset images:", n, "->", OUT)
