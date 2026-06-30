---
name: tk_emoji_xft_segfault
description: Tk(Xft) のカラー絵文字で ttk ウィジェット生成が segfault する実機環境ゴッチャ
metadata: 
  node_type: memory
  type: project
  originSessionId: 1851ba0b-f126-4c5b-bac2-857ebeb6c448
---

この実機 (DISPLAY :0) の Python3.10 Tk では、**ttk ウィジェットの text に
カラー絵文字 (📁🔄📷 等) を入れると生成中に segfault** することがある。
faulthandler trace は `ttk.py __init__ → tkinter __init__ line 2601` を指す。

**Why:** Xft のカラー絵文字テキストレイアウトのバグ。フォントキャッシュ状態
依存で**間欠的** — 単発の小さなテスト (bare `tk.Tk()`) では再現しないが、
実アプリで多数ウィジェットを mapped 表示すると落ちる。これが
`modules/stroke_picker.py` の「選択...」押下クラッシュの真因だった
(2026-06-01, fix=abd613c で絵文字全廃)。

**How to apply:** GUI (wall_drawing_gui / stroke_picker 等) の
ラベル・ボタン・Combobox 値に絵文字を使わない。装飾は ASCII (`->` 等) で。
分離テストで再現しなくても「実アプリで落ちる」報告なら絵文字を疑う。
関連: [[wall_gui_launch_command]]
