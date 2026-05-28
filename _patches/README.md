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

### `_ux_polish.patch` (約 180 行)

危険度別 色分けで誤操作リスクを下げる UX 改善:

**🔴 赤 (最危険、 緊急停止 / 強い警告)**:
- 「⚠ ティーチ開始 (マスターモード)」 — master mode 入り、 終了に電源
  cycle 必要
- 「■ 中止 (drag-teach)」 — 4 隅記録を捨てる
- 「■ 中止 (strokes)」 — 描画中断

**🟢 緑 (安全な確定/保存)**:
- 「✅ 保存して終了 (マスター解除)」 — drag-teach 結果を確定

**🟠 橙 (実機動作の主要ボタン)**:
- 「▶ 中心に正方形 (描画開始)」
- 「▶ 中心に丸」 / 「▶ 中心に三角」
- 「▶ 描画開始 (strokes)」
- 「✨ Frida Smooth」

**🟡 薄橙 (接触系、 注意必要)**:
- 「✏ ペン下げ (probe)」
- 「✏ ペン下げ (tune)」

ダイアログ強化:
- `on_strokes_draw` 確認に 事前チェックリスト (人/障害物、 紙、 ペン
  contact_x、 緊急停止) 追加

機能変更なし、 純粋に UX 改善。 ttk.Button → tk.Button は state/font 等
の API 互換、 既存 callback はそのまま動く。

## なぜ draw_piper の `_patches/` に置くか

- piper_test に push 権限が無い (remote container の制限)
- ユーザ環境では両リポが同一マシンにある (`~/piper_test/` と
  `~/draw_piper/`) ので、 draw_piper を pull した後にローカル patch 適用
  だけで完結する
- 将来 piper_test に直接コミット権が付与されれば `_patches/` ごと削除可
