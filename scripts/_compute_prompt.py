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
# 共通スタイル=モデル既定。 CLIP77 で末尾が切れても消えないよう prompt 先頭に置く。
# (SDXL は後段で preset の style_hint も末尾付与するが冗長・切れてOK)
DEFAULT_STYLE = "manga style, clean bold black ink lineart on white background"
img_path, out = sys.argv[1], sys.argv[2]
from modules.vlm import VLM
vlm = VLM(verbose=True)
inp = Image.open(img_path).convert("RGB")
scene = vlm.describe_scene(inp) or "subject"
vision = vlm.design_instruction(inp, scene, mode="complete") or scene
# スタイル(既定)を先頭、 デザイン指示(厚め)を本文に。
prompt = f"{DEFAULT_STYLE}, {vision}"
Path(out).write_text(prompt, encoding="utf-8")
print("[prompt] scene:", scene)
print("[prompt] PROMPT:", prompt)
