"""align(S2 OFF)×現在プロンプトの生成結果(/tmp/align_out)を webapp(new_combo_align)へ。
20_final を vectorize して strokes.json(image_shape付) を作り、 disp dir に配置。
"""
import sys, json, shutil, subprocess, time
from pathlib import Path
from PIL import Image
ROOT=Path("/home/jizaiedev2026/draw_piper"); sys.path.insert(0,str(ROOT))
import os; os.chdir(ROOT)
SRC=Path("/tmp/align_out")
OUT=ROOT/"sketch_variations/disp_2026-06-04-ALIGN/new_combo_align/v1_seed42"
BR="claude/style-pool-rebalance-20260529"
OUT.mkdir(parents=True,exist_ok=True)
from modules.vectorizer import Vectorizer, load_binarize_config
vc=Vectorizer(gen_line_mode="canny_centerline",**load_binarize_config())
gen=Image.open(SRC/"20_final_no_ip_adapter.png").convert("RGB"); W,H=gen.size
r=vc.vectorize(generated_image=gen,user_image=None)
json.dump({"image_shape":[H,W],"n_strokes":r.n_strokes,
           "n_points":sum(len(s) for s in r.strokes),"strokes":r.strokes},
          open(OUT/"strokes.json","w"))
shutil.copy(SRC/"20_final_no_ip_adapter.png", OUT/"generated.png")
shutil.copy(SRC/"30_vectorized_strokes.png", OUT/"30_vectorized_strokes.png")
(OUT/"00_meta.json").write_text(json.dumps({"sid":"new_combo_align","route":"align_S2off_currentprompt",
  "note":"align(S2 OFF)=illustrious inpaint+MistoLine, IP-Adapterなし。 現在(M19)プロンプト。 分割せず1枚。 ※SDXL CLIP77でlineartスタイル語が切れ写真的に",
  "prompt":Path("/tmp/align_prompt.txt").read_text()},ensure_ascii=False))
shutil.copy(ROOT/"assets/IMG_4368.jpg", ROOT/"sketch_variations/_inputs/new_combo_align.png")
print(f"strokes={r.n_strokes} image_shape=[{H},{W}] -> {OUT}",flush=True)
subprocess.run(["./venv/bin/python","-m","scripts.build_selection_webapp"])
subprocess.run(["git","add","sketch_variations/disp_2026-06-04-ALIGN","sketch_variations/_inputs/new_combo_align.png",
                "scripts/build_selection_webapp.py","scripts/test_companion_mode.py" if False else "scripts/_compute_prompt.py",
                "scripts/finalize_align_webapp.py","docs/selection/index.html"])
subprocess.run(["git","commit","-q","-m","align(S2 OFF)×現在プロンプトでIMG_4368を分割せず1枚生成 (SDXL CLIP77でlineart切れ→写真的)"])
for _ in range(6):
    if subprocess.run(["git","push","origin",BR]).returncode==0: break
    time.sleep(12)
print("ALIGN_WEBAPP_DONE",flush=True)
