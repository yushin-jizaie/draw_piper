# StrokePicker 組み込み手順 (wall_drawing_gui.py 側)

> 日時: 2026-05-26 23:30 (JST) / 2026-05-27 01:10 patch 自動生成済み
> ステータス: ✅ **picker モジュール完成 + piper_test 側 patch 生成済み**
> 対象: `~/piper_test/wall_drawing_gui_full_dev.py`(別リポ)
>
> patch は `_patches/wall_drawing_gui_full_dev_stroke_picker.patch`、
> 完全置換版は `_patches/wall_drawing_gui_full_dev.patched.py`。
> 適用方法は `_patches/README.md` を参照。 以下は仕組みの説明。

## 概要

ロボットアームの描画 GUI で「ストロークを選ぶ」UI が OS のファインダー
(`tkinter.filedialog.askopenfilename`) になっている。
これを **logs/vlm_to_image_*/cycle_*/ をパースしてカード一覧で見せる**
モーダルダイアログに置き換える。

ダイアログは `modules/stroke_picker.StrokePicker` クラスとして提供済み。
画像は生成画像 (`generated.png`) と ストローク PNG (`vec_debug/06_strokes.png`)
を横並びに、 各カードに 題材(subject) + タイムスタンプ + n_strokes を
表示。 クリックで選択、 ダブルクリックで確定。

## 戻り値

```python
{
  "cycle_dir":       Path,    # logs/vlm_to_image_<ts>/cycle_NN/
  "generated_image": Path | None,  # cycle_dir/generated.png
  "strokes_png":     Path | None,  # cycle_dir/vec_debug/06_strokes.png
  "strokes_json":    Path | None,  # cycle_dir/strokes.json (= 描画用)
  "input_sketch":    Path | None,  # cycle_dir/input_sketch.jpg
  "subject":         str,          # topic_guess.json から
  "timestamp":       str,          # "YYYYMMDD_HHMMSS"
}
```

キャンセル時は `None`。

## 単体テスト

```bash
cd ~/draw_piper
python3 -m modules.stroke_picker
```

logs/ が空 / cycle_dir が無い場合は「該当エントリなし」が表示される。

## wall_drawing_gui.py への組み込み diff (例)

`~/piper_test/wall_drawing_gui.py` で「ストローク読み込み」ボタンの
ハンドラ(おそらく `_load_strokes_from_file` 的な名前) を以下のように
差し替える。

### Before (推定形)

```python
from tkinter import filedialog

def _on_load_strokes(self):
    path = filedialog.askopenfilename(
        title="strokes.json を選択",
        filetypes=[("JSON", "*.json"), ("All", "*.*")],
        initialdir="~/draw_piper/logs",
    )
    if not path:
        return
    self._load_strokes_json(Path(path))
```

### After

```python
import sys
from pathlib import Path

# draw_piper のモジュールが import できるよう sys.path を通す
_DRAW_PIPER = Path.home() / "draw_piper"
if str(_DRAW_PIPER) not in sys.path:
    sys.path.insert(0, str(_DRAW_PIPER))

from modules.stroke_picker import StrokePicker

def _on_load_strokes(self):
    selected = StrokePicker.show(self.root)  # self.root = メイン Tk window
    if selected is None:
        return
    strokes_json = selected.get("strokes_json")
    if strokes_json is None:
        # cycle に strokes.json が無い (新しすぎる or 古いログ)
        messagebox.showwarning("strokes.json なし",
            f"{selected['cycle_dir']} に strokes.json がありません")
        return
    self._load_strokes_json(strokes_json)
    self.log(f"strokes ロード: {strokes_json}  "
             f"題材={selected.get('subject')} ({selected.get('timestamp')})")
```

ポイント:

- `StrokePicker.show(parent)` はモーダルで、 OK/Cancel を待ってから返る
- `parent` は wall_drawing_gui のメイン `tk.Tk()` または `Toplevel` を渡す
- 戻り値 dict の `strokes_json` が描画に使うパス
- ユーザが古いログ(strokes.json 未生成) を選んだ場合のフォールバックは
  caller 側で

## カスタマイズ

`StrokePicker.show()` には以下のオプションがある:

```python
StrokePicker.show(
    parent,
    logs_dir=Path("~/draw_piper/logs").expanduser(),  # 既定: project_root/logs
    title="ストロークを選んでください",                # 既定: "ストローク選択 (logs/ から)"
)
```

logs_dir を変えれば別ディレクトリのログも参照可能(将来別マシンの
ログを SSHFS でマウントしたとき等)。

## 注意点

- **logs/ パスは draw_piper プロジェクトルート基準**。 wall_drawing_gui の
  作業 dir と違うので、 上の例の `_DRAW_PIPER` を環境に合わせて
- 大量(数百カード以上) のとき、 初回 render が数秒かかる(各 PNG を
  PIL でサムネ化)。 2 回目以降はキャッシュで早い
- **PIL (`pillow`) が必須**(`Image` import 時にチェックされ、 無いと
  プレースホルダ表示になる)

## 関連

- `modules/stroke_picker.py` — 本実装
- `scripts/test_vlm_to_image.py` — logs/ にエントリを生成する側
- `docs/20260525_2210_full_dev_integration_section5.md` — vlm_to_image 出力 spec
