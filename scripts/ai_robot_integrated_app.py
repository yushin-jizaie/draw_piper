#!/usr/bin/env python3
"""画像生成AI + Piper 壁面描画 統合アプリ。

開始ボタンで実行ウィンドウを開き、そのウィンドウがアクティブな状態で
Enter を押すと:

  撮影 → OpenCV 正規化 → VLM → 画像生成 → ストローク化 → ロボットアーム描画

を一気通貫で実行する。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

ROOT = Path(__file__).resolve().parent.parent
PIPELINE_SCRIPT = ROOT / "scripts" / "gen_latest_route.py"
PIPER_TEST = Path("/home/jizaiedev2026/piper_test")
DRAW_SCRIPT = PIPER_TEST / "draw_strokes_wall_dev.py"
PYTHON = ROOT / "venv" / "bin" / "python"
LOGS_DIR = ROOT / "logs"

CAMERA_ROTATE = "ccw"

# pipeline_test_gui.py の既定値に合わせる。
PIPELINE_DEFAULTS = {
    "route": "flux_decorate",
    "steps": 4,
    "seed": "",
    "vstretch": "1.0",
    "literal_only": False,
    "warp_correct": False,
    "one_stroke": False,
    "no_split": False,
    "category": "character",
    "design_mode": "decorate",
    "ip_scale": "0.60",
    "stage2_strength": "0.45",
    "ip_diff": True,
    "min_feature": "8.0",
    "ip_frac": "0.38",
    "place_scale": "1.00",
    "place_dx": "0",
    "place_dy": "0",
    "flux_style": "decorate",
    "lora_str": "0.6",
}

# wall_gui2 / wall_drawing_gui_full_dev2.py の Section 5/Frida 系既定値。
DRAW_DEFAULTS = {
    "max_strokes": "0",
    "travel_speed": 50,
    "draw_speed": 25,
    "inter_point_delay": "0.03",  # var_strokes_interpoint_ms = 30
}

try:
    import cv2
    import numpy as np
except Exception as e:  # pragma: no cover - GUIで表示する
    cv2 = None
    np = None
    _CV2_IMPORT_ERROR = e
else:
    _CV2_IMPORT_ERROR = None

try:
    from PIL import Image, ImageTk
except Exception as e:  # pragma: no cover - GUIで表示する
    Image = None
    ImageTk = None
    _PIL_IMPORT_ERROR = e
else:
    _PIL_IMPORT_ERROR = None


def rotate_camera_frame(frame):
    if cv2 is None or frame is None or CAMERA_ROTATE is None:
        return frame
    code = {
        "ccw": cv2.ROTATE_90_COUNTERCLOCKWISE,
        "cw": cv2.ROTATE_90_CLOCKWISE,
        "180": cv2.ROTATE_180,
    }.get(CAMERA_ROTATE)
    return cv2.rotate(frame, code) if code is not None else frame


def normalize_capture(frame_bgr, out_dir: Path) -> Path:
    """OpenCVで撮影画像を線画入力向けに正規化し、白地黒線画像を返す。

    保存する中間画像:
      00_raw.png, 01_rotated.png, 02_gray.png, 03_clahe.png,
      04_denoised.png, 05_bg_norm.png, 06_adaptive_binary.png,
      07_morph_clean.png, input_normalized.png
    """
    if cv2 is None or np is None:
        raise RuntimeError(f"cv2 import failed: {_CV2_IMPORT_ERROR}")
    out_dir.mkdir(parents=True, exist_ok=True)

    cv2.imwrite(str(out_dir / "00_raw.png"), frame_bgr)
    frame = rotate_camera_frame(frame_bgr)
    cv2.imwrite(str(out_dir / "01_rotated.png"), frame)

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    cv2.imwrite(str(out_dir / "02_gray.png"), gray)

    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    cv2.imwrite(str(out_dir / "03_clahe.png"), clahe)

    denoised = cv2.bilateralFilter(clahe, 7, 35, 35)
    cv2.imwrite(str(out_dir / "04_denoised.png"), denoised)

    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31))
    bg = cv2.morphologyEx(denoised, cv2.MORPH_CLOSE, k)
    norm = cv2.divide(denoised, bg, scale=255)
    cv2.imwrite(str(out_dir / "05_bg_norm.png"), norm)

    binary_inv = cv2.adaptiveThreshold(
        norm, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV, 31, 9)
    cv2.imwrite(str(out_dir / "06_adaptive_binary.png"), binary_inv)

    open_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2, 2))
    cleaned = cv2.morphologyEx(binary_inv, cv2.MORPH_OPEN, open_k)
    close_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2, 2))
    cleaned = cv2.morphologyEx(cleaned, cv2.MORPH_CLOSE, close_k)
    cv2.imwrite(str(out_dir / "07_morph_clean.png"), cleaned)

    white_bg_black_line = 255 - cleaned
    out = out_dir / "input_normalized.png"
    cv2.imwrite(str(out), white_bg_black_line)
    return out


class IntegratedApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("画像生成AI + ロボットアーム 統合アプリ")
        self.root.geometry("520x180")
        self.exec_window: ExecutionWindow | None = None
        self._build()

    def _build(self):
        frame = ttk.Frame(self.root, padding=18)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            frame,
            text="画像生成AIシステムとロボットアームシステムを統合",
            font=("", 14, "bold"),
        ).pack(anchor=tk.W, pady=(0, 12))
        ttk.Button(
            frame,
            text="開始",
            command=self.open_execution_window,
            width=24,
        ).pack(anchor=tk.W)
        ttk.Label(
            frame,
            text="実行ウィンドウをアクティブにして Enter で自動実行",
            foreground="#666",
        ).pack(anchor=tk.W, pady=(12, 0))

    def open_execution_window(self):
        if self.exec_window is not None and self.exec_window.exists():
            self.exec_window.lift()
            return
        self.exec_window = ExecutionWindow(self.root)


class ExecutionWindow:
    def __init__(self, master):
        self.win = tk.Toplevel(master)
        self.win.title("実行モード - Enterで撮影から描画まで実行")
        self.win.geometry("1120x760")
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self.win.bind("<Return>", self.on_enter)

        self.cap = None
        self.camera_device = tk.IntVar(value=0)
        self.route = tk.StringVar(value=PIPELINE_DEFAULTS["route"])
        self.steps = tk.IntVar(value=PIPELINE_DEFAULTS["steps"])
        self.seed = tk.StringVar(value=PIPELINE_DEFAULTS["seed"])
        self.vstretch = tk.StringVar(value=PIPELINE_DEFAULTS["vstretch"])
        self.literal_only = tk.BooleanVar(value=PIPELINE_DEFAULTS["literal_only"])
        self.warp_correct = tk.BooleanVar(value=PIPELINE_DEFAULTS["warp_correct"])
        self.one_stroke = tk.BooleanVar(value=PIPELINE_DEFAULTS["one_stroke"])
        self.no_split = tk.BooleanVar(value=PIPELINE_DEFAULTS["no_split"])
        self.category = tk.StringVar(value=PIPELINE_DEFAULTS["category"])
        self.design_mode = tk.StringVar(value=PIPELINE_DEFAULTS["design_mode"])
        self.ip_scale = tk.StringVar(value=PIPELINE_DEFAULTS["ip_scale"])
        self.stage2_strength = tk.StringVar(value=PIPELINE_DEFAULTS["stage2_strength"])
        self.ip_diff = tk.BooleanVar(value=PIPELINE_DEFAULTS["ip_diff"])
        self.min_feature = tk.StringVar(value=PIPELINE_DEFAULTS["min_feature"])
        self.ip_frac = tk.StringVar(value=PIPELINE_DEFAULTS["ip_frac"])
        self.place_scale = tk.StringVar(value=PIPELINE_DEFAULTS["place_scale"])
        self.place_dx = tk.StringVar(value=PIPELINE_DEFAULTS["place_dx"])
        self.place_dy = tk.StringVar(value=PIPELINE_DEFAULTS["place_dy"])
        self.flux_style = tk.StringVar(value=PIPELINE_DEFAULTS["flux_style"])
        self.lora_str = tk.StringVar(value=PIPELINE_DEFAULTS["lora_str"])
        self.max_strokes = tk.StringVar(value=DRAW_DEFAULTS["max_strokes"])
        self.travel_speed = tk.IntVar(value=DRAW_DEFAULTS["travel_speed"])
        self.draw_speed = tk.IntVar(value=DRAW_DEFAULTS["draw_speed"])
        self.inter_point_delay = tk.StringVar(value=DRAW_DEFAULTS["inter_point_delay"])
        self.running = False
        self.last_frame = None
        self.log_q: queue.Queue[str] = queue.Queue()

        self._photo = None
        self._build()
        self._open_camera()
        self._schedule_camera()
        self._schedule_logs()
        self.log("実行モード準備完了。Enterで開始。")

    def exists(self):
        return bool(self.win.winfo_exists())

    def _build(self):
        top = ttk.LabelFrame(self.win, text="入力 / 生成", padding=8)
        top.pack(fill=tk.X)
        ttk.Label(top, text="Camera").pack(side=tk.LEFT)
        tk.Spinbox(top, from_=0, to=9, textvariable=self.camera_device,
                   width=3, command=self._reopen_camera).pack(side=tk.LEFT, padx=4)
        ttk.Button(top, text="カメラ再接続", command=self._reopen_camera).pack(side=tk.LEFT, padx=4)
        ttk.Label(top, text="Route").pack(side=tk.LEFT, padx=(18, 4))
        ttk.Combobox(
            top, textvariable=self.route, width=16, state="readonly",
            values=["flux_decorate", "sdxl_routed", "sdxl_text2img", "ip_matsumoto"],
        ).pack(side=tk.LEFT)
        ttk.Label(top, text="Design").pack(side=tk.LEFT, padx=(12, 4))
        ttk.Combobox(
            top, textvariable=self.design_mode, width=9, state="readonly",
            values=["decorate", "complete", "finish"],
        ).pack(side=tk.LEFT)
        ttk.Label(top, text="FLUX").pack(side=tk.LEFT, padx=(12, 4))
        ttk.Combobox(
            top, textvariable=self.flux_style, width=9, state="readonly",
            values=["decorate", "simple"],
        ).pack(side=tk.LEFT)
        ttk.Label(top, text="Steps").pack(side=tk.LEFT, padx=(12, 4))
        tk.Spinbox(top, from_=1, to=30, textvariable=self.steps, width=4).pack(side=tk.LEFT)
        ttk.Label(top, text="Seed").pack(side=tk.LEFT, padx=(12, 4))
        tk.Entry(top, textvariable=self.seed, width=8).pack(side=tk.LEFT)
        ttk.Label(top, text="LoRA").pack(side=tk.LEFT, padx=(12, 4))
        tk.Entry(top, textvariable=self.lora_str, width=5).pack(side=tk.LEFT)

        opt = ttk.LabelFrame(self.win, text="pipeline_test_gui.py 参照パラメータ", padding=8)
        opt.pack(fill=tk.X, padx=8, pady=(0, 4))
        ttk.Label(opt, text="vstretch").pack(side=tk.LEFT)
        tk.Entry(opt, textvariable=self.vstretch, width=5).pack(side=tk.LEFT, padx=(4, 10))
        ttk.Label(opt, text="min_feature").pack(side=tk.LEFT)
        tk.Entry(opt, textvariable=self.min_feature, width=5).pack(side=tk.LEFT, padx=(4, 10))
        ttk.Label(opt, text="place scale/dx/dy").pack(side=tk.LEFT)
        tk.Entry(opt, textvariable=self.place_scale, width=5).pack(side=tk.LEFT, padx=2)
        tk.Entry(opt, textvariable=self.place_dx, width=5).pack(side=tk.LEFT, padx=2)
        tk.Entry(opt, textvariable=self.place_dy, width=5).pack(side=tk.LEFT, padx=(2, 10))
        ttk.Label(opt, text="IP category").pack(side=tk.LEFT)
        ttk.Combobox(
            opt, textvariable=self.category, width=10, state="readonly",
            values=["character", "object", "other"],
        ).pack(side=tk.LEFT, padx=(4, 10))
        ttk.Label(opt, text="IP scale/strength/frac").pack(side=tk.LEFT)
        tk.Entry(opt, textvariable=self.ip_scale, width=5).pack(side=tk.LEFT, padx=2)
        tk.Entry(opt, textvariable=self.stage2_strength, width=5).pack(side=tk.LEFT, padx=2)
        tk.Entry(opt, textvariable=self.ip_frac, width=5).pack(side=tk.LEFT, padx=(2, 8))
        ttk.Checkbutton(opt, text="IP diff", variable=self.ip_diff).pack(side=tk.LEFT, padx=4)
        ttk.Checkbutton(opt, text="literal", variable=self.literal_only).pack(side=tk.LEFT, padx=4)
        ttk.Checkbutton(opt, text="warp", variable=self.warp_correct).pack(side=tk.LEFT, padx=4)
        ttk.Checkbutton(opt, text="one stroke", variable=self.one_stroke).pack(side=tk.LEFT, padx=4)
        ttk.Checkbutton(opt, text="no split", variable=self.no_split).pack(side=tk.LEFT, padx=4)

        draw = ttk.LabelFrame(self.win, text="wall_gui2 参照パラメータ", padding=8)
        draw.pack(fill=tk.X, padx=8, pady=(0, 4))
        ttk.Label(draw, text="Max strokes").pack(side=tk.LEFT, padx=(0, 4))
        tk.Entry(draw, textvariable=self.max_strokes, width=6).pack(side=tk.LEFT)
        ttk.Label(draw, text="Travel").pack(side=tk.LEFT, padx=(12, 4))
        tk.Spinbox(draw, from_=1, to=100, textvariable=self.travel_speed, width=4).pack(side=tk.LEFT)
        ttk.Label(draw, text="Draw").pack(side=tk.LEFT, padx=(12, 4))
        tk.Spinbox(draw, from_=1, to=100, textvariable=self.draw_speed, width=4).pack(side=tk.LEFT)
        ttk.Label(draw, text="Inter-point sec").pack(side=tk.LEFT, padx=(12, 4))
        tk.Entry(draw, textvariable=self.inter_point_delay, width=6).pack(side=tk.LEFT)

        self.btn_enter = ttk.Button(draw, text="Enter 実行", command=self.on_enter)
        self.btn_enter.pack(side=tk.RIGHT, padx=4)

        body = ttk.PanedWindow(self.win, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        left = ttk.LabelFrame(body, text="USBカメラ映像", padding=6)
        body.add(left, weight=3)
        self.video = tk.Canvas(left, width=640, height=520, bg="#111", highlightthickness=0)
        self.video.pack(fill=tk.BOTH, expand=True)

        right = ttk.LabelFrame(body, text="実行ログ", padding=6)
        body.add(right, weight=2)
        self.status = ttk.Label(right, text="待機中", foreground="#555")
        self.status.pack(anchor=tk.W, pady=(0, 4))
        self.log_text = scrolledtext.ScrolledText(
            right, font=("Monaco", 9), wrap=tk.WORD, height=30)
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def _open_camera(self):
        if cv2 is None:
            self.log(f"cv2 import failed: {_CV2_IMPORT_ERROR}")
            return
        device = int(self.camera_device.get())
        self.cap = cv2.VideoCapture(device)
        if not self.cap.isOpened():
            self.log(f"カメラを開けません: device={device}")
            self.cap = None
            return
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        self.log(f"カメラ起動: device={device}")

    def _reopen_camera(self):
        self._close_camera()
        self._open_camera()

    def _close_camera(self):
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
        self.cap = None

    def _schedule_camera(self):
        if self.cap is not None:
            ok, frame = self.cap.read()
            if ok:
                self.last_frame = frame
                self._show_frame(rotate_camera_frame(frame))
        self.win.after(80, self._schedule_camera)

    def _show_frame(self, frame_bgr):
        if cv2 is None or Image is None or ImageTk is None:
            return
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        pil = Image.fromarray(rgb)
        cw = max(200, self.video.winfo_width())
        ch = max(200, self.video.winfo_height())
        iw, ih = pil.size
        scale = min(cw / iw, ch / ih)
        pil = pil.resize((max(1, int(iw * scale)), max(1, int(ih * scale))), Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(pil)
        self.video.delete("all")
        self.video.create_image(cw // 2, ch // 2, image=self._photo, anchor=tk.CENTER)

    def log(self, msg: str):
        ts = _dt.datetime.now().strftime("%H:%M:%S")
        self.log_q.put(f"[{ts}] {msg}")

    def _schedule_logs(self):
        try:
            while True:
                line = self.log_q.get_nowait()
                self.log_text.insert(tk.END, line + "\n")
                self.log_text.see(tk.END)
        except queue.Empty:
            pass
        self.win.after(100, self._schedule_logs)

    def set_status(self, text: str):
        self.win.after(0, lambda: self.status.config(text=text))

    def on_enter(self, event=None):
        if self.running:
            self.log("実行中です。完了まで待ってください。")
            return
        if self.last_frame is None:
            messagebox.showerror("撮影不可", "カメラ映像がまだ取得できていません。")
            return
        self.running = True
        self.btn_enter.config(state=tk.DISABLED)
        threading.Thread(target=self._run_pipeline, daemon=True).start()

    def _run_pipeline(self):
        run_ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = LOGS_DIR / f"integrated_run_{run_ts}"
        norm_dir = run_dir / "opencv_normalization"
        run_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.set_status("撮影・OpenCV正規化中")
            self.log("ENTER → 撮影")
            frame = self.last_frame.copy()
            self.log("OpenCV正規化: rotate / gray / CLAHE / denoise / bg normalize / adaptive threshold / morph")
            input_path = normalize_capture(frame, norm_dir)
            self.log(f"正規化入力: {input_path}")

            self.set_status("VLM → 画像生成 → ストローク化")
            cycle_dir = self._run_image_pipeline(input_path)
            strokes = cycle_dir / "strokes.json"
            if not strokes.exists():
                raise RuntimeError(f"strokes.json が見つかりません: {strokes}")
            self.log(f"ストローク化完了: {strokes}")

            self.set_status("ロボットアーム実行中")
            self._run_robot_draw(strokes)
            self.set_status("完了")
            self.log("完了: 撮影 → VLM → 画像生成 → ストローク化 → ロボットアーム実行")
        except Exception as e:
            self.set_status("エラー")
            self.log(f"ERROR: {e}")
            self.win.after(0, lambda: messagebox.showerror("実行エラー", str(e)))
        finally:
            self.running = False
            self.win.after(0, lambda: self.btn_enter.config(state=tk.NORMAL))

    def _run_image_pipeline(self, input_path: Path) -> Path:
        before = {str(p) for p in LOGS_DIR.glob("vlm_to_image_*/cycle_*")}
        cmd = [
            str(PYTHON), str(PIPELINE_SCRIPT),
            "--sketch", str(input_path),
            "--steps", str(int(self.steps.get())),
            "--cycles", "1",
            "--log-dir", str(LOGS_DIR),
            "--route", self.route.get(),
            "--design-mode", self.design_mode.get(),
            "--vstretch", self.vstretch.get(),
            "--min-feature", self.min_feature.get(),
            "--place-scale", self.place_scale.get(),
            "--place-dx-mm", self.place_dx.get(),
            "--place-dy-mm", self.place_dy.get(),
            "--flux-style", self.flux_style.get(),
            "--lora-str", self.lora_str.get(),
        ]
        seed = self.seed.get().strip()
        if seed:
            int(seed)
            cmd.extend(["--seed", seed])
        if self.route.get() == "ip_matsumoto":
            cmd.extend([
                "--category", self.category.get(),
                "--ip-scale", self.ip_scale.get(),
                "--stage2-strength", self.stage2_strength.get(),
                "--ip-frac", self.ip_frac.get(),
            ])
            if not self.ip_diff.get():
                cmd.append("--ip-no-diff")
        if self.literal_only.get():
            cmd.append("--literal-only")
        if self.warp_correct.get():
            cmd.append("--warp-correct")
        if self.one_stroke.get():
            cmd.append("--one-stroke")
        if self.no_split.get():
            cmd.append("--no-split")
        self.log("$ " + " ".join(cmd))
        self._run_subprocess(cmd, cwd=ROOT)

        after = sorted(
            [p for p in LOGS_DIR.glob("vlm_to_image_*/cycle_*") if str(p) not in before],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if after:
            return after[0]
        all_cycles = sorted(LOGS_DIR.glob("vlm_to_image_*/cycle_*"),
                            key=lambda p: p.stat().st_mtime, reverse=True)
        if not all_cycles:
            raise RuntimeError("生成パイプラインの出力 cycle_dir が見つかりません")
        return all_cycles[0]

    def _run_robot_draw(self, strokes_json: Path):
        cmd = [
            str(PYTHON), str(DRAW_SCRIPT), str(strokes_json),
            "--live",
            "--travel-speed", str(int(self.travel_speed.get())),
            "--draw-speed", str(int(self.draw_speed.get())),
            "--inter-point-delay", self.inter_point_delay.get(),
        ]
        max_s = self.max_strokes.get().strip()
        if max_s and max_s != "0":
            int(max_s)
            cmd.extend(["--max-strokes", max_s])
        self.log("$ " + " ".join(cmd))
        self._run_subprocess(cmd, cwd=PIPER_TEST)

    def _run_subprocess(self, cmd, cwd: Path):
        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            self.log(line.rstrip())
        rc = proc.wait()
        if rc != 0:
            raise RuntimeError(f"subprocess failed ({rc}): {' '.join(cmd)}")

    def lift(self):
        self.win.lift()
        self.win.focus_force()

    def close(self):
        if self.running and not messagebox.askyesno("実行中", "実行中です。閉じますか?"):
            return
        self._close_camera()
        self.win.destroy()


def main():
    root = tk.Tk()
    app = IntegratedApp(root)
    root.mainloop()
    return app


if __name__ == "__main__":
    main()
