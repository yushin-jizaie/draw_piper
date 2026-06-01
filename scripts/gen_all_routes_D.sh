#!/usr/bin/env bash
# 勝ちルート (stylize / framed / scatter+direct) を全サンプルに通して 2026-06-01-D へ。
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT"
PY="$ROOT/venv/bin/python"; BR="claude/style-pool-rebalance-20260529"
BASE="sketch_variations/disp_2026-06-01-D"; LOG="$ROOT/logs/genD_$(date +%Y%m%d_%H%M%S).log"
# sid:file:category(person/object)
INPUTS=(
  "B_round_smiley:B_round_smiley:person" "C_face_with_neck:C_face_with_neck:person"
  "D_stick_figure:D_stick_figure:person" "F_angry_face:F_angry_face:person"
  "portrait_person:portrait_person:person" "scatter:scatter_input:person"
  "house:house:object" "tree:tree:object" "cat:cat:object" "car:car:object"
  "circle:circle:object" "tree_big:tree_big:object" "cat_big:cat_big:object"
  "house_big:house_big:object" "car_big:car_big:object"
)
log(){ echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }
run(){ timeout 900 "$PY" -m scripts.gen_routed "$@" >>"$LOG" 2>&1; log "  rc=$?"; }
log "=== genD start (${#INPUTS[@]} inputs × 3 routes) ==="
for e in "${INPUTS[@]}"; do
  IFS=: read -r sid file cat <<< "$e"
  p="sketch_variations/_inputs/${file}.png"
  log "##### $sid ($cat) #####"
  log "  stylize"; run --input "$p" --sid "$sid" --force-route stylize --category "$cat" --output-base "$BASE" --n 3
  log "  framed";  run --input "$p" --sid "$sid" --force-route framed  --category "$cat" --output-base "$BASE" --n 3
  log "  scatter+direct"; run --input "$p" --sid "$sid" --force-route companion --output-base "$BASE" --n 3
done
log "=== rebuild + push ==="
"$PY" -m scripts.build_selection_webapp >>"$LOG" 2>&1
git add "$BASE" docs/selection/index.html >>"$LOG" 2>&1
git commit -q -m "2026-06-01-D: 勝ちルート(stylize/framed/scatter/direct)を全サンプルに通して生成" >>"$LOG" 2>&1
git push origin "$BR" >>"$LOG" 2>&1
log "  push rc=$?"; echo "GEND_DONE" | tee -a "$LOG"
