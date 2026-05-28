# dual-mode 比較: shift (位置ずらし) vs align (位置合わせ)

2026-05-28 17:59 実行。 `scripts/test_companion_mode.py` の `--placement {shift, align}` 統合
([commit 9365854](../../commit/9365854)) の動作確認。

## 入力

- sketch: [`scripts/test_sketch.jpg`](../../scripts/test_sketch.jpg) (簡易 smiley face)
- VLM 推定 (両モード共通): `subject_ja=顔 / location_ja=不明 / action_ja=不明 / confidence=0.90`
- seed: 42

## 結果

### shift モード (M16 位置ずらし、 既存)

- prompt template: `COMPANION_TEMPLATE` (Matsumoto companion)
- 経路: object preset (M16 illustrious_v2_object = img2img + CN 0.65 soft hint) で生成
  → 中央 detailed object → 入力 sketch の **空白地帯** (cv2 blob 検出) に配置
- 出力: [shift/](shift/)
  - `stage1/illustrious_v2_object.png` — M16 object 生成出力 (中央 detailed)
  - `20_input_strokes.png` — 入力 sketch を Vectorize
  - `21_transformed_gen_strokes.png` — 生成 strokes を空白地帯に移動
  - `30_companion_strokes.png` — 入力 + 移動生成 の合成 (**最終出力**)
- 結果: 14 strokes / 288 pts
- 実行時間: 48 秒

### align モード (位置合わせ、 F_angry_face 経路、 新規統合)

- prompt template: `CHARACTER_TEMPLATE` (M15/M16 character、 2-stage 用)
- 経路: 入力 sketch をそのまま Stage 1 (Plan E inpaint) → Stage 2 (IP-Adapter で 松本 style 転写)
  → 入力の構図を保ったまま画風だけ変換
- 内部: `test_ip_adapter_two_stage --category character --stage2-strength 0.45 --ip-scale 0.6
  --stage1-resolution 1024 --resolution 768` を subprocess 呼び出し
- style ref: `training/matsumoto_taiyo/raw/IMG_4311.JPG` (character pool から auto pick、 seed=42)
- 出力: [align/](align/)
  - `stage1/illustrious_v2_inpaint.png` — Plan E inpaint で構図確定 (1024px)
  - `10_init_from_stage1.png` — Stage 2 init image (768px に縮小)
  - `11_style_ref.png` — IP-Adapter 用 style ref (松本作品)
  - `20_stage2_str0.45_ip0.60.png` — IP-Adapter で style 転写後 (**最終生成画像**)
  - `30_vectorized_strokes.png` — robot 描画用 strokes (**最終出力**)
- 結果: 35 strokes / 771 pts
- 実行時間: 64 秒

## F_angry_face との対応

F_angry_face ([phase-e ブランチ](https://github.com/yushin-jizaie/draw_piper/tree/phase-e-results-20260528/sketch_variations/F_angry_face)) は
`怒り顔の sketch + 同じ 2-stage パイプライン` の出力で、 入出力の対応は align モードと同じ:

| F_angry_face のファイル | align モードの対応 |
|---|---|
| `00_input.png` | (今回は `scripts/test_sketch.jpg`) |
| `10_stage1_plan_e.png` | `align/stage1/illustrious_v2_inpaint.png` |
| `20_stage2_ip_adapter.png` | `align/20_stage2_str0.45_ip0.60.png` |
| `30_vectorized_strokes.png` | `align/30_vectorized_strokes.png` |

## 再現コマンド

```bash
# shift モード (M16 位置ずらし)
./venv/bin/python -m scripts.test_companion_mode \
    --user-sketch scripts/test_sketch.jpg \
    --auto-prompt \
    --output sketch_variations/dual_mode_<ts>/shift \
    --seed 42

# align モード (F_angry_face 経路、 位置合わせ)
./venv/bin/python -m scripts.test_companion_mode \
    --user-sketch scripts/test_sketch.jpg \
    --placement align --auto-prompt \
    --output sketch_variations/dual_mode_<ts>/align \
    --seed 42
```

## 戻り方 (align 統合が違う場合)

| 状況 | コマンド | 戻り先 |
|---|---|---|
| 1 段戻す (位置ずらし最新は残す) | `git reset --hard 45cbb1f` | ② |
| 2 段戻す (stash pop 前) | `git reset --hard fe76415` | 元 |
