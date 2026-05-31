#!/usr/bin/env bash
# 全パイプライン × 全サンプル を一晩で生成し、 選定 webapp に反映する。
# 経路: shift (companion 位置ずらし) / gacha (松本タッチ×3 seed) / scatter (撒く)。
# 各 item は subprocess + timeout で隔離し、 失敗しても継続 (set -e しない)。
# 最後に webapp 再ビルド + commit + push。
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="$ROOT/venv/bin/python"
RES="704x1472"
BRANCH="claude/style-pool-rebalance-20260529"
TS="$(date +%Y%m%d_%H%M%S)"
LOG="$ROOT/logs/overnight_batch_$TS.log"
SHIFT_DIR="sketch_variations/disp_overnight_shift_$TS"
GACHA_DIR="sketch_variations/disp_overnight_gacha_$TS"
SCAT_DIR="sketch_variations/disp_overnight_scatter_$TS"
mkdir -p "$ROOT/logs"

# sid : 入力 png : kind(character/object)
INPUTS=(
  "B_round_smiley:sketch_variations/_inputs/B_round_smiley.png:character"
  "C_face_with_neck:sketch_variations/_inputs/C_face_with_neck.png:character"
  "D_stick_figure:sketch_variations/_inputs/D_stick_figure.png:character"
  "F_angry_face:sketch_variations/_inputs/F_angry_face.png:character"
  "portrait_person:sketch_variations/_inputs/portrait_person.png:character"
  "scatter:sketch_variations/_inputs/scatter_input.png:character"
  "house:sketch_variations/_inputs/house.png:object"
  "tree:sketch_variations/_inputs/tree.png:object"
  "cat:sketch_variations/_inputs/cat.png:object"
  "car:sketch_variations/_inputs/car.png:object"
  "circle:sketch_variations/_inputs/circle.png:object"
)

log()  { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }
run()  { log "RUN $*"; timeout 700 "$@" >>"$LOG" 2>&1; log "  rc=$?"; }

log "=== overnight batch start: $TS (inputs=${#INPUTS[@]}) ==="
for entry in "${INPUTS[@]}"; do
  IFS=: read -r sid path kind <<< "$entry"
  log "##### INPUT: $sid ($kind) #####"
  # 1) shift (companion 位置ずらし)
  run "$PY" -m scripts.test_companion_mode \
      --user-sketch "$path" --output "$SHIFT_DIR/${sid}_shift" \
      --placement shift --auto-prompt --resolution "$RES"
  # 2) gacha (松本タッチ × 3 seed)
  run "$PY" -m scripts.generate_gacha \
      --user-sketch "$path" --category "$kind" --n 3 --auto-prompt \
      --output "$GACHA_DIR/$sid" --resolution "$RES" --master-seed 100
  # 3) scatter (キャラを撒く)
  run "$PY" -m scripts.scatter_companions \
      --input "$path" --output "$SCAT_DIR/$sid/v1_seed555" \
      --seed 555 --resolution "$RES"
done

log "=== webapp 再ビルド (online) ==="
"$PY" -m scripts.build_selection_webapp >>"$LOG" 2>&1
log "  rebuild rc=$?"

log "=== git commit + push ==="
git add sketch_variations/disp_overnight_shift_$TS \
        sketch_variations/disp_overnight_gacha_$TS \
        sketch_variations/disp_overnight_scatter_$TS \
        docs/selection/index.html >>"$LOG" 2>&1
git commit -q -m "overnight: 全パイプライン×全サンプル生成 ($TS)" >>"$LOG" 2>&1
log "  commit rc=$?"
git push origin "$BRANCH" >>"$LOG" 2>&1
log "  push rc=$?"
log "=== overnight batch DONE: $TS ==="
