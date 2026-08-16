# draw_piper

Live Collaborative Drawing Robot System (v0.4) — MVP workspace.

人がホワイトボードに線を引き、 VLM がトピックを推定 → SDXL + ControlNet が
線画を生成 → Vectorizer が stroke 化 → Piper ロボットアームが描き足す、
というパイプラインを実装した repo。

設計詳細: `docs/20260521_1757_drawing_system_v04_design.md`

---

## GUI の立ち上げ方

このリポには **GUI が 2 つ** あります。 用途が違うので使い分けます。

| GUI | 用途 | 場所 | 実機要 |
|---|---|---|---|
| **パイプラインテスト GUI** | カメラ or ファイル入力 → VLM → 画像生成 → vectorize までを GUI から実行・確認 | `scripts/pipeline_test_gui.py` (この repo) | 不要 (GPU はあれば速い) |
| **壁面描画 GUI** | 実機ロボットでホワイトボードに描画。 drag-teach キャリブ + 四角描画 + stroke 読み込み | `~/piper_test/wall_drawing_gui.py` (**別 repo**) | 必須 (Piper + USB-CAN) |

### ① パイプラインテスト GUI (このリポ)

```bash
cd ~/draw_piper
./venv/bin/python scripts/pipeline_test_gui.py
```

- 引数なし。 起動すると Tk ウィンドウが開く
- 入力モード: ① USB カメラで撮影 (`modules.camera.Camera` 経由 median 合成) /
  ② ローカル画像ファイルを選択
- 「SDXL / プロンプト 設定」 ボタンで `calibration/imagegen_config.yaml` を編集
  (preset / steps / negative prompt / Panel readout / auto_from_panel /
  手動 W×H)
- 出力: `logs/vlm_to_image_YYYYMMDD_HHMMSS/cycle_01/`
  (生成画像 + strokes.json + topic_guess.json + vec_debug/)

#### Panel 寸法を確認したい (新)

「SDXL / プロンプト 設定」 を開くと **Panel readout** セクションに
canvas_calibration.yaml の実測値 + 推奨 SDXL bucket + mm/px が出ます。
canvas を測り直した直後は 「🔄 再計測値で更新」 で reload。 CLI からも確認可:

```bash
python3 scripts/check_panel_geometry.py             # 整合 diff を表示
python3 scripts/check_panel_geometry.py --sync      # panel_frame.panel.size_mm を canvas に揃える
```

### ② 壁面描画 GUI (`~/piper_test/wall_drawing_gui.py` — 別 repo)

実機ホワイトボード描画用。 **`~/piper_test/` は別 repo** で、 このリポからは
触らない (削除厳禁)。

```bash
# 事前: USB-CAN を上げる (root 権限要、 pkexec が wrap してくれる)
sudo ip link set can0 up type can bitrate 1000000   # GUI からも pkexec で実行可

cd ~/piper_test
~/draw_piper/venv/bin/python wall_drawing_gui.py
```

- venv は draw_piper 側を流用 (`~/draw_piper/venv`)
- GUI 内 4 セクション: Connection / Drag-Teach (2-phase) / Tune Contact /
  Draw Square
- 出力 yaml: `~/draw_piper/calibration/canvas_calibration.yaml`
  (draw_piper 側の vectorizer / panel_geometry が参照する)
- 詳細: `docs/20260525_1447_canvas_calibration_v3_design.md`

> 注: 過去 master mode 解除や CAN TX 失敗で詰まった経緯あり。
> 詰まったら `MILESTONES.md` の N1/N2/N3/N4 (CAN / master mode 系) を参照。

---

## 代表 CLI

GUI なしで実行したいとき用。 全て `./venv/bin/python` 経由。

```bash
# VLM → ImageGen → Vectorizer をワンショットで (パイプライン GUI の中身)
./venv/bin/python scripts/test_vlm_to_image.py --sketch scripts/test_sketch.jpg --steps 4

# 入力 sketch から N variants (gacha mode)
./venv/bin/python -m scripts.generate_gacha \
    --user-sketch scripts/test_sketch.jpg --category character \
    --output logs/gacha_$(date +%Y%m%d_%H%M%S) --n 3

# image_gen preset 比較
./venv/bin/python -m scripts.compare_imagegen_models \
    --guide scripts/test_sketch.jpg \
    --prompt "1boy, solo, messy hair, simple t-shirt" \
    --presets illustrious_v2_inpaint --seed 42

# Panel geometry 整合チェック
python3 scripts/check_panel_geometry.py
```

朝イチで一気通貫したいとき: `scripts/MORNING_RUNBOOK.md` に
日替わりの一括手順あり。

---

## ディレクトリ構成

```
draw_piper/
├── modules/         # コア (image_gen / vectorizer / robot / panel_geometry 等)
├── scripts/        # CLI と GUI、 ベンチ、 検証ツール
├── calibration/    # 各種 yaml (canvas_calibration / panel_frame / imagegen_config / camera_to_robot)
├── docs/           # 設計・引き継ぎ MD (時系列 YYYYMMDD_HHMM_*.md)
├── logs/           # 実行出力 (生成画像、 strokes.json 等)
└── venv/           # Python 仮想環境 (Python 3.11、 torch + diffusers + transformers)
```

進捗・正常地点の地図: `MILESTONES.md`
Claude Code 運用メモ: `CLAUDE.md`
