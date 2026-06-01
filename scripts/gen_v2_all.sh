#!/usr/bin/env bash
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT"
PY="$ROOT/venv/bin/python"; BRANCH="claude/style-pool-rebalance-20260529"
TS="$(date +%Y%m%d_%H%M%S)"; BASE="sketch_variations/disp_v2_$TS"
LOG="$ROOT/logs/gen_v2_$TS.log"
INPUTS=(B_round_smiley C_face_with_neck D_stick_figure F_angry_face portrait_person
        scatter:scatter_input house tree cat car circle
        tree_big cat_big house_big car_big)
log(){ echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }
log "=== gen_v2_all start $TS (${#INPUTS[@]} inputs) ==="
for item in "${INPUTS[@]}"; do
  sid="${item%%:*}"; file="${item##*:}"; [ "$sid" = "$file" ] && file="$sid"
  path="sketch_variations/_inputs/${file}.png"
  log "##### $sid #####"
  timeout 1000 "$PY" -m scripts.gen_routed --input "$path" --sid "$sid" \
    --output-base "$BASE" --n 3 >>"$LOG" 2>&1
  log "  $sid rc=$?"
done
# 旧 disp_routed / disp_bigobj は v2 で置換 → 削除
rm -rf sketch_variations/disp_routed_20260601_114254 sketch_variations/disp_bigobj_*
log "=== rebuild + push ==="
"$PY" -m scripts.build_selection_webapp >>"$LOG" 2>&1
git add "$BASE" docs/selection/index.html >>"$LOG" 2>&1
git rm -r --cached --quiet sketch_variations/disp_routed_20260601_114254 sketch_variations/disp_bigobj_* 2>/dev/null
git add -A sketch_variations/ >>"$LOG" 2>&1
git commit -q -m "gen_v2: 全入力を framed/stylize/scatter 統合パイプラインで再生成 $TS" >>"$LOG" 2>&1
git push origin "$BRANCH" >>"$LOG" 2>&1
log "  push rc=$?"; log "=== gen_v2_all DONE $TS ==="
