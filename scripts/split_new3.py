"""assets/IMG_4368.jpg の3つの手描き(木A/木B/車)を connected components で領域分割し、
各々を白地黒線の単体入力 PNG として sketch_variations/_inputs/ に保存。
"""
import sys
from pathlib import Path
import numpy as np, cv2
from PIL import Image
ROOT=Path("/home/jizaiedev2026/draw_piper")
SRC=ROOT/"assets/IMG_4368.jpg"
OUTD=ROOT/"sketch_variations/_inputs"
PAD=40
g=cv2.cvtColor(cv2.imread(str(SRC)),cv2.COLOR_BGR2GRAY)
H,W=g.shape
binv=cv2.threshold(g,200,255,cv2.THRESH_BINARY_INV)[1]      # 線=255
# 同一ドローイング内のストロークを連結するため大きく dilate
dil=cv2.dilate(binv,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(35,35)),iterations=2)
n,lab,stats,cent=cv2.connectedComponentsWithStats(dil,8)
comps=[i for i in range(1,n) if stats[i,cv2.CC_STAT_AREA]>5000]
comps=sorted(comps,key=lambda i:-stats[i,cv2.CC_STAT_AREA])[:3]   # 大きい順 top3
# 位置でラベル: y 小2つ=木(x で A/B)、 y 大=車
boxes=[]
for i in comps:
    x,y,w,h,_=stats[i]; cx,cy=cent[i]; boxes.append((x,y,w,h,cx,cy))
boxes_sorted=sorted(boxes,key=lambda b:b[5])     # cy 昇順
trees=sorted(boxes_sorted[:2],key=lambda b:b[4]) # 上2つを x 昇順
car=boxes_sorted[2]
named=[("new_tree_a",trees[0]),("new_tree_b",trees[1]),("new_car",car)]
for name,(x,y,w,h,cx,cy) in named:
    x0,y0=max(0,x-PAD),max(0,y-PAD); x1,y1=min(W,x+w+PAD),min(H,y+h+PAD)
    crop=binv[y0:y1,x0:x1]                        # 線=255 の crop
    out=255-crop                                  # 白地黒線へ反転
    Image.fromarray(out).convert("RGB").save(OUTD/f"{name}.png")
    print(f"{name}: bbox=({x},{y},{w},{h}) -> {OUTD/(name+'.png')} size={out.shape[::-1]}",flush=True)
# 確認用モンタージュ
montage=Image.new("RGB",(W,H),(255,255,255))
mg=Image.fromarray(255-binv).convert("RGB"); montage.paste(mg,(0,0))
montage.save("/tmp/new3_check.png")
print("DONE split",flush=True)
