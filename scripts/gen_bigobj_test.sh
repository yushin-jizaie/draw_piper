#!/usr/bin/env bash
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT"
PY="$ROOT/venv/bin/python"; BRANCH="claude/style-pool-rebalance-20260529"
TS="$(date +%Y%m%d_%H%M%S)"; BASE="sketch_variations/disp_bigobj_$TS"
LOG="$ROOT/logs/bigobj_$TS.log"
INPUTS=(
  "tree_big:sketch_variations/_inputs/tree_big.png"
  "cat_big:sketch_variations/_inputs/cat_big.png"
  "house_big:sketch_variations/_inputs/house_big.png"
  "car_big:sketch_variations/_inputs/car_big.png"
)
log(){ echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }
log "=== bigobj test start $TS ==="
for e in "${INPUTS[@]}"; do
  IFS=: read -r sid path <<< "$e"
  log "##### $sid #####"
  timeout 900 "$PY" -m scripts.gen_routed --input "$path" --sid "$sid" \
    --output-base "$BASE" --n 3 >>"$LOG" 2>&1
  log "  $sid rc=$?"
done
log "=== rebuild + push ==="
"$PY" -m scripts.build_selection_webapp >>"$LOG" 2>&1
git add "$BASE" docs/selection/index.html >>"$LOG" 2>&1
git commit -q -m "bigobj test: 大オブジェクトのルーティング生成 (縦長→stylize/横長→scatter) $TS" >>"$LOG" 2>&1
git push origin "$BRANCH" >>"$LOG" 2>&1
log "  push rc=$?"; log "=== bigobj DONE $TS ==="
