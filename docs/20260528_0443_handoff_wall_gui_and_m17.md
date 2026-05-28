# 引き継ぎ: 壁面描画 GUI 起動 + M17 実機検証 (2026-05-28 04:43 時点)

> ブランチ: `claude/smooth-curve-rendering-e88Vb` (全 push 済)
> 親プロジェクト: `~/draw_piper`
> 対象: ローカル実機セッション (jizaiedev2026 マシン)

---

## TL;DR (5 行)

1. `~/draw_piper/venv/bin/python3` が存在しない (symlink 欠落) → GUI が起動できない状態
2. `~/piper_test/wall_drawing_gui.py` のバージョンが古い可能性 → git pull で確認
3. Panel geometry alignment (M17) が **実機検証待ち**の下書き状態
4. 最優先: venv 修正 → GUI 起動確認 → M17 実機検証 (生成 → vectorize → robot 描画で歪み無し)
5. 画像生成 (image_gen / SDXL) 系は変更しない

---

## 現状詳細

### 1. venv の python binary が壊れている

`~/draw_piper/venv/bin/` に `accelerate`, `diffusers-cli` 等のスクリプトは存在するが、
`python` / `python3` の symlink が欠落している。

```
$ ls ~/draw_piper/venv/bin/python*   # → 何も返らない
$ which python3                       # → /usr/bin/python3
```

**修正手順**:
```bash
ln -s /usr/bin/python3 ~/draw_piper/venv/bin/python3
ln -s /usr/bin/python3 ~/draw_piper/venv/bin/python
# 確認
~/draw_piper/venv/bin/python3 --version
```

### 2. wall_drawing_gui.py のバージョン確認

`~/piper_test` は draw_piper とは別 repo。 リモートセッションでは触っていない。
ローカルで変更があったかどうか確認:

```bash
cd ~/piper_test
git log --oneline -5
git status
git remote -v
# 最新化するなら
git pull
```

### 3. M17: Panel geometry alignment (下書き → 実機で確定)

**実装済み内容** (commit `021979c`):
- `modules/panel_geometry.py` 新規: canvas_calibration.yaml の `whiteboard_computed` を真値として SDXL bucket を自動選択
- `modules/image_gen.py`: `auto_from_panel: true` で bucket 自動解決、 height/width 明示
- `scripts/check_panel_geometry.py`: 整合 diff CLI (`--sync` で panel_frame に書き戻し)
- `scripts/pipeline_test_gui.py` ImageGenCalibWindow: Panel readout + auto_from_panel トグル + 「🔄 再計測値で更新」

**現在のキャリブ値** (canvas_calibration.yaml):
- panel: 92.93 × 193.52 mm (aspect 0.480)
- 推奨 bucket: 704 × 1472 px (aspect err 0.4%, mm/px=(0.132, 0.132) で等方)
- 旧: 1024 × 1024 強制 → mm/px=(0.091, 0.189) と縦方向 2.1x 伸び

**整合確認コマンド**:
```bash
cd ~/draw_piper
~/draw_piper/venv/bin/python3 scripts/check_panel_geometry.py
```

期待出力:
```
[5] 推奨 PanelGeometry  (source=canvas_calibration)
    panel_size_mm  : 92.93 × 193.52 mm
    panel_image_size : 704 × 1472 px
    ...
[6] 整合 diff
    ✅ canvas ↔ panel_frame.panel.size_mm 一致
    ...
```

**M17 確定のための実機検証タスク**:
1. `check_panel_geometry.py` で整合確認 (上記)
2. パイプラインテスト GUI (`scripts/pipeline_test_gui.py`) でスケッチを入力 → 生成
3. 生成画像が panel aspect (縦長 0.48) に合っているか目視確認
4. Vectorizer → Robot.draw_stroke_panel で実際に描画 → 縦横比の歪みが消えているか確認
5. PASS したら MILESTONES.md の M17 を `●` に確定 + `★ 現在地` を M17 に更新

---

## キーファイル

| ファイル | 内容 |
|---------|------|
| `modules/panel_geometry.py` | PanelGeometry / load_panel_geometry / select_sdxl_bucket |
| `calibration/canvas_calibration.yaml` | whiteboard_computed (真値 92.93×193.52 mm) |
| `calibration/panel_frame.yaml` | panel.size_mm (robot 側が読む値) |
| `calibration/imagegen_config.yaml` | `auto_from_panel: true` |
| `scripts/check_panel_geometry.py` | 整合 diff CLI |
| `scripts/pipeline_test_gui.py` | パイプラインテスト GUI |
| `~/piper_test/wall_drawing_gui.py` | 壁面描画 GUI (別 repo) |

---

## やらないこと (このセッション範囲外)

- `phase_a_calibration.panel_size_mm` の更新 (scripts/calibrate_panel.py 再実行が必要、 4 点クリックキャリブ)
- LoRA 学習・IP-Adapter 関連 (画像生成スレッドで別途管理)
- `~/piper_test` への新機能追加 (別 repo、 今回は起動確認のみ)

---

## ブランチ状態

```
● claude/smooth-curve-rendering-e88Vb  (全 push 済)
  931f316 README: GUI 立ち上げ方を整理
  a94d082 MILESTONES: M17 (下書き) Panel geometry alignment 追記
  021979c panel_geometry: canvas → SDXL bucket 共有 + image_gen non-square 化
  e9bd4ea companion mode v1: blob 検出 + stroke transform + pipeline
  ... (計 25 commits ahead of main)
```
