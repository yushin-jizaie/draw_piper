# dual-mode 比較 (複数入力): shift (位置ずらし) vs align (位置合わせ)

2026-05-28 18:15 実行。 [commit 9365854](../../commit/9365854) の `--placement {shift, align}` 統合に対する
**入力多様性ロバストネス検証**。 phase-e の F_angry_face と直接比較できるよう、 同じ 6 種 input の
うち代表 4 種を選んで両モードを実行。

## 入力 sketch (phase-e と同じ画像 SHA)

[`logs/sketch_variations_20260528_084706/inputs/`](../../tree/HEAD/logs/sketch_variations_20260528_084706/inputs) から:

| ID | 内容 | 狙い |
|---|---|---|
| B | round + smiley (目+口) | 顔ディテール多めの baseline |
| C | 顔 + 首 + 肩 V 字 | input で体位置を示唆 |
| D | 棒人間 (頭+胴+腕+脚) | 全身構図 を input で固定 |
| F | 怒り顔 (眉+鋭目+口) | **phase-e の F_angry_face と同入力** |

(A_oval_2dots は最小入力で出力スパースになることが既知のため割愛、 E_small_face_top は
代表性が C/D と被るため割愛)

## 結果概要

全 4 入力 × 2 モード = 8 実行成功 (seed=42 固定)。

| Sketch | VLM 推定 (subject_ja, conf) | shift 結果 | align 結果 |
|---|---|---|---|
| B_round_smiley | 顔 (0.90) | [shift/](B_round_smiley/shift/) 19 strokes / 411 pts | [align/](B_round_smiley/align/) |
| C_face_with_neck | 人 (0.70) | [shift/](C_face_with_neck/shift/) 18 strokes / 321 pts | [align/](C_face_with_neck/align/) |
| D_stick_figure | 人 (0.90) | [shift/](D_stick_figure/shift/) 9 strokes / 301 pts | [align/](D_stick_figure/align/) |
| F_angry_face | 顔 (0.90) | [shift/](F_angry_face/shift/) 24 strokes / 377 pts | [align/](F_angry_face/align/) |

## モード別 出力ファイル構造

各 `<sketch>/<mode>/` 配下:

### shift モード (M16 位置ずらし)

```
shift/
├── 00_auto_prompt.txt         VLM 推定 + COMPANION_TEMPLATE prompt
├── stage1/
│   ├── illustrious_v2_object.png   M16 object 生成 (中央 detailed)
│   ├── 00_guide.png / grid.png / summary.json
├── 20_input_strokes.png       入力 sketch を Vectorize
├── 21_transformed_gen_strokes.png   生成 strokes を空白地帯に移動
└── 30_companion_strokes.png   ★最終出力: 入力 + 移動生成 の合成
```

### align モード (位置合わせ、 F_angry_face 経路)

```
align/
├── 00_auto_prompt.txt         VLM 推定 + CHARACTER_TEMPLATE prompt
├── stage1/
│   └── illustrious_v2_inpaint.png  Plan E inpaint で構図確定 (1024px)
├── 10_init_from_stage1.png    Stage 2 init image (768px)
├── 11_style_ref.png           IP-Adapter 用 style ref (松本作品、 character pool から auto pick)
├── 20_stage2_str0.45_ip0.60.png   ★IP-Adapter で style 転写後 (= 20_stage2_ip_adapter.png 相当)
└── 30_vectorized_strokes.png  ★最終出力: robot 描画用 strokes
```

## phase-e の F_angry_face との対応 (重要)

[phase-e ブランチの F_angry_face](https://github.com/yushin-jizaie/draw_piper/tree/phase-e-results-20260528/sketch_variations/F_angry_face) (commit `30178e8`、
当時の `test_ip_adapter_two_stage.py` SHA `81b1f9b0` = `c32c2c1`) との対応:

| phase-e ファイル | 本リポの align 出力 |
|---|---|
| `F_angry_face/00_input.png` | (本リポ同等: `logs/sketch_variations_20260528_084706/inputs/sketch_F_angry_face.png`) |
| `F_angry_face/10_stage1_plan_e.png` | [`F_angry_face/align/stage1/illustrious_v2_inpaint.png`](F_angry_face/align/stage1/illustrious_v2_inpaint.png) |
| `F_angry_face/20_stage2_ip_adapter.png` | [`F_angry_face/align/20_stage2_str0.45_ip0.60.png`](F_angry_face/align/20_stage2_str0.45_ip0.60.png) |
| `F_angry_face/30_vectorized_strokes.png` | [`F_angry_face/align/30_vectorized_strokes.png`](F_angry_face/align/30_vectorized_strokes.png) |

**確認ポイント**: F_angry_face/align の出力が phase-e の F_angry_face と **目視で同等**
(怒り表情の保持、 松本タッチの style 転写) なら、 align 統合は F_angry_face 時の挙動を
正しく引き継いでいることになる。

## VLM 推定 prompt の差異

両モードで同じ VLM 推定結果から、 placement に応じた template で prompt を生成:

| Sketch | shift (COMPANION_TEMPLATE) | align (CHARACTER_TEMPLATE) |
|---|---|---|
| 顔系 (B/F) | `a detailed Matsumoto-style face, manga style, expressive ink lines, ...` | `face, manga style character, dynamic pose, expressive ink lines, detailed lineart, ...` |
| 人系 (C/D) | `a detailed Matsumoto-style person, ...` | `person, manga style character, ...` |

shift は「Matsumoto-style **object/companion**」 的な誘導、 align は「manga style **character**」
で 2-stage IP-Adapter が style を後乗せする前提の構図優先 prompt。

## 再現コマンド

```bash
for sk in B_round_smiley C_face_with_neck D_stick_figure F_angry_face; do
  for mode in shift align; do
    EXTRA=""
    [ "$mode" = "align" ] && EXTRA="--placement align"
    ./venv/bin/python -m scripts.test_companion_mode \
        --user-sketch logs/sketch_variations_20260528_084706/inputs/sketch_${sk}.png \
        --auto-prompt ${EXTRA} \
        --output sketch_variations/dual_mode_multi_<ts>/${sk}/${mode} \
        --seed 42
  done
done
```

## 戻り方 (align 統合が違った場合)

| 状況 | コマンド |
|---|---|
| 1 段戻す (位置ずらし最新は残す) | `git reset --hard 45cbb1f` |
| 2 段戻す (stash pop 前) | `git reset --hard fe76415` |

## 関連

- 単一 sketch (test_sketch.jpg) での比較: [../dual_mode_20260528_175936/](../dual_mode_20260528_175936/)
- 統合実装: [scripts/test_companion_mode.py](../../scripts/test_companion_mode.py), commit `9365854`
- phase-e 結果: https://github.com/yushin-jizaie/draw_piper/tree/phase-e-results-20260528/sketch_variations
