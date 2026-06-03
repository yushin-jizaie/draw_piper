"""現在(M19)のプロンプト方式で input 画像から生成 prompt を計算し file に書き出す。
prompt = "{describe_scene→design_instruction(complete)} {manga default style}"
(SDXL align ルート用なので FLUX 用 trigger 'tklineart' は付けない)
usage: _compute_prompt.py <img> <outfile>
"""
import sys
from pathlib import Path
from PIL import Image
ROOT = Path("/home/jizaiedev2026/draw_piper"); sys.path.insert(0, str(ROOT))
import os; os.chdir(ROOT)
STYLE = "manga style, clean bold ink lineart, white background, appealing design, multiple"
img_path, out = sys.argv[1], sys.argv[2]
from modules.vlm import VLM
vlm = VLM(verbose=True)
inp = Image.open(img_path).convert("RGB")
scene = vlm.describe_scene(inp) or "subject"
vision = vlm.design_instruction(inp, scene, mode="complete") or scene
prompt = f"{vision} {STYLE}"
Path(out).write_text(prompt, encoding="utf-8")
print("[prompt] scene:", scene)
print("[prompt] PROMPT:", prompt)
