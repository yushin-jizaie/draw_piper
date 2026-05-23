# SDXL Turbo + Canny + 差分 + ベクトル化前哨実験 (Step C クローズ)

> 日時: 2026-05-23 15:30 (JST)
> 関連既存ファイル: `20260521_1757_drawing_system_v04_design.md`, `20260522_2250_vlm_vram_measurement.md`, `PROGRESS.md`
> ステータス: **Step C クローズ**。本ファイル末尾の「設計 v0.4 の補強事項」を v0.4.1 として正本に反映する想定。

## 実施したこと

設計 v0.4 の Step C (画像生成パイプライン構築) を進める中で、以下の4つの実験を順次実施した:

1. **SDXL Turbo + Lineart ControlNet (MistoLine) 単体 VRAM 計測** — モデルロードと推論ピーク値の取得
2. **プロンプト調整 + ControlNet 差し替え実験** — 写実画問題の対処を試行
3. **Canny 系後処理実験** — 写実画から線画への変換可能性検証
4. **差分検出実験** — ユーザの絵 vs 生成画像のロボット描画用差分抽出
5. **ベクトル化前哨実験** — ロボット実用ストローク数までの削減処理パイプライン確立

VLM 計測のときと同様、各実験で `scripts/measure_*.py` / `scripts/experiment_*.py` を作成し、ログを `logs/` に残す形で進めた。

### 環境

- GPU: NVIDIA RTX 2000 Ada (16GB → 利用可能 15.57GB)
- venv: `~/draw_piper/venv` (Python 3.10)
- torch: `2.12.0+cu130`
- diffusers: 0.38.0
- 新規導入: `controlnet-aux 0.0.10`, `scikit-image 0.25.2`, `scipy 1.15.3`, `einops 0.8.2`, `timm 1.0.27`, `opencv-python-headless 4.13.0` (controlnet-aux の依存で自動導入)
- `requirements-imagegen.txt` を新規作成(VLM 計測のときの `requirements-vlm.txt` と同じ思想で別ファイル管理)

## 結果

### 1. SDXL Turbo + MistoLine 単体 VRAM (v1)

| 時点 | allocated | reserved | peak |
|---|---|---|---|
| baseline | 0.00 GB | 0.00 GB | 0.00 GB |
| after pipeline to(cuda) | **8.96 GB** | 9.24 GB | 8.96 GB |
| after warmup | 8.97 GB | 14.56 GB | **12.93 GB** |
| after inference (4 step) | 8.97 GB | 14.56 GB | **12.94 GB** |
| after cleanup | 0.01 GB | 0.02 GB | 12.94 GB |

- 静的重み: **8.96 GB** (VLM 5.51 + SDXL 8.96 = **14.47 GB**、16GB に収まる)
- 推論ピーク: **12.94 GB** (動的バッファ +4GB が湧く)
- レイテンシ: 1-step **1.49s** / 2-step **1.87s** / 4-step **2.71s**

VLM 単体 peak 5.98GB と合算すると **18.92 GB で 16GB 予算を 2.92 GB 超過**。ただし VLM 推論と SDXL 推論は同スレッドで順次実行される設計なので、動的バッファは同時には湧かない可能性が高い (要同居計測)。

### 2. 写実画問題の発見

3パターン (オリジナル / プロンプト変更 / Scribble-SDXL ControlNet) を試した結果、**全パターンとも写実的鉛筆スケッチが出力**。プロンプトもControlNetも本質的な改善には効かなかった。

**原因**: SDXL Turbo は SDXL の蒸留モデルで写実方向に強く引っ張られる。Turbo はプロンプト追従が弱い (高速化の代償) ため `flat 2D line art` `cartoon style` などの指示を無視する。

加えて、ControlNet は「ガイドに整合する画像」を作る仕組みのため、**ユーザの円+点が画像中央に "鼻" としてそのまま残る**。これは設計 v0.4 の「次に描き足されそうな部分を生成」の意図と噛み合っていない。

### 3. Canny 系後処理実験

21 枚 × 8 手法 = 168 通りを試した結果、`05_canny_strong_blur` (GaussianBlur σ=3.0 + Canny 50/150) が圧倒的に良いメトリクスを示した:

- line_pixel_ratio: 1〜3% (理想ゾーン)
- estimated_strokes: 100〜300 (連結成分数ベース)

**1-step より 4-step の方が安定して良い結果**(ノイズが減るため)。

### 4. 差分検出実験

`generated_canny` から **ユーザの円+点(と周辺ゴミ線)を除去**する処理を実装:

```
1. ユーザ絵を Otsu で二値化 → user_mask
2. user_mask を dilate (膨張) → 周辺含めて「除外エリア」化
3. generated_mask AND NOT(user_dilated) → 差分マスク
```

dilate カーネルサイズ 5/11/21/31/51 を試行。**k=21 以上で安定**。視覚確認すると、確かに中央の楕円(ユーザの円が出力に残ったもの)が消えていた。

ただし `estimated_strokes` 指標では効果が見えにくかった (`479 → 479` のような)。これは連結成分カウントが「長い線も短いノイズも 1個」と数える鈍感な指標だったため。**「ユーザの円が消えた」という質的変化は視覚確認でしか判定できなかった**。

### 5. ベクトル化前哨実験 (本実験の山場)

4 枚の差分後画像に対して、6 段階のパイプラインを適用:

```
1. 入力 (差分済み線画)
2. 連結成分フィルタ (min_pixels=50)  ← ノイズ除去
3. モルフォロジー CLOSE (kernel=3)   ← 線繋ぎ
4. 細線化 (skimage skeletonize)      ← 1px 幅へ
5. findContours + approxPolyDP       ← ポリライン化
6. 長さフィルタ (min_length=10)      ← 短すぎる線を除外
```

**結果**:

| 入力 | 元 components | 最終 strokes | 削減率 | total_points |
|---|---|---|---|---|
| 141838 (難題: 線多め) | 479 | **100** | 79% | 1609 |
| 141517 (中程度) | 147 | **39** | 73% | 651 |
| 144723 (中程度) | 121 | **26** | 78% | 478 |
| 141851 (限界: 元から少) | 38 | **17** | 55% | 314 |

**4枚とも目標値 (30〜150 strokes) または近傍に収束**。視覚評価でも顔の主要構造 (目・鼻・口・輪郭) が認識できる線画として残った。

### ロボット描画時間の見積もり

`min_length=10` での代表値:
- ストローク数: 26〜100
- total_length: 10547〜31894 px

実機換算 (1024px → 200mm幅 = 1px = 0.2mm、ペン速度 50mm/s 仮定):
- 31894 px ≒ 6.4 m の描画距離
- 128 秒 = **約 2 分 10 秒**

**設計 v0.4 の 2 分サイクルにほぼギリ収まる**。`min_length=15` あたりで詰めれば余裕を持って 2 分以内。

## つまずいた点

### 1. プロンプトと ControlNet の差し替えが効かなかった

「プロンプトに `cartoon style` `bold outlines` を入れれば線画になる」「Scribble-SDXL なら写実から離れる」と予想したが、両方とも効かず。SDXL Turbo がプロンプト追従弱い + ベース SDXL が写実寄り、という構造的問題と判明。最初の方向性 (生成段階で線画にする) は諦め、後処理 (Canny) に切り替えた。

### 2. 差分処理が「効いてないように見えた」

`estimated_strokes` が `479 → 479` のように変化なしに見えて、最初「差分が機能していない」と判断しかけた。視覚確認で「中央の楕円が消えている」と気付くまで時間を要した。**メトリクスだけで判定すると見落とすパターン**。

### 3. 中間スクリプトのコピー忘れ

`measure_sdxl_v2_prompt.py` と `measure_sdxl_v2_scribble.py` をユーザが `cp` した後、私が「中身は手動で書き換えてください」と伝え忘れた。結果、両スクリプトとも v1 と同じ動作をしていた。最初のログを見て気付き、書き換え済みのドラフトを出し直した。**スクリプト diff を作る際は、書き換え後の完全版を出すべき**(教訓)。

### 4. 画像アップロードの同名ファイル問題

`05_canny_strong_blur.png` のような共通ファイル名で複数フォルダから上げられたとき、こちら側ではファイル名・パスが見えず順番でしか判別できない。「Image 1 はどれか」をユーザに毎回確認する必要があった。**今後は、複数枚アップロード時はアップロード順序をメッセージに書いてもらう習慣を作るべき**。

## 学んだこと

### 設計 v0.4 の "穴" が3つ判明

設計 v0.4 では `画像生成 → ベクトル化 → ロボット` のフローを想定していたが、実機データで以下の3つの不整合が露呈した:

1. **画像生成が写実画を出す**: 設計 v0.4 は「ラフな線画が出る」前提だった
2. **ユーザの絵が完成形に残る**: 設計 v0.4 は「次に描き足す部分だけ生成される」前提だった
3. **線が多すぎる**: 設計 v0.4 では「ベクトル化すればストローク列になる」が、実際は 200〜500 本の細切れ線が出る

これら3つの不整合を、それぞれ Canny strong_blur / 差分検出 / 連結成分フィルタ+長さフィルタ で解決できた。

### SDXL Turbo はプロンプト追従が弱い

`flat 2D line art` `cartoon style` を強く指示しても、ベース SDXL の写実傾向が支配的になる。これは Turbo の高速化(蒸留)の代償。**Turbo を使う限り、プロンプトで生成傾向を制御するのは諦めるべき**。

### 「描き足し」を生成モデルだけで実現するのは難しい

ControlNet は「ガイドに整合する画像」を作る設計なので、構造的に「ガイドにあるものを残しつつ新しいものを足す」ことができない。**差分処理という別レイヤーで「描き足し」を実現する方が筋がいい**。

### 連結成分の数は鈍感な指標

「線の数」を見るのに `cv2.connectedComponents` の数だけ使うのは不十分。「短いノイズ線」も「長い意味のある線」も同じ1個として数えるため。**ポリライン化した後の `len(polylines)` と `total_length` のセットで見るべき**。

### Canny の前のぼかしが大きく効く

`GaussianBlur(σ=3.0)` で細部 (髪の毛、肌の質感) を吸収してから Canny にかけると、主要輪郭だけが残る。ぼかしなし Canny は使い物にならない (10〜15% line_ratio、3000+ strokes)。

## 設計 v0.4 の補強事項 (→ v0.4.1 として正本反映を推奨)

### 確定したパイプライン

```
[キーボード Enter]
   ↓
[median 合成キャプチャ]       ← ユーザの絵
   ↓
[VLM 意図予測]               ← Qwen2.5-VL-7B INT4
   ↓ (テキストプロンプト)
[SDXL Turbo + Lineart ControlNet]  ← 4-step 推奨。出力は写実画
   ↓ (写実画)
[Canny strong_blur 線画化]   ← GaussianBlur(9,9,σ=3.0) + Canny(50,150)
   ↓ (線画)
[差分検出: vs キャプチャ]    ← user_mask を dilate(k=21〜51) して AND NOT
   ↓ (描き足し用線画)
[連結成分フィルタ]           ← min_pixels=50 で小ノイズ除去
   ↓
[モルフォロジー CLOSE]       ← kernel=3 で線繋ぎ
   ↓
[細線化]                     ← skimage skeletonize
   ↓
[approxPolyDP ポリライン化]  ← epsilon=2.0
   ↓
[長さフィルタ]               ← min_length=10〜15
   ↓ (ストローク列)
[mm 単位への変換]            ← ArUcoキャリブ(Step B)を使用
   ↓
[piper_sdk EndPoseCtrl で描画]
```

### `modules/vectorizer.py` の責務 (詳細化)

設計 v0.4 では「OpenCV skeletonize → findContours → approxPolyDP」とざっくりだったが、実験で以下の8ステップ構成が必要と判明:

1. 線画化 (Canny strong_blur)
2. 差分 (vs median 合成キャプチャ)
3. 連結成分フィルタ
4. モルフォロジー CLOSE
5. 細線化
6. ポリライン化 + 長さフィルタ
7. mm 単位への座標変換 (Step B のキャリブを使う)
8. ストローク順序の最適化 (将来のスループット改善用、後回し可)

### 推奨パラメータ(本番運用初期値)

| パラメータ | 値 | 根拠 |
|---|---|---|
| SDXL `num_inference_steps` | **4** | 1-stepより安定、2.71秒 |
| SDXL `guidance_scale` | 0.0 | SDXL Turbo 公式推奨 |
| SDXL `controlnet_conditioning_scale` | 0.8 | 経験則、調整余地あり |
| ControlNet | **MistoLine** | Scribble との差は後処理で吸収可能、品質高い方を選ぶ |
| Canny ぼかし | `GaussianBlur(9,9,σ=3.0)` | 細部吸収、主要輪郭残す |
| Canny 閾値 | 50, 150 | 標準的 |
| 差分 dilate kernel | **21** | k=11 でも効くが余裕見て |
| 連結成分 min_pixels | 50 | 小ノイズ除去 |
| モルフォロジー CLOSE kernel | 3 | 線繋ぎ |
| approxPolyDP epsilon | 2.0 | 軌道点が適正量 |
| ポリライン min_length | **10〜15** | 10で strokes=26〜100、15でさらに削減 |

## 次にやること

### 短期 (今後の Step C 関連 / 結果反映)

- 🔲 設計 v0.4 を **v0.4.1** に更新 (本ファイルの「補強事項」を反映)
- 🔲 `HANDOFF_TO_CLAUDE_CODE_1_.md` に Step C 完了とパイプライン詳細を追記
- 🔲 `PROGRESS.md` の Step C セクションを「✅ COMPLETED」に更新
- 🔲 VLM 実装チャットに Step C 完了を共有 (vlm.py の出力フォーマットと prompt_builder の入力契約を詰める材料になる)

### 中期 (Step D 着手準備)

- 🔲 **VLM + SDXL 同居計測** — VRAM 18.92GB 想定の実測検証。これが Step C 最後の未解決事項
- 🔲 `modules/vectorizer.py` 本体実装 (今回の `experiment_vectorize.py` をクラス化)
  - `Vectorizer.vectorize(generated_image, user_image) → List[Stroke]` の構造
  - `Stroke = List[Tuple[float, float]]` (mm 単位)
- 🔲 `modules/image_gen.py` 本体実装 (`measure_sdxl_vram.py` をクラス化)
  - `ImageGenerator.generate(prompt, guide_image) → np.ndarray` の構造

### 長期 (本番運用に向けた調整、Step F 以降で扱う)

- 🔲 本物の median 合成キャプチャ画像 (test_sketch.jpg ではない実画像) でパイプラインを通す
- 🔲 描画開始点の決定 (空白領域検出 or random、設計 v0.4 の `start_point.py`)
- 🔲 ストローク順序最適化 (移動距離最小化、TSP的処理)
- 🔲 描画品質の主観評価 (実機で実描画して、絵として成立しているか)

### Claude Code との同期

- 🔲 Claude Code が Step E (`run_draw_test.py`) 完了次第、Step D (vectorizer.py) を任せるか検討
- 🔲 任せる場合、本ファイル + `experiment_vectorize.py` + 推奨パラメータを引き継ぎ材料として渡す
- 🔲 `requirements-imagegen.txt` を `requirements.txt` に統合するかは Claude Code 側 Step C 着手時に判断 (現状は VLM 計測時の方針と同じく分離)

### 参照

- 計測ログ生ファイル: `~/draw_piper/logs/sdxl_vram_*.log`
- 実験スクリプト:
  - `scripts/measure_sdxl_vram.py` (v1: MistoLine + 標準プロンプト)
  - `scripts/measure_sdxl_v2_prompt.py` (v2-prompt: 線画特化プロンプト)
  - `scripts/measure_sdxl_v2_scribble.py` (v2-scribble: Scribble ControlNet)
  - `scripts/experiment_canny.py` (8 種の Canny / 二値化処理)
  - `scripts/experiment_diff.py` (差分検出, dilate kernel size 5/11/21/31/51)
  - `scripts/experiment_vectorize.py` (連結成分フィルタ + ベクトル化, min_length 3/5/10/20)
- 生成画像 (21 枚): `logs/sdxl_output_*.png`
- 後処理結果: `logs/canny_experiment/`, `logs/diff_experiment/`, `logs/vectorize_experiment/`
- 入力スケッチ: `scripts/test_sketch.jpg` (VLM 計測時に生成済み)
