# Live Collaborative Drawing System — Design v0.5 (Topic Catalog Model)

> 日時: 2026-05-22 23:30 (JST)
> 関連既存ファイル: `20260521_1757_drawing_system_v04_design.md` (v0.4), `20260522_2250_vlm_vram_measurement.md`
> 前バージョン: v0.4
> 注: v0.4 のシステムループ・キャプチャ機構・軌道再生は**そのまま維持**。VLMのタスク定義と出力構造のみ差し替える。

## v0.4 → v0.5 の差分サマリー

| 観点 | v0.4 | v0.5 |
|---|---|---|
| お題の出所 | なし (VLM が自由に推測) | **物理カード** (主体・場所・動作 各1枚) |
| お題を知る主体 | (お題の概念がない) | **ユーザのみ** (システムは引かれたカードを知らない) |
| VLM のタスク | 自由意図予測 (subject / missing / next_likely を自然言語で記述) | **カタログ内分類** + 不足要素特定 |
| VLM への入力 | スケッチ画像 + 自由作文プロンプト | スケッチ画像 + **選択肢リスト** + JSON要求プロンプト |
| VLM の出力 | 自然言語 (パース要) | **JSON** (パース確実) |
| `IntentPrediction` のフィールド | `subject: str`, `missing: list[str]`, `next_likely: str` | `subject: TopicEntry`, `location: TopicEntry`, `action: TopicEntry`, `missing_elements: list[str]`, `confidence: float` |
| prompt_builder | 文字列を SDXL に渡すだけ | **英訳テーブル** で `{subject_en}_{location_en}_{action_en}` をテンプレに埋める |

**変更しないもの**:
- median 合成キャプチャ (10枚/2秒)
- Enter トリガー (ユーザがカードを引いて準備したら Enter)
- SDXL Turbo + Lineart ControlNet
- OpenCV ベクトル化
- piper_sdk 直叩きでの軌道再生
- threading 構成 (Robot Thread + Prep Thread + Trigger Thread)

つまり v0.5 は **Step C (VLM + prompt_builder) の内部仕様改訂**であり、システム全体のアーキテクチャは v0.4 そのまま。

## v0.5 の核となる発想

### 「ロボットがユーザの意図を当てに来る」体験設計

ユーザは物理カードを3枚引く。引いたお題はユーザだけが知る。

```
ユーザだけ知ってる: 「公園で走る犬」 (3枚の組み合わせ)
ユーザは描き始める: 犬の輪郭だけ描く
↓
[Enter 押下]
↓
median キャプチャ → VLM 推測 → 「これは犬を描こうとしている。胴体と脚と背景がない」
↓
SDXL: 「公園で走る犬」の線画生成 (推測ベース)
↓
ロボットが描き足す
↓
ユーザ: 「お、ちゃんと犬だってわかってる！」
```

「お題を当てに来る」エンタメ性と、「不足要素を補う」協調性の両方を成立させる。

### なぜ VLM に「選択肢」を渡すのか

VLM (Qwen2.5-VL) は自由作文だと:
- 「これは抽象アートかもしれない」
- 「これは未来の宇宙船の設計図かもしれない」

のように無限の解釈空間を持つ。実測 (2026-05-22) でも、未完成スケッチに対して鼻と目を混同するなどの誤推測が見られた。

選択肢付きのタスクに変換すると:
- 主体は事前定義の20個のうちどれか?
- 場所は事前定義の20個のうちどれか?
- 動作は事前定義の20個のうちどれか?

→ **絞り込み済みの分類問題**になり、VLM の出力が大幅に安定する。これは VLM の得意な visual grounding に近いタスク。

### お題プールが SDXL の語彙とも整合する

お題プールは「ユーザ向け日本語ラベル」と「SDXL向け英語キーワード」のペアで管理する。VLM が選んだものを SDXL に渡すパスが**辞書ルックアップ1回**で済む。

## お題プールの設計

### 構造

```python
@dataclass(frozen=True)
class TopicEntry:
    """お題カタログの1エントリ。日英ペアで管理。"""
    ja: str          # ユーザ表示用 (カードに書く文字、ログ表示)
    en: str          # SDXL プロンプト用
    sdxl_hint: str = ""  # 追加の SDXL ヒント (省略可)


SUBJECTS: list[TopicEntry] = [
    TopicEntry("犬", "dog"),
    TopicEntry("猫", "cat"),
    TopicEntry("ロボット", "robot"),
    TopicEntry("鳥", "bird"),
    TopicEntry("魚", "fish"),
    TopicEntry("龍", "dragon"),
    TopicEntry("ユニコーン", "unicorn"),
    TopicEntry("カエル", "frog"),
    TopicEntry("くま", "bear"),
    TopicEntry("うさぎ", "rabbit"),
    TopicEntry("ぞう", "elephant"),
    TopicEntry("ペンギン", "penguin"),
    # ... 15-20個に拡張予定
]

LOCATIONS: list[TopicEntry] = [
    TopicEntry("公園", "park"),
    TopicEntry("海", "by the sea"),
    TopicEntry("山", "in the mountains"),
    TopicEntry("宇宙", "in space"),
    TopicEntry("森", "in the forest"),
    TopicEntry("砂漠", "in the desert"),
    TopicEntry("月の上", "on the moon"),
    TopicEntry("お風呂", "in a bath"),
    TopicEntry("教室", "in a classroom"),
    TopicEntry("台所", "in a kitchen"),
    # ... 15-20個
]

ACTIONS: list[TopicEntry] = [
    TopicEntry("走っている", "running"),
    TopicEntry("寝ている", "sleeping"),
    TopicEntry("飛んでいる", "flying"),
    TopicEntry("食べている", "eating"),
    TopicEntry("踊っている", "dancing"),
    TopicEntry("歌っている", "singing"),
    TopicEntry("読書している", "reading a book"),
    TopicEntry("泳いでいる", "swimming"),
    TopicEntry("ジャンプしている", "jumping"),
    TopicEntry("座っている", "sitting"),
    # ... 15-20個
]
```

組み合わせ数: 20 × 20 × 20 = **8,000通り**。物理カードのデッキとしては、各カテゴリ20枚で十分多様。

### 各カテゴリの設計指針

| カテゴリ | 目安数 | 設計指針 |
|---|---|---|
| SUBJECTS | 15〜20 | 視覚的に判別しやすい主体。線画にしやすいもの。「物」より「生き物」が線画として活きる |
| LOCATIONS | 15〜20 | 背景要素として描けるもの。あまりに抽象的 (例: 「夢の中」) は避ける |
| ACTIONS | 15〜20 | ポーズで表現できる動作。「考えている」より「ジャンプしている」のように動きが見えるもの |

「SDXL 線画として安定して生成できるか」が裏の基準。初期は20個程度でスタートし、運用で「これは生成が壊れる」「これは VLM が判別できない」と判明したものを差し替える。

### 「該当なし」を許す設計

VLM が「どれにも当てはまらない」と判断するケースは普通にある。`TopicEntry` 以外に **`UNKNOWN`** を返せる構造にする。

```python
UNKNOWN_SUBJECT = TopicEntry("不明", "abstract shape")
```

VLM の選択肢提示プロンプトにも「該当なしの場合は『不明』と答えてください」と明記。

## カードの物理運用

### カード仕様

- 1カテゴリ = 1デッキ。3つのデッキ。
- 各デッキ20枚。
- 表面: 日本語ラベル (手書き or 印字)。
- カードは **ホワイトボード上には貼らない** (システムが読み取らないため意味がない)。
- ユーザの手元または描画板の脇に置く。

### 操作フロー

```
1. ユーザが各デッキから1枚ずつ引く (合計3枚)
2. 引いたカードを手元に置く (システムには見えない)
3. ホワイトボードに絵を描き始める
4. 任意のタイミングで Enter キーを押す (描画継続中でも、描き始めてすぐでも OK)
5. システムが median キャプチャ → VLM 推測 → SDXL 生成 → ロボット描画
6. ロボット描画完了 → ユーザがさらに描き足す or 次のサイクルへ → Enter
```

カード自体はシステムが触らない、純粋にユーザの内的状態を保つ仕掛け。

### カードの作成

MVP では:
- 厚紙に手書きでマジック書き → 60枚 (20 × 3)
- カテゴリごとに色分け (例: 主体=赤、場所=青、動作=緑) で判別しやすく
- 展示用に作り直すなら印刷した方が綺麗

## 新しい VLM プロンプト

### プロンプト本体 (日本語、選択肢付き、JSON要求)

```text
あなたはユーザが何を描こうとしているかを推測するアシスタントです。

ユーザは「主体」「場所」「動作」のお題カードを各カテゴリから1枚ずつ引いて、
それを元にホワイトボードに絵を描こうとしています。お題そのものはあなたには
教えられません。あなたはスケッチを見て、以下の選択肢の中から最も近いものを
推測してください。

【主体の選択肢】
{subjects_list}

【場所の選択肢】
{locations_list}

【動作の選択肢】
{actions_list}

スケッチが選択肢のどれにも当てはまらないと判断したカテゴリは "不明" としてください。

回答は以下の JSON 形式のみで返してください。前後に説明文を入れないでください。

{
  "subject_ja": "<選択肢の中から1つ、または '不明'>",
  "location_ja": "<選択肢の中から1つ、または '不明'>",
  "action_ja": "<選択肢の中から1つ、または '不明'>",
  "missing_elements": ["<まだ描かれていない要素を日本語で短く列挙>"],
  "confidence": <0.0 から 1.0 の数値。スケッチがほぼ白紙なら 0.1 程度、明確に判別できれば 0.8+>
}
```

`{subjects_list}` 等は実行時に展開:

```
- 犬
- 猫
- ロボット
- 鳥
- ...
```

### JSON 強制の効きと、効かなかった場合のフォールバック

Qwen2.5-VL は JSON 出力を要求するとほぼ素直に従う傾向があるが、保険として:

1. 出力先頭/末尾に `` ```json `` などの fence が付くケースを除去
2. JSON パースに失敗したら、`subject_ja` / `location_ja` / `action_ja` を `"不明"` で埋めた fallback を返す
3. fallback の場合、`confidence=0.0` とする

これにより VLM が完全に意味不明な出力を返しても、システムは止まらない。

## 新しい `IntentPrediction` (= `TopicGuess`)

v0.4 の `IntentPrediction` を `TopicGuess` に改名し、構造化:

```python
@dataclass
class TopicGuess:
    """カタログ内分類によるユーザ意図推測。"""
    subject: TopicEntry             # SUBJECTS の中の1つ、または UNKNOWN_SUBJECT
    location: TopicEntry            # LOCATIONS の中の1つ、または UNKNOWN_LOCATION
    action: TopicEntry              # ACTIONS の中の1つ、または UNKNOWN_ACTION
    missing_elements: list[str]     # 「胴体」「背景」など、自然言語のまま
    confidence: float               # 0.0 - 1.0
    raw_text: str                   # モデル生出力 (デバッグ用)
    infer_time_s: float
    n_tokens: int

    def is_certain(self, threshold: float = 0.3) -> bool:
        """confidence が閾値を超えているか。"""
        return self.confidence >= threshold

    def has_known_subject(self) -> bool:
        return self.subject is not UNKNOWN_SUBJECT
```

### `to_sdxl_prompt()` メソッドは持たせない

責務分離: TopicGuess は「VLM の出力」であり、SDXL プロンプト構築は `prompt_builder.py` の責務。
`TopicGuess.subject.en` 等を `prompt_builder.build(guess) -> str` が拾う。

## prompt_builder の新仕様

```python
# modules/prompt_builder.py

from modules.topic import TopicGuess, UNKNOWN_SUBJECT

_BASE_TEMPLATE = (
    "{subject_en} {action_en} {location_en}, "
    "line art, black on white, simple, clean lines, "
    "minimal detail, no shading, white background"
)

_FALLBACK_TEMPLATE = (
    "simple line drawing, abstract shapes, "
    "black on white, clean lines, white background"
)


def build_prompt(guess: TopicGuess) -> str:
    """TopicGuess を SDXL Turbo 用の英語プロンプトに変換する。"""
    if not guess.is_certain() or guess.subject is UNKNOWN_SUBJECT:
        return _FALLBACK_TEMPLATE
    return _BASE_TEMPLATE.format(
        subject_en=guess.subject.en,
        action_en=guess.action.en,
        location_en=guess.location.en,
    )
```

### 不足要素 (`missing_elements`) の扱い

VLM が「胴体がない」と言っているとして、SDXL プロンプトに「draw the body」と入れるべきか?

→ **入れない**。SDXL Turbo + Lineart ControlNet は「現在のスケッチを ControlNet に入れて、足りない部分を生成」する設計なので、SDXL 側は完成形を生成すれば良く、差分は ControlNet が見る。

`missing_elements` はログとデバッグ用に保持するだけ。将来「明確に欠けている要素だけ描く」モードが必要になったら使う。

## 影響を受けるモジュール

### 新規追加

- `modules/topic.py`: お題プール定義 + `TopicEntry` + `TopicGuess` + ユーティリティ

### 大幅変更

- `modules/vlm.py`: プロンプトを選択肢付き JSON 要求に切り替え、出力を `TopicGuess` で返す
- `modules/prompt_builder.py`: TopicGuess を受け取り英訳テンプレを返す

### 軽微変更

- `modules/__init__.py`: 必要に応じて export 追加

### 影響なし

- `modules/camera.py` (median 合成)
- `modules/robot.py` (Step A 完了済み)
- `modules/image_gen.py` (Step C SDXL Turbo 側)
- `modules/vectorizer.py` (Step D)
- `modules/trajectory.py`
- `modules/start_point.py`
- `modules/trigger.py`
- `orchestrator.py`

→ Claude Code が Step E (run_draw_test.py) で進めている領域には**一切干渉しない**。

## 未解決の論点

### 1. お題プールの初期語彙の決定

20 × 20 × 20 の具体的な語彙リストを誰がいつ決めるか。
- 案 A: 設計v0.5 確定時に固定リストとして commit する
- 案 B: `topic.py` 内に最初 5 × 5 × 5 程度で書き、運用で増やす
- **推奨: 案 B**。MVP は語彙が少ない方がデバッグしやすい。

### 2. VLM の選択肢提示時のトークン数増加

選択肢を 60 個 (20×3) プロンプトに含めると、入力トークン数が増えて推論時間が伸びる。
- 実測: 計測時のプロンプトで 152 トークン出力で 6.4 秒
- 選択肢追加で入力 +200 トークン程度の見込み → 推論時間への影響は 0.5 秒以下のはず
- 要検証

### 3. `confidence` の閾値設定

VLM が返す `confidence` の絶対値は VLM 依存で、安定しない可能性がある。
- 運用しながら閾値 (現案: 0.3) を調整
- 閾値以下なら「描画スキップ」「ランダム要素を補う」など別フォールバック動作も検討

### 4. 同じお題が連続で出ることへの対処 (将来)

ユーザがカードをシャッフルせずに引くと同じ組み合わせが続く可能性。MVP では人間運用 (シャッフル徹底) で吸収。物理カードを使う以上、システム側ではこれ以上できることがない。

### 5. カード作成 (ハードウェア準備)

実装と並行して、物理カード 60 枚を作る必要がある。MVP では:
- 厚紙 + マジック手書き
- カテゴリごとに色分け
- 角を丸めて引きやすく

これは**実装が終わってから**作ればよい (テストは画面表示でデバッグできる)。

## 移行戦略

### 段階1: 基盤実装 (今日〜明日)

- 🔲 `modules/topic.py` を新規作成 (小さなプール 5×5×5)
- 🔲 `modules/vlm.py` を改修 (`TopicGuess` を返すように)
- 🔲 `modules/prompt_builder.py` を実装 (`build_prompt(guess) -> str`)
- 🔲 スモークテスト: ダミー画像で TopicGuess が返るか、prompt_builder で英語が組めるか

### 段階2: プール拡張 (明日以降)

- 🔲 SUBJECTS / LOCATIONS / ACTIONS を各 15〜20 個に拡張
- 🔲 各語彙の SDXL 出力品質を `image_gen.py` 完成後にチェック
- 🔲 不安定な語彙は差し替え

### 段階3: 物理カード作成

- 🔲 厚紙にラベル記入 (合計 60 枚)
- 🔲 デッキとして使えるよう束ねる

### 段階4: 統合テスト

- 🔲 Enter → median → VLM → prompt_builder → SDXL → vectorize → robot draw のフルパス
- 🔲 ユーザがお題を引いて実演 → ロボットの推測が当たるか・外れるかを目視評価

## v0.4 ドキュメントとの関係

v0.4 ドキュメント (`20260521_1757_drawing_system_v04_design.md`) は **無効化しない**。
システムループ、キャプチャ機構、軌道再生、threading 構成、Step A/B/D/E/F/G の方針は v0.4 のまま有効。

v0.5 で上書きされるのは Step C (VLM + 画像生成) の **VLM 部分の内部仕様** のみ。

Claude Code が v0.4 をベースに進めている Step E (run_draw_test.py) には影響がない。
Step C に着手する段階で v0.5 を参照すること。

## 参照

- v0.4 設計: `20260521_1757_drawing_system_v04_design.md`
- VLM VRAM 計測: `20260522_2250_vlm_vram_measurement.md`
- Claude Code 引き継ぎ: `HANDOFF_TO_CLAUDE_CODE(1).md` (v0.4 ベース、Step C 部分のみ v0.5 で更新)
