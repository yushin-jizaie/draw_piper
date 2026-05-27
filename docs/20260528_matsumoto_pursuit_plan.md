# 松本大洋画風 獲得 追求プラン (2026-05-28)

> 「松本大洋の画風になるまで実装を続けてください」 (ユーザ指示) への 答え。
> 自分セッション (深夜帯) で 詰める範囲を script に固める + 朝の bash 一括承認で
> 一気に sweep を回す段取り。

---

## 背景 (これまでの試行 まとめ)

### 失敗した試行 chain

| 試行 | 結果 |
|---|---|
| LoRA v0 (panel 学習) | 黒テクスチャ + ハッチング暴走 |
| LoRA v1 (lineart + bolden 2 値化) | 大面積黒テクスチャ暴走 |
| LoRA v2 (lineart grayscale) | 黒テクスチャ + RGB カラーノイズ暴走 |
| LoRA v3 (dataset 精選 13 枚 + rank/lr 緩め) | 周辺に日本語っぽい文字 + 青網点暴走 |
| LineAniRedmond LoRA (汎用 manga lineart) | 太い outline + シャツに数字文字、 Matsumoto タッチではない |
| v0 LoRA scale 0.7/1.0 on Illustrious | 背景紫テクスチャ、 タッチは微妙に変化するも 松本独特感 弱い |
| prompt: "tekkonkinkreet style" 等 作品名 | シャツに英字ロゴ 呼び出し → ロボット描画 NG |
| prompt: "artist:taiyou_matsumoto" tag | 効果無し (model が認知してない) |

### 真因 (確定診断)

**grayscale lineart の VAE encode が 高周波ハッチング latent として
表現される** → LoRA はそれを 「松本らしさ」 として学習 → 推論時に 全画面
ハッチング暴走。 bolden/no-bolden に関係なく、 dataset 表現形式の根本問題。

### 構造的に妥協できないユーザ制約

ロボットアームで ペン 1 本で描く前提:
- 線のみ
- 白背景
- ハッチング NG
- 塗りつぶし NG
- 紙質感 NG

**でも松本タッチの本質的特徴は ハッチング/紙質感/影 を含む** ため、 純線画
だけで松本タッチを再現するのは構造的に難しい。 目指すは:
- 「線の質感」 (太い細い、 ラフ、 不規則) は松本寄り
- 「塗り / ハッチング」 は出さない

---

## 今回の追加実験プラン (3 phase)

### Phase A: 既存 LoRA sweep (済)

実行済。 結果: LineAniRedmond + v0 LoRA いずれも松本タッチ獲得には不十分。

### Phase B: LoRA v4 (新規学習) — 厳格 binarize + 低 rank

**仮説**: 過去 LoRA は dataset preprocessing 起因の暴走。 完全 2 値の
lineart で学習すれば 「線の質感」 だけ学べる可能性。

実行手順 (明日 bash 承認時):
```bash
bash scripts/train_lora_v4.sh
```

内訳:
1. `scripts/binarize_lineart_v4.py` で lineart_b/ → lineart_v4_binary/ (threshold 50, 完全 2 値)
2. prepare_style_dataset で dataset_v4/ 構築
3. v0-v3 で復元した caption を流用
4. train_style_lora.py で **rank=8, lr=5e-5, steps=600** (極めて控えめ)

完走後 (~22 分):
- preset `illustrious_v2_inpaint_v4` (lora_scale 0.8 default) で推論
- sweep で scale 0.5 / 0.8 / 1.2 を比較

期待: 過去の暴走パターン (黒テクスチャ + 文字 + 紙質感) を **物理的に**
回避し、 線の質感だけ取り込めるか?

### Phase C: IP-Adapter style transfer

**仮説**: LoRA 学習は dataset の問題で破綻するなら、 学習せず inference 時の
attention 注入で style transfer する手はどう?

実行手順:
```bash
./venv/bin/python -m scripts.test_ip_adapter_style \
    --user-sketch scripts/test_sketch.jpg \
    --style-ref training/matsumoto_taiyo/raw/IMG_4321.JPG \
    --prompt "1boy, solo, ..." \
    --ip-scale 0.6 --seed 42 \
    --output logs/ip_adapter_test_YYYYMMDD
```

IP-Adapter は SDXL 公式 (`h94/IP-Adapter`) を使用。 dataset preprocessing 不要、
raw 画像をそのまま style reference にできる。

5 種の raw 画像 × 2 scale (0.4, 0.7) で 10 試行。 ~12 分。

### Phase D: 組み合わせ + Vectorizer 評価

全結果を 1 つの sweep dir に集約 → 視認で評価 → 最良候補を選んで Vectorizer
通し試験 → strokes の Matsumoto らしさを最終判定。

実行: `bash scripts/sweep_matsumoto_experiments.sh`

---

## 明日朝の bash 一括承認用 コマンドリスト

許可待ち。 朝起きたら 1 行ずつ承認 or まとめて 1 行で:

```bash
# 1. LoRA v4 学習開始 (background ~22 min)
bash scripts/train_lora_v4.sh

# 2. 学習中に並行で sweep の Phase A/C 実行 (~17 min)
bash scripts/sweep_matsumoto_experiments.sh

# 3. v4 完走後、 Phase B 部分を sweep に追加 (~5 min)
#    sweep_matsumoto_experiments.sh 内で 自動 detect 済
```

GPU 16GB 制約: **学習 と 推論 sweep を 同時走行 すると OOM**。 順番に:
1. まず Phase A/C sweep (~17 min)
2. 終わったら v4 学習 (~22 min) ← Phase A/C 結果 review しながら
3. v4 完走後、 v4 検証 sweep (~5 min)

合計 ~45 分の morning routine。

---

## 期待される結果と決断ロジック

| パターン | 次の手 |
|---|---|
| v4 LoRA で 松本タッチ獲得 + ロボット適性 OK | 🎉 v4 採用、 完成 |
| v4 で 過去同様の暴走 | LoRA approach は放棄、 IP-Adapter 一本化 |
| IP-Adapter で 松本タッチ獲得 + ロボット適性 OK | 🎉 IP-Adapter 採用 + image_gen.py に統合 |
| IP-Adapter も ハッチング/塗り 呼び出す | Multi-controlnet (Canny + lineart) で 制約強化 試行 |
| すべて失敗 | 「松本タッチ」 は LoRA 不要 で 現状 (illustrious_v2_inpaint) のまま 妥協 |

---

## 既存の準備済 ファイル

- `scripts/binarize_lineart_v4.py` — 厳格 2 値化 preprocessor
- `scripts/train_lora_v4.sh` — v4 LoRA エンドツーエンド学習
- `scripts/test_ip_adapter_style.py` — IP-Adapter 単発テスト
- `scripts/sweep_matsumoto_experiments.sh` — 全 phase sweep master
- `modules/image_gen.py` — preset `illustrious_v2_inpaint_v4` 追加済

---

## ロボット結合状態 (現時点)

Plan E core (illustrious_v2_inpaint LoRA off) で:
- ✅ 顔保持 + 体描き足し + 純線画 + ロボット適性 strokes 達成
- ⚠️ 松本独特タッチは未獲得
- ✅ Vectorizer + Robot.draw_stroke_panel (mock + real CAN) 動作確認済

= **松本タッチ獲得が undone でも、 ロボット描画自体は今すぐ実行可能**。
本プランで松本タッチが付加できれば 「最終形」 と言える。

---

## 失敗 fallback (Plan F)

全部ダメだった場合の最終受け入れ案:

1. **「松本タッチ」 は LoRA 不要 + IP-Adapter 不要 で諦める**
2. illustrious_v2_inpaint (clean lineart) で「先に進める」
3. 後段で 「線の質感に rough さを加える」 ポストプロセス (cv2 で 線の wobble
   を加える等) を Vectorizer 直前に挟む案
4. ロボット描画 stroke 時に 描画速度を不規則化して「手書き感」 を出す
   (Robot 側 sw 対処、 描画モデル変更不要)

これらは LoRA / Matsumoto に依存しないので、 確実に achievable。

---

## メモ: なぜ LoRA がここまで難しかったか

松本大洋作品の特徴 = 「ラフな線 + 紙質感 + screentone + 黒塗り」
これが パッケージで「松本らしさ」 を構成している。

LoRA は dataset 中の特徴を区別なく抽出するため、 「ラフな線」 だけ学ばせる
ことが原理的に難しい。 dataset から 紙質感/screentone/黒塗り を 物理的に
排除しても、 LoRA は依然 「微妙な濃淡 = ハッチング」 として学習。

これは LoRA の学習粒度の限界。 IP-Adapter は 「画像全体の style embedding」
を condition に使うため、 「線質と塗りを切り離す」 表現がそもそもできない
かもしれない。 ハッチング込みで style transfer する可能性大。

→ **結論的に「松本タッチ + 純線画」 の両立は本質的に難しい題材**。 50% でも
獲得できれば成功と判断、 100% は理論上の上限が低い可能性。

---

最終的にどうしても松本らしさが必要なら、 後段の cv2 ポストプロセス案
(Plan F #3) が現実解。 LoRA / IP-Adapter は 「タッチを乗せる試み」 として
価値あるが、 100% にはコミットしない。
