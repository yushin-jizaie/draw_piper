# フルパス統合 (VLM → ImageGen → Vectorizer) と prompt 整形

> 日時: 2026-05-23 17:10 (JST)
> 関連既存ファイル: `20260523_1530_step_c_completion.md`, `20260523_1640_vlm_to_image_integration.md`, `20260523_1650_vectorizer_class_implementation.md`

## 実施したこと

前々セッション (`20260523_1640`) で VLM → ImageGen の段階的スワップを実証し、前セッション (`20260523_1650`) で `modules/vectorizer.py` をクラス化した。本セッションは両者の宿題を片付け、**フルパス (VLM → prompt_builder → ImageGen → Vectorizer) を 1 つの統合テストで通す** ところまで持っていく作業。

### 1. `prompt_builder.py` のカンマ前スペース修正

前々セッションで持ち越した宿題。`_normalize_spaces()` が `dragon dancing , line art, ...` のように **カンマ直前の空白** を残してしまっていた件。

修正:

```python
import re

def _normalize_spaces(s: str) -> str:
    """連続する空白を1つにまとめ、カンマ直前の空白も除去する。"""
    s = re.sub(r"\s+,", ",", s)   # カンマ前の空白を除去
    return " ".join(s.split())    # 連続空白を 1 つに
```

スモークテスト (`python3 -m modules.prompt_builder`) で 4 ケース全てを確認。`location` 不明ケースが `dragon dancing , line art, ...` → `dragon dancing, line art, ...` に修正、他 3 ケースはリグレッション無し。

### 2. `test_vlm_to_image.py` に vectorize ステージを追加

前セッションで実装した `Vectorizer` を、既存の VLM → ImageGen パイプラインの 9 ステージ目として接続。

変更点:
- `from modules.vectorizer import Vectorizer` を import
- `Vectorizer` インスタンスを `main()` で 1 つだけ作って `run_one_cycle()` に渡す (ステートレスなので load/unload 不要)
- STAGE 8 (ImageGen unload) の直後に STAGE 9 (Vectorizer) を追加
  - `vectorize(generated, user_image=in_copy, debug_dir=cycle_dir / "vec_debug")` を呼ぶ
  - `vec_debug/` に 00〜06 の中間画像と `diagnostics.json` が落ちる
  - 結果を `cycle_dir / "strokes.json"` にピクセル単位で保存
- SUMMARY のキー一覧に `vectorize_s` を追加

`Vectorizer` は CPU 処理なので GPU メモリには影響しないが、STAGE 9 前後で `gpu_mem_snapshot` は念のため残した (定常で allocated=0.01GB のまま動くことの確認用)。

mm 変換 (`vectorize_to_panel`) は今回は使わない。Step B キャリブ未完了で本番値の `PanelFrame` が無いため、px 単位の `strokes.json` だけ残す。Step F 着手時に `panel` を渡す呼び出しに切り替える。

## 結果

### 3 サイクル安定性 (`--steps 4 --cycles 3`)

タイミング (秒):

| ステージ | Cycle 1 | Cycle 2 | Cycle 3 | 性質 |
|---|---|---|---|---|
| vlm_load_s | 10.31 | 7.69 | 7.77 | 初回 HF メタ問い合わせ ~2.5s 消え定常化 |
| vlm_predict_s | 4.10 | 3.67 | 3.70 | ほぼ一定 |
| vlm_unload_s | 0.52 | 0.59 | 0.55 | 一定 |
| image_gen_load_s | 3.06 | 2.56 | 2.63 | 同じく初回オーバヘッド消える |
| image_gen_warmup_s | 1.62 | 1.54 | 1.48 | 一定 |
| image_gen_generate_s | 2.68 | 2.69 | 2.72 | 完全一定 (4-step) |
| image_gen_unload_s | 0.74 | 0.74 | 0.75 | 一定 |
| **vectorize_s** | **0.17** | **0.16** | **0.15** | **CPU、画像内容に依存しない** |
| **cycle total** | **23.2s** | **19.6s** | **19.8s** | |

ストローク数のばらつき:

| | Cycle 1 | Cycle 2 | Cycle 3 | レンジ |
|---|---|---|---|---|
| n_strokes | 204 | 207 | 187 | 187〜207 (CV ≈ 5%) |
| n_points | 4054 | 4203 | 4130 | 4054〜4203 |
| total_length_px | 52632 | 54928 | 53700 | 52632〜54928 |

GPU リーク: 3 サイクル通して unload 後 allocated=0.01GB へ戻る。前回 (170305) と同じ挙動でリーク兆候なし。

### 中間画像の目視評価

入力スケッチ (黒い円+目2つ) に対して、3 サイクル全てで `subject="robot"` 推定。SDXL 出力 `generated.png` は毎回違うロボットを生成 (上半身 / 横向き / 円盤持ち)。各 `vec_debug/06_strokes.png` は **ロボットとして認識可能な線画** として出力された。

3 サイクル比較:

| | cycle 1 | cycle 2 | cycle 3 |
|---|---|---|---|
| 構図 | 上半身、両腕下げ | 半身斜め、両腕広げ + 棒 | 右寄り、左腕に円盤を持つ |
| ユーザ円+目の吸収先 | 顔の目 | 胸部メータ + 顔目 | **左腕の円盤として外出** |
| 差分後の本体保全 | OK | 胴体中央欠落 (円盤と本体メータが重畳) | 完璧 (円盤側のみ消去) |
| 06_strokes の完成度 | 良 | 中 (胴体スカスカ) | 良 |

### ストローク数の運用目標との対比

`docs/20260523_1650` で示された運用目標値:

| 指標 | 目標 | 今回 (3 サイクル平均) | 評価 |
|---|---|---|---|
| strokes | 30〜150 | **199** | ⚠️ 上限を 33% 超過 |
| total_points | 500〜2000 | **4129** | ⚠️ 2 倍超過 |
| total_length_px | 10000〜50000 | **53753** | ⚠️ ほぼ上限 |

3 指標すべて運用想定の上限〜超過。これは入力スケッチが「円+目2つ」と素朴で、SDXL が周辺をロボットの細部で埋めて細線が大量に出るパターン (前セッションの `experiment_diff.py` で見た「線が多い: strokes=479」と同系統)。Step F 着手時に `min_length=10 → 15` への調整、または `approx_epsilon=2.0 → 3.0` への変更で対処する想定。今は**フルパスが通ること**を優先する段階なのでデフォルトのまま運用記録を残す。

## つまずいた点

### 1. `_normalize_spaces` の修正が一度反映されていなかった

最初の指示で「`_normalize_spaces` 関数の置き換え + `import re` 追加」を提示したが、ユーザが実行した `python3 -m modules.prompt_builder` で `dragon dancing , line art, ...` の出力がそのまま再現された。原因は部分修正の反映漏れと推測される (前セッションの「別ターミナル `~/piper_test`」事案と同種のトラップを警戒)。

対処として、ファイル全文を heredoc で書き出す手順に切り替えて確実に置き換えた。**部分編集の反映確認には `grep -n "import re" modules/prompt_builder.py` のような明示確認を最初から入れた方が安全**。

### 2. `ls -td logs/vlm_to_image_*` がログファイルを拾った

統合テスト出力の確認コマンドを `RUN=$(ls -td logs/vlm_to_image_* | head -1)` と書いたが、`logs/` 直下には `vlm_to_image_<ts>.log` (ログファイル) と `vlm_to_image_<ts>/` (中間ファイルディレクトリ) の両方が落ちる。`ls -td` で `.log` ファイルが先に拾われ `RUN=logs/vlm_to_image_20260523_170305.log` になり、`$RUN/cycle_01/` で `NotADirectoryError`。

対処: `ls -td logs/vlm_to_image_*/` の末尾スラッシュを付ければディレクトリだけがマッチする。**ディレクトリ目的の glob は末尾 `/` を付ける** を以後のコマンドに統一。

## 学んだこと

### Vectorizer を加えても他ステージのパフォーマンスは無影響

vectorize_s は 3 サイクルで 0.15〜0.17s と完全に一定。CPU 処理で GPU は触らないため、ImageGen unload 後の clean な GPU 状態を壊さない。次サイクルの VLM load タイミングにも影響しない。**段階的スワップ設計が Vectorizer の追加で破綻しない**ことが実機で裏付けられた。

### シードランダムでも strokes 数が驚くほど安定

3 サイクルとも入力スケッチは同一、SDXL のシードは未指定 (毎回違う)。結果として構図は全く違うのに **strokes 数は 187〜207 (±5%) に収まる**。これは:

- ロボットの複雑度は構図によらずほぼ一定
- SDXL Turbo の細線生成傾向もシードで大差ない
- 差分・連結成分フィルタ・min_length の組み合わせが安定して同じ密度に収束させている

運用予測として「subject=robot のとき、min_length=10 で strokes は 180〜220 のレンジ」と仮定して問題なさそう。他の subject (dog, dragon 等) でも同様の安定性が出るかは別途確認が必要。

### ユーザの円+目の "吸収先" がシードでランダム

3 サイクルで観察された吸収パターン:
- cycle 1: 顔の目に転写
- cycle 2: 胸部メータに転写 (+ 顔目残り)
- cycle 3: 本体外の "持ち物" として円盤化

cycle 3 のパターン (持ち物として外出し) が差分検出にとっては理想的。本体が無傷で残り、円盤だけが差分マスクで除外される。逆に cycle 2 のように「胸部メータが本体と一体化した位置に来る」と、ユーザの円を除外するための dilate kernel=21 が本体線まで巻き添えで消してしまう。

これは **prompt や ControlNet 強度を調整して "持ち物パターン" を意図的に誘導できれば** 安定して良い結果が出る可能性がある示唆。ただし今は深追いせず、Step F 着手時の品質チューニング項目として記録に留める。

### 全身が画角に収まらない構図が 3/3

入力スケッチが画面上半分に「円+目」を置く構造のため、SDXL が頭部をその位置に配置すると自然と足が画角外に流れる傾向。3 サイクルすべて足先がカット気味。本物の median 合成キャプチャ (ユーザがホワイトボードの中央に描く) で同じ傾向が出るかは要観察。

## 次にやること

### 短期 (実機接続前の宿題)

- 🔲 ストローク列を `trajectory.py` で軌道に変換 → `robot.py` に流す結合テスト (Step F 着手の前哨)
- 🔲 本物の median 合成キャプチャ画像 (実カメラ入力) でフルパス検証

### 中期 (Step F 本体)

- 🔲 Step B キャリブ完了後、`test_vlm_to_image.py` を `vectorize_to_panel()` 呼び出しに切り替え (panel を渡すだけで mm 単位 strokes が得られる)
- 🔲 Enter トリガーと組み合わせて Cycle 0 から Cycle N までの繰り返し動作確認 (実機描画)

### 長期 (運用調整)

- 🔲 ストローク数が運用目標 (30〜150) を超過する場合の `min_length` / `approx_epsilon` 調整パラメータの体系化
- 🔲 subject 別の strokes 数のばらつき計測 (robot, dog, dragon, ...)
- 🔲 prompt 文言調整による「ユーザの絵を本体外に追いやる」誘導の検証 (cycle 3 パターンの再現性向上)

### Claude Code との同期

- 🔲 本ファイルと修正後の `modules/prompt_builder.py` / `scripts/test_vlm_to_image.py` の commit を Claude Code 側と共有
- 🔲 `MILESTONES.md` に「✅ フルパス統合 (VLM → ImageGen → Vectorizer) 完了」を `●` として追記提案

## 参照

- 統合テストアーティファクト:
  - 1 サイクル動作確認: `~/draw_piper/logs/vlm_to_image_20260523_170305/cycle_01/`
  - 3 サイクル安定性: `~/draw_piper/logs/vlm_to_image_20260523_170947/cycle_{01,02,03}/`
- 統合テストログ: `~/draw_piper/logs/vlm_to_image_20260523_17*.log`
- 修正済みファイル:
  - `~/draw_piper/modules/prompt_builder.py` (カンマ前スペース修正)
  - `~/draw_piper/scripts/test_vlm_to_image.py` (STAGE 9 vectorize 追加)
- 関連モジュール: `modules/vlm.py`, `modules/image_gen.py`, `modules/vectorizer.py`
- 前段ドキュメント:
  - `docs/20260523_1530_step_c_completion.md` (8 ステップ確定パラメータ)
  - `docs/20260523_1640_vlm_to_image_integration.md` (VLM → ImageGen 段階的スワップ実証)
  - `docs/20260523_1650_vectorizer_class_implementation.md` (Vectorizer クラス化)
