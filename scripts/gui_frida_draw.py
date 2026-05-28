"""Tkinter GUI for Frida-inspired smooth drawing (UX 重視リライト版)。

`wall_drawing_gui` (M11、 ~/piper_test/) とは独立した薄い Tkinter GUI。
画像選択 → 安全確認 → プレビュー → 描画 の 4 ステップ ワンストップ。

UX 設計:
  - セクション (LabelFrame) で「入力 / パラメータ / 実行 / ログ」 を区別
  - 危険ボタン (実機描画) は赤背景、 安全ボタン (Mock/Preview) は通常色
  - 描画中は全ボタン無効化 → 二重実行 / 誤操作 防止
  - パラメータは折りたたみ可能 (default 折りたたみ、 advanced user 向け)
  - tooltip で各パラメータの意味を hover 表示
  - 状態バー (アイドル / 描画中 / エラー) を常時表示
  - 最後に開いたディレクトリ / 描画パラメータを ~/.gui_frida_state.json に保存

使い方:
  python3 -m scripts.gui_frida_draw
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from tkinter import (Tk, Frame, Button, Label, Entry, StringVar, IntVar,
                      OptionMenu, BooleanVar, Checkbutton, Text, Scrollbar,
                      Toplevel, DISABLED, NORMAL)
from tkinter import ttk
from tkinter import filedialog, messagebox

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from modules.robot import Robot, PanelFrame                          # noqa: E402
from modules.vectorizer import Vectorizer                            # noqa: E402
from modules.stroke_planner import (                                 # noqa: E402
    reorder_strokes_tsp, plan_clear_heights, total_travel_distance,
    stroke_set_diagnostics,
)
from modules.stroke_visualizer import (                              # noqa: E402
    render_comparison_grid, render_stroke_animation,
)


SCENES_AVAILABLE = ["face_lite", "scattered", "zigzag"]
STATE_PATH = Path.home() / ".gui_frida_state.json"


# ============================================================ tooltip
class Tooltip:
    """Simple hover tooltip for any widget. Tk 標準のみ、 依存追加なし。"""
    def __init__(self, widget, text: str, delay_ms: int = 500):
        self.widget = widget
        self.text = text
        self.delay = delay_ms
        self.tip = None
        self._after_id = None
        widget.bind("<Enter>", self._schedule)
        widget.bind("<Leave>", self._hide)
        widget.bind("<ButtonPress>", self._hide)

    def _schedule(self, _evt=None):
        self._cancel()
        self._after_id = self.widget.after(self.delay, self._show)

    def _show(self):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        Label(self.tip, text=self.text, justify="left",
              bg="#ffffe0", fg="#000", relief="solid", borderwidth=1,
              font=("Monaco", 9), wraplength=380).pack(ipadx=4, ipady=2)

    def _hide(self, _evt=None):
        self._cancel()
        if self.tip:
            self.tip.destroy()
            self.tip = None

    def _cancel(self):
        if self._after_id:
            self.widget.after_cancel(self._after_id)
            self._after_id = None


# ============================================================ main GUI
class FridaGui:
    def __init__(self, root: Tk):
        self.root = root
        root.title("Frida Smooth Draw")
        root.geometry("820x780")
        root.minsize(720, 640)
        self._worker = None
        self._is_busy = False
        self.panel = None
        self.strokes_mm = None
        self._action_widgets = []     # 描画中 disable する widget
        self._last_dir = str(Path.home())   # 画像選択履歴

        self._load_state()
        self._build_ui()
        self._load_panel()
        self._set_status("起動完了", "ok")

    # ---------------------------------------------------------- state I/O
    def _load_state(self):
        if not STATE_PATH.exists():
            self._state = {}
            return
        try:
            self._state = json.loads(STATE_PATH.read_text())
        except Exception:
            self._state = {}

    def _save_state(self):
        state = {
            "last_image": self.var_image.get(),
            "last_scene": self.var_scene.get(),
            "last_dir": self._last_dir,
            "params": {
                "speed_base": self.var_speed_base.get(),
                "speed_min": self.var_speed_min.get(),
                "speed_max": self.var_speed_max.get(),
                "travel_speed": self.var_travel_speed.get(),
                "near_mm": self.var_near_mm.get(),
                "step_mm": self.var_step_mm.get(),
                "merge_mm": self.var_merge_mm.get(),
                "merge_lift_mm": self.var_merge_lift_mm.get(),
                "reorder": self.var_reorder.get(),
            },
        }
        try:
            STATE_PATH.write_text(json.dumps(state, indent=2))
        except Exception:
            pass    # 設定保存失敗は致命的じゃないので silent

    # ---------------------------------------------------------- UI build
    def _build_ui(self):
        # status bar (top)
        self.status_frame = Frame(self.root, bg="#333", height=26)
        self.status_frame.pack(fill="x")
        self.status_label = Label(self.status_frame, text="...",
                                   bg="#333", fg="#fff",
                                   font=("Monaco", 10), anchor="w", padx=10)
        self.status_label.pack(side="left", fill="x", expand=True)

        # main content
        main = Frame(self.root, padx=10, pady=6)
        main.pack(fill="both", expand=True)

        # ---- Section: 入力 ----
        sec_in = ttk.LabelFrame(main, text="1. 入力", padding=8)
        sec_in.pack(fill="x", pady=(0, 6))
        row = Frame(sec_in)
        row.pack(fill="x")
        Label(row, text="画像ファイル:").pack(side="left")
        self.var_image = StringVar(value=self._state.get("last_image", ""))
        e_img = Entry(row, textvariable=self.var_image, width=50)
        e_img.pack(side="left", padx=4, fill="x", expand=True)
        Tooltip(e_img,
                "VLM/ImageGen の出力画像 (PNG/JPG)。\n"
                "Vectorizer で線画を抽出して描画 strokes に変換します。")
        b = Button(row, text="📁 選択...", command=self._on_pick_image)
        b.pack(side="left", padx=2)
        self._action_widgets.append(b)
        row2 = Frame(sec_in)
        row2.pack(fill="x", pady=(4, 0))
        Label(row2, text="または内蔵シーン:").pack(side="left")
        self.var_scene = StringVar(value=self._state.get("last_scene", ""))
        opt = OptionMenu(row2, self.var_scene, "", "", *SCENES_AVAILABLE)
        opt.pack(side="left", padx=4)
        self._action_widgets.append(opt)
        Tooltip(opt,
                "テスト用の内蔵 stroke set。 画像なしで動作確認用。\n"
                "  face_lite  : 顔のスケッチ (5 strokes)\n"
                "  scattered : 64 個の小 stroke (TSP 効果大)\n"
                "  zigzag    : 1 stroke の zigzag (速度プロファイル可視化)")

        # ---- Section: パラメータ (collapsible) ----
        self._params_visible = BooleanVar(value=False)
        sec_p = ttk.LabelFrame(main, text="2. パラメータ (advanced)",
                                padding=4)
        sec_p.pack(fill="x", pady=(0, 6))
        toggle_row = Frame(sec_p)
        toggle_row.pack(fill="x")
        toggle_btn = Button(toggle_row, text="▶ 表示",
                             command=self._toggle_params, width=8)
        toggle_btn.pack(side="left", padx=4)
        self._params_toggle_btn = toggle_btn
        Label(toggle_row,
              text="速度・距離閾値・stroke 連続化など (default で実用範囲)",
              fg="#666", font=("Monaco", 9)).pack(side="left", padx=4)
        self._params_frame = Frame(sec_p)
        # 折りたたみ default — pack はあとから条件付き

        # parameters (cached state or defaults)
        ps = self._state.get("params", {})
        self.var_speed_base = IntVar(value=ps.get("speed_base", 30))
        self.var_speed_min = IntVar(value=ps.get("speed_min", 10))
        self.var_speed_max = IntVar(value=ps.get("speed_max", 50))
        self.var_travel_speed = IntVar(value=ps.get("travel_speed", 60))
        self.var_near_mm = IntVar(value=ps.get("near_mm", 15))
        self.var_step_mm = StringVar(value=str(ps.get("step_mm", "2.0")))
        self.var_merge_mm = StringVar(value=str(ps.get("merge_mm", "0")))
        self.var_merge_lift_mm = StringVar(
            value=str(ps.get("merge_lift_mm", "0")))
        self.var_reorder = BooleanVar(value=ps.get("reorder", True))

        # parameter rows inside collapsible frame
        speed_lf = ttk.LabelFrame(self._params_frame, text="速度 (%)", padding=4)
        speed_lf.pack(fill="x", pady=2)
        for lab, var, tip in [
            ("緩い曲線", self.var_speed_base,
             "曲率の小さい (= 緩い R) 部分の描画速度 %"),
            ("鋭い曲線", self.var_speed_min,
             "鋭い曲線で減速する最低速度 % (jerk 抑制)"),
            ("直線部上限", self.var_speed_max,
             "ほぼ直線部で加速する上限速度 %"),
            ("travel", self.var_travel_speed,
             "stroke 間 travel の速度 % (pen-up 状態)"),
        ]:
            self._param_row(speed_lf, lab, var, 5, tip)

        dist_lf = ttk.LabelFrame(self._params_frame, text="距離 (mm)", padding=4)
        dist_lf.pack(fill="x", pady=2)
        for lab, var, tip in [
            ("near 閾値", self.var_near_mm,
             "次 stroke までの距離がこれ以下なら pen-up を浅くする (mm)"),
            ("step", self.var_step_mm,
             "smooth_polyline のリサンプル間隔 (mm)、 小さいほど滑らか"),
        ]:
            self._param_row(dist_lf, lab, var, 6, tip)

        merge_lf = ttk.LabelFrame(self._params_frame,
                                   text="stroke 連続化 (任意、 default OFF)",
                                   padding=4)
        merge_lf.pack(fill="x", pady=2)
        self._param_row(merge_lf, "merge 閾値", self.var_merge_mm, 6,
                         "この距離以下の隣接 stroke を pen-up せず接続。\n"
                         "0 = OFF (default)。 ON だと travel 削減大、 副作用で\n"
                         "接続線が描かれる。")
        self._param_row(merge_lf, "pen 浮かし", self.var_merge_lift_mm, 6,
                         "merge 接続時に pen を w_contact から N mm 持ち上げる。\n"
                         "0 = pen-down (接続線描画)、 0.3-1.0 = 軽量化\n"
                         "(実機のペン圧 / spring 次第)。")

        misc_lf = Frame(self._params_frame)
        misc_lf.pack(fill="x", pady=2)
        cb = Checkbutton(misc_lf, text="TSP で stroke 順を最適化",
                          variable=self.var_reorder)
        cb.pack(side="left", padx=4)
        Tooltip(cb,
                "ON (default): TSP greedy + 2-opt で stroke 順を距離最短に\n"
                "OFF: 元の Vectorizer 出力順 (比較用)")

        # ---- Section: 実行 ----
        sec_run = ttk.LabelFrame(main, text="3. 実行", padding=8)
        sec_run.pack(fill="x", pady=(0, 6))
        run_row1 = Frame(sec_run)
        run_row1.pack(fill="x")
        b1 = Button(run_row1, text="🔍 安全性チェック (描画なし)",
                     command=self._on_preflight, width=22)
        b1.pack(side="left", padx=2)
        self._action_widgets.append(b1)
        Tooltip(b1,
                "全 stroke が panel 内に収まるか、 TSP の効果、 推定 timing を\n"
                "ログに表示。 描画コマンドは発行しない。 実機接続も不要。")
        b2 = Button(run_row1, text="🖼  描画プレビュー画像",
                     command=self._on_preview, width=22)
        b2.pack(side="left", padx=2)
        self._action_widgets.append(b2)
        Tooltip(b2,
                "raw 順 vs TSP 後の比較 grid を PNG で生成して別 window で表示。\n"
                "logs/gui_frida/preview_<ts>.png に保存。 実機接続不要。")
        b3 = Button(run_row1, text="🎬 GIF アニメ (描画順)",
                     command=self._on_render_anim, width=22)
        b3.pack(side="left", padx=2)
        self._action_widgets.append(b3)
        Tooltip(b3,
                "1 stroke ずつ累積描画する GIF アニメを生成。\n"
                "現 stroke が赤強調、 「どの順で何を描くか」 を視覚確認可。\n"
                "logs/gui_frida/anim_<ts>.gif に保存。")

        run_row2 = Frame(sec_run)
        run_row2.pack(fill="x", pady=(6, 0))
        b4 = Button(run_row2, text="🧪 Mock 描画 (動作確認)",
                     command=lambda: self._start_draw(use_real=False),
                     width=22)
        b4.pack(side="left", padx=2)
        self._action_widgets.append(b4)
        Tooltip(b4,
                "mock Robot で描画コマンドを発行。 実機なし、 sandbox 動作確認用。\n"
                "実機の動きはしないが、 戻り値の diagnostics を確認できる。")
        b5 = Button(run_row2, text="⚠️  実機で描画 (REAL)",
                     command=lambda: self._start_draw(use_real=True),
                     width=22, bg="#fee", fg="#a00",
                     activebackground="#fcc", activeforeground="#900")
        b5.pack(side="left", padx=2)
        self._action_widgets.append(b5)
        Tooltip(b5,
                "Piper 実機で描画します。\n"
                "事前に: panel に紙、 CAN bus UP、 アーム通電を確認。\n"
                "クリック後 確認ダイアログが出ます。")

        # ---- panel info row ----
        info_row = Frame(main)
        info_row.pack(fill="x", pady=(0, 4))
        self.lbl_panel = Label(info_row, text="panel: (loading...)",
                                fg="#555", font=("Monaco", 9))
        self.lbl_panel.pack(side="left")
        Button(info_row, text="ログクリア",
                command=self._clear_log,
                font=("Monaco", 8)).pack(side="right", padx=2)

        # ---- Section: log ----
        sec_log = ttk.LabelFrame(main, text="4. ログ", padding=4)
        sec_log.pack(fill="both", expand=True)
        sb = Scrollbar(sec_log)
        sb.pack(side="right", fill="y")
        self.log = Text(sec_log, wrap="word", yscrollcommand=sb.set,
                        font=("Monaco", 10), bg="#111", fg="#cfc",
                        height=12)
        self.log.pack(side="left", fill="both", expand=True)
        sb.config(command=self.log.yview)
        self._log("[gui] Frida Smooth Draw 起動")
        self._log(f"[gui] 設定ファイル: {STATE_PATH}")

        # window close: save state
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _param_row(self, parent, label, var, width, tooltip):
        row = Frame(parent)
        row.pack(fill="x", pady=1)
        lbl = Label(row, text=label, width=12, anchor="w")
        lbl.pack(side="left")
        ent = Entry(row, textvariable=var, width=width)
        ent.pack(side="left", padx=2)
        Tooltip(lbl, tooltip)
        Tooltip(ent, tooltip)

    # ---------------------------------------------------------- UI helpers
    def _toggle_params(self):
        if self._params_visible.get():
            self._params_frame.pack_forget()
            self._params_toggle_btn.config(text="▶ 表示")
            self._params_visible.set(False)
        else:
            self._params_frame.pack(fill="x", padx=2, pady=2)
            self._params_toggle_btn.config(text="▼ 隠す")
            self._params_visible.set(True)

    def _log(self, msg: str):
        t = time.strftime("%H:%M:%S")
        self.log.insert("end", f"{t}  {msg}\n")
        self.log.see("end")

    def _clear_log(self):
        self.log.delete("1.0", "end")

    def _set_status(self, msg: str, kind: str = "info"):
        colors = {
            "ok": ("#0a4", "#fff"),
            "info": ("#333", "#fff"),
            "busy": ("#c80", "#fff"),
            "error": ("#a00", "#fff"),
        }
        bg, fg = colors.get(kind, ("#333", "#fff"))
        self.status_frame.config(bg=bg)
        self.status_label.config(bg=bg, fg=fg, text=msg)

    def _set_busy(self, busy: bool, msg: str = ""):
        self._is_busy = busy
        state = DISABLED if busy else NORMAL
        for w in self._action_widgets:
            try:
                w.config(state=state)
            except Exception:
                pass
        if busy:
            self._set_status(msg or "実行中...", "busy")
        else:
            self._set_status(msg or "アイドル", "ok")

    # ---------------------------------------------------------- panel
    def _load_panel(self):
        path = _ROOT / "calibration" / "panel_frame.yaml"
        if not path.exists():
            self._log(f"[panel] yaml not found: {path}")
            self.lbl_panel.config(text=f"panel: ❌ NOT FOUND ({path.name})",
                                   fg="#a00")
            self._set_status(f"panel_frame.yaml が無い: {path}", "error")
            return
        try:
            self.panel = PanelFrame.from_yaml(path)
        except Exception as e:
            self._log(f"[panel] load failed: {e}")
            self.lbl_panel.config(text=f"panel: ❌ load failed", fg="#a00")
            self._set_status(f"panel load 失敗: {e}", "error")
            return
        cal = "✅ calibrated" if self.panel.calibrated else "⚠️ PLACEHOLDER"
        sz = self.panel.size_mm
        self.lbl_panel.config(
            text=f"panel: {sz[0]:.1f} × {sz[1]:.1f} mm  {cal}",
            fg="#080" if self.panel.calibrated else "#a40")
        self._log(f"[panel] loaded ({cal}), size = {sz}")

    # ---------------------------------------------------------- load strokes
    def _resolve_strokes(self):
        if self.panel is None:
            return None, "panel_frame.yaml が読み込めてません"
        img = self.var_image.get().strip()
        scene = self.var_scene.get().strip()
        if img:
            ip = Path(img)
            if not ip.exists():
                return None, f"画像が見つかりません: {ip}"
            try:
                vec = Vectorizer(verbose=False)
                result = vec.vectorize_to_panel(str(ip), None, self.panel,
                                                  clip_to_bounds=True)
                return result.strokes_mm or [], f"image ({ip.name})"
            except Exception as e:
                return None, f"画像のベクトル化 失敗: {e}"
        if scene:
            try:
                from scripts.test_draw_strokes_smooth import SCENES
                return SCENES[scene](self.panel), f"scene ({scene})"
            except Exception as e:
                return None, f"scene の load 失敗: {e}"
        return None, "画像も scene も指定されていません"

    # ---------------------------------------------------------- preflight
    def _on_preflight(self):
        strokes, src = self._resolve_strokes()
        if strokes is None:
            self._log(f"[preflight] ❌ {src}")
            self._set_status(src, "error")
            return
        self.strokes_mm = strokes
        self._log(f"[preflight] source = {src}, {len(strokes)} strokes")
        n_oob = sum(1 for s in strokes
                    for (u, v) in s if not self.panel.in_bounds(u, v))
        n_total = sum(len(s) for s in strokes)
        if n_oob == 0:
            self._log(f"  ✓ in_bounds: all {n_total} points")
            self._set_status(f"安全性 OK ({n_total} 点全て panel 内)", "ok")
        else:
            self._log(f"  ⚠️ in_bounds: {n_oob}/{n_total} points OUT OF BOUNDS")
            self._set_status(f"⚠️ {n_oob} 点が panel 外", "error")
        diag = stroke_set_diagnostics(strokes, start_point=None)
        before = total_travel_distance(strokes, start_point=None)
        reordered, _ = reorder_strokes_tsp(strokes, start_point=None)
        after = total_travel_distance(reordered, start_point=None)
        saved = 100 * (before - after) / max(1e-9, before)
        self._log(f"  draw 長     : {diag['draw_length_mm']:.1f} mm")
        self._log(f"  travel raw  : {before:.1f} mm")
        self._log(f"  travel TSP後: {after:.1f} mm (-{saved:.1f}%)")

    # ---------------------------------------------------------- preview
    def _on_preview(self):
        strokes, src = self._resolve_strokes()
        if strokes is None:
            messagebox.showerror("preview", src)
            return
        self.strokes_mm = strokes
        reordered, _ = reorder_strokes_tsp(strokes, start_point=None)
        img = render_comparison_grid(
            strokes, tuple(self.panel.size_mm),
            reordered_strokes_mm=reordered,
            out_size_px=(1200, 600),
        )
        out_dir = _ROOT / "logs" / "gui_frida"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"preview_{time.strftime('%Y%m%d_%H%M%S')}.png"
        img.save(out_path)
        self._log(f"[preview] 保存: {out_path}")
        self._set_status(f"preview 生成: {out_path.name}", "ok")
        win = Toplevel(self.root)
        win.title("描画プレビュー (左=raw / 右=TSP後)")
        try:
            from PIL import ImageTk
            tk_img = ImageTk.PhotoImage(img)
            lab = Label(win, image=tk_img)
            lab.image = tk_img
            lab.pack()
            Label(win,
                   text=f"📄 {out_path.name}",
                   font=("Monaco", 9), fg="#666").pack(pady=4)
        except Exception as e:
            self._log(f"[preview] window 表示失敗 (PIL.ImageTk): {e} — "
                      "ファイルとしては保存済")
            messagebox.showinfo("preview",
                f"画像は保存しましたが、 GUI 表示には PIL.ImageTk が必要です:\n"
                f"  {out_path}\nファイルを直接開いてください。")

    # ---------------------------------------------------------- anim
    def _on_render_anim(self):
        strokes, src = self._resolve_strokes()
        if strokes is None:
            messagebox.showerror("anim", src)
            return
        reordered, _ = reorder_strokes_tsp(strokes, start_point=None)
        out_dir = _ROOT / "logs" / "gui_frida"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"anim_{time.strftime('%Y%m%d_%H%M%S')}.gif"
        self._set_status("GIF 生成中...", "busy")
        try:
            n, p = render_stroke_animation(
                reordered, tuple(self.panel.size_mm), out_path,
                out_size_px=(800, 800), frame_ms=200,
            )
            self._log(f"[anim] {n} frames → {p}")
            self._set_status(f"GIF 生成: {Path(p).name} ({n} frames)", "ok")
            messagebox.showinfo("GIF アニメ生成",
                f"{n} frames を出力しました:\n{p}")
        except Exception as e:
            self._log(f"[anim] ❌ {e}")
            self._set_status(f"GIF 生成失敗: {e}", "error")

    # ---------------------------------------------------------- draw
    def _start_draw(self, *, use_real: bool):
        if self._is_busy:
            return
        strokes, src = self._resolve_strokes()
        if strokes is None:
            messagebox.showerror("draw", src)
            return
        if use_real:
            if not messagebox.askyesno(
                "⚠️ 実機描画 確認",
                f"REAL モードで {len(strokes)} 本の stroke を実機描画します。\n\n"
                "事前確認:\n"
                "  ☐ panel に紙 / ホワイトボードが配置されている\n"
                "  ☐ アームが通電 + can0 が UP\n"
                "  ☐ panel calibrated (drag-teach 済み)\n\n"
                "続けますか?"):
                return
        self._worker = threading.Thread(
            target=self._draw_thread, args=(strokes, src, use_real),
            daemon=True)
        self._worker.start()

    def _draw_thread(self, strokes, src, use_real):
        self.root.after(0, lambda: self._set_busy(True,
                          f"{'REAL' if use_real else 'MOCK'} 描画中..."))
        self._log(f"[draw] 開始 ({'REAL' if use_real else 'MOCK'}) "
                  f"— source = {src}, {len(strokes)} strokes")
        robot = None
        try:
            robot = Robot(mock=not use_real, panel_frame=self.panel,
                           use_feedback_workaround=use_real)
            robot.connect()
            if not use_real:
                robot.wait_for_pose = lambda *a, **kw: True   # type: ignore
            if use_real:
                self._log("[draw] ready pose に移動中 ...")
                robot.goto_ready_pose(speed_pct=15, settle_s=10.0)
            try:
                merge_mm = float(self.var_merge_mm.get() or 0)
            except ValueError:
                merge_mm = 0.0
            try:
                merge_lift = float(self.var_merge_lift_mm.get() or 0)
            except ValueError:
                merge_lift = 0.0
            t0 = time.time()
            diag = robot.draw_strokes_panel_smooth(
                strokes,
                travel_speed=int(self.var_travel_speed.get()),
                draw_speed_base=int(self.var_speed_base.get()),
                draw_speed_min=int(self.var_speed_min.get()),
                draw_speed_max=int(self.var_speed_max.get()),
                near_threshold_mm=float(self.var_near_mm.get()),
                step_mm=float(self.var_step_mm.get()),
                reorder=bool(self.var_reorder.get()),
                merge_threshold_mm=merge_mm,
                merge_pen_lift_mm=merge_lift,
            )
            elapsed = time.time() - t0
            self._log(f"[draw] ✅ 完了 ({elapsed:.2f}s)")
            self._log(f"  arc 数      = {diag.get('n_arcs')}")
            self._log(f"  travel 削減 = {diag.get('travel_saved_mm', 0):.1f} mm")
            self._log(f"  速度 平均/最大 = "
                      f"{diag.get('speed_mean_pct', 0):.1f}% / "
                      f"{diag.get('speed_max_pct', 0)}%")
            if use_real:
                self._log("[draw] ready pose に戻り中 ...")
                robot.goto_ready_pose(speed_pct=15, settle_s=6.0)
            self.root.after(0,
                lambda: self._set_status(f"描画完了 ({elapsed:.1f}s)", "ok"))
        except Exception as e:
            self._log(f"[draw] ❌ {e}")
            self.root.after(0,
                lambda e=e: self._set_status(f"描画 失敗: {e}", "error"))
        finally:
            if robot is not None:
                try:
                    robot.disconnect()
                except Exception:
                    pass
            self.root.after(0, lambda: self._set_busy(False))

    # ---------------------------------------------------------- events
    def _on_pick_image(self):
        path = filedialog.askopenfilename(
            title="画像を選択",
            initialdir=self._last_dir,
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp"),
                       ("All", "*")])
        if path:
            self.var_image.set(path)
            self.var_scene.set("")
            self._last_dir = str(Path(path).parent)
            self._log(f"[ui] 画像: {Path(path).name}")
            self._set_status(f"画像: {Path(path).name}", "ok")

    def _on_close(self):
        self._save_state()
        self.root.destroy()


def main():
    root = Tk()
    FridaGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
