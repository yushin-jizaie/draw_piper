# 明朝起床時の Bash 承認用 runbook (2026-05-28 夜)

> このファイルだけ見れば朝の作業が完結します。 順に承認して実行ください。
> 各 step は独立、 結果が悪ければ次の手に進む形。

---

## 推奨実行順

### Step 0: 状態確認 (1 分)

```bash
cd ~/draw_piper
git status --short | grep -v venv/ | head -10
# 期待: M modules/image_gen.py, ?? scripts/binarize_lineart_v4.py 等の新ファイル
ls scripts/binarize_lineart_v4.py scripts/test_ip_adapter_style.py \
   scripts/train_lora_v4.sh scripts/sweep_matsumoto_experiments.sh \
   scripts/demo_wobble_sweep.py modules/stroke_wobble.py \
   modules/stroke_render.py docs/20260528_matsumoto_pursuit_plan.md
# 全部 存在すれば OK
```

### Step 1: モジュール自己テスト (GPU 不要、 数秒)

```bash
./venv/bin/python -m modules.stroke_wobble    # wobble 関数の動作確認
./venv/bin/python -m modules.stroke_render    # render 関数の動作確認 (出力 /tmp/)
```

期待: 「self test PASSED」 が出る。 NG なら module の bug、 require 確認。

### Step 2: wobble デモ (GPU 不要、 ~10 秒)

**LoRA 不要、 IP-Adapter 不要で 即「松本タッチ風 (rough)」 を試す**:

```bash
./venv/bin/python -m scripts.demo_wobble_sweep \
    --generated logs/imagegen_comparison_20260528_011016/illustrious_v2_inpaint.png \
    --user-sketch scripts/test_sketch.jpg \
    --output logs/wobble_demo_$(date +%Y%m%d_%H%M%S)
```

出力に compare_grid.png が出る。 wobble amp 1/3/5 + freq 0.02/0.05/0.10 の
比較 grid。 もし「これでいい」 感が出るなら、 LoRA / IP-Adapter スキップで
Plan F (wobble 後処理採用) で 完成可能。

### Step 3: 既存 LoRA + IP-Adapter sweep (~17 分、 GPU 使用)

```bash
bash scripts/sweep_matsumoto_experiments.sh
```

Phase A (LineAniRedmond + v0 LoRA, 5 試行) + Phase C (IP-Adapter, 10 試行) を
一気に。 結果は logs/matsumoto_sweep_<ts>/ に集約。

### Step 4: LoRA v4 新規学習 (~22 分、 GPU 使用、 Step 3 と並行 NG)

Step 3 が完走後:

```bash
bash scripts/train_lora_v4.sh
```

学習は background で走るので、 完走待ち。 完走後に v4 用 preset で
推論テスト:

```bash
./venv/bin/python -m scripts.compare_imagegen_models \
    --guide scripts/test_sketch.jpg \
    --prompt "1boy, solo, young boy with full body, messy hair, surprised expression, simple t-shirt, standing" \
    --presets illustrious_v2_inpaint_v4 --seed 42

# scale 振り (0.5, 0.8, 1.2)
for s in 0.5 0.8 1.2; do
  ./venv/bin/python -m scripts.compare_imagegen_models \
      --guide scripts/test_sketch.jpg \
      --prompt "1boy, solo, young boy with full body, messy hair, surprised expression, simple t-shirt, standing" \
      --presets illustrious_v2_inpaint_v4 --seed 42 --lora-scale $s
done
```

### Step 5: 最良結果を選定 + 整理

```bash
# 全試行 結果一覧
ls -lt logs/matsumoto_sweep_*/ logs/wobble_demo_*/ logs/imagegen_comparison_*/ \
    2>/dev/null | head -30

# 良かった候補を recovered_yaml 風に 専用 branch へ
WT=/tmp/matsumoto_winners_wt
git worktree add -b matsumoto-winners-20260528 "$WT" HEAD
cd "$WT"
mkdir -p winners
# 良かった画像を winners/ に cp して commit + push
# (具体名は試行結果次第)
```

---

## 1 行で全部 (理想線):

```bash
cd ~/draw_piper && \
  ./venv/bin/python -m modules.stroke_wobble && \
  ./venv/bin/python -m scripts.demo_wobble_sweep \
    --generated logs/imagegen_comparison_20260528_011016/illustrious_v2_inpaint.png \
    --user-sketch scripts/test_sketch.jpg \
    --output logs/wobble_demo_$(date +%Y%m%d_%H%M%S) && \
  bash scripts/sweep_matsumoto_experiments.sh && \
  bash scripts/train_lora_v4.sh
```

**所要時間予測**: ~40 分 (sweep 17 + train 22 + minor)。

---

## 結果判定 ルール

各 phase の出力画像を視認で次の項目で評価:

| 観点 | OK | NG |
|---|---|---|
| 線の質感 | rough / 不規則 / 太い細い変化 | smooth / 機械的 / 一様な太さ |
| 白背景 | 95% 以上 白 | グレー or 紫 or 模様あり |
| 文字混入 | 無し | 「918」「松本大洋」 等の文字残存 |
| ハッチング | 無し | screentone / 細線群 |
| 顔輪郭 | スケッチを保持 | 別の顔が複製 / 位置ズレ |

### 決断フロー

1. **すべての観点 OK で 松本タッチも乗ってる** → 採用、 image_gen.py の
   default preset を変更、 MILESTONES.md に提案
2. **松本タッチは弱いが OK** → Step 2 の wobble 後処理を追加で乗せて完成
3. **ロボット適性は OK だが Matsumoto タッチが乗らない** → wobble 一本化で
   完成 (Plan F 採用)
4. **すべて NG** → 今夜時点の Plan E (illustrious_v2_inpaint LoRA off) で
   妥協、 「松本タッチは将来課題」 として MILESTONES に記録

---

## 危険操作 (絶対やらない)

過去 disaster の教訓:
- `git clean -fd` 禁止 (orphan branch で .gitignore が無視される)
- `git reset --hard` 禁止 (uncommitted changes が消える)
- `git checkout --orphan` 禁止 (上記 2 つを誘発)

代わりに `git worktree add` で隔離してから push (前回 phase-e-results-20260528
で実証済)。

---

## 整理: 作成済ファイル

| ファイル | 目的 |
|---|---|
| `scripts/binarize_lineart_v4.py` | 厳格 2 値化 (threshold 50) preprocessor |
| `scripts/train_lora_v4.sh` | v4 LoRA エンドツーエンド学習 |
| `scripts/test_ip_adapter_style.py` | IP-Adapter 単発実験 |
| `scripts/sweep_matsumoto_experiments.sh` | 全候補 sweep master |
| `scripts/demo_wobble_sweep.py` | wobble 後処理 デモ |
| `modules/stroke_wobble.py` | 手描き風 wobble モジュール |
| `modules/stroke_render.py` | strokes → PNG renderer |
| `modules/image_gen.py` | preset `illustrious_v2_inpaint_v4` 追加 |
| `docs/20260528_matsumoto_pursuit_plan.md` | 全体計画ドキュメント |

すべて Read/Edit/Write のみで作成、 Bash 不要。
