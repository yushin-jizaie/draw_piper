# piper_test 側パッチ集

このディレクトリには `~/piper_test/` リポジトリのファイルに当てる patch と
修正済みファイルを置きます。 本来は piper_test 側で管理すべきですが、
このセッション (Claude Code on the web の remote container) は
**draw_piper リポへの書き込みのみ許可** されているため、 piper_test に
直接 push できません。 patch をここに置いて、 ユーザがローカルで適用する
運用にします。

## ファイル

- `wall_drawing_gui_full_dev_stroke_picker.patch`
  unified diff、 約 50 行 — Section 5 の strokes select で
  カード一覧 UI (StrokePicker) を使う
- ⭐ `wall_drawing_gui_full_dev_frida_smooth.patch` (新規)
  unified diff、 約 160 行 — Section 5 に `✨ Frida Smooth` ボタンを追加
  (PR #2 の `Robot.draw_strokes_panel_smooth` を 別 Robot で呼ぶ)
- ⭐ `wall_drawing_gui_full_dev_ux_polish.patch` (新規)
  unified diff、 約 80 行 — 描画系ボタンの視認性 UX 改善 (危険ボタン橙/赤
  ハイライト、 確認ダイアログにチェックリスト追加)
- `wall_drawing_gui_full_dev.patched.py`
  上記 **3 つすべて適用済の完全版**。 cp で上書き派の人向け

## 適用方法 (どちらか好きな方)

### 方法 A: patch コマンド (推奨、 既存編集との衝突検知あり)

```bash
cd ~/piper_test

# 順番に当てる (stroke_picker → frida_smooth → ux_polish)
patch -p0 < ~/draw_piper/_patches/wall_drawing_gui_full_dev_stroke_picker.patch
patch -p0 < ~/draw_piper/_patches/wall_drawing_gui_full_dev_frida_smooth.patch
patch -p0 < ~/draw_piper/_patches/wall_drawing_gui_full_dev_ux_polish.patch

# 失敗時は --dry-run で先に確認
patch -p0 --dry-run < ~/draw_piper/_patches/wall_drawing_gui_full_dev_ux_polish.patch
```

### 方法 B: ファイル丸ごと上書き (両 patch 込み)

```bash
cp ~/draw_piper/_patches/wall_drawing_gui_full_dev.patched.py \
   ~/piper_test/wall_drawing_gui_full_dev.py
```

⚠️  方法 B は他のローカル編集を上書きします。 git で commit してから当てるか
方法 A を使ってください。

## 確認

適用後、 piper_test 側で:

```bash
cd ~/piper_test
git diff wall_drawing_gui_full_dev.py | head -50
# → 新 import + on_strokes_draw_smooth + ✨ Frida Smooth ボタン が見える

# GUI を起動
~/draw_piper/venv/bin/python wall_drawing_gui_full_dev.py
# Section 5 で 「✨ Frida Smooth」 ボタンが表示される
# strokes.json 選択後 → ✨ Frida Smooth クリック → 確認 → 実機描画
```

`from modules.robot import Robot, PanelFrame` で `~/draw_piper` から
読まれるので、 draw_piper 側の最新 (claude/frida-smoothness-20260527
ブランチ、 dev 取り込み後は dev/main) が pull 済みであることが前提。

## 変更内容サマリー

### `_stroke_picker.patch` (約 50 行)

1. import 群直後に `from modules.stroke_picker import StrokePicker`
2. `on_strokes_select_file()` を 2 段構成: 主 = StrokePicker、 fallback = filedialog

### `_frida_smooth.patch` (約 160 行)

1. import: `from modules.robot import Robot as _DPRobot, PanelFrame as _DPPanelFrame`
2. Section 5 ボタン列に `✨ Frida Smooth` 追加 (中止ボタンの右隣)
3. メソッド追加:
   - `on_strokes_draw_smooth()`: 確認ダイアログ + worker thread 起動
   - `_do_strokes_draw_smooth()`: strokes.json → uv mm 変換 → Robot 接続 →
     `draw_strokes_panel_smooth()` → 切断
4. 既存 `on_strokes_draw` (IK + MOVE J chained) と並存。 user が ボタンで使い分け

⚠️ Frida ボタンは別 Robot インスタンスで CAN bus 共有のため、 描画中は他の
GUI ボタンを押さないこと (確認ダイアログで警告)。

### `_ux_polish.patch` (約 80 行)

描画系ボタンの視認性を高めて誤操作リスクを下げる:

1. 「描画開始」 系 (3 か所: Section 4 「中心に正方形」、 Section 5
   「描画開始」、 Section 5 「✨ Frida Smooth」) を `tk.Button` に置換、
   bg=橙 + fg=濃橙 + bold で 「実機が動くボタン」 として明示
2. 「中止」 を `tk.Button` で bg=赤 + fg=濃赤 で 緊急停止を強調
3. `on_strokes_draw` 確認ダイアログを 事前チェックリスト付きに改善:
   - 「アームの可動範囲に人や障害物がない」
   - 「panel に紙が貼られている」
   - 「ペンが付いていて contact_x 調整済」
   - 「緊急停止ボタンが手元にある」

機能変更なし、 純粋に UX 改善。 ttk.Button → tk.Button は state/font 等
の API 互換、 既存 callback はそのまま動く。

## なぜ draw_piper の `_patches/` に置くか

- piper_test に push 権限が無い (remote container の制限)
- ユーザ環境では両リポが同一マシンにある (`~/piper_test/` と
  `~/draw_piper/`) ので、 draw_piper を pull した後にローカル patch 適用
  だけで完結する
- 将来 piper_test に直接コミット権が付与されれば `_patches/` ごと削除可
