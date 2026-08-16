"""松本大洋スタイル FLUX LoRA 用データセット作成。
training/matsumoto_taiyo/raw/ の混在形式(jpg/png/gif/webp)を RGB 化し、
短辺を 768 にリサイズ→中央 768 正方クロップして出力。 style LoRA 用 (単一 instance_prompt)。
"""
import sys
from pathlib import Path
from PIL import Image, ImageSequence
ROOT=Path("/home/jizaiedev2026/draw_piper")
SRC=ROOT/"training/matsumoto_taiyo/raw"
OUT=ROOT/"sketch_variations/matsumoto_lora_dataset"
S=768
OUT.mkdir(parents=True,exist_ok=True)
for f in OUT.glob("*.png"): f.unlink()
n=0
for p in sorted(SRC.iterdir()):
    if not p.is_file(): continue
    try:
        im=Image.open(p)
        if getattr(im,"is_animated",False):           # gif → 1 フレーム目
            im=next(ImageSequence.Iterator(im))
        im=im.convert("RGB")
    except Exception as e:
        print("skip",p.name,repr(e)); continue
    w,h=im.size
    s=S/min(w,h)
    im=im.resize((max(S,int(round(w*s))),max(S,int(round(h*s)))),Image.LANCZOS)
    w,h=im.size
    l,t=(w-S)//2,(h-S)//2
    im=im.crop((l,t,l+S,t+S))
    n+=1
    im.save(OUT/f"mt_{n:03d}.png")
print(f"DATASET {n} imgs -> {OUT}")
