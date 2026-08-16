#!/usr/bin/env bash
# D/E 生成中、 15分ごとに「ストロークまで生成できた候補」 を rebuild→commit→push。
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT"
PY="$ROOT/venv/bin/python"; BR="claude/style-pool-rebalance-20260529"
LOG="$ROOT/logs/incrpush_$(date +%Y%m%d_%H%M%S).log"
log(){ echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }
gp(){ for i in 1 2 3 4 5; do git push origin "$BR" >>"$LOG" 2>&1 && return 0; sleep 15; done; log "push retry尽きた"; }
flush(){
  "$PY" -m scripts.build_selection_webapp >>"$LOG" 2>&1 || log "build失敗(続行)"
  git add sketch_variations/disp_2026-06-01-[A-Z]* \
          docs/selection/index.html >>"$LOG" 2>&1 || true
  if ! git diff --cached --quiet 2>/dev/null; then
    local n=$(find sketch_variations/disp_2026-06-01-[A-Z]* \
               -name "30_*strokes.png" 2>/dev/null | wc -l)
    git commit -q -m "incremental: 生成済み候補を順次push (計${n}候補, $(date +%H:%M))" >>"$LOG" 2>&1 || true
    gp; log "pushed (累計 ${n} 候補)"
  else
    log "新規なし (skip)"
  fi
}
log "=== incremental push 開始 (15分間隔) ==="
while true; do
  sleep 900
  flush
  if grep -q "GENE_DONE" "$ROOT"/logs/genE_*.log 2>/dev/null; then
    sleep 30; flush; log "=== E 完了 → 最終 push して終了 ==="; echo "INCR_DONE"; break
  fi
done
