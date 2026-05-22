# Qwen2.5-VL-7B VRAM 計測 (Step C 単体VLM)

> 日時: 2026-05-22 22:50 (JST)
> 関連既存ファイル: `20260521_1757_drawing_system_v04_design.md`, `PROGRESS.md`

## 実施したこと

Claude Code が Step E (実機統合 / `run_draw_test.py`) を進めるのと並行して、別ターミナルで Qwen2.5-VL-7B の単体 VRAM 計測を実施。設計 v0.4 で「6〜8GB を想定」としていた VLM 占有量の実測値を取得し、SDXL Turbo との共存可否判定の一次データを確保した。

### 環境

- GPU: NVIDIA RTX 2000 Ada (16GB)
- Driver: 595.71.05 / CUDA 13.2
- venv: `~/draw_piper/venv` (Python 3.10)
- torch: `2.12.0+cu130`
- transformers: 5.9.0
- bitsandbytes: 0.49.2

### 量子化方式の選択

最終的に **bitsandbytes NF4 (4bit) + double quant** を採用。

検討段階で公式 AWQ 版 (`Qwen/Qwen2.5-VL-7B-Instruct-AWQ`) も候補としたが、torch 2.12 / CUDA 13 環境での `autoawq` の事前ビルド済み wheel 有無が不明で、ソースビルドで時間を浪費するリスクがあった。既にインストール済みの bitsandbytes 0.49.2 が同 torch 環境で動作することを確認できたため、まず bnb で測定して数値を確保する方針に変更。

### 追加インストール

`requirements-vlm.txt` を新規作成（既存 `requirements.txt` には触れない / Claude Code とのマージ衝突回避のため）:

```
torch
torchvision
transformers>=4.49.0
accelerate
bitsandbytes
qwen-vl-utils
pillow
```

このうち `accelerate-1.13.0`, `bitsandbytes-0.49.2`, `transformers-5.9.0`, `torch-2.12.0+cu130`, `qwen-vl-utils-0.0.14`, その他 nvidia 系ランタイムが新規導入された。

### 計測スクリプト

`scripts/measure_vlm_vram.py` を新規作成。以下を計測:

- baseline / model load 後 / 入力準備後 / warmup 後 / 推論後 / cleanup 後 の各時点での allocated / reserved / peak VRAM
- 推論レイテンシとトークン/秒
- 実出力テキスト（プロンプト適合性の目視確認用）

入力は `scripts/test_sketch.jpg` (1024×768、白地に黒丸＋目2つの簡易顔スケッチ) を自動生成。
プロンプトは設計 v0.4 の「意図予測テンプレ」をそのまま使用。

## 結果

### VRAM 計測値

| 時点 | allocated | reserved | peak |
|---|---|---|---|
| baseline | 0.00 GB | 0.00 GB | 0.00 GB |
| after model load | **5.51 GB** | 5.61 GB | 5.55 GB |
| after inputs prepared | 5.52 GB | 5.61 GB | 5.55 GB |
| after warmup | 5.53 GB | 6.29 GB | 5.98 GB |
| after inference (256 tok) | 5.53 GB | 6.29 GB | **5.98 GB** |
| after cleanup | 0.01 GB | 0.02 GB | 5.98 GB |

設計 v0.4 の事前予想 (6〜8GB) より **0.5〜2.5GB 軽い**。SDXL Turbo (FP16 〜10GB) + Lineart ControlNet (〜1.5GB) との共存合計は約 17.5GB 想定で、依然 16GB を超過するが、共存余地は予想より広い。

### レイテンシ

- モデルロード: 166.2s (DL 込み、5 shards、合計約 16GB ダウンロード後 bnb で NF4 量子化)
- 推論: **6.40s / 152 tokens = 23.8 tok/s** (max_new_tokens=256, do_sample=False, warmup 後)
- 2 分サイクル内の VLM 推論パートとしては十分な余裕

### モデル出力 (品質確認)

入力: 「白地に黒い円＋目2つ」のダミースケッチ

```
このスケッチは、人間の顔の一部を示しています。主な要素は以下の通りです：
1. **主題**: 人間の顔のスケッチ。
2. **未完成な要素**: 目と鼻が描かれていますが、口や顔の他の部分はまだ描かれていません。
3. **次に描き足されそうな部分**: 頭部全体、顔の輪郭、そして口や鼻の詳細な部分が追加されると考えられます。
このスケッチは、人間の顔の基本的な構造を示しており、次にその詳細を追加して完成させる予定であることが伺えます。
```

設計 v0.4 で意図した「主題 / 未完成要素 / 次に描き足されそうな部分」の3項目で構造化された応答が得られた。`prompt_builder.py` で正規表現パース or 二段 LLM で構造化データに落とし込める形。

(細かい指摘: 「目」を「鼻」と誤認している箇所があるが、これは入力が顔として不完全（鼻が無い）ため、VLM が概念的に補完したと解釈できる。実画像では改善する見込み)

## つまずいた点

### 1. 最初のディレクトリ取り違え疑い

`source venv/bin/activate` を `~/piper_test` で実行した後に `cd ~/draw_piper` していたため、`~/piper_test/venv` の方に依存が入っている可能性があった。`which python3` と `echo $VIRTUAL_ENV` で `~/draw_piper/venv/bin/python3` を確認して問題ないと判定。

### 2. AWQ → bitsandbytes への切り替え判断

最初の手順案では AWQ 版を勧めたが、`autoawq` の torch 2.12 / CUDA 13 対応状況が不明で事前ビルド済み wheel が無いとソースビルドで30分以上を要するリスクがあった。既に動作確認済みの bitsandbytes 0.49.2 に切り替えて、まず数値を取る方針に。AWQ への切り替えはいつでも10分でできる。

### 3. bitsandbytes の FutureWarning

`_check_is_size will be removed in a future PyTorch release` が2回表示された (NF4 ロード時と推論時)。動作影響なし。torch のバージョンが将来上がったとき bnb 更新が必要、というメモのみ。

## 学んだこと

### NF4 + double quant は予想より軽い

設計 v0.4 では「INT4 で 6〜8GB」と保守的に見積もったが、NF4 + double quant で 5.51GB ロード / 5.98GB ピークに収まる。SDXL Turbo との共存設計に余裕が出る。

### transformers 5.x でも Qwen2_5_VLForConditionalGeneration クラス名は健在

事前に `from transformers import Qwen2_5_VLForConditionalGeneration` のチェックを入れたが OK。API 変更なし。

### モデル DL の所要時間

5 shards で合計約 16GB を 156s 程度でDL完了 (約 100MB/s)。HF_TOKEN 未設定の警告は出るが、公開モデルなので問題なし。

### 設計 v0.4 のプロンプトはそのまま使える

意図予測テンプレが期待通り構造化応答を返した。日本語応答も自然。`prompt_builder.py` の実装方針は「3項目を抽出して画像生成プロンプトを組む」で確定できる。

## 次にやること

### 短期 (今後の VLM 関連)

- 🔲 `modules/vlm.py` 本体実装 (計測スクリプトの中身をクラス化、`VLMIntentPredictor.predict_intent() → IntentPrediction(dataclass)` の構造化出力)
- 🔲 実画像 (median 合成後のキャプチャを模した低品質画像) でのプロンプト適合性検証
- 🔲 max_new_tokens の最適値検討 (128 / 256 / 512 で VRAM とレイテンシ推移)
- 🔲 画像解像度感度の確認 (1024×768 vs 1920×1080)

### 中期 (Step C 全体)

- 🔲 SDXL Turbo + Lineart ControlNet を**同条件で**単体計測 (`measure_sdxl_vram.py` を同様に作成)
- 🔲 VLM + SDXL Turbo の同居計測 (本番想定)
  - 予想: 合計 17〜18GB → 16GB 超過 → ControlNet を CPU offload で対応の見込み

### Claude Code との同期

- 🔲 計測結果を `PROGRESS.md` の Step C セクションに**ローカル側でも**追記（Claude Code が Step E 完了後に Step C/D へ向かう前に共有）
- 🔲 `requirements-vlm.txt` を `requirements.txt` に統合するかは Claude Code 側 Step C 着手時に判断

### 参照

- 計測ログ生ファイル: `~/draw_piper/logs/vlm_vram_20260522_*.log`
- スクリプト: `~/draw_piper/scripts/measure_vlm_vram.py`
- ダミー入力画像: `~/draw_piper/scripts/test_sketch.jpg`
