#!/bin/bash
# v4 LoRA エンドツーエンド学習スクリプト (2026-05-28 作成)
# 過去の v0-v3 (黒テクスチャ + 文字暴走) を回避するため、 厳格 binarize +
# 低 rank + 低 lr + 短 steps で 「線の質感だけ」 を学ばせる試み。
#
# 明日の朝の bash 一括承認時に これを 1 行で実行:
#   bash scripts/train_lora_v4.sh
#
# 完走後 (推定 ~22 分) は test_v4 関数で 推論テスト。
set -e
cd "$(dirname "$0")/.."

VENV=./venv/bin/python
DATASET_NAME=matsumoto_taiyo_v4
DST_LINEART=training/matsumoto_taiyo/lineart_v4_binary
DST_DATASET=training/matsumoto_taiyo/dataset_v4
LOG_DIR=training/lora_runs/v4_$(date +%Y%m%d_%H%M%S)

echo "=== STEP 1: 厳格 binarize ==="
$VENV -m scripts.binarize_lineart_v4 \
    --input  training/matsumoto_taiyo/lineart_b \
    --output "$DST_LINEART" \
    --threshold 50

echo ""
echo "=== STEP 2: dataset 準備 (BLIP-2 caption は使わず trigger only) ==="
$VENV -m scripts.prepare_style_dataset \
    --input  "$DST_LINEART" \
    --output "$DST_DATASET" \
    --trigger mt_taiyo_style --no-caption

# v0 から復元した dataset の caption (BLIP-2 出力) があれば cp
if [ -d "training/matsumoto_taiyo/dataset" ]; then
    for txt in training/matsumoto_taiyo/dataset/*.txt; do
        stem=$(basename "$txt" .txt)
        if [ -f "$DST_DATASET/$stem.png" ]; then
            cp "$txt" "$DST_DATASET/$stem.txt"
        fi
    done
fi
# orphan txt 掃除
for txt in "$DST_DATASET"/*.txt; do
    png="${txt%.txt}.png"
    [ -f "$png" ] || rm "$txt"
done
echo "dataset: $(ls "$DST_DATASET" | wc -l) files ($(ls "$DST_DATASET"/*.png | wc -l) png)"

echo ""
echo "=== STEP 3: LoRA v4 学習 (rank 8, lr 5e-5, steps 600) ==="
# 既存 LoRA は退避
if [ -f training/lora/matsumoto_taiyo.safetensors ]; then
    cp training/lora/matsumoto_taiyo.safetensors \
       training/lora/matsumoto_taiyo_v0_backup_$(date +%Y%m%d).safetensors
fi
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/train.log"
echo "log: $LOG"

# 学習: 低 rank で「テクスチャ暗記」 を物理的に抑制、 低 lr で 過学習回避
nohup $VENV -m scripts.train_style_lora \
    --dataset "$DST_DATASET" \
    --name matsumoto_taiyo \
    --base cagliostrolab/animagine-xl-3.1 \
    --rank 8 --steps 600 --lr 5e-5 \
    > "$LOG" 2>&1 &
PID=$!
disown
echo "PID: $PID, log: $LOG"
echo "[$DATASET_NAME] training launched. ~22 min ETA."
echo "tail with: tail -f $LOG"
echo ""
echo "完走後の確認:"
echo "  ps -p $PID                                              # 終了確認"
echo "  ls -lh training/lora/matsumoto_taiyo.safetensors       # 出力 (~45MB 想定)"
echo ""
echo "推論テスト (完走後):"
echo "  bash scripts/sweep_matsumoto_experiments.sh             # 全候補一気"
