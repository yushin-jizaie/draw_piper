# phase-e F_angry_face vs 現 align/F_angry_face 差分解析

Claude 視点 (画像視覚 + コード読解) で 両出力の差分と原因を特定。

## 視覚比較

### Stage 1 (Plan E inpaint) — 構図確定段階

| | phase-e (c32c2c1 時) | 現 align (commit 9365854) |
|---|---|---|
| 出力 | **怒り顔の少年が全身**、 spiky 髪、 拳を握ってジャンプ動作、 漫画的キャラ | **入力の怒り顔 oval だけ残る**、 周りは抽象的な丘 + 草模様、 体・髪・手脚は無い |
| キャラ性 | あり (full body character) | なし (face + 風景) |

### Stage 2 (IP-Adapter で松本タッチ転写後)

| | phase-e | 現 align |
|---|---|---|
| 出力 | Stage 1 のキャラに松本タッチ重畳、 線が手書き感増 | 緑がかった背景 + 顔と丘構造はそのまま、 風景画的 |
| 評価 | F_angry_face として **意図通り** (キャラ全身+表情) | **入力に忠実だが期待と違う** (キャラを描き足さなかった) |

## 共通条件 (差分原因ではない)

| 項目 | 値 | 出典 |
|---|---|---|
| Stage 1 preset | `illustrious_v2_inpaint` | 両方とも (CATEGORY_TO_STAGE1_PRESET['character']) |
| Stage 1 解像度 | 1024 px | `--stage1-resolution 1024` |
| Stage 2 解像度 | 768 px | `--resolution 768` |
| `stage2-strength` | 0.45 | F_angry_face と align のデフォルト同値 |
| `ip-scale` | 0.6 | F_angry_face と align のデフォルト同値 |
| style ref | `IMG_4311.JPG` | 両方 (phase-e は明示指定、 現 align は character pool seed=42 で auto pick → 同じファイル) |
| seed | 42 | 両方 |

→ コード経路 (Plan E inpaint → IP-Adapter) は **完全に等価**。 SDXL pipeline 自体に差はない。

## 差分の主因: **Stage 1 prompt の違い**

### phase-e (script default、 c32c2c1 時)

```
"1boy, solo, young boy with full body, messy hair, surprised expression, simple t-shirt"
```

→ **「1boy, solo, full body, ... standing」** で人物全身を強く誘導。 SDXL は input sketch (怒り顔 oval) を 「顔の hint」 と解釈し、 周囲に体・手・脚・髪を描き足す。

### 現 align (VLM auto-prompt + CHARACTER_TEMPLATE)

VLM (Qwen2.5-VL) 推定: `subject_ja=顔 / confidence=0.90`
→ `{subject_en} {action_en} {location_en}` テンプレートで:

```
"face, manga style character, dynamic pose, expressive ink lines, detailed lineart,
 single continuous black line on plain white background, clean smooth strokes, no shading"
```

→ **「face」だけ** が subject、 体・全身を示す語が無い。 SDXL は「face oval だけ忠実に保ち、 周りは abstract に埋める」 解釈。 結果として風景画的な出力。

## なぜ 「これはこれで面白い」 のか

両者は **本質的に違うパラダイム** の出力:

| パラダイム | phase-e (1boy prompt) | 現 align (face + VLM) |
|---|---|---|
| 入力 sketch の扱い | **構図の最小 hint** | **忠実に保持すべき構造** |
| SDXL の役割 | 周囲を描き足してキャラ化 | sketch を尊重して風景化 |
| 用途 | キャラクター生成 (絵を「拡張」) | sketch の質感変換 (絵を「翻訳」) |

ユーザー要望:
> 「位置合わせはこれはこれで面白いので一つのパターンとして残しておいてください」

= 現 align (sketch 忠実) パラダイムは **新規価値** として保持。 phase-e 風 (キャラ拡張) は 別オプションとして将来 復元可能。

## 復元方法 (phase-e 同等出力が欲しい場合)

`--auto-prompt` を使わず 手動 prompt 指定:

```bash
./venv/bin/python -m scripts.test_companion_mode \
    --user-sketch logs/sketch_variations_20260528_084706/inputs/sketch_F_angry_face.png \
    --placement align \
    --style-ref training/matsumoto_taiyo/raw/IMG_4311.JPG \
    --prompt "1boy, solo, young boy with full body, messy hair, surprised expression, simple t-shirt, standing" \
    --output sketch_variations/align_phase_e_repro_<ts> \
    --seed 42
```

これで phase-e の F_angry_face と機能等価な出力が得られるはず (style ref + prompt + strength + ip_scale + seed すべて同じ)。

## VLM auto-prompt の改良ヒント (今後の作業用)

現 `CHARACTER_TEMPLATE` (modules/prompt_builder.py):
```
"{subject_en} {action_en} {location_en}, manga style character, dynamic pose,
 expressive ink lines, detailed lineart, ..."
```

subject_en = "face" の時に **「full body, standing, with body」 等を補う** ロジックを足せば、 phase-e 風出力に近づく:

```python
# 案:
if subject_en in ("face", "顔") and action_en in ("unknown", ""):
    subject_en = "1boy, solo, young boy with full body"
    action_en = "standing"
```

もしくは VLM prompt 側で「sketch から想像できる全身像を推定して」 と明示誘導する。

## まとめ

- コード経路は **完全に等価** (Plan E inpaint → IP-Adapter)。 align 統合バグなし。
- 差分は **Stage 1 prompt の違い** に起因 (固定 "1boy full body" vs VLM auto "face")。
- 現 align (sketch 忠実) は新しい価値で保持、 phase-e 風 (キャラ拡張) は手動 prompt で復元可能。
- VLM auto-prompt の CHARACTER_TEMPLATE は subject=「顔」 時に体構図を補う改良余地あり。
