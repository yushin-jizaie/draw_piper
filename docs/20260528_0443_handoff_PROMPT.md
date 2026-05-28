# ローカルセッション用 貼り付けプロンプト (2026-05-28 04:43)

---

## ▼ ここからコピー

draw_piper ロボット描画プロジェクトのローカル実機セッションです。
リモートで実装を進めて push 済みです。 続きをお願いします。

### 環境

- プロジェクト: `~/draw_piper`
- ブランチ: `claude/smooth-curve-rendering-e88Vb`
- 実機: Piper アーム + CAN bus + GPU (ローカル)
- 別 repo: `~/piper_test/wall_drawing_gui.py` (壁面描画 GUI)

### 最初に読んで

引き継ぎ詳細: `docs/20260528_0443_handoff_wall_gui_and_m17.md`

### 優先タスク

**P0: venv の python binary を修正して GUI を起動できる状態にする**

`~/draw_piper/venv/bin/python3` の symlink が欠落しています:

```bash
ln -s /usr/bin/python3 ~/draw_piper/venv/bin/python3
ln -s /usr/bin/python3 ~/draw_piper/venv/bin/python
~/draw_piper/venv/bin/python3 --version   # 確認
```

**P1: wall_drawing_gui.py を最新化して起動確認**

```bash
cd ~/piper_test
git log --oneline -3   # バージョン確認
git pull               # 必要なら最新化
~/draw_piper/venv/bin/python3 wall_drawing_gui.py
```

**P2: Panel geometry alignment (M17) の実機検証**

```bash
cd ~/draw_piper
~/draw_piper/venv/bin/python3 scripts/check_panel_geometry.py
```

整合確認後、パイプラインテスト GUI でスケッチ入力 → 生成 → 描画。
縦横比の歪み (旧: 縦 2.1x 伸び) が解消されていれば M17 確定。
PASS したら MILESTONES.md の M17 を `●` に確定 + `★ 現在地` を M17 に更新。

### 注意

- `modules/image_gen.py` / SDXL 関連は変更しない (画像生成スレッドで別管理)
- `~/piper_test` は別 repo。 削除・強制リセット厳禁
- 詰まったら `candump can0 | head -60` で CAN 状態を確認してから相談

### 開始

まず `ln -s` で venv 修正 → `python3 --version` で確認 → 結果を教えてください。

## ▲ ここまでコピー
