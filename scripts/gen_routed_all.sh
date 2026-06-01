#!/usr/bin/env bash
# 統一ルーティング生成 (gen_routed) を残り全入力に展開。
# 既存 disp_routed_20260601_114254 に追記 (portrait_person / cat は生成済)。
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="$ROOT/venv/bin/python"
BRANCH="claude/style-pool-rebalance-20260529"
BASE="sketch_variations/disp_routed_20260601_114254"
LOG="$ROOT/logs/gen_routed_all_$(date +%Y%m%d_%H%M%S).log"
mkdir -p "$ROOT/logs"

# sid : 入力 png : 被写体語(prompt 差し込み)
INPUTS=(
  "B_round_smiley:sketch_variations/_inputs/B_round_smiley.png:a smiling round face character"
  "C_face_with_neck:sketch_variations/_inputs/C_face_with_neck.png:a character face"
  "D_stick_figure:sketch_variations/_inputs/D_stick_figure.png:1boy, full body"
  "F_angry_face:sketch_variations/_inputs/F_angry_face.png:an angry face character"
  "scatter:sketch_variations/_inputs/scatter_input.png:a character"
  "house:sketch_variations/_inputs/house.png:a house"
  "tree:sketch_variations/_inputs/tree.png:a tree"
  "car:sketch_variations/_inputs/car.png:a car"
  "circle:sketch_variations/_inputs/circle.png:a round creature"
)

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }
log "=== gen_routed_all start (${#INPUTS[@]} inputs) ==="
for entry in "${INPUTS[@]}"; do
  IFS=: read -r sid path subj <<< "$entry"
  log "##### $sid (subj=$subj) #####"
  timeout 900 "$PY" -m scripts.gen_routed \
      --input "$path" --sid "$sid" --subject "$subj" \
      --output-base "$BASE" --n 3 >>"$LOG" 2>&1
  log "  $sid rc=$?"
done

log "=== webapp 再ビルド + push ==="
"$PY" -m scripts.build_selection_webapp >>"$LOG" 2>&1
git add "$BASE" docs/selection/index.html >>"$LOG" 2>&1
git commit -q -m "gen_routed: 残り全入力にルーティング生成を展開 (統一レシピ)" >>"$LOG" 2>&1
git push origin "$BRANCH" >>"$LOG" 2>&1
log "  push rc=$?"
log "=== gen_routed_all DONE ==="
