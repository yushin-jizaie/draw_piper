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
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

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


# 2026-06-03: 最新ルート(M19: FLUX+winners LoRA+VLM完成形+CN0.2+manga+OpenCV線抽出
# +複数被写体 分割/合成+中心→外側描画順) のバックエンドに差し替え。
# 旧 SDXL パイプラインに戻すなら scripts/test_vlm_to_image.py を指す。
PIPELINE_SCRIPT = ROOT / "scripts" / "gen_latest_route.py"
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
        # 旧 preview_imgtk は撤廃。 画像参照は _img_input/_img_gen 等で保持
        self.pipeline_thread: threading.Thread | None = None
        self.pipeline_proc: subprocess.Popen | None = None
        self.input_mode_var = tk.StringVar(value="file")
        self.var_camera_device = tk.IntVar(value=0)
        self.var_sdxl_steps = tk.IntVar(value=4)
        self.var_seed = tk.StringVar(value="")  # 空 = 自動
        # 縦伸ばし比率: ロボット側の縦潰れ/横伸びの応急補正。 生成画像を縦に
        # この倍率で引き伸ばしてからストローク化する (1.0 = 補正なし)。
        self.var_vstretch = tk.StringVar(value="1.0")
        # ワープ補正(生成側): アーム側のワープ補正が効かないので、 生成後の
        # ストロークに draw_warp_correction の affine を事前適用する。
        self.var_warp_correct = tk.BooleanVar(value=False)
        # 一筆書き: 全ストロークを 1 本に連結 (ペンを上げない連続描画)。
        self.var_one_stroke = tk.BooleanVar(value=False)
        # literal-only: カード推論をやめ「何に見えるか」 を生成 prompt に使い、
        # vectorize も full 抽出 (diff しない) でテストする。
        self.var_literal_only = tk.BooleanVar(value=False)
        # 透明ボード線抽出 (背景差分 + 色フィルタ) 用の state
        self.background_bgr = None          # 空ボード基準フレーム (np.ndarray BGR)
        self.var_line_mode = tk.StringVar(value="dark")   # dark/black/blue/red/green
        self.var_line_diff = tk.IntVar(value=30)          # 背景差分 閾値
        self.var_line_dark_v = tk.IntVar(value=90)        # 暗い線の V 上限

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

        # 透明ボード線抽出 (背景差分 + 特定色) — カメラモード時のみ意味あり
        self.lineext_frame = ttk.LabelFrame(input_frame,
            text="透明ボード線抽出 (背景差分 + 特定色)", padding=6)
        self.lineext_frame.pack(fill=tk.X, pady=(6, 2))
        row1 = ttk.Frame(self.lineext_frame); row1.pack(fill=tk.X)
        self.btn_bg_capture = ttk.Button(row1,
            text="背景キャプチャ (空ボード)",
            command=self.on_capture_background, width=24)
        self.btn_bg_capture.pack(side=tk.LEFT, padx=2)
        self.lbl_bg_status = ttk.Label(row1, text="背景: 未取得",
            font=("Monaco", 9), foreground="#a33")
        self.lbl_bg_status.pack(side=tk.LEFT, padx=8)
        row2 = ttk.Frame(self.lineext_frame); row2.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(row2, text="線の色:").pack(side=tk.LEFT, padx=(0, 2))
        ttk.Combobox(row2, textvariable=self.var_line_mode, width=7,
            state="readonly",
            values=["dark", "black", "blue", "red", "green"]
        ).pack(side=tk.LEFT, padx=2)
        ttk.Label(row2, text="差分閾値:").pack(side=tk.LEFT, padx=(8, 2))
        tk.Spinbox(row2, from_=5, to=120, width=4,
            textvariable=self.var_line_diff).pack(side=tk.LEFT, padx=2)
        ttk.Label(row2, text="暗線V上限:").pack(side=tk.LEFT, padx=(8, 2))
        tk.Spinbox(row2, from_=30, to=200, width=4,
            textvariable=self.var_line_dark_v).pack(side=tk.LEFT, padx=2)
        self.btn_extract_lines = ttk.Button(row2,
            text="線抽出 → 入力に設定",
            command=self.on_extract_lines, width=20)
        self.btn_extract_lines.pack(side=tk.LEFT, padx=(10, 2))

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
        ttk.Checkbutton(
            run_frame, text="literal (カード推論なし)",
            variable=self.var_literal_only,
        ).pack(side=tk.LEFT, padx=(8, 2))
        ttk.Label(run_frame, text="縦伸ばし比率:"
                  ).pack(side=tk.LEFT, padx=(8, 2))
        tk.Spinbox(run_frame, from_=0.5, to=2.5, increment=0.05, width=5,
            format="%.2f", textvariable=self.var_vstretch
        ).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(
            run_frame, text="ワープ補正(生成側)",
            variable=self.var_warp_correct,
        ).pack(side=tk.LEFT, padx=(8, 2))
        ttk.Checkbutton(
            run_frame, text="一筆書き",
            variable=self.var_one_stroke,
        ).pack(side=tk.LEFT, padx=(8, 2))
        self.btn_run = ttk.Button(run_frame,
            text="▶ 実行 (VLM → ImageGen → Vectorizer)",
            command=self.on_run_pipeline, width=40)
        self.btn_run.pack(side=tk.LEFT, padx=(12, 4))
        self.btn_abort = ttk.Button(run_frame,
            text="■ 生成キャンセル",
            command=self.on_abort_pipeline, width=14,
            state=tk.DISABLED)
        self.btn_abort.pack(side=tk.LEFT, padx=2)

        # ③ 4 画像プレビュー (横並び、 コンパクト)
        preview_frame = ttk.LabelFrame(self.root,
            text="③ プレビュー (左から: 元画像 / 生成画像 / "
                 "UserBinary / Strokes)", padding=4)
        preview_frame.pack(fill=tk.X, padx=6, pady=4)
        # cycle path 表示
        self.lbl_last_cycle = ttk.Label(preview_frame,
            text="(まだ実行されていません)",
            font=("Monaco", 8), foreground="#555")
        self.lbl_last_cycle.pack(anchor=tk.W, padx=4)
        # 4 画像 grid
        imgs_grid = ttk.Frame(preview_frame)
        imgs_grid.pack(fill=tk.X, pady=2)
        THUMB = 170  # 各サムネイルの一辺 (px)
        # 1. 元画像
        in_box = ttk.LabelFrame(imgs_grid, text="元画像", padding=2)
        in_box.grid(row=0, column=0, sticky="nwe", padx=2)
        self.canvas_input = tk.Canvas(in_box, width=THUMB, height=THUMB,
            bg="#222", highlightthickness=0)
        self.canvas_input.pack()
        self.canvas_input.create_text(THUMB // 2, THUMB // 2,
            text="(未選択)", fill="#888", font=("Monaco", 9))
        # 2. 生成画像
        gen_box = ttk.LabelFrame(imgs_grid, text="生成画像", padding=2)
        gen_box.grid(row=0, column=1, sticky="nwe", padx=2)
        self.canvas_gen = tk.Canvas(gen_box, width=THUMB, height=THUMB,
            bg="#222", highlightthickness=0)
        self.canvas_gen.pack()
        self.canvas_gen.create_text(THUMB // 2, THUMB // 2,
            text="(未実行)", fill="#888", font=("Monaco", 9))
        # 3. UserBinary
        ub_box = ttk.LabelFrame(imgs_grid, text="UserBinary",
                                 padding=2)
        ub_box.grid(row=0, column=2, sticky="nwe", padx=2)
        self.canvas_userbin = tk.Canvas(ub_box, width=THUMB, height=THUMB,
            bg="#222", highlightthickness=0)
        self.canvas_userbin.pack()
        self.canvas_userbin.create_text(THUMB // 2, THUMB // 2,
            text="(未実行)", fill="#888", font=("Monaco", 9))
        # 4. Strokes (06_strokes.png)
        st_box = ttk.LabelFrame(imgs_grid, text="Strokes",
                                  padding=2)
        st_box.grid(row=0, column=3, sticky="nwe", padx=2)
        self.canvas_strokes = tk.Canvas(st_box, width=THUMB, height=THUMB,
            bg="#222", highlightthickness=0)
        self.canvas_strokes.pack()
        self.canvas_strokes.create_text(THUMB // 2, THUMB // 2,
            text="(未実行)", fill="#888", font=("Monaco", 9))
        for c in range(4):
            imgs_grid.grid_columnconfigure(c, weight=1, uniform="img")
        # 画像参照保持
        self._img_input = None
        self._img_gen = None
        self._img_userbin = None
        self._img_strokes = None

        # ④ VLM 結果 + プロンプト (テキスト) + Strokes 統計
        info_frame = ttk.LabelFrame(self.root,
            text="④ VLM 結果 / SDXL プロンプト / Strokes 統計",
            padding=4)
        info_frame.pack(fill=tk.X, padx=6, pady=(0, 4))
        info_grid = ttk.Frame(info_frame)
        info_grid.pack(fill=tk.X)
        # VLM 結果
        self.lbl_vlm = tk.Label(info_grid,
            text="(未実行)", font=("Monaco", 9),
            justify=tk.LEFT, anchor="nw",
            wraplength=240, fg="#555")
        self.lbl_vlm.grid(row=0, column=0, sticky="nwe", padx=4)
        # プロンプト
        self.txt_prompt = tk.Text(info_grid, height=3, width=40,
            font=("Monaco", 9), wrap=tk.WORD,
            bg="#f8f8f8", fg="#333")
        self.txt_prompt.grid(row=0, column=1, sticky="nwe", padx=4)
        self.txt_prompt.insert("1.0", "(未実行)")
        self.txt_prompt.config(state=tk.DISABLED)
        # Strokes 統計
        self.lbl_strokes_stat = tk.Label(info_grid,
            text="(未実行)", font=("Monaco", 9),
            justify=tk.LEFT, anchor="nw",
            wraplength=180, fg="#555")
        self.lbl_strokes_stat.grid(row=0, column=2, sticky="nwe", padx=4)
        info_grid.grid_columnconfigure(0, weight=2, uniform="info")
        info_grid.grid_columnconfigure(1, weight=3, uniform="info")
        info_grid.grid_columnconfigure(2, weight=1, uniform="info")
        # 操作ボタン
        btn_row = ttk.Frame(info_frame)
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
        self.btn_upload = ttk.Button(btn_row,
            text="⬆ webapp にアップロード",
            command=self.on_upload_webapp, width=22,
            state=tk.DISABLED)
        self.btn_upload.pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row,
            text="🔧 二値化キャリブ",
            command=self.on_binarize_calib, width=18
        ).pack(side=tk.RIGHT, padx=2)
        ttk.Button(btn_row,
            text="🎨 SDXL/プロンプト設定",
            command=self.on_imagegen_calib, width=22
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
        """元画像サムネイルを更新。"""
        self._img_input = self._fit_to_canvas(pil_img, self.canvas_input)

    def _fit_to_canvas(self, pil_img, canvas):
        """PIL.Image を canvas のサイズに fit させて表示。 photo 参照を返す。"""
        if ImageTk is None:
            return None
        # winfo_width が 1 (= まだ表示されていない、 ジオメトリ未確定) の
        # 場合は要求された width を使う
        cw = canvas.winfo_width()
        ch = canvas.winfo_height()
        if cw < 50:
            try:
                cw = int(canvas.cget("width"))
            except Exception:
                cw = 170
        if ch < 50:
            try:
                ch = int(canvas.cget("height"))
            except Exception:
                ch = 170
        iw, ih = pil_img.size
        scale = min(cw / iw, ch / ih, 1.0)
        nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
        resized = pil_img.resize((nw, nh), Image.LANCZOS)
        photo = ImageTk.PhotoImage(resized)
        canvas.delete("all")
        canvas.create_image(cw // 2, ch // 2, image=photo, anchor=tk.CENTER)
        return photo

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

    # ---------- 透明ボード線抽出 (背景差分 + 特定色) ----------

    def on_capture_background(self):
        """空ボードを 1 枚撮って背景差分の基準にする。"""
        if self.camera is None:
            messagebox.showinfo("カメラ未起動",
                "先に「カメラ起動」 してから、 線を消した空ボードを撮ってください。")
            return
        try:
            self.background_bgr = self.camera.capture_single()
        except Exception as e:
            self.log(f"背景キャプチャ失敗: {e}")
            messagebox.showerror("背景キャプチャ失敗", str(e))
            return
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        bg_path = LOGS_DIR / f"line_background_{ts}.png"
        cv2.imwrite(str(bg_path), self.background_bgr)
        self.lbl_bg_status.config(text=f"背景: 取得済 ({ts})", foreground="#262")
        self.log(f"背景キャプチャ → {bg_path}")

    def on_extract_lines(self):
        """現在の入力画像から線を抽出して selected_sketch_path に差し替える。

        背景差分 (背景取得済なら) + 特定色フィルタ。 背景未取得でも色のみで動く。
        """
        if self.selected_sketch_path is None or \
                not self.selected_sketch_path.exists():
            messagebox.showinfo("入力なし",
                "先にカメラ撮影 (またはファイル選択) で線入りの画像を確定してください。")
            return
        try:
            from modules.line_extract import extract_lines_image
        except Exception as e:
            messagebox.showerror("line_extract import 失敗", str(e))
            return
        frame = cv2.imread(str(self.selected_sketch_path))
        if frame is None:
            messagebox.showerror("読み込み失敗",
                f"画像を読めません:\n{self.selected_sketch_path}")
            return
        mode = self.var_line_mode.get()
        try:
            img = extract_lines_image(
                frame, self.background_bgr, mode=mode,
                dark_v_max=int(self.var_line_dark_v.get()),
                diff_thresh=int(self.var_line_diff.get()))
        except Exception as e:
            self.log(f"線抽出失敗: {e}")
            messagebox.showerror("線抽出失敗", str(e))
            return
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = LOGS_DIR / f"line_extracted_{ts}.png"
        img.save(out_path)
        self.selected_sketch_path = out_path
        bg_note = "背景差分+色" if self.background_bgr is not None else "色のみ(背景未取得)"
        self.log(f"線抽出完了 ({mode}, {bg_note}) → {out_path}")
        self.lbl_file_path.config(text=str(out_path), foreground="black")
        if Image is not None:
            self._show_preview_pil(img)

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
        # pre-run snapshot: 今 run より前に存在した cycle_dir を集合化
        self._pre_run_cycles = set(
            str(p) for p in LOGS_DIR.glob("vlm_to_image_*/cycle_*"))
        # last_cycle_dir もクリア (前回 path の残り表示を防止)
        self.last_cycle_dir = None
        self.lbl_last_cycle.config(
            text="(新規 cycle_dir 待機中...)", foreground="#555")
        self.lbl_vlm.config(text="(待機中...)", fg="#555")
        self.txt_prompt.config(state=tk.NORMAL)
        self.txt_prompt.delete("1.0", tk.END)
        self.txt_prompt.insert("1.0", "(待機中...)")
        self.txt_prompt.config(state=tk.DISABLED)
        self.lbl_strokes_stat.config(text="(待機中...)", fg="#555")
        # 4 つのキャンバスもリセット (元画像以外、 元画像は手元の選択を保持)
        for canvas, label in [
            (self.canvas_gen, "(待機中...)"),
            (self.canvas_userbin, "(待機中...)"),
            (self.canvas_strokes, "(待機中...)"),
        ]:
            canvas.delete("all")
            cw = canvas.winfo_width() or 170
            ch = canvas.winfo_height() or 170
            canvas.create_text(cw // 2, ch // 2,
                text=label, fill="#888", font=("Monaco", 9))
        # 画像参照クリア (前回 result を解放)
        self._img_gen = None
        self._img_userbin = None
        self._img_strokes = None
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
        try:
            vstretch = float(self.var_vstretch.get())
        except Exception:
            vstretch = 1.0
        cmd = [
            python, str(PIPELINE_SCRIPT),
            "--sketch", str(sketch_path),
            "--steps", str(steps),
            "--cycles", "1",
            "--log-dir", str(LOGS_DIR),
            "--vstretch", f"{vstretch:.3f}",
        ] + seed_arg
        if self.var_warp_correct.get():
            cmd.append("--warp-correct")
        if self.var_one_stroke.get():
            cmd.append("--one-stroke")
        if self.var_literal_only.get():
            cmd.append("--literal-only")
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
                elif "imagegen" in low or "sdxl" in low or "flux" in low:
                    self.root.after(0, lambda:
                        self._set_status("画像生成中 (FLUX 最新ルート)...", "blue"))
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
            self.root.after(0, lambda:
                self.btn_upload.config(state=tk.NORMAL))
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
        latest = self._find_latest_cycle_after_pre_snapshot() \
            or self._find_latest_cycle()
        if latest is None:
            return
        files = {
            "topic_guess": latest / "topic_guess.json",
            "prompt": latest / "prompt.txt",
            "generated": latest / "generated.png",
            "strokes": latest / "strokes.json",
            "userbinary": latest / "vec_debug" / "02a_user_binary.png",
            "strokes_png": latest / "vec_debug" / "06_strokes.png",
        }
        updaters = {
            "topic_guess": self._update_stage_vlm,
            "prompt": self._update_stage_prompt,
            "generated": self._update_stage_generated,
            "strokes": self._update_stage_strokes,
            "userbinary": self._update_stage_userbinary,
            "strokes_png": self._update_stage_strokes_png,
        }
        for name, f in files.items():
            if name in self._stage_seen:
                continue
            if f.exists():
                self._stage_seen.add(name)
                updaters[name](f)

    def _poll_stage_files(self):
        """パイプライン実行中、 今 run で生成された cycle_dir を監視して、
        ファイル (topic_guess.json / prompt.txt / generated.png /
        strokes.json / vec_debug/02a_user_binary.png /
        vec_debug/06_strokes.png) が現れたら UI を更新する。
        """
        if not getattr(self, "_stage_polling", False):
            return
        # 今 run の cycle_dir を探す: pre-run snapshot に無いもの (新規) のみ。
        # fallback で前 run の dir を読むと前回画像が表示されるので使わない。
        latest = self._find_latest_cycle_after_pre_snapshot()
        if latest is not None:
            try:
                if str(latest) != self.lbl_last_cycle.cget("text"):
                    self.lbl_last_cycle.config(
                        text=str(latest), foreground="black")
            except Exception:
                pass
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
            ub = latest / "vec_debug" / "02a_user_binary.png"
            if "userbinary" not in self._stage_seen and ub.exists():
                self._stage_seen.add("userbinary")
                self._update_stage_userbinary(ub)
            sj = latest / "strokes.json"
            if "strokes" not in self._stage_seen and sj.exists():
                self._stage_seen.add("strokes")
                self._update_stage_strokes(sj)
            sp = latest / "vec_debug" / "06_strokes.png"
            if "strokes_png" not in self._stage_seen and sp.exists():
                self._stage_seen.add("strokes_png")
                self._update_stage_strokes_png(sp)
        if self._stage_polling:
            self.root.after(500, self._poll_stage_files)

    def _find_latest_cycle_after_pre_snapshot(self) -> Path | None:
        """pre-run snapshot に無い cycle_dir を返す (= 今 run で新しく
        作られた dir)。 見つからなければ None。
        """
        pre = getattr(self, "_pre_run_cycles", set())
        candidates = sorted(LOGS_DIR.glob("vlm_to_image_*/cycle_*"))
        for c in reversed(candidates):
            if str(c) not in pre:
                return c
        return None

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
        self.log(f"  プレビュー: 生成画像 検出 {gen_path}")
        if Image is None or ImageTk is None:
            self.log("    PIL/ImageTk 未 import、 表示できず")
            return
        try:
            img = Image.open(gen_path).convert("RGB")
        except Exception as e:
            self.log(f"    生成画像 読込失敗: {e}")
            return
        try:
            self._img_gen = self._fit_to_canvas(img, self.canvas_gen)
            self.btn_view_gen.config(state=tk.NORMAL)
            self.log(f"    生成画像 表示 (size={img.size})")
        except Exception as e:
            self.log(f"    canvas 描画失敗: {e}")

    def _update_stage_userbinary(self, ub_path: Path):
        if Image is None or ImageTk is None:
            return
        try:
            img = Image.open(ub_path).convert("RGB")
        except Exception:
            return
        self._img_userbin = self._fit_to_canvas(img, self.canvas_userbin)

    def _update_stage_strokes_png(self, sp_path: Path):
        if Image is None or ImageTk is None:
            return
        try:
            img = Image.open(sp_path).convert("RGB")
        except Exception:
            return
        self._img_strokes = self._fit_to_canvas(img, self.canvas_strokes)

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
        proc = self.pipeline_proc
        if proc is None:
            return
        try:
            proc.terminate()
            self.log("⛔ 生成キャンセル要求 (terminate)")
            self.btn_abort.config(state=tk.DISABLED)
            # FLUX 生成中は terminate で即死しないことがある → 2 秒後に強制 kill。
            def _force_kill():
                try:
                    if proc.poll() is None:
                        proc.kill()
                        self.log("⛔ 強制終了 (kill)")
                except Exception:
                    pass
            self.root.after(2000, _force_kill)
        except Exception as e:
            self.log(f"キャンセル失敗: {e}")

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

    def on_upload_webapp(self):
        """直近の cycle_dir を選定 webapp の候補としてアップロード。

        ローカル再ビルド (push なし) が既定。 確認ダイアログで push も選べる。
        upload_to_webapp.py を subprocess で実行 (重い import を別プロセス化)。
        """
        if not self.last_cycle_dir or not self.last_cycle_dir.exists():
            messagebox.showinfo("結果なし",
                "先にパイプラインを実行して結果を生成してください。")
            return
        if not (self.last_cycle_dir / "strokes.json").exists():
            messagebox.showerror("strokes なし",
                f"strokes.json が見つかりません:\n{self.last_cycle_dir}")
            return
        label = simpledialog.askstring("webapp アップロード",
            "候補の表示名 (sketch_id) を入力:", initialvalue="camera",
            parent=self.root)
        if not label:
            return
        do_push = messagebox.askyesno("公開設定",
            "GitHub に push してオンライン (GitHub Pages) でも見られるように "
            "しますか?\n\n"
            "「はい」 = push (リモートでも見える、 反映まで ~1 分)\n"
            "「いいえ」 = ローカルのみ (scripts/webapp_local で確認)")
        mode_arg = "--push" if do_push else "--local"
        cmd = [sys.executable, "-m", "scripts.upload_to_webapp",
               "--cycle", str(self.last_cycle_dir),
               "--label", label, mode_arg]
        if self.selected_sketch_path and self.selected_sketch_path.exists():
            cmd += ["--input", str(self.selected_sketch_path)]
        self.log(f"webapp アップロード中... ({'push' if do_push else 'local'})")
        self._set_status("webapp アップロード中...", "blue")

        def _worker():
            try:
                r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True,
                                   text=True, timeout=300)
            except Exception as e:
                self.root.after(0, lambda: self._upload_done(False, str(e)))
                return
            ok = (r.returncode == 0)
            msg = (r.stdout or "") + (r.stderr or "")
            self.root.after(0, lambda: self._upload_done(ok, msg[-800:]))

        threading.Thread(target=_worker, daemon=True).start()

    def _upload_done(self, ok: bool, msg: str):
        if ok:
            self._set_status("webapp アップロード完了", "green")
            self.log("webapp アップロード完了")
            messagebox.showinfo("完了",
                "webapp に候補を追加しました。\n\n"
                "ローカル確認: scripts/webapp_local を起動\n"
                "(push した場合) オンライン: GitHub Pages に ~1 分で反映")
        else:
            self._set_status("webapp アップロード失敗", "red")
            self.log(f"webapp アップロード失敗:\n{msg}")
            messagebox.showerror("アップロード失敗", msg or "不明なエラー")

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

    def on_imagegen_calib(self):
        """SDXL + プロンプト設定 popup を開く。"""
        ImageGenCalibWindow(self)

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


class ImageGenCalibWindow:
    """SDXL + プロンプト設定を編集する Toplevel ウィンドウ。
    imagegen_config.yaml に保存。 次回パイプライン実行から反映。
    """

    def __init__(self, parent_gui: "PipelineTestGUI"):
        self.parent = parent_gui
        from modules.image_gen import (
            load_imagegen_config, save_imagegen_config,
            DEFAULT_NEGATIVE_PROMPT, MODEL_PRESETS)
        from modules.prompt_builder import _BASE_TEMPLATE, _FALLBACK_TEMPLATE
        self._save_cfg = save_imagegen_config
        self._model_presets = MODEL_PRESETS
        cfg = load_imagegen_config()
        # 既定テンプレートが None なら組込みを表示
        base = cfg.get("base_template") or _BASE_TEMPLATE
        fallback = cfg.get("fallback_template") or _FALLBACK_TEMPLATE
        # Vars
        # preset 選択 (空文字 = preset なし = 既定 SDXL Turbo + MistoLine)
        self.var_preset = tk.StringVar(value=cfg.get("preset") or "")
        self.var_steps = tk.IntVar(value=cfg["num_inference_steps"])
        self.var_guidance = tk.DoubleVar(value=cfg["guidance_scale"])
        self.var_cn = tk.DoubleVar(
            value=cfg["controlnet_conditioning_scale"])
        self.var_conf = tk.DoubleVar(value=cfg["confidence_threshold"])
        # 解像度 (auto_from_panel ON / OFF + 手動指定)
        self.var_auto_panel = tk.BooleanVar(
            value=bool(cfg.get("auto_from_panel", False)))
        res = cfg.get("resolution")
        if isinstance(res, (list, tuple)) and len(res) == 2:
            self.var_res_w = tk.IntVar(value=int(res[0]))
            self.var_res_h = tk.IntVar(value=int(res[1]))
        elif isinstance(res, (int, float)):
            self.var_res_w = tk.IntVar(value=int(res))
            self.var_res_h = tk.IntVar(value=int(res))
        else:
            self.var_res_w = tk.IntVar(value=1024)
            self.var_res_h = tk.IntVar(value=1024)
        # 組込み defaults (リセット用)
        self._builtin_base = _BASE_TEMPLATE
        self._builtin_fallback = _FALLBACK_TEMPLATE
        self._builtin_neg = DEFAULT_NEGATIVE_PROMPT
        # Window
        self.win = tk.Toplevel(parent_gui.root)
        self.win.title("SDXL / プロンプト 設定")
        self.win.geometry("840x880")
        self._build_ui(base, fallback, str(cfg["negative_prompt"]))
        # panel readout + 初期 enable/disable は build 後に呼び出し
        self._refresh_panel_readout()
        self._on_auto_panel_toggle()

    def _build_ui(self, base, fallback, neg):
        # モデル preset (base + controlnet + LoRA を一括切替)
        preset_box = ttk.LabelFrame(self.win,
            text="モデル preset (base + ControlNet + LoRA)", padding=8)
        preset_box.pack(fill=tk.X, padx=8, pady=(8, 4))
        pr_row = ttk.Frame(preset_box)
        pr_row.pack(fill=tk.X)
        ttk.Label(pr_row, text="preset:").pack(side=tk.LEFT, padx=4)
        preset_values = [""] + list(self._model_presets.keys())
        self.cmb_preset = ttk.Combobox(pr_row,
            textvariable=self.var_preset, values=preset_values,
            width=36, state="readonly")
        self.cmb_preset.pack(side=tk.LEFT, padx=4)
        self.cmb_preset.bind("<<ComboboxSelected>>", self._on_preset_changed)
        ttk.Label(pr_row,
            text="(空 = 既定 / SDXL Turbo + MistoLine)",
            font=("Monaco", 9), foreground="#777"
        ).pack(side=tk.LEFT, padx=4)
        # preset 詳細表示 (選んだ瞬間に下に出る)
        self.lbl_preset_info = ttk.Label(preset_box,
            text="", font=("Monaco", 9), foreground="#555",
            justify=tk.LEFT, wraplength=780)
        self.lbl_preset_info.pack(fill=tk.X, padx=4, pady=(4, 0), anchor=tk.W)
        self._update_preset_info_label()

        # SDXL 数値パラメータ
        num_box = ttk.LabelFrame(self.win,
            text="SDXL 数値パラメータ (preset 選択時もここで上書き可)", padding=8)
        num_box.pack(fill=tk.X, padx=8, pady=(8, 4))
        r1 = ttk.Frame(num_box)
        r1.pack(fill=tk.X)
        ttk.Label(r1, text="num_inference_steps:"
                  ).pack(side=tk.LEFT, padx=4)
        tk.Spinbox(r1, from_=1, to=30, width=4,
            textvariable=self.var_steps
        ).pack(side=tk.LEFT, padx=2)
        ttk.Label(r1,
            text="  (Turbo は 1-4 が標準、 多いほど clean、 少ないほど 速い)"
            , font=("Monaco", 9), foreground="#777"
        ).pack(side=tk.LEFT, padx=4)
        r2 = ttk.Frame(num_box)
        r2.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(r2, text="guidance_scale:").pack(side=tk.LEFT, padx=4)
        tk.Scale(r2, from_=0.0, to=15.0, orient=tk.HORIZONTAL,
            variable=self.var_guidance, length=300, resolution=0.1
        ).pack(side=tk.LEFT, padx=4)
        ttk.Label(r2,
            text="(Turbo は 0.0、 通常 SDXL は 5-7。 大きいほどプロンプト遵守)"
            , font=("Monaco", 9), foreground="#777"
        ).pack(side=tk.LEFT, padx=4)
        r3 = ttk.Frame(num_box)
        r3.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(r3, text="controlnet_conditioning_scale:"
                  ).pack(side=tk.LEFT, padx=4)
        tk.Scale(r3, from_=0.0, to=2.0, orient=tk.HORIZONTAL,
            variable=self.var_cn, length=300, resolution=0.05
        ).pack(side=tk.LEFT, padx=4)
        ttk.Label(r3,
            text="(1.0 = 入力線厳守、 低=自由、 高=固定)"
            , font=("Monaco", 9), foreground="#777"
        ).pack(side=tk.LEFT, padx=4)

        # Panel 寸法 → 生成解像度 (SDXL bucket)
        panel_box = ttk.LabelFrame(self.win,
            text="Panel 寸法 → 生成解像度  "
                 "(canvas_calibration ↔ 画像生成 の整合)",
            padding=8)
        panel_box.pack(fill=tk.X, padx=8, pady=(8, 4))
        # readout (canvas mm + SDXL bucket)
        self.lbl_panel_readout = ttk.Label(panel_box,
            text="(panel 計測値読込中…)", font=("Monaco", 9),
            foreground="#555", justify=tk.LEFT,
            wraplength=820)
        self.lbl_panel_readout.pack(fill=tk.X, padx=4, pady=(0, 6),
                                      anchor=tk.W)
        # auto toggle + manual override row
        pr_row = ttk.Frame(panel_box)
        pr_row.pack(fill=tk.X)
        ttk.Checkbutton(pr_row,
            text="auto_from_panel  (canvas 計測の aspect から SDXL bucket 自動選択)",
            variable=self.var_auto_panel,
            command=self._on_auto_panel_toggle,
        ).pack(side=tk.LEFT, padx=4)
        # manual W / H
        mr_row = ttk.Frame(panel_box)
        mr_row.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(mr_row, text="手動 (W × H):"
                  ).pack(side=tk.LEFT, padx=4)
        self.spin_res_w = tk.Spinbox(mr_row, from_=512, to=2048,
            increment=64, width=6, textvariable=self.var_res_w)
        self.spin_res_w.pack(side=tk.LEFT, padx=2)
        ttk.Label(mr_row, text="×").pack(side=tk.LEFT, padx=2)
        self.spin_res_h = tk.Spinbox(mr_row, from_=512, to=2048,
            increment=64, width=6, textvariable=self.var_res_h)
        self.spin_res_h.pack(side=tk.LEFT, padx=2)
        ttk.Button(mr_row, text="🔄 再計測値で更新",
            command=self._refresh_panel_readout, width=18,
        ).pack(side=tk.LEFT, padx=8)
        ttk.Button(mr_row, text="📐 bucket を手動欄に反映",
            command=self._apply_bucket_to_manual, width=20,
        ).pack(side=tk.LEFT, padx=2)
        ttk.Label(panel_box,
            text="(auto ON 時、 手動欄は保存対象から除外。 OFF 時のみ "
                 "resolution=[W, H] が yaml に書かれる)",
            font=("Monaco", 9), foreground="#777",
        ).pack(anchor=tk.W, padx=4, pady=(2, 0))

        # プロンプト
        pr_box = ttk.LabelFrame(self.win,
            text="プロンプトテンプレート (英語、 "
                 "{subject_en} {action_en} {location_en} を含める)",
            padding=8)
        pr_box.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        ttk.Label(pr_box,
            text="メイン (高信頼度時):"
        ).pack(anchor=tk.W)
        self.txt_base = tk.Text(pr_box, height=4, font=("Monaco", 9),
            wrap=tk.WORD)
        self.txt_base.pack(fill=tk.X, pady=(2, 6))
        self.txt_base.insert("1.0", base)
        ttk.Label(pr_box,
            text="フォールバック (低信頼度 / 不明時):"
        ).pack(anchor=tk.W)
        self.txt_fallback = tk.Text(pr_box, height=3, font=("Monaco", 9),
            wrap=tk.WORD)
        self.txt_fallback.pack(fill=tk.X, pady=(2, 6))
        self.txt_fallback.insert("1.0", fallback)
        ttk.Label(pr_box,
            text="Negative プロンプト (noise / scribble / 等の抑制):"
        ).pack(anchor=tk.W)
        self.txt_neg = tk.Text(pr_box, height=3, font=("Monaco", 9),
            wrap=tk.WORD)
        self.txt_neg.pack(fill=tk.X, pady=(2, 6))
        self.txt_neg.insert("1.0", neg)
        # confidence threshold
        conf_row = ttk.Frame(pr_box)
        conf_row.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(conf_row,
            text="confidence_threshold (これ未満は fallback):"
        ).pack(side=tk.LEFT, padx=4)
        tk.Scale(conf_row, from_=0.0, to=1.0, orient=tk.HORIZONTAL,
            variable=self.var_conf, length=200, resolution=0.05
        ).pack(side=tk.LEFT, padx=4)

        # ボタン行
        b_row = ttk.Frame(self.win)
        b_row.pack(fill=tk.X, padx=8, pady=(0, 8))
        ttk.Button(b_row, text="🔄 組込み既定値に戻す",
            command=self._reset_to_builtin, width=22
        ).pack(side=tk.LEFT, padx=2)
        ttk.Button(b_row, text="❌ キャンセル",
            command=self.win.destroy, width=14
        ).pack(side=tk.RIGHT, padx=2)
        ttk.Button(b_row, text="💾 yaml に保存",
            command=self._save, width=18
        ).pack(side=tk.RIGHT, padx=2)

    def _refresh_panel_readout(self):
        """canvas_calibration.yaml を再読込し、 panel 寸法 + 推奨 SDXL bucket
        を readout に表示。 物理 panel を計測し直した直後にこのボタンで反映。"""
        try:
            from modules.panel_geometry import load_panel_geometry
            geom = load_panel_geometry()
            mu, mv = geom.mm_per_px
            self._last_bucket = geom.panel_image_size
            txt = (
                f"📏 panel: {geom.panel_size_mm[0]:.2f} × "
                f"{geom.panel_size_mm[1]:.2f} mm  "
                f"(aspect {geom.aspect:.3f}, source={geom.source})\n"
                f"🪣 推奨 SDXL bucket: {geom.panel_image_size[0]} × "
                f"{geom.panel_image_size[1]} px  "
                f"(aspect err {geom.bucket_aspect_err * 100:.2f}%)\n"
                f"📐 mm/px = ({mu:.4f}, {mv:.4f})  "
                f"← 縦横で等しければ panel に貼った時に歪まない"
            )
            self.lbl_panel_readout.config(text=txt, foreground="#080")
        except Exception as e:
            self._last_bucket = None
            self.lbl_panel_readout.config(
                text=f"⚠ panel readout 読込失敗: {e}",
                foreground="#a00")

    def _on_auto_panel_toggle(self):
        """auto_from_panel ON で 手動 W/H 欄を disable、 OFF で enable。"""
        state = "disabled" if self.var_auto_panel.get() else "normal"
        try:
            self.spin_res_w.config(state=state)
            self.spin_res_h.config(state=state)
        except tk.TclError:
            pass

    def _apply_bucket_to_manual(self):
        """推奨 bucket を 手動 W/H 欄に流し込む (auto OFF にして編集を引き継ぐ)。"""
        if not getattr(self, "_last_bucket", None):
            messagebox.showwarning("bucket 未取得",
                "panel readout が未取得です。 先に「再計測値で更新」 を押下。")
            return
        w, h = self._last_bucket
        self.var_res_w.set(int(w))
        self.var_res_h.set(int(h))
        self.var_auto_panel.set(False)
        self._on_auto_panel_toggle()

    def _reset_to_builtin(self):
        if not messagebox.askyesno("既定値リセット",
                "プロンプト / negative / 数値パラメータを 組込み既定値に "
                "戻しますか?\n(保存は別途 「💾 yaml に保存」 で)"):
            return
        from modules.image_gen import (
            DEFAULT_NUM_INFERENCE_STEPS, DEFAULT_GUIDANCE_SCALE,
            DEFAULT_CONTROLNET_SCALE, DEFAULT_NEGATIVE_PROMPT)
        self.var_steps.set(DEFAULT_NUM_INFERENCE_STEPS)
        self.var_guidance.set(DEFAULT_GUIDANCE_SCALE)
        self.var_cn.set(DEFAULT_CONTROLNET_SCALE)
        self.var_conf.set(0.3)
        for txt, content in [
            (self.txt_base, self._builtin_base),
            (self.txt_fallback, self._builtin_fallback),
            (self.txt_neg, self._builtin_neg),
        ]:
            txt.delete("1.0", tk.END)
            txt.insert("1.0", content)

    def _on_preset_changed(self, _event=None):
        """preset 切替時、 数値パラメータをその preset の推奨値に
        プリフィル (ユーザが上書き変更していた値はリセットされる)。"""
        name = self.var_preset.get().strip()
        if name and name in self._model_presets:
            cfg = self._model_presets[name]
            self.var_steps.set(int(cfg["num_inference_steps"]))
            self.var_guidance.set(float(cfg["guidance_scale"]))
            self.var_cn.set(float(cfg["controlnet_conditioning_scale"]))
        self._update_preset_info_label()

    def _update_preset_info_label(self):
        name = self.var_preset.get().strip()
        if not name:
            self.lbl_preset_info.config(
                text="(preset 未選択 — 既定の SDXL Turbo + MistoLine を使う)")
            return
        if name not in self._model_presets:
            self.lbl_preset_info.config(text=f"⚠️  unknown preset: {name}")
            return
        cfg = self._model_presets[name]
        lora = cfg.get("lora_path") or "(none)"
        lora_scale = cfg.get("lora_scale", "—")
        text = (
            f"base: {cfg['base_model_id']}\n"
            f"controlnet: {cfg['controlnet_id']}  (variant={cfg.get('variant')})\n"
            f"LoRA: {lora}  scale={lora_scale}\n"
            f"style_hint: {cfg.get('style_hint', '(none)')}"
        )
        self.lbl_preset_info.config(text=text)

    def _save(self):
        base = self.txt_base.get("1.0", "end").strip()
        fallback = self.txt_fallback.get("1.0", "end").strip()
        neg = self.txt_neg.get("1.0", "end").strip()
        # 組込み既定と一致するなら None で保存 (将来既定が更新された
        # 時にも追従)
        base_for_yaml = None if base == self._builtin_base.strip() else base
        fb_for_yaml = None if fallback == self._builtin_fallback.strip() \
                      else fallback
        preset = self.var_preset.get().strip() or None
        # 解像度: auto_from_panel ON のとき resolution は None で yaml に書かない、
        # OFF のとき手動 W/H を [W, H] で書き出す。
        auto_panel = bool(self.var_auto_panel.get())
        if auto_panel:
            resolution_to_save = None
        else:
            resolution_to_save = (int(self.var_res_w.get()),
                                   int(self.var_res_h.get()))
        try:
            saved_path = self._save_cfg(
                num_inference_steps=int(self.var_steps.get()),
                guidance_scale=float(self.var_guidance.get()),
                controlnet_conditioning_scale=float(self.var_cn.get()),
                negative_prompt=neg,
                base_template=base_for_yaml,
                fallback_template=fb_for_yaml,
                confidence_threshold=float(self.var_conf.get()),
                preset=preset,
                resolution=resolution_to_save,
                auto_from_panel=auto_panel)
        except Exception as e:
            messagebox.showerror("保存失敗", str(e))
            return
        messagebox.showinfo("保存完了",
            f"{saved_path} に保存。\n"
            "次回パイプライン実行時から反映されます。")
        self.parent.log(
            f"imagegen_config.yaml 保存: preset={preset or '(none)'} "
            f"steps={self.var_steps.get()} cn={self.var_cn.get():.2f}")
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
