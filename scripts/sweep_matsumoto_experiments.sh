#!/bin/bash
# 「松本大洋画風 獲得」 を狙う 全実験を 1 行 で 順次実行する master script。
# 明日の朝 まとめて bash 承認時に これを kick:
#   bash scripts/sweep_matsumoto_experiments.sh
#
# 全試行を 1 つの出力 dir に集約 → grid で比較できるよう同じ seed = 42 で固定。
#
# 想定所要時間:
#   - LoRA 系 inference  各 30 秒 × 9 種 ≈ 5 分
#   - IP-Adapter 系     各 60 秒 (load 込み) × 12 種 ≈ 12 分
#   - 合計             ~17 分 (training は別)
#
# 出力構造:
#   logs/matsumoto_sweep_YYYYMMDD_HHMMSS/
#     ├ A_existing_lora/          (Phase A: LineAniRedmond + v0 LoRA scales)
#     ├ B_v4_lora/                (Phase B: v4 LoRA scales)  ← 学習完走後のみ
#     ├ C_ip_adapter/             (Phase C: IP-Adapter + style refs)
#     └ summary.md                (結果まとめ)

set -e
cd "$(dirname "$0")/.."

VENV=./venv/bin/python
TS=$(date +%Y%m%d_%H%M%S)
OUT_ROOT=logs/matsumoto_sweep_$TS
mkdir -p "$OUT_ROOT"

USER_SKETCH=scripts/test_sketch.jpg
PROMPT="1boy, solo, young boy with full body, messy hair, surprised expression, simple t-shirt, standing"
SEED=42

echo "=============================================="
echo "[$TS] 松本大洋画風 sweep 実験"
echo "Output: $OUT_ROOT"
echo "=============================================="

# ----------------------------------------------------------
# Phase A: 既存 LoRA (LineAniRedmond + v0 matsumoto LoRA) sweep
# ----------------------------------------------------------
echo ""
echo "[Phase A] 既存 LoRA sweep (LineAniRedmond + v0)"
mkdir -p "$OUT_ROOT/A_existing_lora"

# A1. LoRA off (baseline = 現状の clean line art 達成版)
$VENV -m scripts.compare_imagegen_models \
    --guide "$USER_SKETCH" --prompt "$PROMPT" \
    --presets illustrious_v2_inpaint --seed $SEED \
    --out "$OUT_ROOT/A_existing_lora/A1_no_lora" || echo "A1 failed"

# A2. LineAniRedmond at scale 1.0
$VENV -m scripts.compare_imagegen_models \
    --guide "$USER_SKETCH" --prompt "$PROMPT" \
    --presets illustrious_v2_inpaint_lineani --seed $SEED \
    --out "$OUT_ROOT/A_existing_lora/A2_lineani_10" || echo "A2 failed"

# A3-5. v0 LoRA at scale 0.3 / 0.7 / 1.0
for SCALE in 0.3 0.7 1.0; do
    $VENV -m scripts.compare_imagegen_models \
        --guide "$USER_SKETCH" --prompt "$PROMPT" \
        --presets illustrious_v2_inpaint_mt --seed $SEED \
        --lora-scale $SCALE \
        --out "$OUT_ROOT/A_existing_lora/A_v0lora_${SCALE//./}" || echo "v0lora $SCALE failed"
done

# ----------------------------------------------------------
# Phase B: v4 LoRA (新規学習) sweep — 学習が完走してれば
# ----------------------------------------------------------
echo ""
echo "[Phase B] v4 LoRA sweep — 学習結果次第"
mkdir -p "$OUT_ROOT/B_v4_lora"
if [ -f training/lora/matsumoto_taiyo.safetensors ]; then
    # 既存と区別するため、 v4 用 preset (modules/image_gen.py に追加予定) を使う
    for SCALE in 0.5 0.8 1.2; do
        $VENV -m scripts.compare_imagegen_models \
            --guide "$USER_SKETCH" --prompt "$PROMPT" \
            --presets illustrious_v2_inpaint_mt --seed $SEED \
            --lora-scale $SCALE \
            --out "$OUT_ROOT/B_v4_lora/B_v4_${SCALE//./}" || echo "v4 $SCALE failed"
    done
else
    echo "  SKIPPED: training/lora/matsumoto_taiyo.safetensors not found"
fi

# ----------------------------------------------------------
# Phase C: IP-Adapter + 異なる style references
# ----------------------------------------------------------
echo ""
echo "[Phase C] IP-Adapter style transfer"
mkdir -p "$OUT_ROOT/C_ip_adapter"

# style ref 候補 (raw 36 枚から「比較的線が clean な」 5 枚を選定)
STYLE_REFS=(
    "training/matsumoto_taiyo/raw/IMG_4321.JPG"           # ナンバーファイブ街並 2人
    "training/matsumoto_taiyo/raw/IMG_4311.JPG"           # 花男表紙 2選手
    "training/matsumoto_taiyo/raw/feccbf2756d31b496b18e31694969146.jpg"  # Peco 靴ひも
    "training/matsumoto_taiyo/raw/o0600045013450720343.jpg"  # 5人並び
    "training/matsumoto_taiyo/raw/f341cbadd1aede96e2fdef7bc84cc3c6.jpg"  # 3人正面
)

for REF in "${STYLE_REFS[@]}"; do
    NAME=$(basename "$REF" | sed 's/\..*$//' | cut -c1-20)
    for IP_SCALE in 0.4 0.7; do
        OUT="$OUT_ROOT/C_ip_adapter/${NAME}_ip${IP_SCALE//./}"
        $VENV -m scripts.test_ip_adapter_style \
            --user-sketch "$USER_SKETCH" \
            --style-ref "$REF" \
            --prompt "$PROMPT" \
            --ip-scale $IP_SCALE --seed $SEED \
            --output "$OUT" 2>&1 | tail -2 || echo "IP-Adapter $NAME $IP_SCALE failed"
    done
done

# ----------------------------------------------------------
# Phase D: 合算 grid + summary
# ----------------------------------------------------------
echo ""
echo "[Phase D] summary"
SUMMARY="$OUT_ROOT/summary.md"
{
    echo "# Matsumoto sweep $TS"
    echo ""
    echo "## 出力一覧"
    find "$OUT_ROOT" -name "*.png" | sort
    echo ""
    echo "## 評価観点"
    echo "- 線の質感 (rough sketchy vs clean smooth)"
    echo "- 表情の expressivity (松本的か?)"
    echo "- ハッチング/塗りつぶし の混入有無 (Vectorizer ノイズ源)"
    echo "- 顔輪郭の保持精度"
    echo "- 全体の白背景率"
} > "$SUMMARY"
cat "$SUMMARY"

echo ""
echo "=============================================="
echo "Sweep 完了: $OUT_ROOT"
echo "=============================================="
