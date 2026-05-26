#!/usr/bin/env python3
"""パイプラインテスト GUI

VLM → prompt → ImageGenerator → Vectorizer の一連を GUI から実行する
テスト用ツール。 2 つの入力モード:

  ① USB カメラ で撮影 (modules.camera.Camera 経由、 median 合成)
  ② ローカル画像ファイル を選択

出力 (生成画像 + strokes.json + topic_guess.json 等) は
~/draw_piper/logs/vlm_to_image_YYYYMMDD_HHMMSS/cycle_01/ に保存。

実行: ~/draw_piper/venv/bin/python ~/draw_piper/scripts/pipeline_test_gui.py
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 画像処理は重い (cv2 + PIL のみ最初に import、 VLM 等は subprocess に任せる)
try:
    import cv2
except Exception as e:
    print(f"WARNING: cv2 import failed: {e}", file=sys.stderr)
    cv2 = None
try:
    from PIL import Image, ImageTk
except Exception as e:
    print(f"WARNING: PIL import failed: {e}", file=sys.stderr)
    Image = None
    ImageTk = None


PIPELINE_SCRIPT = ROOT / "scripts" / "test_vlm_to_image.py"
LOGS_DIR = ROOT / "logs"


class PipelineTestGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("パイプライン テスト (VLM → ImageGen → Vectorizer)")
        self.root.geometry("960x820")

        # State
        self.selected_sketch_path: Path | None = None
        self.last_cycle_dir: Path | None = None
        self.camera = None  # modules.camera.Camera (lazy import)
        self.preview_imgtk = None  # 参照保持
        self.pipeline_thread: threading.Thread | None = None
        self.pipeline_proc: subprocess.Popen | None = None
        self.input_mode_var = tk.StringVar(value="file")
        self.var_camera_device = tk.IntVar(value=0)
        self.var_sdxl_steps = tk.IntVar(value=4)
        self.var_seed = tk.StringVar(value="")  # 空 = 自動

        self._build_ui()

    # ---------- UI ----------

    def _build_ui(self):
        # ステータスバー
        status_bar = ttk.LabelFrame(self.root, text="ステータス", padding=6)
        status_bar.pack(fill=tk.X, padx=6, pady=4)
        self.lbl_status = ttk.Label(status_bar, text="待機中",
                                     foreground="gray",
                                     font=("Monaco", 11))
        self.lbl_status.pack(side=tk.LEFT, padx=4)
        ttk.Button(status_bar, text="出力フォルダを開く",
            command=self.open_output_folder, width=20
        ).pack(side=tk.RIGHT, padx=4)
        ttk.Button(status_bar, text="ログフォルダを開く",
            command=self.open_logs_folder, width=20
        ).pack(side=tk.RIGHT, padx=4)

        # ① 入力ソース選択
        input_frame = ttk.LabelFrame(self.root,
            text="① 入力ソース", padding=8)
        input_frame.pack(fill=tk.X, padx=6, pady=4)
        # 入力モード ラジオ
        mode_row = ttk.Frame(input_frame)
        mode_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Radiobutton(mode_row, text="ローカルファイル",
            variable=self.input_mode_var, value="file",
            command=self._refresh_input_buttons
        ).pack(side=tk.LEFT, padx=4)
        ttk.Radiobutton(mode_row, text="USB カメラ",
            variable=self.input_mode_var, value="camera",
            command=self._refresh_input_buttons
        ).pack(side=tk.LEFT, padx=4)

        # ファイル選択
        self.file_frame = ttk.Frame(input_frame)
        self.file_frame.pack(fill=tk.X, pady=2)
        self.btn_file_select = ttk.Button(self.file_frame,
            text="ファイルを選択...",
            command=self.on_file_select, width=18)
        self.btn_file_select.pack(side=tk.LEFT, padx=2)
        self.lbl_file_path = ttk.Label(self.file_frame,
            text="(未選択)", font=("Monaco", 9), foreground="#777")
        self.lbl_file_path.pack(side=tk.LEFT, padx=8, fill=tk.X, expand=True)

        # カメラ設定
        self.camera_frame = ttk.Frame(input_frame)
        self.camera_frame.pack(fill=tk.X, pady=2)
        ttk.Label(self.camera_frame, text="デバイス ID:").pack(side=tk.LEFT,
                                                              padx=(0, 4))
        tk.Spinbox(self.camera_frame, from_=0, to=9, width=3,
            textvariable=self.var_camera_device
        ).pack(side=tk.LEFT, padx=(2, 10))
        self.btn_cam_open = ttk.Button(self.camera_frame,
            text="カメラ起動",
            command=self.on_camera_open, width=12)
        self.btn_cam_open.pack(side=tk.LEFT, padx=2)
        self.btn_cam_capture = ttk.Button(self.camera_frame,
            text="撮影",
            command=self.on_camera_capture, width=10)
        self.btn_cam_capture.pack(side=tk.LEFT, padx=2)
        self.btn_cam_close = ttk.Button(self.camera_frame,
            text="カメラ閉じる",
            command=self.on_camera_close, width=12)
        self.btn_cam_close.pack(side=tk.LEFT, padx=2)

        # プレビュー (Canvas)
        preview_frame = ttk.LabelFrame(self.root,
            text="プレビュー (入力画像)", padding=4)
        preview_frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)
        self.preview_canvas = tk.Canvas(preview_frame, bg="#222",
                                         width=640, height=360,
                                         highlightthickness=0)
        self.preview_canvas.pack(fill=tk.BOTH, expand=True)
        self.preview_canvas.create_text(320, 180,
            text="(入力画像が選択されたらここに表示)",
            fill="#888", font=("Monaco", 11))

        # ② パイプライン実行
        run_frame = ttk.LabelFrame(self.root,
            text="② パイプライン実行", padding=8)
        run_frame.pack(fill=tk.X, padx=6, pady=4)
        ttk.Label(run_frame, text="SDXL steps:").pack(side=tk.LEFT, padx=2)
        tk.Spinbox(run_frame, from_=1, to=30, width=4,
            textvariable=self.var_sdxl_steps
        ).pack(side=tk.LEFT, padx=2)
        ttk.Label(run_frame, text="Seed (空=自動):"
                  ).pack(side=tk.LEFT, padx=(8, 2))
        tk.Entry(run_frame, textvariable=self.var_seed, width=8
                 ).pack(side=tk.LEFT, padx=2)
        self.btn_run = ttk.Button(run_frame,
            text="▶ 実行 (VLM → ImageGen → Vectorizer)",
            command=self.on_run_pipeline, width=40)
        self.btn_run.pack(side=tk.LEFT, padx=(12, 4))
        self.btn_abort = ttk.Button(run_frame,
            text="中止",
            command=self.on_abort_pipeline, width=8,
            state=tk.DISABLED)
        self.btn_abort.pack(side=tk.LEFT, padx=2)

        # ③ ステージ プレビュー (パイプライン実行中に逐次更新)
        stage_frame = ttk.LabelFrame(self.root,
            text="③ ステージ プレビュー (実行中 逐次更新)", padding=6)
        stage_frame.pack(fill=tk.X, padx=6, pady=4)
        # cycle path 行
        cycle_row = ttk.Frame(stage_frame)
        cycle_row.pack(fill=tk.X, pady=(0, 4))
        self.lbl_last_cycle = ttk.Label(cycle_row,
            text="(まだ実行されていません)",
            font=("Monaco", 9), foreground="#555")
        self.lbl_last_cycle.pack(side=tk.LEFT, padx=4,
                                  fill=tk.X, expand=True)
        # 4 段 + 操作ボタン
        stages_grid = ttk.Frame(stage_frame)
        stages_grid.pack(fill=tk.X)
        # Stage 1: VLM 結果
        vlm_box = ttk.LabelFrame(stages_grid,
            text="① VLM 結果", padding=4)
        vlm_box.grid(row=0, column=0, sticky="nwe", padx=2)
        self.lbl_vlm = tk.Label(vlm_box,
            text="(未実行)", font=("Monaco", 9),
            justify=tk.LEFT, anchor="nw", width=24,
            wraplength=180, fg="#555")
        self.lbl_vlm.pack(fill=tk.BOTH, expand=True)
        # Stage 2: SDXL プロンプト
        pr_box = ttk.LabelFrame(stages_grid,
            text="② SDXL プロンプト", padding=4)
        pr_box.grid(row=0, column=1, sticky="nwe", padx=2)
        self.txt_prompt = tk.Text(pr_box, height=4, width=30,
            font=("Monaco", 9), wrap=tk.WORD,
            bg="#f8f8f8", fg="#333")
        self.txt_prompt.pack(fill=tk.BOTH, expand=True)
        self.txt_prompt.insert("1.0", "(未実行)")
        self.txt_prompt.config(state=tk.DISABLED)
        # Stage 3: 生成画像 thumb
        gen_box = ttk.LabelFrame(stages_grid,
            text="③ 生成画像", padding=4)
        gen_box.grid(row=0, column=2, sticky="nwe", padx=2)
        self.canvas_gen = tk.Canvas(gen_box, width=140, height=140,
            bg="#222", highlightthickness=0)
        self.canvas_gen.pack()
        self.canvas_gen.create_text(70, 70, text="(未実行)",
            fill="#888", font=("Monaco", 9))
        # Stage 4: Strokes
        st_box = ttk.LabelFrame(stages_grid,
            text="④ Strokes", padding=4)
        st_box.grid(row=0, column=3, sticky="nwe", padx=2)
        self.lbl_strokes_stat = tk.Label(st_box,
            text="(未実行)", font=("Monaco", 9),
            justify=tk.LEFT, anchor="nw", width=18,
            wraplength=140, fg="#555")
        self.lbl_strokes_stat.pack(fill=tk.BOTH, expand=True)
        # 各列を均等に
        for c in range(4):
            stages_grid.grid_columnconfigure(c, weight=1, uniform="stage")
        # 操作ボタン行
        btn_row = ttk.Frame(stage_frame)
        btn_row.pack(fill=tk.X, pady=(4, 0))
        self.btn_view_gen = ttk.Button(btn_row, text="生成画像を拡大",
            command=self.on_view_generated, width=18,
            state=tk.DISABLED)
        self.btn_view_gen.pack(side=tk.LEFT, padx=2)
        self.btn_view_strokes = ttk.Button(btn_row,
            text="strokes プレビュー (大)",
            command=self.on_view_strokes, width=22,
            state=tk.DISABLED)
        self.btn_view_strokes.pack(side=tk.LEFT, padx=2)
        self.btn_view_topic = ttk.Button(btn_row,
            text="VLM 結果 JSON",
            command=self.on_view_topic, width=18,
            state=tk.DISABLED)
        self.btn_view_topic.pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row,
            text="🔧 二値化キャリブ",
            command=self.on_binarize_calib, width=18
        ).pack(side=tk.RIGHT, padx=2)
        # ステージプレビュー画像参照保持
        self._stage_gen_imgtk = None
        # cycle dir 監視用
        self._stage_seen = set()

        # ログ
        log_frame = ttk.LabelFrame(self.root,
            text="ログ", padding=4)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)
        self.log_text = scrolledtext.ScrolledText(log_frame,
            font=("Monaco", 9), wrap=tk.WORD, height=8)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        self._refresh_input_buttons()
        self.log("パイプライン テスト GUI 準備完了")

    def _refresh_input_buttons(self):
        mode = self.input_mode_var.get()
        if mode == "file":
            # camera buttons disable
            self.btn_cam_open.config(state=tk.DISABLED)
            self.btn_cam_capture.config(state=tk.DISABLED)
            self.btn_cam_close.config(state=tk.DISABLED)
            self.btn_file_select.config(state=tk.NORMAL)
        else:
            self.btn_file_select.config(state=tk.DISABLED)
            cam_open = (self.camera is not None)
            self.btn_cam_open.config(
                state=tk.DISABLED if cam_open else tk.NORMAL)
            self.btn_cam_capture.config(
                state=tk.NORMAL if cam_open else tk.DISABLED)
            self.btn_cam_close.config(
                state=tk.NORMAL if cam_open else tk.DISABLED)

    # ---------- ファイル選択 ----------

    def on_file_select(self):
        path = filedialog.askopenfilename(
            title="入力スケッチ画像を選択",
            filetypes=[
                ("画像ファイル", "*.jpg *.jpeg *.png *.bmp"),
                ("All", "*.*"),
            ],
            initialdir=str(ROOT / "scripts"))
        if not path:
            return
        self.selected_sketch_path = Path(path)
        self.lbl_file_path.config(
            text=str(self.selected_sketch_path), foreground="black")
        self._show_preview_from_file(self.selected_sketch_path)
        self.log(f"ファイル選択: {self.selected_sketch_path}")

    def _show_preview_from_file(self, path: Path):
        if Image is None or ImageTk is None:
            self.log("PIL 未 import、 プレビュー不可")
            return
        try:
            img = Image.open(path).convert("RGB")
        except Exception as e:
            self.log(f"プレビュー読込失敗: {e}")
            return
        self._show_preview_pil(img)

    def _show_preview_pil(self, pil_img):
        """PIL.Image をプレビューキャンバスにフィット表示。"""
        if ImageTk is None:
            return
        # Canvas サイズに合わせて縮小
        cw = self.preview_canvas.winfo_width() or 640
        ch = self.preview_canvas.winfo_height() or 360
        iw, ih = pil_img.size
        scale = min(cw / iw, ch / ih, 1.0)
        nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
        resized = pil_img.resize((nw, nh), Image.LANCZOS)
        self.preview_imgtk = ImageTk.PhotoImage(resized)
        self.preview_canvas.delete("all")
        self.preview_canvas.create_image(cw // 2, ch // 2,
            image=self.preview_imgtk, anchor=tk.CENTER)

    # ---------- カメラ ----------

    def on_camera_open(self):
        if self.camera is not None:
            self.log("カメラは既に開いてます")
            return
        try:
            from modules.camera import Camera
        except Exception as e:
            messagebox.showerror("カメラ初期化失敗",
                f"modules.camera が import できません:\n{e}")
            return
        device = int(self.var_camera_device.get())
        try:
            self.camera = Camera(device_id=device, verbose=True)
            self.camera.open()
        except Exception as e:
            messagebox.showerror("カメラ起動失敗",
                f"デバイス {device} を開けません:\n{e}")
            self.camera = None
            return
        self.log(f"カメラ起動 (device={device})")
        self._refresh_input_buttons()
        # ライブプレビュー開始 (1 fps 程度)
        self._schedule_live_preview()

    def _schedule_live_preview(self):
        if self.camera is None:
            return
        try:
            frame_bgr = self.camera._read_one()  # 1 frame だけ取り出し
            if cv2 is not None:
                rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                pil = Image.fromarray(rgb) if Image else None
                if pil:
                    self._show_preview_pil(pil)
        except Exception as e:
            self.log(f"ライブプレビュー失敗 (続行): {e}")
        if self.camera is not None:
            self.root.after(500, self._schedule_live_preview)

    def on_camera_capture(self):
        if self.camera is None:
            return
        self.log("撮影中 ...")
        # 別スレッドで撮影
        threading.Thread(target=self._do_camera_capture,
                         daemon=True).start()

    def _do_camera_capture(self):
        # カメラがアーム先端搭載で撮影時は arm 停止のため、 旧 median
        # (動体除去) は不要。 single frame capture で十分。
        try:
            captured_bgr = self.camera.capture_single()
        except Exception as e:
            self.log(f"撮影失敗: {e}")
            return
        # 一時ファイル保存
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        capture_path = LOGS_DIR / f"camera_capture_{ts}.png"
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(capture_path), captured_bgr)
        self.selected_sketch_path = capture_path
        self.log(f"撮影完了 → {capture_path}")
        self.root.after(0, lambda:
            self.lbl_file_path.config(text=str(capture_path),
                                       foreground="black"))
        # プレビュー更新
        if cv2 is not None and Image is not None:
            rgb = cv2.cvtColor(captured_bgr, cv2.COLOR_BGR2RGB)
            pil = Image.fromarray(rgb)
            self.root.after(0, lambda: self._show_preview_pil(pil))

    def on_camera_close(self):
        if self.camera is None:
            return
        try:
            self.camera.close()
        except Exception as e:
            self.log(f"カメラ閉じる失敗 (続行): {e}")
        self.camera = None
        self.log("カメラ閉じました")
        self._refresh_input_buttons()

    # ---------- パイプライン実行 ----------

    def on_run_pipeline(self):
        if self.selected_sketch_path is None:
            messagebox.showerror("入力画像未指定",
                "ファイル選択 or カメラ撮影 で入力画像を確定してください。")
            return
        if not self.selected_sketch_path.exists():
            messagebox.showerror("ファイルなし",
                f"見つかりません:\n{self.selected_sketch_path}")
            return
        if self.pipeline_thread is not None \
                and self.pipeline_thread.is_alive():
            messagebox.showerror("実行中",
                "別のパイプラインが実行中です。")
            return
        steps = int(self.var_sdxl_steps.get())
        seed_str = self.var_seed.get().strip()
        seed_arg = []
        if seed_str:
            try:
                seed_int = int(seed_str)
                seed_arg = ["--seed", str(seed_int)]
            except Exception:
                messagebox.showerror("Seed 不正",
                    "Seed は整数または空 (自動) で指定してください。")
                return
        if not messagebox.askyesno("パイプライン実行確認",
                f"以下の設定でパイプラインを実行しますか?\n\n"
                f"  入力: {self.selected_sketch_path}\n"
                f"  SDXL steps: {steps}\n"
                f"  Seed: {seed_str or '自動'}\n\n"
                "VLM → ImageGen → Vectorizer の順で実行 "
                "(数分かかります)。"):
            return
        self.btn_run.config(state=tk.DISABLED)
        self.btn_abort.config(state=tk.NORMAL)
        self._set_status("実行中 (VLM 起動)...", "blue")
        # ステージプレビュー リセット
        self._stage_seen = set()
        self.lbl_vlm.config(text="(待機中...)", fg="#555")
        self.txt_prompt.config(state=tk.NORMAL)
        self.txt_prompt.delete("1.0", tk.END)
        self.txt_prompt.insert("1.0", "(待機中...)")
        self.txt_prompt.config(state=tk.DISABLED)
        self.canvas_gen.delete("all")
        self.canvas_gen.create_text(70, 70, text="(待機中...)",
            fill="#888", font=("Monaco", 9))
        self.lbl_strokes_stat.config(text="(待機中...)", fg="#555")
        self.btn_view_gen.config(state=tk.DISABLED)
        self.btn_view_strokes.config(state=tk.DISABLED)
        self.btn_view_topic.config(state=tk.DISABLED)
        self.pipeline_thread = threading.Thread(
            target=self._do_run_pipeline,
            args=(self.selected_sketch_path, steps, seed_arg),
            daemon=True)
        self.pipeline_thread.start()
        # cycle_dir 監視ループも起動
        self._stage_polling = True
        self.root.after(500, self._poll_stage_files)

    def _do_run_pipeline(self, sketch_path: Path, steps: int,
                         seed_arg: list[str]):
        python = sys.executable
        cmd = [
            python, str(PIPELINE_SCRIPT),
            "--sketch", str(sketch_path),
            "--steps", str(steps),
            "--cycles", "1",
            "--log-dir", str(LOGS_DIR),
        ] + seed_arg
        self.log(f"subprocess 起動: {' '.join(cmd)}")
        t0 = time.time()
        try:
            self.pipeline_proc = subprocess.Popen(
                cmd,
                cwd=str(ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1)
            assert self.pipeline_proc.stdout is not None
            for line in self.pipeline_proc.stdout:
                line = line.rstrip()
                if not line:
                    continue
                self.log(line)
                # フェーズ検出
                low = line.lower()
                if "vlm load" in low:
                    self.root.after(0, lambda:
                        self._set_status("VLM 起動中...", "blue"))
                elif "vlm predict" in low or "predict_intent" in low:
                    self.root.after(0, lambda:
                        self._set_status("VLM 推論中...", "blue"))
                elif "imagegen" in low or "sdxl" in low:
                    self.root.after(0, lambda:
                        self._set_status("画像生成中 (SDXL)...", "blue"))
                elif "vector" in low:
                    self.root.after(0, lambda:
                        self._set_status("ベクトル化中...", "blue"))
            rc = self.pipeline_proc.wait()
        except Exception as e:
            self.log(f"subprocess 失敗: {e}")
            self.root.after(0, lambda:
                self._set_status(f"失敗: {e}", "red"))
            self.root.after(0, lambda:
                self.btn_run.config(state=tk.NORMAL))
            self.root.after(0, lambda:
                self.btn_abort.config(state=tk.DISABLED))
            return
        elapsed = time.time() - t0
        self.log(f"パイプライン終了 (rc={rc}, 経過 {elapsed:.1f}s)")
        # 最新の vlm_to_image_*/cycle_01 を検索
        latest = self._find_latest_cycle()
        self.last_cycle_dir = latest
        if latest:
            self.log(f"出力先: {latest}")
            self.root.after(0, lambda d=latest:
                self.lbl_last_cycle.config(
                    text=str(d), foreground="black"))
            self.root.after(0, lambda:
                self.btn_view_gen.config(state=tk.NORMAL))
            self.root.after(0, lambda:
                self.btn_view_strokes.config(state=tk.NORMAL))
            self.root.after(0, lambda:
                self.btn_view_topic.config(state=tk.NORMAL))
        if rc == 0:
            self.root.after(0, lambda:
                self._set_status(
                    f"完了 ({elapsed:.0f}s)", "green"))
        else:
            self.root.after(0, lambda:
                self._set_status(
                    f"失敗 rc={rc}", "red"))
        self.root.after(0, lambda:
            self.btn_run.config(state=tk.NORMAL))
        self.root.after(0, lambda:
            self.btn_abort.config(state=tk.DISABLED))
        # ステージ監視停止 (もう一度 cycle_dir を一括チェックして取り残し
        # 防止 → その後 stop)
        self._stage_polling = False
        self.root.after(0, lambda: self._final_stage_sweep())
        self.pipeline_proc = None

    def _final_stage_sweep(self):
        """パイプライン終了直後に未取得のステージファイルを最後に拾う。"""
        latest = self._find_latest_cycle()
        if latest is None:
            return
        for name, updater in [
            ("topic_guess", lambda: self._update_stage_vlm(latest / "topic_guess.json")),
            ("prompt", lambda: self._update_stage_prompt(latest / "prompt.txt")),
            ("generated", lambda: self._update_stage_generated(latest / "generated.png")),
            ("strokes", lambda: self._update_stage_strokes(latest / "strokes.json")),
        ]:
            if name in self._stage_seen:
                continue
            f = latest / {
                "topic_guess": "topic_guess.json",
                "prompt": "prompt.txt",
                "generated": "generated.png",
                "strokes": "strokes.json",
            }[name]
            if f.exists():
                self._stage_seen.add(name)
                updater()

    def _poll_stage_files(self):
        """パイプライン実行中に最新の cycle_dir を監視して、 各ステージの
        出力ファイル (topic_guess.json / prompt.txt / generated.png /
        strokes.json) が生成されたら UI を更新する。
        """
        if not getattr(self, "_stage_polling", False):
            return
        # 最新の cycle_dir を探す (パイプライン起動直後はまだ無い)
        latest = self._find_latest_cycle()
        if latest is not None:
            # 表示更新
            try:
                if str(latest) != self.lbl_last_cycle.cget("text"):
                    self.lbl_last_cycle.config(
                        text=str(latest), foreground="black")
            except Exception:
                pass
            # 各ファイル
            tg = latest / "topic_guess.json"
            if "topic_guess" not in self._stage_seen and tg.exists():
                self._stage_seen.add("topic_guess")
                self._update_stage_vlm(tg)
            pr = latest / "prompt.txt"
            if "prompt" not in self._stage_seen and pr.exists():
                self._stage_seen.add("prompt")
                self._update_stage_prompt(pr)
            gen = latest / "generated.png"
            if "generated" not in self._stage_seen and gen.exists():
                self._stage_seen.add("generated")
                self._update_stage_generated(gen)
            sj = latest / "strokes.json"
            if "strokes" not in self._stage_seen and sj.exists():
                self._stage_seen.add("strokes")
                self._update_stage_strokes(sj)
        if self._stage_polling:
            self.root.after(500, self._poll_stage_files)

    def _update_stage_vlm(self, topic_path: Path):
        try:
            d = json.loads(topic_path.read_text(encoding="utf-8"))
        except Exception as e:
            self.lbl_vlm.config(text=f"読込失敗:\n{e}", fg="red")
            return
        sub = d.get("subject") or {}
        loc = d.get("location") or {}
        act = d.get("action") or {}
        conf = d.get("confidence", 0.0)
        txt = (f"subject: {sub.get('ja', '?')} ({sub.get('en', '?')})\n"
                f"location: {loc.get('ja', '?')}\n"
                f"action: {act.get('ja', '?')}\n"
                f"confidence: {conf:.2f}")
        self.lbl_vlm.config(text=txt, fg="black")
        self.btn_view_topic.config(state=tk.NORMAL)
        self._set_status(f"VLM 完了 → {sub.get('ja', '?')}", "blue")

    def _update_stage_prompt(self, prompt_path: Path):
        try:
            text = prompt_path.read_text(encoding="utf-8")
        except Exception as e:
            text = f"読込失敗: {e}"
        self.txt_prompt.config(state=tk.NORMAL)
        self.txt_prompt.delete("1.0", tk.END)
        self.txt_prompt.insert("1.0", text)
        self.txt_prompt.config(state=tk.DISABLED)

    def _update_stage_generated(self, gen_path: Path):
        if Image is None or ImageTk is None:
            return
        try:
            img = Image.open(gen_path).convert("RGB")
        except Exception as e:
            return
        # 140x140 サムネイル
        img.thumbnail((140, 140), Image.LANCZOS)
        self._stage_gen_imgtk = ImageTk.PhotoImage(img)
        self.canvas_gen.delete("all")
        self.canvas_gen.create_image(70, 70,
            image=self._stage_gen_imgtk, anchor=tk.CENTER)
        self.btn_view_gen.config(state=tk.NORMAL)

    def _update_stage_strokes(self, strokes_path: Path):
        try:
            d = json.loads(strokes_path.read_text(encoding="utf-8"))
        except Exception as e:
            self.lbl_strokes_stat.config(text=f"読込失敗:\n{e}",
                                          fg="red")
            return
        strokes = d.get("strokes") or []
        n_pts = sum(len(s) for s in strokes)
        meta = d.get("meta") or {}
        coord = meta.get("coordinate_system", "px")
        txt = (f"{len(strokes)} 本\n"
                f"{n_pts} 点\n"
                f"coord: {coord}")
        self.lbl_strokes_stat.config(text=txt, fg="black")
        self.btn_view_strokes.config(state=tk.NORMAL)

    def on_abort_pipeline(self):
        if self.pipeline_proc is None:
            return
        try:
            self.pipeline_proc.terminate()
            self.log("⛔ パイプライン中止リクエスト")
        except Exception as e:
            self.log(f"中止失敗: {e}")

    def _find_latest_cycle(self) -> Path | None:
        candidates = sorted(LOGS_DIR.glob("vlm_to_image_*/cycle_*"))
        return candidates[-1] if candidates else None

    # ---------- 結果表示 ----------

    def on_view_generated(self):
        if not self.last_cycle_dir:
            return
        path = self.last_cycle_dir / "generated.png"
        if not path.exists():
            messagebox.showerror("ファイルなし", f"{path}")
            return
        self._show_image_popup(path, "生成画像")

    def on_view_strokes(self):
        if not self.last_cycle_dir:
            return
        path = self.last_cycle_dir / "strokes.json"
        if not path.exists():
            messagebox.showerror("ファイルなし", f"{path}")
            return
        self._show_strokes_popup(path)

    def on_view_topic(self):
        if not self.last_cycle_dir:
            return
        path = self.last_cycle_dir / "topic_guess.json"
        if not path.exists():
            messagebox.showerror("ファイルなし", f"{path}")
            return
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as e:
            messagebox.showerror("読込失敗", f"{e}")
            return
        win = tk.Toplevel(self.root)
        win.title("VLM topic_guess.json")
        win.geometry("640x480")
        st = scrolledtext.ScrolledText(win, font=("Monaco", 10), wrap=tk.WORD)
        st.pack(fill=tk.BOTH, expand=True)
        st.insert("1.0", text)
        st.config(state=tk.DISABLED)

    def _show_image_popup(self, path: Path, title: str):
        if Image is None or ImageTk is None:
            messagebox.showerror("PIL 未 import", "プレビュー不可")
            return
        win = tk.Toplevel(self.root)
        win.title(title)
        try:
            img = Image.open(path).convert("RGB")
        except Exception as e:
            messagebox.showerror("読込失敗", f"{e}")
            return
        # フィット
        max_dim = 800
        iw, ih = img.size
        scale = min(max_dim / iw, max_dim / ih, 1.0)
        nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
        resized = img.resize((nw, nh), Image.LANCZOS)
        photo = ImageTk.PhotoImage(resized)
        lbl = tk.Label(win, image=photo)
        lbl.image = photo  # 参照保持
        lbl.pack(padx=8, pady=8)
        ttk.Label(win, text=str(path), font=("Monaco", 9),
                  foreground="#555").pack(pady=(0, 4))

    def _show_strokes_popup(self, path: Path):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            messagebox.showerror("読込失敗", f"{e}")
            return
        strokes = data.get("strokes") or []
        meta = data.get("meta") or {}
        if not strokes:
            messagebox.showerror("strokes なし", "ストロークがありません。")
            return
        win = tk.Toplevel(self.root)
        win.title(f"strokes プレビュー ({len(strokes)} 本)")
        # キャンバスサイズ
        is_panel_uv = meta.get("coordinate_system") == "panel_uv_mm"
        if is_panel_uv:
            psize = (meta.get("panel_size_mm")
                      or data.get("panel_size_mm") or [200.0, 200.0])
            w_units, h_units = float(psize[0]), float(psize[1])
        else:
            ish = data.get("image_shape") or [1024, 1024]
            h_units, w_units = float(ish[0]), float(ish[1])
        max_dim = 600.0
        scale = max_dim / max(w_units, h_units)
        cw = int(w_units * scale)
        ch = int(h_units * scale)
        c = tk.Canvas(win, width=cw, height=ch, bg="white")
        c.pack(padx=8, pady=8)
        for stroke in strokes:
            if len(stroke) < 2:
                continue
            flat = []
            for pt in stroke:
                x = pt[0] * scale
                y = pt[1] * scale
                if is_panel_uv:
                    # panel uv は v が上向き、 Canvas は y が下向きなので反転
                    y = ch - y
                flat.extend([x, y])
            c.create_line(*flat, fill="black", width=1)
            x0, y0 = stroke[0]
            xL, yL = stroke[-1]
            if is_panel_uv:
                y0_c = ch - y0 * scale
                yL_c = ch - yL * scale
            else:
                y0_c = y0 * scale
                yL_c = yL * scale
            r = 2
            c.create_oval(x0 * scale - r, y0_c - r,
                          x0 * scale + r, y0_c + r,
                          fill="green", outline="green")
            c.create_oval(xL * scale - r, yL_c - r,
                          xL * scale + r, yL_c + r,
                          fill="red", outline="red")
        info = (f"{w_units:.0f}×{h_units:.0f} "
                + ("mm (panel_uv)" if is_panel_uv else "px") +
                f"、 {len(strokes)} ストローク。 緑=始点 赤=終点")
        ttk.Label(win, text=info, font=("Monaco", 9)).pack(pady=(0, 6))

    # ---------- ユーティリティ ----------

    def on_binarize_calib(self):
        """ユーザ画像を選んで OTSU / Adaptive / Fixed をリアルタイム比較。
        確定時に calibration/vectorizer_config.yaml に保存。
        """
        # 起点となる画像: 直前 cycle の 00_user_input.png か、 ファイル選択
        default_img = None
        if self.last_cycle_dir:
            cand = self.last_cycle_dir / "vec_debug" / "00_user_input.png"
            if cand.exists():
                default_img = cand
        if default_img is None and self.selected_sketch_path:
            default_img = self.selected_sketch_path
        if default_img is None:
            messagebox.showerror("画像なし",
                "二値化キャリブ用の画像がありません。 \n"
                "・ パイプライン実行後に試す (00_user_input.png 使用)\n"
                "・ または ① でファイル選択/撮影")
            return
        BinarizeCalibWindow(self, default_img)

    def open_output_folder(self):
        target = self.last_cycle_dir or LOGS_DIR
        try:
            subprocess.Popen(["xdg-open", str(target)])
        except Exception as e:
            messagebox.showerror("失敗",
                f"フォルダを開けませんでした:\n{e}\n\n{target}")

    def open_logs_folder(self):
        try:
            subprocess.Popen(["xdg-open", str(LOGS_DIR)])
        except Exception as e:
            messagebox.showerror("失敗",
                f"フォルダを開けませんでした:\n{e}\n\n{LOGS_DIR}")

    def _set_status(self, text, color="black"):
        self.lbl_status.config(text=text, foreground=color)

    def log(self, msg):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}\n"
        try:
            self.log_text.insert(tk.END, line)
            self.log_text.see(tk.END)
        except Exception:
            print(line, end="")


class BinarizeCalibWindow:
    """二値化パラメータをライブプレビューしながらキャリブする Toplevel。

    左に元画像、 右に二値化結果。 method ラジオ + slider で調整。
    「保存」 で calibration/vectorizer_config.yaml に書き出し。
    """

    def __init__(self, parent_gui: "PipelineTestGUI", image_path: Path):
        self.parent = parent_gui
        self.image_path = image_path
        # PIL / cv2 import
        from modules.vectorizer import (
            load_binarize_config, save_binarize_config, _binarize_user)
        import numpy as np
        self._binarize_user = _binarize_user
        self._save_cfg = save_binarize_config
        self._np = np
        cfg = load_binarize_config()
        # 画像読み込み (グレースケール)
        if cv2 is None:
            messagebox.showerror("cv2 不要", "cv2 が import できません")
            return
        gray = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
        if gray is None:
            messagebox.showerror("読込失敗", f"画像読込失敗:\n{image_path}")
            return
        self.gray = gray
        # Tk vars
        self.var_method = tk.StringVar(value=cfg["binarize_method"])
        self.var_block = tk.IntVar(value=cfg["adaptive_block_size"])
        self.var_c = tk.IntVar(value=cfg["adaptive_c"])
        self.var_fixed = tk.IntVar(value=cfg["fixed_threshold"])
        # filter 系 (Vectorizer の細部保持/削除に直結)
        self.var_min_pixels = tk.IntVar(value=cfg["min_pixels"])
        self.var_min_length = tk.IntVar(value=cfg["min_length"])
        self.var_epsilon = tk.DoubleVar(value=cfg["approx_epsilon"])
        # Window
        self.win = tk.Toplevel(parent_gui.root)
        self.win.title("二値化キャリブ")
        self.win.geometry("1100x600")
        self._build_ui()
        self._update_preview()

    def _build_ui(self):
        # 上部: 元画像 + 二値化結果 (2 並び)
        top = ttk.Frame(self.win)
        top.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        orig_box = ttk.LabelFrame(top, text="元画像 (グレースケール)",
                                    padding=4)
        orig_box.grid(row=0, column=0, sticky="nwes", padx=4)
        self.canvas_orig = tk.Canvas(orig_box, width=480, height=480,
                                       bg="#222", highlightthickness=0)
        self.canvas_orig.pack()
        bin_box = ttk.LabelFrame(top, text="二値化結果 (黒線=ink)",
                                   padding=4)
        bin_box.grid(row=0, column=1, sticky="nwes", padx=4)
        self.canvas_bin = tk.Canvas(bin_box, width=480, height=480,
                                      bg="#222", highlightthickness=0)
        self.canvas_bin.pack()
        top.grid_columnconfigure(0, weight=1, uniform="col")
        top.grid_columnconfigure(1, weight=1, uniform="col")

        # 中段: method ラジオ + パラメータ
        ctrl = ttk.LabelFrame(self.win, text="パラメータ", padding=8)
        ctrl.pack(fill=tk.X, padx=8, pady=(0, 8))
        # method
        m_row = ttk.Frame(ctrl)
        m_row.pack(fill=tk.X)
        ttk.Label(m_row, text="手法:",
                  font=("Monaco", 10, "bold")
                  ).pack(side=tk.LEFT, padx=4)
        for v, txt in [
            ("adaptive", "Adaptive (局所、 推奨)"),
            ("otsu", "Otsu (全体 1 閾値、 旧版互換)"),
            ("fixed", "Fixed (絶対値指定)"),
        ]:
            ttk.Radiobutton(m_row, text=txt, value=v,
                variable=self.var_method,
                command=self._update_preview
            ).pack(side=tk.LEFT, padx=8)
        # adaptive params
        ap_row = ttk.Frame(ctrl)
        ap_row.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(ap_row, text="ブロックサイズ (奇数):"
                  ).pack(side=tk.LEFT, padx=4)
        tk.Scale(ap_row, from_=3, to=151, orient=tk.HORIZONTAL,
            variable=self.var_block, length=200, resolution=2,
            command=lambda _v: self._update_preview()
        ).pack(side=tk.LEFT, padx=4)
        ttk.Label(ap_row, text="  オフセット C:"
                  ).pack(side=tk.LEFT, padx=(12, 4))
        tk.Scale(ap_row, from_=-20, to=40, orient=tk.HORIZONTAL,
            variable=self.var_c, length=200,
            command=lambda _v: self._update_preview()
        ).pack(side=tk.LEFT, padx=4)
        # fixed param
        fx_row = ttk.Frame(ctrl)
        fx_row.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(fx_row, text="Fixed 閾値:"
                  ).pack(side=tk.LEFT, padx=4)
        tk.Scale(fx_row, from_=0, to=255, orient=tk.HORIZONTAL,
            variable=self.var_fixed, length=300,
            command=lambda _v: self._update_preview()
        ).pack(side=tk.LEFT, padx=4)
        # Filter 系 (ベクトル化での細部削除に直結。 数値小 = 細部残る)
        ttk.Separator(ctrl, orient=tk.HORIZONTAL).pack(
            fill=tk.X, pady=(10, 4))
        ttk.Label(ctrl,
            text="Filter (Vectorize 後段、 小さくすると細部が残る、 "
                 "大きくするとストローク本数減って描画速い):",
            font=("Monaco", 9, "bold")).pack(anchor=tk.W, padx=4)
        f_row = ttk.Frame(ctrl)
        f_row.pack(fill=tk.X, pady=(2, 0))
        ttk.Label(f_row, text="min_pixels (連結成分 最小 px):"
                  ).pack(side=tk.LEFT, padx=4)
        tk.Scale(f_row, from_=5, to=100, orient=tk.HORIZONTAL,
            variable=self.var_min_pixels, length=160
        ).pack(side=tk.LEFT, padx=4)
        ttk.Label(f_row, text="  min_length (ポリライン 最短 点数):"
                  ).pack(side=tk.LEFT, padx=(8, 4))
        tk.Scale(f_row, from_=2, to=30, orient=tk.HORIZONTAL,
            variable=self.var_min_length, length=160
        ).pack(side=tk.LEFT, padx=4)
        ttk.Label(f_row, text="  approx_epsilon (折線簡略化):"
                  ).pack(side=tk.LEFT, padx=(8, 4))
        tk.Scale(f_row, from_=0.5, to=5.0, orient=tk.HORIZONTAL,
            variable=self.var_epsilon, length=140, resolution=0.1
        ).pack(side=tk.LEFT, padx=4)

        # 下段: 状態 + 保存ボタン
        st_row = ttk.Frame(self.win)
        st_row.pack(fill=tk.X, padx=8, pady=(0, 8))
        self.lbl_state = ttk.Label(st_row,
            text="(プレビュー反映待ち)",
            font=("Monaco", 9), foreground="#555")
        self.lbl_state.pack(side=tk.LEFT, padx=4)
        ttk.Button(st_row, text="別の画像を選択",
            command=self._reselect_image, width=18
        ).pack(side=tk.RIGHT, padx=2)
        ttk.Button(st_row, text="❌ キャンセル",
            command=self.win.destroy, width=14
        ).pack(side=tk.RIGHT, padx=2)
        ttk.Button(st_row, text="💾 yaml に保存",
            command=self._save, width=18
        ).pack(side=tk.RIGHT, padx=2)

        # 画像参照保持
        self._tk_orig = None
        self._tk_bin = None

    def _reselect_image(self):
        path = filedialog.askopenfilename(
            title="二値化キャリブ用画像を選択",
            filetypes=[("画像", "*.png *.jpg *.jpeg"), ("All", "*.*")],
            initialdir=str(self.image_path.parent) if self.image_path
                         else str(LOGS_DIR))
        if not path:
            return
        gray = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if gray is None:
            messagebox.showerror("読込失敗", path)
            return
        self.image_path = Path(path)
        self.gray = gray
        self._update_preview()

    def _update_preview(self):
        if Image is None or ImageTk is None:
            return
        # 元画像
        pil_orig = Image.fromarray(self.gray)
        self._tk_orig = self._fit_canvas_image(pil_orig, self.canvas_orig)
        # 二値化結果
        method = self.var_method.get()
        block = int(self.var_block.get())
        c = int(self.var_c.get())
        fixed = int(self.var_fixed.get())
        try:
            mask = self._binarize_user(
                self.gray, method=method,
                adaptive_block_size=block, adaptive_c=c,
                fixed_threshold=fixed)
        except Exception as e:
            self.lbl_state.config(text=f"binarize 失敗: {e}",
                                   foreground="red")
            return
        # 白地黒線で表示 (mask は ink=255 なので反転)
        bin_disp = 255 - mask
        pil_bin = Image.fromarray(bin_disp)
        self._tk_bin = self._fit_canvas_image(pil_bin, self.canvas_bin)
        # 状態 (ink ピクセル率)
        ink_ratio = float((mask == 255).mean())
        self.lbl_state.config(
            text=(f"method={method}  block={block}  c={c}  fixed={fixed}"
                   f"  ink 率={ink_ratio:.1%}  画像={self.image_path.name}"),
            foreground="black")

    def _fit_canvas_image(self, pil_img, canvas):
        cw = canvas.winfo_width() or 480
        ch = canvas.winfo_height() or 480
        iw, ih = pil_img.size
        scale = min(cw / iw, ch / ih)
        nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
        resized = pil_img.resize((nw, nh), Image.LANCZOS)
        photo = ImageTk.PhotoImage(resized)
        canvas.delete("all")
        canvas.create_image(cw // 2, ch // 2,
            image=photo, anchor=tk.CENTER)
        return photo

    def _save(self):
        method = self.var_method.get()
        block = int(self.var_block.get())
        c = int(self.var_c.get())
        fixed = int(self.var_fixed.get())
        min_pix = int(self.var_min_pixels.get())
        min_len = int(self.var_min_length.get())
        eps = float(self.var_epsilon.get())
        try:
            saved_path = self._save_cfg(
                method=method,
                adaptive_block_size=block,
                adaptive_c=c,
                fixed_threshold=fixed,
                min_pixels=min_pix,
                min_length=min_len,
                approx_epsilon=eps)
        except Exception as e:
            messagebox.showerror("保存失敗", str(e))
            return
        messagebox.showinfo("保存完了",
            f"{saved_path} に保存しました。\n\n"
            f"binarize: method={method} block={block} c={c}\n"
            f"filter: min_pixels={min_pix} min_length={min_len} "
            f"epsilon={eps}\n\n"
            "次回パイプライン実行時から反映されます。")
        self.parent.log(
            f"vectorizer_config.yaml 保存: method={method} "
            f"block={block} c={c} | min_pixels={min_pix} "
            f"min_length={min_len} eps={eps}")
        self.win.destroy()


def main():
    parser = argparse.ArgumentParser(
        description="VLM → ImageGen → Vectorizer パイプライン GUI テスト")
    parser.parse_args()
    root = tk.Tk()
    gui = PipelineTestGUI(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
