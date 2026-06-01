#!/usr/bin/env bash
# 2026-06-01-E: 旧パターン gacha(two_stage character) + shift(関連コンパニオン) を
# 全サンプルで再現 (gacha は framed との比較用)。 D 完了を待ってから実行。
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT"
PY="$ROOT/venv/bin/python"; BR="claude/style-pool-rebalance-20260529"
BASE="sketch_variations/disp_2026-06-01-E"; LOG="$ROOT/logs/genE_$(date +%Y%m%d_%H%M%S).log"
log(){ echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }
# D 完了待ち
log "waiting for D (GEND_DONE)..."
until grep -q "GEND_DONE" "$ROOT"/logs/genD_*.log 2>/dev/null; do sleep 120; done
log "D done. starting E."
INPUTS=(
  "B_round_smiley:B_round_smiley" "C_face_with_neck:C_face_with_neck"
  "D_stick_figure:D_stick_figure" "F_angry_face:F_angry_face"
  "portrait_person:portrait_person" "scatter:scatter_input"
  "house:house" "tree:tree" "cat:cat" "car:car" "circle:circle"
  "tree_big:tree_big" "cat_big:cat_big" "house_big:house_big" "car_big:car_big"
)
for e in "${INPUTS[@]}"; do
  IFS=: read -r sid file <<< "$e"
  p="sketch_variations/_inputs/${file}.png"
  log "##### $sid : gacha #####"
  timeout 1200 "$PY" -m scripts.generate_gacha --user-sketch "$p" --category character \
    --n 3 --auto-prompt --output "$BASE/$sid" --resolution 768x768 --master-seed 100 >>"$LOG" 2>&1
  log "  gacha rc=$?"
  log "##### $sid : shift #####"
  timeout 900 "$PY" -m scripts.test_companion_mode --user-sketch "$p" --placement shift \
    --auto-prompt --output "$BASE/${sid}_shift" --resolution 704x1472 >>"$LOG" 2>&1
  log "  shift rc=$?"
done
# gacha の stage2 raster を generated.png にコピー (modal 比較用)
log "copy stage2 -> generated.png"
for f in "$BASE"/*/v*_seed*/20_stage2_*.png; do
  [ -f "$f" ] && cp "$f" "$(dirname "$f")/generated.png" 2>/dev/null || true
done
log "=== rebuild + push ==="
"$PY" -m scripts.build_selection_webapp >>"$LOG" 2>&1
git add "$BASE" docs/selection/index.html >>"$LOG" 2>&1
git commit -q -m "2026-06-01-E: 旧gacha(two_stage)+shift(関連コンパニオン)を全サンプル再現 (framed比較用)" >>"$LOG" 2>&1
git push origin "$BR" >>"$LOG" 2>&1
log "  push rc=$?"; echo "GENE_DONE" | tee -a "$LOG"
