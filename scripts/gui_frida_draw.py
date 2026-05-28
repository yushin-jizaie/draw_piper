"""Tkinter GUI for Frida-inspired smooth drawing.

`wall_drawing_gui` (M11、 ~/piper_test/) とは別の独立した薄い GUI。
画像 → Vectorizer → preflight → preview → 実機描画 のワンストップ操作。

ボタン構成:
  [画像選択...]  [シーン: face_lite ▼]
  [Preflight 確認]   ← bounds / TSP / 推定 timing を log に表示
  [Preview 画像生成] ← strokes を画像化、 別ウィンドウで表示
  [Mock 描画 (dry)]  ← mock=True で描画コマンド発行のみ (sandbox 動作確認)
  [実機描画 (REAL)]  ← Piper SDK + can0 で本描画 (確認ダイアログあり)
  [中止]            ← 進行中の描画スレッドへ flag を立てる

設定 (調整可、 デフォルト 既存推奨値):
  draw_speed_base / min / max / travel_speed
  near_threshold_mm / step_mm / reorder

ログ Area で実行ログ表示、 結果 JSON は logs/gui_frida/<ts>/ に保存。

使い方:
  python3 -m scripts.gui_frida_draw
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from tkinter import (Tk, Frame, Button, Label, Entry, StringVar, IntVar,
                      OptionMenu, BooleanVar, Checkbutton, Text, Scrollbar,
                      filedialog, messagebox, Toplevel, Canvas)
from tkinter import ttk

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from modules.robot import Robot, PanelFrame                          # noqa: E402
from modules.vectorizer import Vectorizer                            # noqa: E402
from modules.stroke_planner import (                                 # noqa: E402
    reorder_strokes_tsp, plan_clear_heights, total_travel_distance,
    stroke_set_diagnostics,
)
from modules.stroke_visualizer import render_comparison_grid         # noqa: E402


# scenes (test_draw_strokes_smooth と同じ)
SCENES_AVAILABLE = ["face_lite", "scattered", "zigzag"]


class FridaGui:
    def __init__(self, root: Tk):
        self.root = root
        root.title("Frida Smooth Draw")
        root.geometry("760x680")
        self._abort = False
        self._worker = None
        self.panel = None
        self.strokes_mm = None    # 最後にロードした strokes
        self._build_ui()
        self._load_panel()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        # row 0: input source
        row0 = Frame(self.root)
        row0.pack(fill="x", padx=8, pady=4)
        Label(row0, text="入力:").pack(side="left")
        self.var_image = StringVar(value="")
        Entry(row0, textvariable=self.var_image, width=50).pack(
            side="left", padx=4)
        Button(row0, text="画像選択...", command=self._on_pick_image).pack(
            side="left", padx=2)
        Label(row0, text="または scene:").pack(side="left", padx=(10, 2))
        self.var_scene = StringVar(value="")
        OptionMenu(row0, self.var_scene, "", *SCENES_AVAILABLE).pack(
            side="left")

        # row 1: parameters
        row1 = Frame(self.root)
        row1.pack(fill="x", padx=8, pady=4)
        self.var_speed_base = IntVar(value=30)
        self.var_speed_min = IntVar(value=10)
        self.var_speed_max = IntVar(value=50)
        self.var_travel_speed = IntVar(value=60)
        self.var_near_mm = IntVar(value=15)
        self.var_step_mm = StringVar(value="2.0")
        self.var_merge_mm = StringVar(value="0")
        self.var_merge_lift_mm = StringVar(value="0")
        self.var_reorder = BooleanVar(value=True)
        for lab, var, w in [
            ("draw base", self.var_speed_base, 4),
            ("min", self.var_speed_min, 4),
            ("max", self.var_speed_max, 4),
            ("travel", self.var_travel_speed, 4),
            ("near_mm", self.var_near_mm, 4),
            ("step_mm", self.var_step_mm, 5),
            ("merge_mm", self.var_merge_mm, 5),
            ("merge_lift", self.var_merge_lift_mm, 5),
        ]:
            Label(row1, text=lab).pack(side="left", padx=(8, 1))
            Entry(row1, textvariable=var, width=w).pack(side="left")
        Checkbutton(row1, text="TSP reorder",
                     variable=self.var_reorder).pack(side="left", padx=8)

        # row 2: action buttons
        row2 = Frame(self.root)
        row2.pack(fill="x", padx=8, pady=6)
        Button(row2, text="Preflight 確認",
                command=self._on_preflight).pack(side="left", padx=2)
        Button(row2, text="Preview 画像",
                command=self._on_preview).pack(side="left", padx=2)
        Button(row2, text="GIF アニメ",
                command=self._on_render_anim).pack(side="left", padx=2)
        Button(row2, text="Mock 描画 (dry)",
                command=lambda: self._start_draw(use_real=False)).pack(
            side="left", padx=2)
        Button(row2, text="実機描画 (REAL)", bg="#e7a",
                command=lambda: self._start_draw(use_real=True)).pack(
            side="left", padx=2)
        Button(row2, text="中止",
                command=self._on_abort).pack(side="left", padx=8)

        # row 3: panel info
        row3 = Frame(self.root)
        row3.pack(fill="x", padx=8)
        self.lbl_panel = Label(row3, text="panel: (loading...)",
                                fg="#555")
        self.lbl_panel.pack(side="left")

        # log area
        log_frame = Frame(self.root)
        log_frame.pack(fill="both", expand=True, padx=8, pady=8)
        sb = Scrollbar(log_frame)
        sb.pack(side="right", fill="y")
        self.log = Text(log_frame, wrap="word", yscrollcommand=sb.set,
                        font=("Monaco", 10), bg="#111", fg="#cfc")
        self.log.pack(side="left", fill="both", expand=True)
        sb.config(command=self.log.yview)
        self._log("[gui] Frida Smooth Draw 起動")

    def _log(self, msg: str):
        t = time.strftime("%H:%M:%S")
        self.log.insert("end", f"{t}  {msg}\n")
        self.log.see("end")

    # ------------------------------------------------------------------ panel
    def _load_panel(self):
        path = _ROOT / "calibration" / "panel_frame.yaml"
        if not path.exists():
            self._log(f"[panel] yaml not found: {path}")
            self.lbl_panel.config(text=f"panel: NOT FOUND ({path.name})")
            return
        try:
            self.panel = PanelFrame.from_yaml(path)
        except Exception as e:
            self._log(f"[panel] load failed: {e}")
            return
        cal = "calibrated" if self.panel.calibrated else "PLACEHOLDER"
        sz = self.panel.size_mm
        self.lbl_panel.config(
            text=f"panel: {sz[0]:.1f} × {sz[1]:.1f} mm ({cal})",
            fg="#080" if self.panel.calibrated else "#a40")
        self._log(f"[panel] loaded ({cal}), size = {sz}")

    # ------------------------------------------------------------------ load strokes
    def _resolve_strokes(self):
        """Return (strokes_mm, source_label) or (None, error_msg)."""
        if self.panel is None:
            return None, "panel が load されていません"
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
                return None, f"vectorize 失敗: {e}"
        if scene:
            try:
                from scripts.test_draw_strokes_smooth import SCENES
                return SCENES[scene](self.panel), f"scene ({scene})"
            except Exception as e:
                return None, f"scene load 失敗: {e}"
        return None, "画像も scene も未指定です"

    # ------------------------------------------------------------------ preflight
    def _on_preflight(self):
        strokes, src = self._resolve_strokes()
        if strokes is None:
            self._log(f"[preflight] {src}")
            return
        self.strokes_mm = strokes
        self._log(f"[preflight] source = {src}, {len(strokes)} strokes")
        n_oob = sum(1 for s in strokes
                    for (u, v) in s if not self.panel.in_bounds(u, v))
        if n_oob == 0:
            self._log(f"  ✓ in_bounds: all "
                      f"{sum(len(s) for s in strokes)} points")
        else:
            self._log(f"  ⚠️ in_bounds: {n_oob} oob points")
        diag = stroke_set_diagnostics(strokes, start_point=None)
        before = total_travel_distance(strokes, start_point=None)
        reordered, _ = reorder_strokes_tsp(strokes, start_point=None)
        after = total_travel_distance(reordered, start_point=None)
        self._log(f"  draw_length_mm   : {diag['draw_length_mm']:.1f}")
        self._log(f"  travel raw       : {before:.1f}")
        self._log(f"  travel TSP       : {after:.1f} "
                  f"(-{100 * (before - after) / max(1e-9, before):.1f}%)")
        ch = plan_clear_heights(
            reordered, w_clear_max_mm=self.panel.w_clear_mm,
            w_clear_near_mm=self.panel.w_contact_mm
                + (self.panel.w_clear_mm - self.panel.w_contact_mm) / 3,
            near_threshold_mm=float(self.var_near_mm.get()))
        n_near = sum(1 for h in ch if h < self.panel.w_clear_mm)
        self._log(f"  look-ahead clear : {n_near}/{len(ch)} strokes use lower")

    # ------------------------------------------------------------------ preview
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
        self._log(f"[preview] saved -> {out_path}")
        # show in Toplevel
        win = Toplevel(self.root)
        win.title("stroke preview (raw vs TSP)")
        try:
            from PIL import ImageTk
            tk_img = ImageTk.PhotoImage(img)
            lab = Label(win, image=tk_img)
            lab.image = tk_img    # keep reference
            lab.pack()
            Label(win,
                   text=f"raw → TSP の travel 比較。 保存先: {out_path.name}",
                   font=("Monaco", 9)).pack(pady=4)
        except Exception as e:
            self._log(f"[preview] window 表示失敗 (PIL.ImageTk): {e} — "
                      "ファイルとしては保存済")

    # ------------------------------------------------------------------ anim
    def _on_render_anim(self):
        """stroke ordering GIF アニメを生成。 描画順を 1 stroke ずつ
        累積表示。 default 200ms/frame。 user 確認用、 描画には影響しない。
        """
        strokes, src = self._resolve_strokes()
        if strokes is None:
            messagebox.showerror("anim", src)
            return
        from modules.stroke_visualizer import render_stroke_animation
        from modules.stroke_planner import reorder_strokes_tsp
        reordered, _ = reorder_strokes_tsp(strokes, start_point=None)
        out_dir = _ROOT / "logs" / "gui_frida"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"anim_{time.strftime('%Y%m%d_%H%M%S')}.gif"
        try:
            n, p = render_stroke_animation(
                reordered, tuple(self.panel.size_mm), out_path,
                out_size_px=(800, 800), frame_ms=200,
            )
            self._log(f"[anim] {n} frames → {p}")
            messagebox.showinfo("GIF アニメ生成",
                f"{n} frames を出力しました:\n{p}")
        except Exception as e:
            self._log(f"[anim] ERROR: {e}")
            messagebox.showerror("anim", str(e))

    # ------------------------------------------------------------------ draw
    def _start_draw(self, *, use_real: bool):
        if self._worker and self._worker.is_alive():
            messagebox.showwarning("busy", "前回の描画スレッド進行中")
            return
        strokes, src = self._resolve_strokes()
        if strokes is None:
            messagebox.showerror("draw", src)
            return
        if use_real:
            if not messagebox.askyesno(
                "実機描画 確認",
                f"REAL モードで {len(strokes)} stroke を描画します。\n"
                "panel に紙が用意されてますか? 続行しますか?"):
                return
        self._abort = False
        self._worker = threading.Thread(
            target=self._draw_thread, args=(strokes, src, use_real),
            daemon=True)
        self._worker.start()

    def _on_abort(self):
        self._abort = True
        self._log("[abort] flag set — 現在の stroke 完了後に停止 (実装次第)")

    def _draw_thread(self, strokes, src, use_real):
        """Worker thread for draw (so GUI stays responsive)."""
        self._log(f"[draw] start ({'REAL' if use_real else 'MOCK'}) "
                  f"— source = {src}, {len(strokes)} strokes")
        robot = None
        try:
            robot = Robot(mock=not use_real, panel_frame=self.panel,
                           use_feedback_workaround=use_real)
            robot.connect()
            if not use_real:
                # speed up mock
                robot.wait_for_pose = lambda *a, **kw: True   # type: ignore
            if use_real:
                self._log("[draw] moving to ready pose ...")
                robot.goto_ready_pose(speed_pct=15, settle_s=10.0)
            t0 = time.time()
            try:
                merge_mm = float(self.var_merge_mm.get() or 0)
            except ValueError:
                merge_mm = 0.0
            try:
                merge_lift = float(self.var_merge_lift_mm.get() or 0)
            except ValueError:
                merge_lift = 0.0
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
            self._log(f"[draw] done in {elapsed:.2f}s")
            self._log(f"  n_arcs           = {diag.get('n_arcs')}")
            self._log(f"  travel saved     = {diag.get('travel_saved_mm', 0):.1f} mm")
            self._log(f"  speed mean/max   = "
                      f"{diag.get('speed_mean_pct', 0):.1f}% / "
                      f"{diag.get('speed_max_pct', 0)}%")
            if use_real:
                self._log("[draw] returning to ready pose ...")
                robot.goto_ready_pose(speed_pct=15, settle_s=6.0)
        except Exception as e:
            self._log(f"[draw] ERROR: {e}")
        finally:
            if robot is not None:
                try:
                    robot.disconnect()
                except Exception:
                    pass

    # ------------------------------------------------------------------ events
    def _on_pick_image(self):
        path = filedialog.askopenfilename(
            title="画像選択",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp"),
                       ("All", "*")])
        if path:
            self.var_image.set(path)
            self.var_scene.set("")    # clear scene
            self._log(f"[ui] image: {path}")


def main():
    root = Tk()
    FridaGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
