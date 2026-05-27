# piper_test 側パッチ集

このディレクトリには `~/piper_test/` リポジトリのファイルに当てる patch と
修正済みファイルを置きます。 本来は piper_test 側で管理すべきですが、
このセッション (Claude Code on the web の remote container) は
**draw_piper リポへの書き込みのみ許可** されているため、 piper_test に
直接 push できません。 patch をここに置いて、 ユーザがローカルで適用する
運用にします。

## ファイル

- `wall_drawing_gui_full_dev_stroke_picker.patch`
  unified diff (`patch -p0` で適用可)
- `wall_drawing_gui_full_dev.patched.py`
  パッチ適用後の完全版ファイル (cp で上書き派の人向け)

## 適用方法 (どちらか好きな方)

### 方法 A: patch コマンド (推奨、 既存編集との衝突検知あり)

```bash
cd ~/piper_test
patch -p0 < ~/draw_piper/_patches/wall_drawing_gui_full_dev_stroke_picker.patch

# 失敗時は --dry-run で先に確認
patch -p0 --dry-run < ~/draw_piper/_patches/wall_drawing_gui_full_dev_stroke_picker.patch
```

### 方法 B: ファイル丸ごと上書き

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
git diff wall_drawing_gui_full_dev.py | head -30
# → import 追加 + on_strokes_select_file の変更が見えるはず

# GUI を起動
~/draw_piper/venv/bin/python wall_drawing_gui_full_dev.py
# 「5. 生成画像描画」 セクションの 「選択...」 ボタンを押す →
# StrokePicker のカード一覧が出るはず
```

`from modules.stroke_picker import StrokePicker` で `~/draw_piper` から
読まれるので、 draw_piper 側の最新 (claude/smooth-curve-rendering-e88Vb
ブランチ) が pull 済みであることが前提。

## 変更内容サマリー (約 50 行)

1. 先頭の import 群直後に sys.path に `~/draw_piper` を追加 + `StrokePicker`
   を try import (失敗時は None で警告)
2. `on_strokes_select_file()` を 2 段構成に書き直し:
   - 主経路: `StrokePicker.show(self.root)` でカード一覧 → `strokes_json`
     パスを取得
   - フォールバック: StrokePicker import 失敗 / 実行時エラーで従来の
     `filedialog.askopenfilename`
   - 共通: `strokes.json` の軽い検証 → `var_strokes_json_path` に set →
     `_refresh_buttons_safe()`

機能的にはユーザ体験のみ変わる(OS finder → カードグリッド)。
state の更新先 (`var_strokes_json_path`) は不変なので後段 (プレビュー /
draw 実行) は全部そのまま動く。

## なぜ draw_piper の `_patches/` に置くか

- piper_test に push 権限が無い (remote container の制限)
- ユーザ環境では両リポが同一マシンにある (`~/piper_test/` と
  `~/draw_piper/`) ので、 draw_piper を pull した後にローカル patch 適用
  だけで完結する
- 将来 piper_test に直接コミット権が付与されれば `_patches/` ごと削除可
