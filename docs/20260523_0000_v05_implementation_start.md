# 設計v0.5 実装着手 (topic / prompt_builder / vlm 改修)

> 日時: 2026-05-23 00:00 (JST)
> 関連既存ファイル: `20260522_2330_drawing_system_v05_design.md`, `20260522_2250_vlm_vram_measurement.md`

## 実施したこと

設計v0.5「お題カタログモデル」の段階1 (基盤実装) に着手。Claude Code が Step E (`run_draw_test.py` / 実機統合) を進めるのと並行して、別ターミナルで以下の3モジュールを実装した。

1. `modules/vlm.py` (旧 skeleton) を一度 v0.4 仕様 (自由作文 + 正規表現パース) でクラス化
2. その後 `modules/topic.py` を新規作成 (お題カタログ + TopicGuess + JSON パーサ)
3. `modules/prompt_builder.py` を実装 (TopicGuess → SDXL 英語プロンプト)
4. `modules/vlm.py` を設計v0.5 仕様 (選択肢付き JSON 要求 → TopicGuess 返却) に改修するスクリプトを準備 (実行は持ち越し)

## 結果

### `modules/vlm.py` (中間版: v0.4 仕様) ✅

設計v0.5 への切り替え前に、まず計測スクリプトをクラス化した版を作成。

- `VLM` クラス: `load()` / `unload()` / `predict_intent()` / context manager 対応
- `IntentPrediction` dataclass: `subject` / `missing` / `next_likely` / `raw_text` / `to_text()`
- 正規表現パースで「主題 / 未完成 / 次に描き足されそうな部分」を抽出
- 画像入力は `PIL.Image | str | Path | np.ndarray` を受け付ける

実行結果:

```
[vlm] loaded in 9.0s  (前回 166s → キャッシュで18倍速)
[vlm] inferred in 6.79s (152 tok, 22.4 tok/s) [OK]

=== IntentPrediction ===
  parsed       : True
  subject      : '人間の顔のスケッチ'
  missing      : ['目と鼻が描かれていますが', '口や顔の他の部分はまだ描かれていません']
  next_likely  : '頭部全体、顔の輪郭、そして口や鼻の詳細な部分が追加されると考えられます'
```

`missing` のパースが「描かれているもの」と「描かれていないもの」を混在させる問題が判明 (自然言語の逆接構造を区切り文字でしか分割していないため)。

→ パーサ強化ではなく、**お題カタログモデル** で構造そのものを変える方針に発展。

### 設計v0.5 への発展経緯 ✅

`missing` パース問題への対処を議論する中で、以下の設計判断に至った:

- **物理カードでお題提示**: ユーザは「主体・場所・動作」の各デッキから1枚ずつ引く
- **VLM は選択肢カタログを知るが、引かれたカードは知らない**: ロボットが意図を「当てに来る」エンタメ性を保つ
- **VLM タスクを自由作文から分類問題へ**: 8000通り (20×20×20) のカタログ内分類で出力安定性が向上
- **JSON 強制出力**: パース問題を構造的に解決
- **`prompt_builder` は辞書ルックアップで日英変換**: TopicGuess の en フィールドを SDXL テンプレに差し込むだけ

設計v0.5 ドキュメントを別ファイル (`20260522_2330_drawing_system_v05_design.md`) として作成。v0.4 の骨格 (median 合成、SDXL Turbo、piper_sdk、threading) は維持し、VLM タスクと出力構造のみ差し替える形。

### `modules/topic.py` 新規作成 ✅

```bash
python3 modules/topic.py
```

実行結果:

```
=== カタログ ===
SUBJECTS (5): ['犬', '猫', '鳥', 'ロボット', '龍']
LOCATIONS (5): ['公園', '海', '山', '宇宙', '森']
ACTIONS (5): ['走っている', '寝ている', '飛んでいる', '食べている', '踊っている']
組み合わせ数: 125

=== format_choices ===
- 犬
- 猫
- 鳥
- ロボット
- 龍

=== ランダム例 ===
  1: ロボット / 山 / 寝ている  (en: 'robot sleeping in the mountains')
  2: 鳥 / 森 / 飛んでいる  (en: 'bird flying in a forest')
  3: 龍 / 山 / 食べている  (en: 'dragon eating in the mountains')

=== JSON パーステスト ===
  case1: subject=犬(dog) location=公園 action=走っている missing=['胴体', '脚'] conf=0.85
  case2: subject=猫 location=不明 action=寝ている conf=0.5
  case3 (recovers UNKNOWN for unknown subject): subject=不明(abstract shape) location=海
  case4 (broken): subject=不明 location=不明 conf=0.0

=== TopicGuess ===
  to_text: 犬が公園で走っている (confidence=0.85)
  is_certain(0.3): True
  has_known_subject: True
```

提供機能:
- `TopicEntry` (frozen dataclass): ja / en / sdxl_hint
- カタログ定数: `SUBJECTS`, `LOCATIONS`, `ACTIONS` (各5個、MVP)
- `UNKNOWN_SUBJECT` / `UNKNOWN_LOCATION` / `UNKNOWN_ACTION`: VLM が「不明」と返した場合の安全値
- `TopicGuess` dataclass: VLM 出力の構造化型 (confidence, raw_text, infer_time_s 等含む)
- `find_subject/location/action(ja_label)`: 日本語ラベルから TopicEntry へ
- `format_choices(catalog)`: VLM プロンプト用に箇条書きへ
- `parse_vlm_json(raw_text)`: JSON 応答をパースして `(subject, location, action, missing, confidence)` を返す。fence 剥がし対応、カタログ外の値は UNKNOWN に降格、壊れた JSON は例外なく全 UNKNOWN
- `random_topic_for_test()`: 開発時のスモークテスト用ガチャ (実運用は物理カードなので未使用)

全テストケース成功:
- ✅ 正常な JSON: subject="犬" → `TopicEntry("犬", "dog")` に解決
- ✅ \`\`\`json fence 付き: 剥がして解決、"不明" → UNKNOWN
- ✅ カタログ外 ("恐竜"): UNKNOWN_SUBJECT に降格、例外なし
- ✅ 壊れた JSON: 全 UNKNOWN、例外なし、confidence=0.0

### `modules/prompt_builder.py` 実装 ⚠️

実装は完了したが、スモークテストでつまずいた (下記)。コード自体は正しい。

設計:
- `build_prompt(guess: TopicGuess, confidence_threshold: float = 0.3) -> str`
- `_BASE_TEMPLATE`: `"{subject_en} {action_en} {location_en}, line art, black ink on white, simple, clean lines, minimal detail, no shading, white background"`
- `_FALLBACK_TEMPLATE`: `"simple abstract line drawing, black ink on white, clean lines, minimal detail, white background"`
- confidence 閾値未満 or subject が UNKNOWN なら fallback
- `_normalize_spaces()` で en が空 (UNKNOWN_LOCATION 等) の場合の連続空白を畳む

## つまずいた点

### 1. ⚠️ 別ターミナルでの作業ディレクトリ取り違え

VLM計測完了後の作業再開時、別タブのターミナルが `~/piper_test` で開いていたまま `cat > modules/topic.py << ...` を実行し、`No such file or directory` エラーが連発した。

```
jizaiedev2026@...:~/piper_test$ cat > modules/topic.py
bash: modules/topic.py: No such file or directory
```

`~/piper_test` は Piper 実機テスト用の別プロジェクト (calibrate_canvas_interactive.py / draw_square_wall.py / contact_detect_wall.py 等の重要な既存資産が入っている、削除厳禁)。

対処:
- `cd ~/draw_piper && pwd` で正しいディレクトリを明示
- `which python3` で venv が `/home/jizaiedev2026/draw_piper/venv/bin/python3` を指していることを確認
- 以降は毎回 `~/draw_piper` 確認を前提とする

### 2. ⚠️ コピペ時に Markdown 地の文がコマンドとして実行された

Claude 側の出力がマークダウン本文とコードブロックを混在していたため、ターミナルに丸ごとペーストすると地の文行が bash コマンドとして解釈され、大量のエラーが出た。

対処: 以降の Claude 側はコードブロック単独のメッセージに分離する運用にした。

### 3. 🔲 `python3 modules/prompt_builder.py` で `ModuleNotFoundError: No module named 'modules'`

`prompt_builder.py` は `from modules.topic import TopicGuess, UNKNOWN_SUBJECT` を持つが、ファイル直接実行 (`python3 modules/prompt_builder.py`) だと、Python は `modules/` ディレクトリ内から `modules` パッケージを参照しようとして見つからない。

解決策: パッケージ実行モードを使う:

```bash
python3 -m modules.prompt_builder
```

`modules/topic.py` の単体テストは相対 import が無かったので `python3 modules/topic.py` で動いていたが、今後 `modules/*` 間で import が増えると同じ問題が出る。以降 `-m` 形式に統一推奨。

→ **次回再開時、まずこれで再実行する**。

## 学んだこと

### v0.5 への発展は「パース改善」を超えた構造変更だった

当初は「missing パースを強化するか、JSON 要求にするか」レベルの問題に見えたが、ユーザの一言「お題モードでいい」「VLM はお題の選択肢は知ってる」「物理メディアでやる」によって、以下が一気に整理された:

- 物理カードでロボット側は答えを知らない → エンタメ性確保
- VLM はカタログを知る → 分類問題化、出力安定
- 日本語ユーザ表示 / 英語機械処理 の自然な分離
- prompt_builder が辞書ルックアップで済む単純さ
- カタログを `modules/topic.py` に集約することで責務が明確

ユーザの直観が、機能要件と実装難度の両方を一発で下げた良い設計判断だった。

### Python パッケージ実行の落とし穴

`from modules.X import Y` を持つモジュールを直接 `python3 modules/X.py` で実行すると失敗する。`-m modules.X` で実行すると `~/draw_piper` がパッケージルート扱いになり解決する。これは v0.4 の orchestrator.py から import するなら問題なかったが、各モジュールの単体スモークテスト時に毎回踏みうる罠。

### Claude とのコピペプロトコル

長文回答にコマンドと地の文が混在するとペースト事故を起こす。「コードブロックのみのメッセージ」に分離する運用を確立した。

### Claude Code との並行作業ルールは機能している

`requirements-vlm.txt` を `requirements.txt` から分離する戦略、`modules/vlm.py` (こちら担当) と `modules/robot.py` / `run_draw_test.py` (Claude Code 担当) のファイル単位分離、これにより一度もマージ衝突を起こしていない。Claude Code が Step E に専念している間、独立して Step C の VLM 側を進められた。

## 次にやること

### 短期 (次回再開時 5分以内に解決)

- 🔲 `python3 -m modules.prompt_builder` でスモークテスト再実行
  - 期待: 高信頼度ケースは `dog running in a park, line art, ...`、低信頼度・subject 不明は fallback、location 不明は空白畳み込み後の `dragon dancing, line art, ...`
- 🔲 `python3 -m modules.topic` でも同じ結果が出るか確認 (パッケージ実行統一)

### 中期 (Step C の続き)

- 🔲 `modules/vlm.py` を v0.5 仕様 (選択肢付き JSON 要求 → `TopicGuess` 返却) に改修
  - 既に書き換え用のコードは準備済み (本日のチャット内)
  - 既存 `modules/topic.py` をそのまま import する
  - スモークテストで `python3 -m modules.vlm` を確認
- 🔲 SDXL Turbo + Lineart ControlNet の VRAM 単体計測 (`scripts/measure_sdxl_vram.py`)
- 🔲 VLM + SDXL Turbo の同居計測 (本番想定)

### 中長期 (段階2 〜 3)

- 🔲 SUBJECTS / LOCATIONS / ACTIONS を各 15〜20 個に拡張 (`modules/topic.py` 内編集のみ)
- 🔲 各語彙の SDXL 出力品質を `image_gen.py` 完成後にチェック、不安定な語彙は差し替え
- 🔲 物理カード作成 (合計 60 枚、厚紙 + マジック手書き、カテゴリ色分け)

### Claude Code との同期

- 🔲 Claude Code の Step E (run_draw_test.py) 完了状況の確認
- 🔲 Step E 完了後、Step C/D に Claude Code が向かう前に v0.5 設計 (本日生成) を共有して image_gen.py / vectorizer.py の方向性を合わせる

## 参照

- 設計 v0.5: `20260522_2330_drawing_system_v05_design.md`
- VLM VRAM 計測: `20260522_2250_vlm_vram_measurement.md`
- 設計 v0.4 (骨格): `20260521_1757_drawing_system_v04_design.md`
- 実装済みファイル:
  - `~/draw_piper/modules/vlm.py` (現在: v0.4 中間版、v0.5 改修待ち)
  - `~/draw_piper/modules/topic.py` (v0.5 完全実装)
  - `~/draw_piper/modules/prompt_builder.py` (v0.5 実装、スモークテスト待ち)
  - `~/draw_piper/scripts/measure_vlm_vram.py` (計測スクリプト)
  - `~/draw_piper/scripts/test_sketch.jpg` (ダミー入力)
- 要注意ディレクトリ: `~/piper_test/` (Piper 実機テスト用、削除厳禁、混同注意)
