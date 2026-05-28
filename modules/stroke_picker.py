"""Reusable stroke-picker dialog for the robot-arm drawing GUI.

Replaces the OS-native filedialog with a card grid that shows generated
images + stroke PNG pairs harvested from the project log tree.

Usage from another Tk GUI (e.g. wall_drawing_gui.py):

    from modules.stroke_picker import StrokePicker

    selected = StrokePicker.show(root_tk_window)
    if selected is None:
        return                # user cancelled
    # selected is a dict:
    #   {
    #     "cycle_dir":       Path,
    #     "generated_image": Path or None,
    #     "strokes_png":     Path or None,
    #     "strokes_json":    Path or None,
    #     "input_sketch":    Path or None,
    #     "subject":         str,
    #     "timestamp":       str (YYYYMMDD_HHMMSS),
    #   }

Standalone test:
    python3 -m modules.stroke_picker

Scans:  <project_root>/logs/**/strokes*.json (vlm_to_image_*/cycle_*/ も含む)
Each card shows:
  - generated.png         (left thumbnail)
  - vec_debug/06_strokes.png (right thumbnail)
  - subject(ja) + cycle dir name + n_strokes (under thumbnails)

Filter:  free-text search on subject + dir name
Sort:    newest first by mtime (default), or by name
"""

from __future__ import annotations

import json
import re
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Optional

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOGS_DIR = PROJECT_ROOT / "logs"

THUMB_W = 220       # 1 card 内のサムネ 1 枚あたり幅
THUMB_H = 220
CARD_PAD = 8
GRID_COLS = 3


class StrokePicker(tk.Toplevel):
    """Modal stroke-picker dialog.

    Use the classmethod ``StrokePicker.show(parent)`` to open it; it blocks
    until the user clicks OK or Cancel and returns either the selected
    entry's metadata dict or ``None``.
    """

    def __init__(self, parent: tk.Misc, logs_dir: Optional[Path] = None,
                  title: str = "ストローク選択 (logs/ から)"):
        super().__init__(parent)
        self.title(title)
        self.geometry("1240x820")
        self.minsize(900, 600)

        self.logs_dir = Path(logs_dir or DEFAULT_LOGS_DIR)
        self.result: Optional[dict] = None

        # state
        self._entries: list[dict] = []
        self._cards: dict[str, ttk.Frame] = {}      # key = cycle_dir name
        self._photo_cache: dict[Path, "ImageTk.PhotoImage"] = {}
        self._selected_key: Optional[str] = None

        # tk vars
        self.var_filter = tk.StringVar()
        self.var_sort = tk.StringVar(value="newest")

        self._build_ui()
        self._refresh()

        # modal
        self.transient(parent)
        self.grab_set()
        self.focus_set()

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    @classmethod
    def show(cls, parent: tk.Misc, logs_dir: Optional[Path] = None,
              title: str = "ストローク選択 (logs/ から)") -> Optional[dict]:
        picker = cls(parent, logs_dir=logs_dir, title=title)
        parent.wait_window(picker)
        return picker.result

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        # toolbar
        tb = ttk.Frame(self, padding=8)
        tb.pack(fill=tk.X)
        ttk.Label(tb, text=f"📁 {self.logs_dir}",
                  foreground="#555").pack(side=tk.LEFT, padx=(0, 12))
        ttk.Label(tb, text="🔍 フィルタ:").pack(side=tk.LEFT)
        ttk.Entry(tb, textvariable=self.var_filter, width=24
                  ).pack(side=tk.LEFT, padx=4)
        self.var_filter.trace_add("write", lambda *_: self._render())
        ttk.Label(tb, text="  並び:").pack(side=tk.LEFT, padx=(12, 0))
        ttk.Radiobutton(tb, text="新しい順", variable=self.var_sort,
                         value="newest", command=self._render
                         ).pack(side=tk.LEFT)
        ttk.Radiobutton(tb, text="古い順", variable=self.var_sort,
                         value="oldest", command=self._render
                         ).pack(side=tk.LEFT)
        ttk.Button(tb, text="🔄 更新", command=self._refresh, width=8
                   ).pack(side=tk.RIGHT)

        # scrollable card grid
        grid_box = ttk.Frame(self)
        grid_box.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        self.canvas = tk.Canvas(grid_box, borderwidth=0, highlightthickness=0,
                                  background="#f5f5f5")
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(grid_box, orient=tk.VERTICAL,
                                command=self.canvas.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.inner = ttk.Frame(self.canvas)
        self.inner_id = self.canvas.create_window((0, 0), window=self.inner,
                                                    anchor="nw")
        self.inner.bind("<Configure>", lambda _e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        # mousewheel
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)       # win/mac
        self.canvas.bind_all("<Button-4>",
                              lambda _e: self.canvas.yview_scroll(-3, "units"))
        self.canvas.bind_all("<Button-5>",
                              lambda _e: self.canvas.yview_scroll(3, "units"))

        # bottom bar
        bot = ttk.Frame(self, padding=8)
        bot.pack(fill=tk.X)
        self.lbl_status = ttk.Label(bot, text="(まだ何も選択していません)",
                                       foreground="#555")
        self.lbl_status.pack(side=tk.LEFT)
        ttk.Button(bot, text="キャンセル", command=self._on_cancel, width=12
                   ).pack(side=tk.RIGHT, padx=4)
        self.btn_ok = ttk.Button(bot, text="OK (選択を確定)",
                                    command=self._on_ok, width=16,
                                    state=tk.DISABLED)
        self.btn_ok.pack(side=tk.RIGHT, padx=4)

    def _on_canvas_resize(self, event):
        # ensure inner frame matches canvas width so cards stay positioned
        self.canvas.itemconfigure(self.inner_id, width=event.width)

    def _on_mousewheel(self, event):
        # delta is +/-120 on Windows/Mac; normalize to 3 units
        units = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(units * 3, "units")

    # ------------------------------------------------------------------
    # log scanning
    # ------------------------------------------------------------------
    def _scan_logs(self) -> list[dict]:
        """logs/ 配下から strokes 系 JSON を一括スキャン。

        対象:
          - vlm_to_image_*/cycle_*/strokes.json       (パイプライン GUI 出力)
          - **/cycle_*/strokes.json                   (他系列の cycle_ 出力)
          - **/strokes*.json                          (robot_strokes_demo 等 直配置)
        """
        if not self.logs_dir.exists():
            return []
        # 候補ディレクトリ収集 (重複排除): strokes*.json を含む dir すべて
        cycle_dirs: set = set()
        for js in self.logs_dir.rglob("strokes*.json"):
            if js.is_file():
                cycle_dirs.add(js.parent)
        entries: list[dict] = []
        for cycle in cycle_dirs:
            if not cycle.is_dir():
                continue
            # strokes.json or strokes_mm.json 等を一つ拾う (優先順)
            strokes_json = None
            for name in ("strokes.json", "strokes_mm.json"):
                cand = cycle / name
                if cand.exists():
                    strokes_json = cand
                    break
            if strokes_json is None:
                # その他 strokes*.json は 1 個目を拾う
                cand_list = sorted(cycle.glob("strokes*.json"))
                if cand_list:
                    strokes_json = cand_list[0]
            gen = cycle / "generated.png"
            strokes_png = cycle / "vec_debug" / "06_strokes.png"
            input_sketch = cycle / "input_sketch.jpg"
            meta: dict = {
                "cycle_dir": cycle,
                "generated_image": gen if gen.exists() else None,
                "strokes_png": strokes_png if strokes_png.exists() else None,
                "strokes_json": strokes_json,
                "input_sketch": input_sketch if input_sketch.exists() else None,
                "subject": "",
                "n_strokes": None,
            }
            # timestamp 抽出: 親 dir 名 / 自 dir 名 から YYYYMMDD_HHMMSS を拾う
            m = re.search(r"(\d{8}_\d{6})", str(cycle.parent.name))
            if not m:
                m = re.search(r"(\d{8}_\d{6})", str(cycle.name))
            if not m:
                m = re.search(r"(\d{8})", str(cycle.parent.name))
            meta["timestamp"] = m.group(1) if m else ""
            # topic_guess.json -> subject (パイプライン GUI のみ)
            tg = cycle / "topic_guess.json"
            if tg.exists():
                try:
                    data = json.loads(tg.read_text(encoding="utf-8"))
                    subj = data.get("subject") or {}
                    if isinstance(subj, dict):
                        meta["subject"] = subj.get("ja") or subj.get("en") or ""
                    elif isinstance(subj, str):
                        meta["subject"] = subj
                except Exception:
                    pass
            # strokes.json -> n_strokes (for label)
            if strokes_json is not None and strokes_json.exists():
                try:
                    sd = json.loads(strokes_json.read_text(encoding="utf-8"))
                    meta["n_strokes"] = sd.get("n_strokes")
                except Exception:
                    pass
            # mtime for sort
            meta["mtime"] = cycle.stat().st_mtime
            entries.append(meta)
        return entries

    def _refresh(self) -> None:
        self._entries = self._scan_logs()
        self._render()

    def _filtered_entries(self) -> list[dict]:
        q = self.var_filter.get().strip().lower()
        items = self._entries
        if q:
            items = [e for e in items
                     if q in (e.get("subject") or "").lower()
                     or q in e["cycle_dir"].name.lower()
                     or q in e.get("timestamp", "")]
        if self.var_sort.get() == "newest":
            items = sorted(items, key=lambda e: e["mtime"], reverse=True)
        else:
            items = sorted(items, key=lambda e: e["mtime"])
        return items

    # ------------------------------------------------------------------
    # rendering
    # ------------------------------------------------------------------
    def _render(self) -> None:
        # clear existing cards
        for w in self.inner.winfo_children():
            w.destroy()
        self._cards.clear()
        self._selected_key = None
        self.btn_ok.config(state=tk.DISABLED)
        self.lbl_status.config(text="(まだ何も選択していません)")

        items = self._filtered_entries()
        if not items:
            ttk.Label(self.inner,
                       text=(f"❎ {self.logs_dir} 配下に "
                             f"vlm_to_image_*/cycle_*/ が見つかりません" if not self._entries
                             else "❎ フィルタに一致するエントリなし"),
                       foreground="#888", padding=20
                       ).pack(pady=40)
            return

        # 3 columns by default, 1 column if width < ~700px (responsive optional)
        for i, entry in enumerate(items):
            row, col = divmod(i, GRID_COLS)
            card = self._build_card(self.inner, entry)
            card.grid(row=row, column=col, padx=CARD_PAD, pady=CARD_PAD,
                       sticky="nsew")
            self._cards[entry["cycle_dir"].name] = card
        # configure column weights
        for c in range(GRID_COLS):
            self.inner.grid_columnconfigure(c, weight=1)

    def _build_card(self, parent, entry: dict) -> ttk.Frame:
        # outer frame -> click selects
        key = entry["cycle_dir"].name
        outer = tk.Frame(parent, bg="#ffffff",
                          highlightbackground="#cccccc",
                          highlightthickness=1, relief=tk.FLAT)
        outer._key = key             # type: ignore[attr-defined]

        # title
        ts = entry.get("timestamp") or "?"
        subj = entry.get("subject") or "(no subject)"
        n_st = entry.get("n_strokes")
        n_lbl = f"  n={n_st}" if isinstance(n_st, int) else ""
        title = tk.Label(outer,
                          text=f"📷 {ts}  {key}{n_lbl}",
                          bg="#ffffff", anchor="w",
                          font=("Monaco", 10, "bold"))
        title.pack(fill=tk.X, padx=4, pady=(4, 0))

        subj_lbl = tk.Label(outer, text=f"題材: {subj}",
                              bg="#ffffff", anchor="w",
                              fg="#444",
                              font=("Monaco", 10))
        subj_lbl.pack(fill=tk.X, padx=4)

        # 2 thumbnails side by side
        thumb_row = tk.Frame(outer, bg="#ffffff")
        thumb_row.pack(padx=4, pady=(4, 4))
        self._add_thumb(thumb_row, entry.get("generated_image"),
                          fallback="(generated.png なし)")
        self._add_thumb(thumb_row, entry.get("strokes_png"),
                          fallback="(strokes.png なし)")

        # bind click
        def _click(_e=None, k=key):
            self._select(k)
        outer.bind("<Button-1>", _click)
        title.bind("<Button-1>", _click)
        subj_lbl.bind("<Button-1>", _click)
        thumb_row.bind("<Button-1>", _click)
        for child in thumb_row.winfo_children():
            child.bind("<Button-1>", _click)
        # double click = select + OK
        for w in (outer, title, subj_lbl, thumb_row):
            w.bind("<Double-Button-1>", lambda _e, k=key: self._double_click(k))
        return outer

    def _add_thumb(self, parent, path: Optional[Path], fallback: str) -> None:
        cell = tk.Frame(parent, bg="#ffffff", width=THUMB_W, height=THUMB_H + 16)
        cell.pack(side=tk.LEFT, padx=4)
        cell.pack_propagate(False)
        if path and path.exists() and Image is not None:
            try:
                photo = self._thumb_for(path)
                lbl = tk.Label(cell, image=photo, bg="#fafafa", bd=1, relief=tk.SUNKEN)
                lbl.image = photo  # keep ref
                lbl.pack()
                tk.Label(cell, text=path.name, bg="#ffffff",
                          fg="#555", font=("Monaco", 9)
                          ).pack()
                return
            except Exception as e:
                fallback = f"({e})"
        # fallback placeholder
        placeholder = tk.Canvas(cell, width=THUMB_W, height=THUMB_H,
                                  bg="#eeeeee", highlightthickness=1,
                                  highlightbackground="#bbbbbb")
        placeholder.create_text(THUMB_W // 2, THUMB_H // 2, text=fallback,
                                 fill="#888")
        placeholder.pack()

    def _thumb_for(self, path: Path) -> "ImageTk.PhotoImage":
        if path in self._photo_cache:
            return self._photo_cache[path]
        im = Image.open(path).convert("RGB")
        im.thumbnail((THUMB_W, THUMB_H), Image.LANCZOS)
        photo = ImageTk.PhotoImage(im)
        self._photo_cache[path] = photo
        return photo

    # ------------------------------------------------------------------
    # selection
    # ------------------------------------------------------------------
    def _select(self, key: str) -> None:
        # de-highlight previous
        if self._selected_key and self._selected_key in self._cards:
            prev = self._cards[self._selected_key]
            prev.config(highlightbackground="#cccccc", highlightthickness=1)
        # highlight new
        if key in self._cards:
            self._cards[key].config(highlightbackground="#0a84ff",
                                     highlightthickness=3)
        self._selected_key = key
        self.btn_ok.config(state=tk.NORMAL)
        # status text
        for e in self._entries:
            if e["cycle_dir"].name == key:
                self.lbl_status.config(
                    text=f"選択中: {key}  ({e.get('subject', '?')})  "
                         f"→ {e['cycle_dir']}")
                break

    def _double_click(self, key: str) -> None:
        self._select(key)
        self._on_ok()

    def _on_ok(self) -> None:
        if not self._selected_key:
            return
        for e in self._entries:
            if e["cycle_dir"].name == self._selected_key:
                self.result = e
                break
        self.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        self.destroy()


# ----- standalone test launcher --------------------------------------------

def _smoke_test() -> int:
    """Launch picker standalone; print selection to stdout."""
    import sys

    root = tk.Tk()
    root.withdraw()
    try:
        selected = StrokePicker.show(root)
    except Exception as e:
        print(f"picker error: {e}", file=sys.stderr)
        root.destroy()
        return 1
    if selected is None:
        print("(cancelled)")
        root.destroy()
        return 0
    print("--- selected stroke ---")
    for k, v in selected.items():
        if k == "mtime":
            continue
        print(f"  {k:18s}: {v}")
    root.destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(_smoke_test())
