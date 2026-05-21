# Live Collaborative Drawing System — Design v0.4 (Turn-Based MVP)

> 日時: 2026-05-21 17:57 (JST)
> 関連既存ファイル: `piper_ros2_setup_progress.md`, `piper_ros2_setup_progress_append_2026-05-21.md`, `HANDOFF_TO_CLAUDE_CODE.md`
> 前バージョン: v0.3（プロジェクト本文 "Live Collaborative Drawing Robot System"）
> 注: 同日 17:45 の同名仕様書は **本ファイルで上書き**。古い方は破棄してよい。

## 実施したこと

スケジュールロス（5日分）を吸収するため、v0.3 の常時観察・反射的介入モデルから、**ターン制ドローイングモデル** に大幅簡略化した設計を策定。VLM の役割を「構図批評」から「ユーザ意図予測」一本に切り替え、Reflective Memory / OpenCV 学習 / Reflection Layer は MVP から削除。技術選定も全てローカル＋シンプル方向で確定。

加えて、「ロボットアームがキャプチャに写り込む」問題への対処として、**時間方向 median 合成** を採用。

## 結果

### 新しいシステムループ

```
[キーボード Enter 押下]
   ↓
[Cycle 0: ウォームアップ]
   ├─ 30秒待機
   └─ 静止画キャプチャ（median合成）→ VLM意図予測
      → 生成プロンプト構築 → 画像生成 → ベクトル化 → 軌道データ
   ↓
[Cycle N: 描画 + 並行下準備]
   ├─ Robot Thread: 軌道データを実機に再生（最大2分）
   └─ Prep Thread: 次サイクル用のデータを並行構築
        ├─ 静止画キャプチャ（median合成）
        ├─ VLM意図予測（更新）
        ├─ 画像生成
        ├─ ベクトル化
        └─ 開始点決定（空白領域 or ランダム）
   ↓
[2分経過 or 描画完了]
   ├─ 現在のストローク終点まで描いて停止
   ├─ ペン上げ
   └─ Cycle N+1 へ
```

### 確定した技術スタック

| レイヤー | 採用 | 理由 |
|---|---|---|
| VLM | Qwen2.5-VL-7B-Instruct (4bit量子化) | 日本語OK、INT4で6〜8GB、SDXL Turboと共存可能 |
| 画像生成 | SDXL Turbo + Lineart ControlNet | 1〜4step (〜2秒)、線画制御が成熟、VRAM 10GB前後 |
| ベクトル化 | OpenCV (skeletonize → findContours → approxPolyDP) | 線/点の領域属性付与は後段で判定 |
| 軌道再生 | piper_sdk 直叩き（EndPoseCtrl） | 平面描画にMoveIt2はオーバースペック。既存テスト資産が再利用可能 |
| 開始トリガー | キーボード Enter | 物理ボタンは後回し。`trigger.py` で抽象化しておけば後で差し替え可能 |
| キャプチャ | **時間方向 median 合成（10枚 / 2秒間）** | アーム写り込みを背景差分の原理で除去。実装20行以下 |

### キャプチャ方式（詳細）

```python
# modules/camera.py の骨格
def capture_median(cap, n_frames=10, interval=0.2):
    """
    n_frames 枚を interval 秒間隔で撮影し、ピクセルごとの中央値を取って返す。
    動いているロボットアームは各ピクセルでマイノリティになり消える。
    静止しているユーザの描画だけが残る。
    """
    frames = []
    for _ in range(n_frames):
        ret, frame = cap.read()
        if ret:
            frames.append(frame)
        time.sleep(interval)
    return np.median(np.stack(frames), axis=0).astype(np.uint8)
```

- 10枚 × 0.2秒 = **2秒で完了**
- median は外れ値（アーム）に頑健
- 描画の一部がたまたまアームに隠されても、他の9枚で多数決を取れる
- 計算コストはほぼゼロ（OpenCV / numpy のみ）

#### この方式が破綻するパターン

| パターン | 対処 |
|---|---|
| アームが10枚すべて同じ場所にいる（静止） | 描画中なら稀。サイクル冒頭はアームが ready pose に居る瞬間を狙う |
| 撮影中にユーザが激しく動く | ユーザの手も median で消える可能性。手は元々描画対象でないのでOK |
| medianがブレてぼやけた画像になる | 必要なら mode (最頻値) や **時間方向の min**（黒い線が暗いほうにブレる）に切替検討 |

### VRAM 16GB 配分計画

```
GPU総量: 16GB (RTX 2000 Ada)
├─ Qwen2.5-VL-7B (INT4)  : 6〜8GB  常駐
├─ SDXL Turbo (FP16)     : 10GB    常駐
└─ ControlNet Lineart    : 1〜2GB  常駐
                            ─────
                            合計 〜17〜20GB（オーバー）

→ 実運用方針:
  1. まず Qwen2.5-VL-7B INT4 + SDXL Turbo + ControlNet を試す
  2. OOMが出たら以下の順で対策:
     a. ControlNet を CPU offload
     b. SDXL Turbo を SD 1.5 + ControlNet に変更（〜6GB）
     c. VLM を Qwen2.5-VL-3B INT4 に変更（〜2GB）
  3. それでも厳しければモデルスワップ方式へ移行
```

### システム構成図

```
┌─────────────────────────────────────────────────────────┐
│ orchestrator.py (Main / Python)                         │
│  - サイクル管理 / 2分タイマー / Enter監視                 │
└─────────────────────────────────────────────────────────┘
        │
        ├── [Robot Thread]
        │     └─ piper_sdk.EndPoseCtrl で軌道再生
        │        - ストロークごとにペン上下
        │        - 中断要求受信で現ストローク完了後に停止
        │
        ├── [Prep Thread]
        │     ├─ camera.capture_median()  → 10枚 → median画像
        │     ├─ vlm.predict_intent()     → Qwen2.5-VL
        │     ├─ prompt_builder.build()    → テキスト処理
        │     ├─ image_gen.generate()    → SDXL Turbo + ControlNet
        │     ├─ vectorizer.vectorize()  → OpenCV pipeline
        │     └─ start_point.pick()      → 空白領域 or random
        │
        └── [Trigger Thread]
              └─ input() で Enter 監視
                 （将来 GPIO/Arduino に差し替え可能）
```

### ディレクトリ構成案

```
~/draw_piper/                            # 新規プロジェクト
├── orchestrator.py                      # メインループ
├── modules/
│   ├── trigger.py                       # トリガー入力抽象化
│   │   ├─ KeyboardTrigger                  ← MVP
│   │   └─ SerialTrigger                  ← 将来
│   ├── camera.py                        # median合成キャプチャ
│   ├── vlm.py                           # Qwen2.5-VL
│   ├── prompt_builder.py                # 意図→生成プロンプト
│   ├── image_gen.py                     # SDXL Turbo
│   ├── vectorizer.py                    # OpenCV
│   ├── start_point.py                   # 空白領域検出
│   ├── trajectory.py                    # ベクトル→軌道
│   └── robot.py                         # piper_sdk ラッパ
├── calibration/
│   ├── aruco_calibrate.py               # 一度だけ走らせる
│   └── camera_to_robot.yaml             # キャリブ結果
└── logs/
    └── YYYYMMDD_HHMMSS/                 # サイクルごとの記録
        ├── raw_frames/                  # 10枚のraw
        ├── median.jpg                   # 合成結果
        ├── intent.txt
        ├── prompt.txt
        ├── generated.png
        ├── vectorized.svg
        └── trajectory.json
```

## つまずいた点

- なし（本サイクルは設計フェーズのみ）。実装フェーズで遭遇した問題は次回以降のログに記録する。

## 学んだこと

### v0.3 と v0.4 の本質的な違い

v0.3 は「ロボットが**観察者として常時思考する**」モデル。VLM が構図批評を続け、メモリに蓄積された過去経験を引きながら、人間の描画にリアルタイムで応答する。これは芸術的には魅力的だが、MVP には複雑すぎる。

v0.4 は「ロボットが**ターン制で介入する**」モデル。各サイクル独立、状態を持たず、ユーザ意図を予測してそれっぽい絵を描き足す。シンプルだが、**動くものを2週間で出すには十分**。

### 「意図予測」プロンプトの設計指針

v0.3 の VLM プロンプトは「アート評論家として批評せよ」だった。v0.4 では完全に変わる:

```
GOOD (v0.4):
「この画像はホワイトボードに人が描きかけのスケッチです。
 この人は何を描こうとしていますか？
 主題、未完成な要素、次に描き足されそうな部分を答えてください。」

BAD (v0.3の名残):
「構図のバランスを批評し、不足する要素を芸術的視点から指摘せよ」
```

意図予測は**ユーザ視点に立つ**こと。批評ではなく共感。

### 平面描画にMoveIt2は要らない

MoveIt2 は3D空間で衝突回避しながら任意姿勢に到達するための重装備。透明アクリルへの2D描画では、

- ペン姿勢は固定（板に垂直）
- Z座標も固定（板の高さ）
- 動かすのは (X, Y) のみ
- 衝突対象は事実上「板そのもの」だけ

これなら `EndPoseCtrl` で十分。MoveIt2 統合 launch の修羅場（HANDOFF_TO_CLAUDE_CODE.md 参照）を回避できる利益が大きい。

### 「点描」を生成段階で扱わない判断

「線画＋点描」をプロンプトで両方頼むと、生成画像の制御性が落ちる。

→ **線画だけ生成** → **OpenCVで領域属性を後付け**（太い塊は点描化、細い線はそのまま）。

これにより:
- 生成プロンプトが単純化される
- 線と点の比率を実行時パラメータで調整可能になる
- ロボットアームの軌道としてもクリーン（点描は離散点列、線描は連続パス）

### 「アーム写り込み」を1枚で解決しようとしない

最初は「描画完了直後にキャプチャすればアームは ready pose に戻っているから1枚でいい」と考えていた。

しかし:
- ready pose に戻る前に強制中断される可能性
- アームの影が板に落ちる
- カメラの角度によってはアーム自体は写らなくても、アームが落とす影でユーザの描画が暗くなる

→ **時間方向 median 合成**でこれら全てを統計的に処理する。実装20行、計算ほぼゼロ、効果絶大。

## 次にやること

### Step 7 (実機統合) の方針変更

HANDOFF_TO_CLAUDE_CODE.md の「pymoveit2 → 実機経路を作る」タスクは**保留**。代わりに以下の順で進める。

#### 🔲 Step A: piper_sdk ベースの軌道再生ユーティリティ

- `~/draw_piper/modules/robot.py` を作成
- 入力: ストロークのリスト（各ストローク = (X,Y)点列 + 描画スタイル）
- 出力: 実機がそのストロークを描く
- 機能:
  - ペンアップ移動 → ペンダウン → ストローク描画 → ペンアップ
  - 中断フラグの監視（現ストローク完了で停止）
  - エラー時の安全停止

#### 🔲 Step B: ArUco キャリブレーション

- USBカメラ機種選定 → 購入
- アクリル板の四隅にArUcoマーカー配置
- `calibration/aruco_calibrate.py` で
  - 画像座標 → 板座標
  - 板座標 → robot base_link 座標
- 結果を YAML に保存して全モジュールで参照

#### 🔲 Step C: 線画生成パイプライン（オフライン検証）

- SDXL Turbo + Lineart ControlNet を動かす
- 入力: スケッチ画像 (キャプチャを模した低品質画像)
- 出力: 線画
- VRAM消費を実測。Qwen2.5-VL-7B INT4 との共存可否を判定

#### 🔲 Step D: ベクトル化パイプライン

- OpenCV処理を組む
- 入力: 線画ラスター
- 出力: ストロークのリスト
- skeletonize → findContours → approxPolyDP → スプライン補間

#### 🔲 Step E: 単体テストを通したパイプライン結合

- ダミーキャプチャ画像 → ダミー意図 → 線画生成 → ベクトル化 → 実機描画
- このルートが一度通れば、あとは VLM とカメラを差し込むだけ

#### 🔲 Step F: median合成キャプチャ + Enter トリガー

- `modules/camera.py` の median 実装
- `modules/trigger.py` の KeyboardTrigger 実装
- カメラが届いてから実画像で検証

#### 🔲 Step G: オーケストレータ統合

- threading or asyncio でメインループ
- 2分タイマー、中断ハンドリング、ロギング

### 着手順序の推奨

```
Step A (robot.py)
   + Step C (画像生成)   並行
   + Step D (ベクトル化) 並行
       ↓
Step E (パイプライン結合)
       ↓
Step B (キャリブ) ← カメラが届いたら
       ↓
Step F (median + Enter)
       ↓
Step G (オーケストレータ)
       ↓
[統合動作確認]
```

### Claude Code への引き継ぎ更新

`HANDOFF_TO_CLAUDE_CODE.md` の以下の項目は **無効化**:
- 「(本命) MoveIt → 実機 Piper への経路を作る」
- 「(継続調査) demo.launch.py での RViz 表示の謎」

代わりに本ファイル `20260521_1757_drawing_system_v04_design.md` を参照する旨を追記する必要あり。
