#!/usr/bin/env python3
"""wall_drawing_gui.py -- Tkinter console for the wall-drawing workflow.

Sections:
  1. Connection         (speed knob + Connect/Recover/Storage/Disconnect)
  2. Drag-Teach         (canvas plane calibration via master mode)
  3. Tune Contact       (X depth tuning at canvas center)
  4. Draw Square        (center/side + 4 corner deltas + Draw)

HARDWARE QUIRKS PRESERVED:
  - drag-teach enters master mode (MasterSlaveConfig 0xFA); after exit
    the in-process SDK becomes unresponsive even after power cycle (the
    same socketcan starvation we hit during debug). The GUI therefore
    insists you EXIT and re-launch it after every drag-teach session.
  - Every motion verifies the result; silent "command ignored" failures
    are escalated to a hard ERROR with a "restart GUI" hint.
  - All moves require ready pose v2 except (a) re-touch from a current
    tune-X state, and (b) wall-facing -> wall-facing nudges.

Run:
    /home/jizaiedev2026/draw_piper/venv/bin/python wall_drawing_gui.py
"""

import datetime
import os
import re
import struct
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox

import numpy as np
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wall_facing_ik import (fk, euler_zyx, JOINT_LIMITS_DEG, solve_ik,
                             INITIAL_GUESSES as IK_INITIAL_GUESSES)
from canvas_calibration_io import (
    make_point, read as read_calibration, write_v3,
)
from drag_sampling_thread import DragSamplingThread
# 統合 dev: 生成画像描画パイプラインを取り込む
import canvas_to_panel_frame_dev as ctp_dev
import draw_strokes_wall_dev as dsw_dev
from tkinter import filedialog

# draw_piper 側の StrokePicker (logs/ をパースしてカード一覧で選ぶ Tk ダイアログ)
# import エラー時はフォールバックで filedialog を使えるよう、 ラップして読む。
import sys as _sys
_DRAW_PIPER_ROOT = "/home/jizaiedev2026/draw_piper"
if _DRAW_PIPER_ROOT not in _sys.path:
    _sys.path.insert(0, _DRAW_PIPER_ROOT)
try:
    from modules.stroke_picker import StrokePicker  # type: ignore
except Exception as _spk_err:
    StrokePicker = None
    print(f"[wall_drawing_gui] StrokePicker import 失敗 ({_spk_err}) — "
          "filedialog にフォールバック")

# Frida-inspired smooth drawing (PR #2, claude/frida-smoothness-20260527)。
# draw_strokes_panel_smooth は draw_piper の Robot に実装済。
# 既存 'on_strokes_draw' (IK + MOVE J chained) とは独立、 別 Robot
# インスタンス + EndPoseCtrl (MOVE_L + MOVE_C) 経路で描画する。
try:
    from modules.robot import Robot as _DPRobot                # type: ignore
    from modules.robot import PanelFrame as _DPPanelFrame      # type: ignore
except Exception as _frida_err:
    _DPRobot = None
    _DPPanelFrame = None
    print(f"[wall_drawing_gui] Frida Robot import 失敗 ({_frida_err}) — "
          "Frida Smooth Draw 無効")

try:
    from piper_sdk import C_PiperInterface_V2
except ImportError:
    C_PiperInterface_V2 = None


# ============================================================================
# Constants
# ============================================================================
OUTPUT_YAML = "/home/jizaiedev2026/draw_piper/calibration/canvas_calibration.yaml"

READY_POSE_V2_DEG = (-45.0, 60.0, -60.0, 0.0, 30.0, 0.0)
WALL_FACING_JOINTS_DEG = (0.000, 54.865, -27.267, 0.001, -22.598, 0.000)
STORAGE_POSE_DEG = (0.0, 10.0, -10.0, 0.0, 0.0, 0.0)
# ペン交換ポーズ: ペン先が真上を向くように関節を設定
# 2026-05-27: J5=70 が limit ギリギリで届かず err 15° で失敗 →
# J5=60 (limit -70..+70 の余裕アリ) に変更。
# J3=-90 で肘を折りたたみ、 全体的に limit 内で穏やかに到達できる値。
PEN_EXCHANGE_POSE_DEG = (0.0, 30.0, -90.0, 0.0, 60.0, 0.0)

SPEED_JOINT_DEFAULT = 5     # MOVE J -- smooth ready-pose moves
SPEED_DRAW_DEFAULT = 2      # legacy const (kept for backwards compat)
SPEED_MIN = 1
SPEED_MAX = 30

CONTACT_X_MM_DEFAULT = 204.3
SQUARE_CENTER_Y_DEFAULT = -2.8
SQUARE_CENTER_Z_DEFAULT = 300.0
SQUARE_SIDE_DEFAULT = 30.0
MAX_PUSH_MM = 2.0
PEN_UP_CLEAR_MM = 30.0   # 2026-05-27: 10 → 30 (ペン上げ時のキャンバスとの距離拡大、 移動中の擦り防止)
# ストローク間や wall-facing 切替時に使う 「遠い退避位置」 (mm)。
# pen_up_x よりさらに canvas から離れる事で、 関節大移動中の擦りを防ぐ。
SAFE_TRAVEL_X_MM = 130.0
ENDPOSE_TOL_MM = 5.0
READY_JOINT_TOL_DEG = 3.0
MOVE_J_VERIFY_TOL_DEG = 5.0     # after a MOVE J, error must be below this

X_NUDGE_STEPS = [-1.0, -0.5, +0.5, +1.0]
# ===== Step 6 (B4) Center Adjustment =====
# Y/Z nudge increments (mm). Smaller than X nudge because the visual
# center can be located more precisely (no contact-force ambiguity).
Y_NUDGE_STEPS = [-1.0, -0.5, +0.5, +1.0]
Z_NUDGE_STEPS = [-1.0, -0.5, +0.5, +1.0]

CANDUMP_RE = re.compile(
    r"^\s*\([0-9.]+\)\s+\S+\s+([0-9A-Fa-f]+)\s+\[\d+\]\s+([0-9A-Fa-f ]+?)\s*$")


# ============================================================================
# Candump listener (parses 0x155-0x157 master broadcast)
# ============================================================================
class CandumpListener(threading.Thread):
    def __init__(self, channel="can0"):
        super().__init__(daemon=True)
        self.channel = channel
        self.proc = None
        self.lock = threading.Lock()
        self._joints_milli = [0, 0, 0, 0, 0, 0]
        self._counts = {0x155: 0, 0x156: 0, 0x157: 0}
        self._total = 0
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=1.0)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass

    def get_joints_deg(self):
        with self.lock:
            return tuple(v / 1000.0 for v in self._joints_milli)

    def get_counts(self):
        with self.lock:
            return dict(self._counts), self._total

    def run(self):
        self.proc = subprocess.Popen(
            ["candump", "-ta", self.channel],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1)
        for line in self.proc.stdout:
            if self._stop.is_set():
                break
            m = CANDUMP_RE.match(line)
            if not m:
                continue
            try:
                cid = int(m.group(1), 16)
            except ValueError:
                continue
            with self.lock:
                self._total += 1
            if cid not in (0x155, 0x156, 0x157):
                continue
            hex_bytes = m.group(2).split()
            if len(hex_bytes) < 8:
                continue
            try:
                data = bytes(int(b, 16) for b in hex_bytes[:8])
            except ValueError:
                continue
            a, b = struct.unpack(">ii", data)
            with self.lock:
                self._counts[cid] += 1
                if cid == 0x155:
                    self._joints_milli[0] = a
                    self._joints_milli[1] = b
                elif cid == 0x156:
                    self._joints_milli[2] = a
                    self._joints_milli[3] = b
                elif cid == 0x157:
                    self._joints_milli[4] = a
                    self._joints_milli[5] = b


def make_spinbox(parent, var, frm, to, increment, width=8, fmt="%.1f",
                  command=None, font=None):
    """tk.Spinbox 生成。 command は ▲▼ 押下で発火 (タイプ変更時は
    <Return> で別途 bind が必要)。"""
    sb_kwargs = {"from_": frm, "to": to, "increment": increment,
                 "textvariable": var, "width": width, "format": fmt}
    if command is not None:
        sb_kwargs["command"] = command
    if font is not None:
        sb_kwargs["font"] = font
    return tk.Spinbox(parent, **sb_kwargs)


def attach_tooltip(widget, text, wrap=320):
    """シンプルなホバーツールチップ。 widget に enter/leave bind。"""
    tip = {"win": None}
    def enter(_=None):
        if tip["win"] is not None:
            return
        x = widget.winfo_rootx() + 20
        y = widget.winfo_rooty() + widget.winfo_height() + 4
        w = tk.Toplevel(widget)
        w.wm_overrideredirect(True)
        w.wm_geometry(f"+{x}+{y}")
        tk.Label(w, text=text, justify=tk.LEFT,
                 background="#ffffe0", relief=tk.SOLID, borderwidth=1,
                 font=("Monaco", 9), wraplength=wrap,
                 padx=6, pady=4).pack()
        tip["win"] = w
    def leave(_=None):
        if tip["win"] is not None:
            tip["win"].destroy()
            tip["win"] = None
    widget.bind("<Enter>", enter)
    widget.bind("<Leave>", leave)


# ============================================================================
# Main GUI
# ============================================================================
class WallDrawingGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Piper 壁面描画コンソール")
        self.root.geometry("1080x940")

        # log の絵文字 ON/OFF。 環境変数 WALL_GUI_NO_EMOJI=1 で OFF。
        # 起動後 UI checkbutton でも変更可。 font 無し環境や log を
        # grep 等で機械処理する時に絵文字邪魔な場合 OFF。
        self._use_emoji = (os.environ.get("WALL_GUI_NO_EMOJI", "0") != "1")

        self.piper = None
        self.connected = False
        self.in_master = False
        self.power_cycle_needed = False
        # After master mode the in-process SDK is unreliable even after power
        # cycle. Force GUI restart -- block all motion until that happens.
        self.gui_restart_required = False
        self.listener = None
        self.busy_lock = threading.Lock()
        self.busy = False

        # Drag-teach state (v3 calibration, 5-phase design)
        #   Phase B1 = corners (TL/TR/BR/BL, ordered, required)
        #   Phase B2 = perimeter trace (drag-teach, auto-sampled @10mm)
        #   Phase B3/B4/B5 = added in Step 5/6/8
        self.CORNER_ORDER = ("tl", "tr", "br", "bl")
        self.CORNER_LABEL = {"tl": "TL", "tr": "TR", "br": "BR", "bl": "BL"}
        # dt_phase values (v3 calibration):
        #   None              -- not in drag-teach session
        #   "corners"         -- B1 collecting corners (0..3 done)
        #   "b2_idle"         -- B1 done, no trace yet
        #   "b2_recording"    -- B2 perimeter trace active
        #   "b2_done"         -- B2 stopped
        #   "b3_tlbr_recording" -- B3 TL→BR diagonal active
        #   "b3_trbl_recording" -- B3 TR→BL diagonal active
        #   "b3_done"         -- B3 trace stopped (at least one diagonal)
        # Any "*_done" / "b2_idle" state allows starting another trace.
        self.dt_phase = None
        self.dt_corner_idx = 0        # 0..4 (4 = corners done)
        self.dt_corners = {}          # {"tl": point_dict, ...}
        # 個別やり直しモード: None なら通常、 "tl"/"tr"/"br"/"bl" なら
        # 次の 「現在地を記録」 がその隅を上書きする
        self.redo_corner_key = None
        # B2/B3/B5 auto-sampled traces (filled by DragSamplingThread).
        self.dt_traces = {"perimeter": [], "diagonal_tl_br": [],
                          "diagonal_tr_bl": [], "surface": []}
        self.sampling_thread = None   # DragSamplingThread or None
        # When sampling_thread is alive, recording_target says which
        # dt_traces[] key its points get drained into on stop.
        self.recording_target = None

        # Tune-X state
        self.tune_x_active = False
        self.tune_x_pen_down = False
        self.tune_y = None
        self.tune_z = None
        self.tune_warm_q = None  # 直前の IK 解 (連続調整の warm start)
        self.cached_wall_rpy = None
        # Cached cartesian XYZ of the wall-facing pose (captured by
        # _ensure_wall_facing). Used as a known-reachable mid-canvas
        # waypoint when chaining probes across opposite reach corners.
        self.cached_wall_xyz = None

        # Probe Reach state (True while chaining probes -- the arm stays
        # pen-up over the canvas instead of returning to ready, and the
        # next Probe button accepts the non-nominal joint config).
        self.probe_active = False
        # True when probe-mode pen is currently pressed against the wall.
        self.probe_pen_down = False

        # ===== Step 6 (B4) Center Adjustment state =====
        # B4 runs OUTSIDE master mode (after Save + GUI restart). The user
        # commands the arm to a computed center (corner-avg or B3
        # intersection), nudges Y/Z to match the visual canvas center, and
        # confirms -- which updates the yaml's whiteboard_computed.center_mm
        # without re-running B1/B2/B3. Shares var_cy / var_cz with Tune
        # Contact and Draw Square so the confirmed center is immediately
        # available for drawing.
        # 旧 B4 状態 (互換用に残置、 _check_at_home_or_warn が参照)
        self.b4_active = False

        # ===== Capture Pose (Task B) state =====
        # Per B2 design: ready_pose = capture_pose. The user drags the arm
        # (in master mode) to a pose where the EOAT camera sees the whole
        # whiteboard with good joint margins, then presses "Record Ready
        # /Capture Pose" -- the joints are written to panel_frame.yaml's
        # panel.ready_pose_deg. On subsequent GUI launches, _do_recover and
        # related functions use this calibrated pose instead of the
        # built-in default. If panel_frame.yaml has no ready_pose_deg, we
        # fall back to READY_POSE_V2_DEG.
        self.calibrated_ready_pose = self._load_ready_pose_from_panel_yaml()

        # ===== Section 5 (full_dev): 生成画像描画パイプライン =====
        self.strokes_abort_flag = False  # 描画中断フラグ
        self.strokes_last_completed_idx = -1  # 直前 run の最終完了 stroke
        self.strokes_current_idx = -1  # 現在描画中 stroke (live preview 用)

        # Load calibration-driven defaults (Center Y/Z, contact X)
        loaded = self._load_calib_defaults()
        contact_x_def = loaded.get("contact_x_mm", CONTACT_X_MM_DEFAULT)
        center_y_def = loaded.get("center_y_mm", SQUARE_CENTER_Y_DEFAULT)
        center_z_def = loaded.get("center_z_mm", SQUARE_CENTER_Z_DEFAULT)
        xoff_def = loaded.get("xoff_mm", 0.0)
        self.contact_x_mm = contact_x_def
        self._xoff_default = xoff_def

        # Tk variables
        self.var_speed_joint = tk.IntVar(value=SPEED_JOINT_DEFAULT)
        # MOVE L factor: multiplier applied to Joint speed for all
        # cartesian moves (positioning + drawing). 0.4 = MOVE L runs at
        # 40% of Joint speed. Lower it if MOVE L feels jerky.
        self.var_movel_factor = tk.DoubleVar(value=0.4)
        # 分割幅 (mm): 0 = 分割なし(スムーズだが SDK 速度に依存して速い)
        # 5-20: 移動を細切れにする(視覚的に遅くなるがカクカク)。
        # SDK の MOVE L 速度パラメータがあまり効かないため、 ユーザが視覚的に
        # 遅くしたいときの代替手段として用意。 デフォルト 0 (なし) で
        # 既存のスムーズな挙動を維持。
        self.var_chunk_mm = tk.IntVar(value=0)
        # 位置決め(中央へ移動 / リーチ確認 / B4 / nudge)を MOVE J モードで
        # 実行するかどうか。 SDK の MOVE L は速度パラメータが効かないが、
        self.var_sampling_interval = tk.IntVar(value=10)  # B2 default 10mm
        # Section 5 (full_dev): 生成画像描画
        self.var_strokes_json_path = tk.StringVar(value="")
        # var_strokes_live は 2026-05-27 に削除。 ストローク描画は常に実機。
        # mock 確認は 「プレビュー」 ボタンで Toplevel 表示に統一。
        self.var_strokes_max = tk.IntVar(value=0)  # 0 = 全部
        self.var_strokes_interpoint_ms = tk.IntVar(value=30)  # 30ms
        self.var_xoff = tk.DoubleVar(value=self._xoff_default)
        self.var_cy = tk.DoubleVar(value=center_y_def)
        self.var_cz = tk.DoubleVar(value=center_z_def)
        self.var_side = tk.DoubleVar(value=SQUARE_SIDE_DEFAULT)
        self.var_dy = [tk.DoubleVar(value=0.0) for _ in range(4)]
        self.var_dz = [tk.DoubleVar(value=0.0) for _ in range(4)]

        self._build_ui()
        self._bind_keys()
        if loaded:
            src = loaded.get("source_path", "?")
            sv = loaded.get("schema_version")
            sv_tag = f"v{sv}" if sv is not None else "v?"
            self.log(f"Loaded defaults ({sv_tag}) from {src}: contact_x="
                     f"{contact_x_def:.1f}, center Y={center_y_def:.1f} "
                     f"Z={center_z_def:.1f}, xoff={xoff_def:+.2f}")
        self._refresh_buttons()
        self._schedule_status_poll()
        self._poll_can()  # also self-reschedules every 2s

    # ------------------------------------------------------------------
    # calibration-yaml-driven defaults
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Capture/Ready pose (Task B) -- panel_frame.yaml integration
    # ------------------------------------------------------------------
    _PANEL_YAML_PATH = (
        "/home/jizaiedev2026/draw_piper/calibration/panel_frame.yaml")

    def _load_ready_pose_from_panel_yaml(self):
        """Read panel.ready_pose_deg from panel_frame.yaml.

        Returns: tuple of 6 float joint angles (deg), or None if the file
        doesn't exist / has no ready_pose_deg / value is malformed.
        """
        try:
            with open(self._PANEL_YAML_PATH) as f:
                data = yaml.safe_load(f) or {}
        except FileNotFoundError:
            return None
        except Exception:
            return None
        panel = data.get("panel") or {}
        rp = panel.get("ready_pose_deg")
        if not (isinstance(rp, list) and len(rp) == 6):
            return None
        try:
            return tuple(float(v) for v in rp)
        except Exception:
            return None

    def _save_ready_pose_to_panel_yaml(self, joints_deg):
        """Update panel.ready_pose_deg in panel_frame.yaml.

        Preserves all other top-level keys (e.g. phase_a_calibration) and
        all other panel: keys. Creates the file/structure if missing.
        Raises on I/O errors.
        """
        if len(joints_deg) != 6:
            raise ValueError(
                f"joints_deg must have 6 elements, got {len(joints_deg)}")
        path = self._PANEL_YAML_PATH
        try:
            with open(path) as f:
                data = yaml.safe_load(f) or {}
        except FileNotFoundError:
            data = {}
        panel = data.get("panel")
        if not isinstance(panel, dict):
            panel = {}
        panel["ready_pose_deg"] = [round(float(v), 4) for v in joints_deg]
        data["panel"] = panel
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w") as f:
            yaml.safe_dump(data, f, sort_keys=False,
                           default_flow_style=False)

    def _active_ready_pose(self):
        """Return the joint-angle tuple to use as 'ready/capture pose'.

        Calibrated pose if recorded, else the built-in default.
        """
        if self.calibrated_ready_pose is not None:
            return tuple(self.calibrated_ready_pose)
        return tuple(READY_POSE_V2_DEG)

    def _load_calib_defaults(self):
        """Read canvas_calibration.yaml via canvas_calibration_io.read().

        Supports v1 (M10), v2 (M11), v3 (M12+) yaml schemas via the
        shared IO module's auto-detection. Returns {} silently if file
        is missing or malformed (GUI falls back to hard-coded defaults).
        """
        try:
            parsed = read_calibration(OUTPUT_YAML)
        except FileNotFoundError:
            return {}
        except Exception:
            return {}
        out = {"source_path": parsed.get("source_path", OUTPUT_YAML),
               "schema_version": parsed.get("schema_version")}
        if parsed.get("center_y_mm") is not None:
            out["center_y_mm"] = float(parsed["center_y_mm"])
        if parsed.get("center_z_mm") is not None:
            out["center_z_mm"] = float(parsed["center_z_mm"])
        if parsed.get("contact_x_mm") is not None:
            out["contact_x_mm"] = float(parsed["contact_x_mm"])
        # X 押し付け補正 (中央調整 保存) を取り込む
        raw = parsed.get("raw") or {}
        computed = raw.get("whiteboard_computed") or {}
        if computed.get("contact_x_offset_mm") is not None:
            try:
                out["xoff_mm"] = float(computed["contact_x_offset_mm"])
            except Exception:
                pass
        return out

    # ------------------------------------------------------------------
    # UI layout
    # ------------------------------------------------------------------
    def _build_ui(self):
        # ---- ステータスバー (上部、 横幅いっぱい) ----
        status_frame = ttk.LabelFrame(self.root, text="📊 ステータス", padding=6)
        status_frame.pack(fill=tk.X, padx=6, pady=4)
        ttk.Button(status_frame, text="GUI 終了",
            command=self.on_quit, width=10
        ).pack(side=tk.RIGHT, padx=4)
        ttk.Button(status_frame, text="GUI 再起動",
            command=self.on_restart_gui, width=12
        ).pack(side=tk.RIGHT, padx=4)
        # log の絵文字 ON/OFF (環境変数 WALL_GUI_NO_EMOJI=1 でも OFF 可)
        self.var_use_emoji = tk.BooleanVar(value=self._use_emoji)
        ttk.Checkbutton(status_frame, text="log に絵文字",
            variable=self.var_use_emoji,
            command=self._on_toggle_emoji
        ).pack(side=tk.RIGHT, padx=4)
        self.lbl_can = ttk.Label(status_frame, text="CAN: ?",
                                 foreground="gray")
        self.lbl_can.pack(side=tk.LEFT, padx=4)
        self.lbl_connected = ttk.Label(status_frame, text="● 未接続",
                                       foreground="gray")
        self.lbl_connected.pack(side=tk.LEFT, padx=4)
        self.lbl_master = ttk.Label(status_frame, text="(通常)",
                                    foreground="gray")
        self.lbl_master.pack(side=tk.LEFT, padx=4)
        self.lbl_powercycle = ttk.Label(status_frame, text="",
                                        foreground="red")
        self.lbl_powercycle.pack(side=tk.LEFT, padx=4)
        self.lbl_joints = ttk.Label(status_frame, text="関節: -",
                                    font=("Monaco", 10), width=70,
                                    anchor=tk.W)
        self.lbl_joints.pack(side=tk.LEFT, padx=10, fill=tk.X, expand=False)

        # ---- 接続セクション: タブ外、 常時表示 (上部固定) ----
        # ヘルプボタンは接続セクションの右端に配置
        conn_frame = ttk.LabelFrame(self.root, text="🔌 接続", padding=6)
        conn_frame.pack(fill=tk.X, padx=6, pady=2)
        self._build_conn_section(conn_frame)

        # ---- 縦分割: 上=操作タブ / 下=ログ (PanedWindow で
        # ドラッグ調整可) ----
        main_paned = ttk.PanedWindow(self.root, orient=tk.VERTICAL)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)
        left_frame = ttk.Frame(main_paned)
        right_frame = ttk.Frame(main_paned)
        main_paned.add(left_frame, weight=3)
        main_paned.add(right_frame, weight=2)

        # ---- タブ: 4 ページに分割 (接続はタブ外、 上部固定) ----
        nb = ttk.Notebook(left_frame)
        nb.pack(fill=tk.BOTH, expand=True, padx=2, pady=(4, 2))
        tab_reach = ttk.Frame(nb)
        tab_calib = ttk.Frame(nb)
        tab_center = ttk.Frame(nb)
        tab_draw = ttk.Frame(nb)
        nb.add(tab_reach, text="① リーチ確認")
        nb.add(tab_calib, text="② キャリブ")
        nb.add(tab_center, text="③ 位置調整")
        nb.add(tab_draw, text="④ 描画")

        # ---- リーチ確認 ----
        probe_frame = ttk.LabelFrame(tab_reach,
            text="🔍 リーチ確認 (キャリブ前に限界点へ移動してマーク)",
            padding=6)
        probe_frame.pack(fill=tk.X, padx=2, pady=2)
        probe_row1 = ttk.Frame(probe_frame)
        probe_row1.pack(fill=tk.X)
        # 日本語コーナー名マップ
        corner_jp = {"TL": "左上", "TR": "右上", "BR": "右下", "BL": "左下"}
        self.btn_probe_corners = {}
        for name, y, z in self.PROBE_CORNERS:
            jp = corner_jp.get(name, name)
            btn = ttk.Button(probe_row1,
                text=f"{jp} ({name}) Y={y:+.0f} Z={z:+.0f}",
                command=lambda n=name: self.on_probe_corner(n),
                width=22)
            btn.pack(side=tk.LEFT, padx=2)
            self.btn_probe_corners[name] = btn
        probe_row2 = ttk.Frame(probe_frame)
        probe_row2.pack(fill=tk.X, pady=(4, 0))
        # 「ペン下げ」 は接触するので 薄橙
        self.btn_probe_pen_down = tk.Button(probe_row2,
            text="✏ ペン下げ (印を付ける)",
            command=self.on_probe_pen_down, width=22,
            bg="#fed", fg="#950",
            activebackground="#fda", activeforeground="#830",
            font=("Monaco", 10, "bold"))
        self.btn_probe_pen_down.pack(side=tk.LEFT, padx=2)
        # ペン上げは安全方向だが motion なので 薄橙で軽くハイライト
        self.btn_probe_pen_up = tk.Button(probe_row2, text="⬆ ペン上げ",
            command=self.on_probe_pen_up, width=12,
            bg="#fed", fg="#950",
            activebackground="#fda", activeforeground="#830",
            font=("Monaco", 10, "bold"))
        self.btn_probe_pen_up.pack(side=tk.LEFT, padx=2)
        # IK 候補選択モード
        self.var_probe_ik_select = tk.BooleanVar(value=False)
        ttk.Checkbutton(probe_row2,
            text="IK 候補を選んで移動 (モーター 5 が内向きで困る時)",
            variable=self.var_probe_ik_select
        ).pack(side=tk.LEFT, padx=(12, 4))

        # ---- 2. キャンバスキャリブ (B1 四隅 + B2 外周 + B3 対角 + B5 内側) ----
        dt_frame = ttk.LabelFrame(tab_calib,
            text="📐 キャンバスキャリブレーション "
                 "(四隅 → 外周 → 対角線 → 任意 内側ジグザグ)",
            padding=6)
        dt_frame.pack(fill=tk.X, padx=2, pady=2)
        row_a = ttk.Frame(dt_frame)
        row_a.pack(fill=tk.X)
        # 「ティーチ開始」 は master mode に入り GUI が不安定化、 終了時に
        # 電源 cycle が必要になる最危険操作。 赤背景で警告。
        self.btn_start_drag = tk.Button(row_a,
            text="⚠ ティーチ開始 (マスターモード)",
            command=self.on_start_drag, width=36,
            bg="#fdd", fg="#a00",
            activebackground="#faa", activeforeground="#800",
            font=("Monaco", 10, "bold"))
        self.btn_start_drag.pack(side=tk.LEFT, padx=2)
        self.lbl_phase = ttk.Label(dt_frame,
            text="(ティーチ未開始)", font=("Monaco", 10),
            foreground="gray")
        self.lbl_phase.pack(anchor=tk.W, padx=2, pady=(4, 0))
        # 四隅手動記録 + 保存/中止
        row_b = ttk.Frame(dt_frame)
        row_b.pack(fill=tk.X, pady=4)
        self.btn_record = ttk.Button(row_b, text="現在地を記録 [Enter]",
            command=self.on_record_point, width=22)
        self.btn_record.pack(side=tk.LEFT, padx=2)
        attach_tooltip(self.btn_record, self._motor_diagram_text(),
                       wrap=520)
        self.btn_undo = ttk.Button(row_b, text="1点取消",
            command=self.on_undo_point, width=10)
        self.btn_undo.pack(side=tk.LEFT, padx=2)
        # モーター配置 ? ボタン (専用 popup)
        btn_motor_help = ttk.Button(row_b, text="? モーター配置",
            command=self._show_motor_diagram, width=14)
        btn_motor_help.pack(side=tk.LEFT, padx=8)
        attach_tooltip(btn_motor_help,
                       "クリックで モーター番号 ↔ アーム関節 の対応図を "
                       "別ウィンドウで表示")
        # 保存系は緑、 中止系は赤
        self.btn_save_drag = tk.Button(row_b,
            text="✅ 保存して終了 (マスター解除)",
            command=self.on_save_drag, width=28,
            bg="#dfd", fg="#060",
            activebackground="#afa", activeforeground="#040",
            font=("Monaco", 10, "bold"))
        self.btn_save_drag.pack(side=tk.RIGHT, padx=2)
        self.btn_abort_drag = tk.Button(row_b,
            text="■ 中止 (保存しない)",
            command=self.on_abort_drag, width=18,
            bg="#fcc", fg="#800",
            activebackground="#f99", activeforeground="#600",
            font=("Monaco", 10, "bold"))
        self.btn_abort_drag.pack(side=tk.RIGHT, padx=2)

        # 個別やり直し行: 既に B1 完了 + B2/B3/B5 やった後で、 特定の
        # 隅だけが関節限界張付などで NG だったケース用。
        row_redo = ttk.Frame(dt_frame)
        row_redo.pack(fill=tk.X, pady=(2, 0))
        ttk.Label(row_redo, text="個別やり直し:",
                  font=("Monaco", 9)).pack(side=tk.LEFT, padx=(0, 4))
        self.btn_redo_corner = {}
        corner_jp_short = {"tl": "左上", "tr": "右上",
                            "br": "右下", "bl": "左下"}
        for k in ("tl", "tr", "br", "bl"):
            b = ttk.Button(row_redo,
                text=f"{corner_jp_short[k]} を再記録",
                command=lambda key=k: self.on_redo_corner(key),
                width=14)
            b.pack(side=tk.LEFT, padx=2)
            self.btn_redo_corner[k] = b
        # B2 外周トレース
        row_c = ttk.Frame(dt_frame)
        row_c.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(row_c, text="サンプリング間隔 (mm):",
                  font=("Monaco", 9)).pack(side=tk.LEFT, padx=(0, 4))
        make_spinbox(row_c, self.var_sampling_interval, 2, 50, 1, width=4,
                     fmt="%.0f").pack(side=tk.LEFT, padx=(0, 8))
        self.btn_b2_start = tk.Button(row_c, text="▶ B2 外周トレース開始",
            bg="#fea", fg="#940",
            activebackground="#fc7", activeforeground="#820",
            font=("Monaco", 10, "bold"),
            command=self.on_b2_start_trace, width=20)
        self.btn_b2_start.pack(side=tk.LEFT, padx=2)
        self.btn_trace_stop = ttk.Button(row_c, text="トレース停止",
            command=self.on_trace_stop, width=14)
        self.btn_trace_stop.pack(side=tk.LEFT, padx=2)
        self.lbl_b2_count = ttk.Label(row_c, text="外周: 0 点",
                                       font=("Monaco", 9), foreground="gray")
        self.lbl_b2_count.pack(side=tk.LEFT, padx=(8, 0))
        # B3 対角線
        row_d = ttk.Frame(dt_frame)
        row_d.pack(fill=tk.X, pady=(2, 0))
        ttk.Label(row_d, text="対角線:",
                  font=("Monaco", 9)).pack(side=tk.LEFT, padx=(0, 4))
        self.btn_b3_tlbr_start = ttk.Button(row_d,
            text="左上→右下 開始",
            command=self.on_b3_tlbr_start, width=16)
        self.btn_b3_tlbr_start.pack(side=tk.LEFT, padx=2)
        self.btn_b3_trbl_start = ttk.Button(row_d,
            text="右上→左下 開始",
            command=self.on_b3_trbl_start, width=16)
        self.btn_b3_trbl_start.pack(side=tk.LEFT, padx=2)
        self.lbl_b3_counts = ttk.Label(row_d,
            text="左上→右下:0  右上→左下:0  (交点中心:-)",
            font=("Monaco", 9), foreground="gray")
        self.lbl_b3_counts.pack(side=tk.LEFT, padx=(8, 0))
        # B5 (任意) 内側ジグザグ
        row_e = ttk.Frame(dt_frame)
        row_e.pack(fill=tk.X, pady=(2, 0))
        ttk.Label(row_e, text="内側ジグザグ (任意):",
                  font=("Monaco", 9)).pack(side=tk.LEFT, padx=(0, 4))
        self.btn_b5_start = ttk.Button(row_e,
            text="内側ジグザグ 開始",
            command=self.on_b5_start_trace, width=20)
        self.btn_b5_start.pack(side=tk.LEFT, padx=2)
        self.lbl_b5_count = ttk.Label(row_e, text="内側: 0 点",
                                       font=("Monaco", 9), foreground="gray")
        self.lbl_b5_count.pack(side=tk.LEFT, padx=(8, 0))

        # 撮影位置 (Task B): ready_pose = カメラがホワイトボード全体を見る位置
        row_f = ttk.Frame(dt_frame)
        row_f.pack(fill=tk.X, pady=(2, 0))
        ttk.Label(row_f, text="撮影/ホーム位置:",
                  font=("Monaco", 9)).pack(side=tk.LEFT, padx=(0, 4))
        self.btn_record_ready = ttk.Button(row_f,
            text="現在の姿勢を撮影/ホームに登録",
            command=self.on_record_ready_pose, width=30)
        self.btn_record_ready.pack(side=tk.LEFT, padx=2)
        self.btn_clear_ready = ttk.Button(row_f,
            text="クリア (デフォルトに戻す)",
            command=self.on_clear_ready_pose, width=24)
        self.btn_clear_ready.pack(side=tk.LEFT, padx=2)
        self.lbl_ready_pose = ttk.Label(row_f,
            text="(未読込)", font=("Monaco", 9), foreground="gray")
        self.lbl_ready_pose.pack(side=tk.LEFT, padx=(8, 0))

        # ---- 3. 中央調整 (キャンバス中央で X 押し付け量を確定) ----
        tune_frame = ttk.LabelFrame(tab_center,
            text="✏ 中央押し付け 調整 (キャンバス中央で X 押し付け量を確定)",
            padding=8)
        tune_frame.pack(fill=tk.X, padx=2, pady=2)
        # 上段: 大きめ spinbox を 3 つ並べる (▲▼ クリックで即アーム移動)
        tune_top = ttk.Frame(tune_frame)
        tune_top.pack(fill=tk.X)
        big_font = ("Monaco", 16)
        ttk.Label(tune_top, text="X 押し付け補正 (mm)",
                  font=("Monaco", 11)).grid(row=0, column=0, padx=4, sticky="w")
        ttk.Label(tune_top, text="中央 Y (mm)",
                  font=("Monaco", 11)).grid(row=0, column=1, padx=4, sticky="w")
        ttk.Label(tune_top, text="中央 Z (mm)",
                  font=("Monaco", 11)).grid(row=0, column=2, padx=4, sticky="w")
        make_spinbox(tune_top, self.var_xoff, -20.0, 20.0, 0.5,
                     width=8, font=big_font,
                     command=self._on_tune_x_spin
                     ).grid(row=1, column=0, padx=4, pady=2)
        make_spinbox(tune_top, self.var_cy, -200.0, 200.0, 0.5,
                     width=8, font=big_font,
                     command=self._on_tune_y_spin
                     ).grid(row=1, column=1, padx=4, pady=2)
        make_spinbox(tune_top, self.var_cz, 100.0, 500.0, 0.5,
                     width=8, font=big_font,
                     command=self._on_tune_z_spin
                     ).grid(row=1, column=2, padx=4, pady=2)

        # 下段: ボタン
        tune_bot = ttk.Frame(tune_frame)
        tune_bot.pack(fill=tk.X, pady=(8, 0))
        self.btn_go_center = ttk.Button(tune_bot,
            text="中央へ移動 (ペン上げ) -- 初回",
            command=self.on_go_center, width=28)
        self.btn_go_center.pack(side=tk.LEFT, padx=2)
        # tune section の「ペン下げ」 も接触なので 薄橙
        self.btn_tune_pen_down = tk.Button(tune_bot, text="✏ ペン下げ",
            command=self.on_tune_pen_down, width=12,
            bg="#fed", fg="#950",
            activebackground="#fda", activeforeground="#830",
            font=("Monaco", 10, "bold"))
        self.btn_tune_pen_down.pack(side=tk.LEFT, padx=(8, 4))
        self.btn_lift_pen = tk.Button(tune_bot, text="⬆ ペン上げ",
            bg="#fed", fg="#950",
            activebackground="#fda", activeforeground="#830",
            font=("Monaco", 10, "bold"),
            command=self.on_lift_pen, width=10)
        self.btn_lift_pen.pack(side=tk.LEFT, padx=(0, 12))
        # 中央調整値 (X 押し付け補正 / 中央 Y / 中央 Z) を yaml に保存
        self.btn_save_tune = ttk.Button(tune_bot,
            text="この値で保存",
            command=self.on_save_tune_values, width=14)
        self.btn_save_tune.pack(side=tk.LEFT, padx=(8, 0))
        attach_tooltip(self.btn_save_tune,
            "現在の X 押し付け補正 / 中央 Y / 中央 Z を "
            "canvas_calibration.yaml に保存。 次回 GUI 起動時に "
            "自動で読み込まれる。", wrap=320)
        # 下位互換用 (空リスト)
        self.btn_nudge_x = []

        # ---- 四つ角微調整 (B4 中央調整の代わり) ----
        ca_frame = ttk.LabelFrame(tab_center,
            text="🔧 四つ角微調整 (各 隅へ移動 → Y/Z spinbox で位置補正 → "
                 "確定で yaml 更新)", padding=6)
        ca_frame.pack(fill=tk.X, padx=2, pady=2)
        # 4 隅選択ボタン
        ca_row1 = ttk.Frame(ca_frame)
        ca_row1.pack(fill=tk.X)
        ttk.Label(ca_row1, text="移動先:",
                  font=("Monaco", 10)).pack(side=tk.LEFT, padx=(0, 6))
        self.btn_corner_adj = {}
        for code, jp in [("tl", "左上"), ("tr", "右上"),
                          ("br", "右下"), ("bl", "左下")]:
            b = ttk.Button(ca_row1, text=f"{jp} ({code.upper()})",
                command=lambda c=code: self.on_corner_adj_goto(c),
                width=12)
            b.pack(side=tk.LEFT, padx=2)
            self.btn_corner_adj[code] = b
        self.lbl_corner_adj_status = ttk.Label(ca_row1,
            text="待機中 (隅ボタンで移動)",
            font=("Monaco", 10), foreground="gray")
        self.lbl_corner_adj_status.pack(side=tk.LEFT, padx=(12, 0))

        # Y/Z spinbox 大きめ + 確定ボタン
        ca_row2 = ttk.Frame(ca_frame)
        ca_row2.pack(fill=tk.X, pady=(8, 0))
        self.var_corner_adj_y = tk.DoubleVar(value=0.0)
        self.var_corner_adj_z = tk.DoubleVar(value=0.0)
        ttk.Label(ca_row2, text="Y (mm)",
                  font=("Monaco", 11)).pack(side=tk.LEFT, padx=4)
        sb_ca_y = make_spinbox(ca_row2, self.var_corner_adj_y, -300, 300, 0.5,
                     width=8, font=("Monaco", 14),
                     command=self._on_corner_adj_spin)
        sb_ca_y.pack(side=tk.LEFT, padx=(0, 12))
        ttk.Label(ca_row2, text="Z (mm)",
                  font=("Monaco", 11)).pack(side=tk.LEFT, padx=4)
        sb_ca_z = make_spinbox(ca_row2, self.var_corner_adj_z, 100, 500, 0.5,
                     width=8, font=("Monaco", 14),
                     command=self._on_corner_adj_spin)
        sb_ca_z.pack(side=tk.LEFT, padx=(0, 12))
        self.btn_corner_adj_confirm = ttk.Button(ca_row2,
            text="この位置で yaml 更新",
            command=self.on_corner_adj_confirm, width=22)
        self.btn_corner_adj_confirm.pack(side=tk.LEFT, padx=4)
        self.btn_corner_adj_cancel = ttk.Button(ca_row2,
            text="キャンセル (ペン上げ)",
            command=self.on_corner_adj_cancel, width=20)
        self.btn_corner_adj_cancel.pack(side=tk.LEFT, padx=4)
        # 四つ角微調整の state
        self.corner_adj_key = None  # "tl"/"tr"/"br"/"bl" or None
        self.corner_adj_warm_q = None

        # ---- 4. テスト描画 ----
        draw_frame = ttk.LabelFrame(tab_draw,
            text="🧪 テスト描画 (図形)", padding=6)
        draw_frame.pack(fill=tk.X, padx=2, pady=2)

        side_row = ttk.Frame(draw_frame)
        side_row.pack(fill=tk.X, pady=2)
        lbl_side = ttk.Label(side_row, text="辺の長さ (mm):")
        lbl_side.pack(side=tk.LEFT)
        attach_tooltip(lbl_side,
            "テスト描画 (正方形 / 中心に丸 / 中心に三角) のサイズ。\n"
            "正方形/三角は 「辺」、 丸は 「直径」 として使われる。\n"
            "中央 Y/Z を中心に左右上下に side/2 だけ広がる。")
        sb_side = make_spinbox(side_row, self.var_side, 5.0, 200.0, 1.0,
                     width=7)
        sb_side.pack(side=tk.LEFT, padx=(2, 12))
        attach_tooltip(sb_side, "テスト描画 (正方形/丸/三角) のサイズ")
        ttk.Button(side_row, text="四隅補正をゼロに",
            command=self.on_reset_corners, width=20
        ).pack(side=tk.RIGHT, padx=4)

        cor_frame = ttk.LabelFrame(draw_frame,
            text="四隅補正 ΔY ΔZ (mm; 正方形の各隅を中央 Y/Z 基準で個別微調整)",
            padding=4)
        attach_tooltip(cor_frame,
            "「中心に正方形」 で描く時、 各隅 (C1/C2/C3/C4) の Y/Z 位置を "
            "個別に補正できる。 通常はすべて 0 のままで OK。\n"
            "用途: キャンバスが完全な矩形でない時の歪み補正、 "
            "ペン圧の偏り検証、 等。 円 / 三角 / 隅合わせ正方形 には "
            "この補正は使われない。")
        cor_frame.pack(fill=tk.X, pady=2)
        # コーナーは描画順 C1=左下→C2=右下→C3=右上→C4=左上 (反時計回り)
        corner_labels = ["C1 左下 (-Y,-Z)", "C2 右下 (+Y,-Z)",
                         "C3 右上 (+Y,+Z)", "C4 左上 (-Y,+Z)"]
        for i, lbl in enumerate(corner_labels):
            sub = ttk.Frame(cor_frame)
            sub.pack(side=tk.LEFT, padx=8, pady=2)
            ttk.Label(sub, text=lbl, font=("Monaco", 9)).pack(anchor=tk.W)
            r = ttk.Frame(sub)
            r.pack()
            ttk.Label(r, text="ΔY").pack(side=tk.LEFT)
            make_spinbox(r, self.var_dy[i], -50.0, 50.0, 0.5,
                         width=6).pack(side=tk.LEFT, padx=(0, 4))
            ttk.Label(r, text="ΔZ").pack(side=tk.LEFT)
            make_spinbox(r, self.var_dz[i], -50.0, 50.0, 0.5,
                         width=6).pack(side=tk.LEFT)

        action_row = ttk.Frame(draw_frame)
        action_row.pack(fill=tk.X, pady=4)
        # 「描画開始」 は実機動作するので 橙背景で視認性↑
        self.btn_draw = tk.Button(action_row,
            text="▶ 中心に正方形 (描画開始)",
            command=self.on_draw_square, width=22,
            bg="#fea", fg="#940",
            activebackground="#fc7", activeforeground="#820",
            font=("Monaco", 10, "bold"))
        self.btn_draw.pack(side=tk.LEFT, padx=2)
        ttk.Label(action_row, font=("Monaco", 9), foreground="#555",
            text="C1 → C2 → C3 → C4 → C1"
        ).pack(side=tk.LEFT, padx=10)

        # 図形バリエーション (中心配置) — 正方形と同じ橙でハイライト
        shape_row = ttk.Frame(draw_frame)
        shape_row.pack(fill=tk.X, pady=(2, 0))
        self.btn_circle = tk.Button(shape_row, text="▶ 中心に丸",
            command=self.on_draw_circle, width=14,
            bg="#fea", fg="#940",
            activebackground="#fc7", activeforeground="#820",
            font=("Monaco", 10, "bold"))
        self.btn_circle.pack(side=tk.LEFT, padx=2)
        self.btn_triangle = tk.Button(shape_row, text="▶ 中心に三角",
            command=self.on_draw_triangle, width=14,
            bg="#fea", fg="#940",
            activebackground="#fc7", activeforeground="#820",
            font=("Monaco", 10, "bold"))
        self.btn_triangle.pack(side=tk.LEFT, padx=2)

        # 四隅合わせ正方形 (キャンバスの各隅に square の対応する角を一致させる)
        corner_align_row = ttk.Frame(draw_frame)
        corner_align_row.pack(fill=tk.X, pady=(2, 0))
        ttk.Label(corner_align_row, text="キャンバス隅合わせ:",
                  font=("Monaco", 9)).pack(side=tk.LEFT, padx=(0, 4))
        self.btn_corner_align = {}
        for code, jp in [("TL", "左上"), ("TR", "右上"),
                          ("BR", "右下"), ("BL", "左下")]:
            b = ttk.Button(corner_align_row,
                text=f"{jp} ({code})",
                command=lambda c=code: self.on_draw_square_at_corner(c),
                width=12)
            b.pack(side=tk.LEFT, padx=1)
            self.btn_corner_align[code] = b

        # ---- 5. 生成画像描画 ----
        strokes_frame = ttk.LabelFrame(tab_draw,
            text="🎨 生成画像描画 (strokes.json を実機描画)", padding=6)
        strokes_frame.pack(fill=tk.X, padx=2, pady=2)
        # 1 行目: JSON ファイル選択 + プレビュー
        sf_r1 = ttk.Frame(strokes_frame)
        sf_r1.pack(fill=tk.X)
        ttk.Label(sf_r1, text="JSON:").pack(side=tk.LEFT, padx=(0, 4))
        ttk.Entry(sf_r1, textvariable=self.var_strokes_json_path,
                  width=36).pack(side=tk.LEFT, padx=(0, 4),
                                  fill=tk.X, expand=True)
        ttk.Button(sf_r1, text="選択...",
            command=self.on_strokes_select_file, width=8
        ).pack(side=tk.LEFT, padx=2)
        ttk.Button(sf_r1, text="プレビュー",
            command=self.on_strokes_preview, width=10
        ).pack(side=tk.LEFT, padx=2)
        # 2 行目: 設定 + 実行ボタン (1 行に集約して見切れ防止)
        sf_r2 = ttk.Frame(strokes_frame)
        sf_r2.pack(fill=tk.X, pady=(4, 0))
        self.btn_panel_convert = ttk.Button(sf_r2,
            text="panel_frame.yaml を更新",
            command=self.on_panel_convert, width=22)
        self.btn_panel_convert.pack(side=tk.LEFT, padx=2)
        attach_tooltip(self.btn_panel_convert,
            "canvas_calibration.yaml の 4 隅から panel_frame.yaml の "
            "panel: ブロック (origin / u_axis / v_axis / size_mm) を "
            "再生成。 ストロークの (u, v) を 実機 base 座標に変換するのに "
            "必要。 キャリブやり直し時 / 初回 のみ実行で OK。", wrap=400)
        ttk.Label(sf_r2, text="最大本数:").pack(side=tk.LEFT,
                                                padx=(8, 4))
        make_spinbox(sf_r2, self.var_strokes_max, 0, 999, 1, width=4,
                     fmt="%.0f").pack(side=tk.LEFT, padx=(0, 8))
        # 「描画開始」 は実機動作するので 橙背景で視認性↑
        self.btn_strokes_draw = tk.Button(sf_r2,
            text="▶ 描画開始", command=self.on_strokes_draw, width=12,
            bg="#fea", fg="#940",
            activebackground="#fc7", activeforeground="#820",
            font=("Monaco", 10, "bold"))
        self.btn_strokes_draw.pack(side=tk.LEFT, padx=2)
        self.btn_strokes_resume = ttk.Button(sf_r2,
            text="再開", command=self.on_strokes_resume, width=8,
            state=tk.DISABLED)
        self.btn_strokes_resume.pack(side=tk.LEFT, padx=2)
        # 「中止」 は緊急停止なので 赤背景
        self.btn_strokes_abort = tk.Button(sf_r2,
            text="■ 中止", command=self.on_strokes_abort, width=8,
            bg="#fcc", fg="#800",
            activebackground="#f99", activeforeground="#600",
            font=("Monaco", 10, "bold"))
        self.btn_strokes_abort.pack(side=tk.LEFT, padx=2)
        # Frida Smooth Draw (PR #2): 多 stroke 最適化 (TSP + 曲率速度 +
        # look-ahead) 経路で別 Robot インスタンス経由で描画する
        self.btn_strokes_draw_smooth = tk.Button(sf_r2,
            text="✨ Frida Smooth", command=self.on_strokes_draw_smooth,
            width=14, bg="#fea", fg="#940",
            activebackground="#fc7", activeforeground="#820",
            font=("Monaco", 10, "bold"))
        self.btn_strokes_draw_smooth.pack(side=tk.LEFT, padx=(8, 2))
        self.lbl_strokes_progress = ttk.Label(sf_r2,
            text="進捗: -", font=("Monaco", 9), foreground="gray")
        self.lbl_strokes_progress.pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(sf_r2, text="📊 進捗プレビュー",
            command=self.on_strokes_live_preview, width=18
        ).pack(side=tk.RIGHT, padx=2)

        # ---- 右ペイン: ログ (大きく取る) ----
        log_frame = ttk.LabelFrame(right_frame, text="📋 ログ", padding=6)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        self.log_text = scrolledtext.ScrolledText(log_frame,
                                                  font=("Monaco", 9),
                                                  wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log("GUI 準備完了。 CAN が UP でアームに電源が入って "
                 "いることを確認してから 「接続」 を押してください。")

    def _bind_keys(self):
        # Enter records a point if drag-teach is in recording mode
        self.root.bind("<Return>", self._on_enter_key)
        self.root.bind("<KP_Enter>", self._on_enter_key)

    def _on_enter_key(self, event=None):
        # Don't fire if user is typing in a Spinbox/Entry (those eat Enter)
        widget = self.root.focus_get()
        if widget and widget.winfo_class() in ("Spinbox", "Entry", "TEntry",
                                               "Text"):
            return
        if str(self.btn_record["state"]) == "normal":
            self.on_record_point()

    # ------------------------------------------------------------------
    # logging
    # ------------------------------------------------------------------
    def log(self, msg):
        ts = time.strftime("%H:%M:%S")
        # 絵文字 ON/OFF (環境変数 WALL_GUI_NO_EMOJI=1 で OFF、
        # default ON)。 OFF 時は log メッセージから既知の絵文字を strip
        # して、 font 無し環境や機械処理用途に対応
        if getattr(self, "_use_emoji", None) is False:
            msg = self._strip_emoji(msg)
        self.log_text.insert(tk.END, f"[{ts}] {msg}\n")
        self.log_text.see(tk.END)

    def log_safe(self, msg):
        self.root.after(0, lambda: self.log(msg))

    def _on_toggle_emoji(self):
        """log の絵文字 ON/OFF を切り替え (Checkbutton から呼ばれる)。"""
        self._use_emoji = bool(self.var_use_emoji.get())
        state = "ON" if self._use_emoji else "OFF"
        self.log(f"log の絵文字を {state} に切り替えました")

    @staticmethod
    def _strip_emoji(s):
        """log で使う既知の絵文字を strip。 全 unicode 絵文字対応ではない。"""
        for ch in ("✓", "✅", "❌", "⚠", "ℹ", "▶", "■", "✏", "⬆",
                    "🏠", "📦", "🖋", "🔴", "🟢", "🟡", "🟠",
                    "📖", "📊", "🔒", "🔓", "🔄", "⚙", "✨",
                    "⛔"):
            s = s.replace(ch, "")
        # 連続スペース整理
        while "  " in s:
            s = s.replace("  ", " ")
        return s.strip()

    # ------------------------------------------------------------------
    # button-state refresh
    # ------------------------------------------------------------------
    def _refresh_buttons(self):
        no_master = not self.in_master
        no_busy = not self.busy
        no_restart = not self.gui_restart_required
        connected_idle = (self.connected and no_master and no_busy
                          and no_restart)

        self.btn_connect.config(state=tk.NORMAL if not self.connected
            and not self.busy and not self.gui_restart_required
            else tk.DISABLED)
        self.btn_disconnect.config(state=tk.NORMAL if self.connected
            and not self.busy else tk.DISABLED)
        # Recover Connection: usable any time we're not busy / restart-locked.
        self.btn_recover_conn.config(state=tk.NORMAL if (
            not self.busy and not self.gui_restart_required
        ) else tk.DISABLED)
        self.btn_recover.config(state=tk.NORMAL if connected_idle
            else tk.DISABLED)
        self.btn_storage.config(state=tk.NORMAL if connected_idle
            else tk.DISABLED)
        self.btn_pen_exchange.config(state=tk.NORMAL if connected_idle
            else tk.DISABLED)
        # グリッパー操作は接続中であれば使用可 (動作中でも OK、 短い CAN
        # コマンド 1 発のみで阻害しない)
        grip_state = tk.NORMAL if self.connected else tk.DISABLED
        self.btn_grip_strong.config(state=grip_state)
        self.btn_grip_release.config(state=grip_state)
        self.btn_grip_home.config(state=grip_state)
        probe_state = (tk.NORMAL if connected_idle
                       and not self.tune_x_active else tk.DISABLED)
        for btn in self.btn_probe_corners.values():
            btn.config(state=probe_state)
        # Pen Down/Up are only meaningful while a probe chain is active.
        pen_down_state = (tk.NORMAL if connected_idle
                          and self.probe_active and not self.probe_pen_down
                          else tk.DISABLED)
        self.btn_probe_pen_down.config(state=pen_down_state)
        pen_up_state = (tk.NORMAL if connected_idle
                        and self.probe_active and self.probe_pen_down
                        else tk.DISABLED)
        self.btn_probe_pen_up.config(state=pen_up_state)

        self.btn_start_drag.config(state=tk.NORMAL if connected_idle
            and not self.tune_x_active else tk.DISABLED)
        is_recording = self.in_master and self.listener is not None
        # v3 drag-teach phase logic
        n_corners = len(self.dt_corners)
        n_perimeter = len(self.dt_traces["perimeter"])
        n_tlbr = len(self.dt_traces["diagonal_tl_br"])
        n_trbl = len(self.dt_traces["diagonal_tr_bl"])
        trace_active = (self.sampling_thread is not None
                        and self.sampling_thread.is_alive())
        # Record button (B1 + 個別再記録モード)
        corner_jp = {"tl": "左上", "tr": "右上", "br": "右下", "bl": "左下"}
        if not is_recording:
            self.btn_record.config(text="現在地を記録 [Enter]",
                                    state=tk.DISABLED)
        elif self.redo_corner_key is not None:
            jp = corner_jp.get(self.redo_corner_key,
                                 self.redo_corner_key.upper())
            self.btn_record.config(
                text=f"⚠ {jp} を上書き再記録 [Enter]",
                state=tk.NORMAL)
        elif self.dt_phase == "corners":
            next_key = self.CORNER_ORDER[self.dt_corner_idx]
            self.btn_record.config(
                text=f"{corner_jp.get(next_key, next_key.upper())}を記録 "
                     "[Enter]",
                state=tk.NORMAL)
        else:
            self.btn_record.config(text="記録 (B1 完了済)",
                                    state=tk.DISABLED)
        can_undo = (is_recording and self.dt_phase == "corners"
                    and n_corners > 0 and self.redo_corner_key is None)
        self.btn_undo.config(
            state=tk.NORMAL if can_undo else tk.DISABLED)
        # 個別やり直しボタン: master mode 中 + その隅が既に記録済の時だけ
        for k in ("tl", "tr", "br", "bl"):
            btn = self.btn_redo_corner.get(k)
            if btn is None:
                continue
            can_redo = (is_recording
                        and k in self.dt_corners
                        and self.redo_corner_key is None
                        and not trace_active)
            btn.config(state=tk.NORMAL if can_redo else tk.DISABLED)
        # Trace-start buttons (B1 完了 + サンプリング中でない時に有効)
        can_start = (is_recording and n_corners == 4 and not trace_active)
        self.btn_b2_start.config(
            state=tk.NORMAL if can_start else tk.DISABLED,
            text=("外周なぞり開始" if n_perimeter == 0
                  else "外周なぞり再開"))
        self.btn_b3_tlbr_start.config(
            state=tk.NORMAL if can_start else tk.DISABLED,
            text=("左上→右下 開始" if n_tlbr == 0 else "左上→右下 再開"))
        self.btn_b3_trbl_start.config(
            state=tk.NORMAL if can_start else tk.DISABLED,
            text=("右上→左下 開始" if n_trbl == 0 else "右上→左下 再開"))
        n_surface = len(self.dt_traces["surface"])
        self.btn_b5_start.config(
            state=tk.NORMAL if can_start else tk.DISABLED,
            text=("内側ジグザグ 開始" if n_surface == 0
                  else "内側ジグザグ 再開"))
        # Stop button: enabled while any sampling thread is active.
        self.btn_trace_stop.config(
            state=tk.NORMAL if trace_active else tk.DISABLED)
        # Save: corners=4 AND not currently sampling.
        can_save = is_recording and n_corners == 4 and not trace_active
        self.btn_save_drag.config(
            state=tk.NORMAL if can_save else tk.DISABLED)
        self.btn_abort_drag.config(
            state=tk.NORMAL if is_recording else tk.DISABLED)
        # Trace count labels: show in-progress + accumulated for whichever
        # target is currently recording.
        if is_recording:
            in_progress_n = (self.sampling_thread.get_point_count()
                             if trace_active else 0)
            tgt = self.recording_target
            perim_disp = n_perimeter + (in_progress_n if tgt == "perimeter"
                                        else 0)
            tlbr_disp = n_tlbr + (in_progress_n if tgt == "diagonal_tl_br"
                                  else 0)
            trbl_disp = n_trbl + (in_progress_n if tgt == "diagonal_tr_bl"
                                  else 0)
            surf_disp = n_surface + (in_progress_n if tgt == "surface"
                                     else 0)
            perim_color = ("blue" if tgt == "perimeter" and trace_active
                           else ("green" if n_perimeter > 0 else "gray"))
            self.lbl_b2_count.config(
                text=f"外周: {perim_disp} 点", foreground=perim_color)
            diag_color = ("blue" if tgt in ("diagonal_tl_br",
                                            "diagonal_tr_bl")
                          and trace_active
                          else ("green" if (n_tlbr or n_trbl) else "gray"))
            cc = self._diagonal_intersection_yz()
            cc_str = (f"Y={cc[0]:.1f} Z={cc[1]:.1f}" if cc is not None
                      else "-")
            self.lbl_b3_counts.config(
                text=(f"左上→右下:{tlbr_disp}  右上→左下:{trbl_disp}  "
                      f"(交点中心: {cc_str})"),
                foreground=diag_color)
            surf_color = ("blue" if tgt == "surface" and trace_active
                          else ("green" if n_surface > 0 else "gray"))
            self.lbl_b5_count.config(
                text=f"内側: {surf_disp} 点", foreground=surf_color)
        else:
            self.lbl_b2_count.config(text="外周: 0 点", foreground="gray")
            self.lbl_b3_counts.config(
                text="左上→右下:0  右上→左下:0  (交点中心: -)",
                foreground="gray")
            self.lbl_b5_count.config(text="内側: 0 点", foreground="gray")
        # Phase indicator label (日本語)
        corner_jp = {"tl": "左上", "tr": "右上", "br": "右下", "bl": "左下"}
        if not is_recording:
            self.lbl_phase.config(text="(ティーチ未開始)",
                                  foreground="gray")
        elif self.dt_phase == "corners":
            # tight = 関節限界張付 (赤フラグ)
            done_disp = []
            any_tight = False
            for k in self.CORNER_ORDER:
                if k not in self.dt_corners:
                    continue
                jp = corner_jp.get(k, k.upper())
                m = self.dt_corners[k].get("margin_deg", 99)
                if m < self.MARGIN_TIGHT_DEG:
                    done_disp.append(f"{jp}⚠️")
                    any_tight = True
                else:
                    done_disp.append(jp)
            next_key = self.CORNER_ORDER[self.dt_corner_idx]
            self.lbl_phase.config(
                text=(f"四隅: {n_corners}/4 記録済み "
                      f"[{', '.join(done_disp) or '-'}]  →  "
                      f"次: {corner_jp.get(next_key, next_key.upper())}"
                      + ("  (⚠️=関節限界張付、取り直し推奨)"
                         if any_tight else "")),
                foreground="red" if any_tight else "blue")
        elif self.dt_phase == "b2_idle":
            self.lbl_phase.config(
                text="四隅完了。 「外周なぞり開始」 か 「対角線」 を "
                     "押して続行、 もしくは保存して終了。",
                foreground="blue")
        elif self.dt_phase == "b2_recording":
            self.lbl_phase.config(
                text="外周なぞり 記録中。 ペンを 左上→右上→右下→左下→"
                     "左上 と外周に沿ってドラッグ。 完了したら "
                     "「トレース停止」",
                foreground="red")
        elif self.dt_phase == "b3_tlbr_recording":
            self.lbl_phase.config(
                text="対角線 左上→右下 記録中。 ペンを左上から右下へ"
                     "ドラッグ。 完了したら 「トレース停止」",
                foreground="red")
        elif self.dt_phase == "b3_trbl_recording":
            self.lbl_phase.config(
                text="対角線 右上→左下 記録中。 ペンを右上から左下へ"
                     "ドラッグ。 完了したら 「トレース停止」",
                foreground="red")
        elif self.dt_phase == "b5_recording":
            self.lbl_phase.config(
                text="内側ジグザグ 記録中。 ペンを内側でジグザグに "
                     "動かす。 完了したら 「トレース停止」",
                foreground="red")
        elif self.dt_phase in ("b2_done", "b3_done", "b5_done"):
            self.lbl_phase.config(
                text=(f"トレース停止。 外周:{n_perimeter}  "
                      f"対角左上→右下:{n_tlbr}  対角右上→左下:{n_trbl}  "
                      f"内側:{n_surface}。 続行 or 保存して終了"),
                foreground="green")

        self.btn_draw.config(state=tk.NORMAL if connected_idle
            else tk.DISABLED)
        # 図形バリエーション + 隅合わせも同じ条件
        draw_state = tk.NORMAL if connected_idle else tk.DISABLED
        self.btn_circle.config(state=draw_state)
        self.btn_triangle.config(state=draw_state)
        for b in self.btn_corner_align.values():
            b.config(state=draw_state)
        self.btn_go_center.config(state=tk.NORMAL if connected_idle
            else tk.DISABLED)
        nudge_state = (tk.NORMAL if connected_idle and self.tune_x_active
                       else tk.DISABLED)
        for b in self.btn_nudge_x:
            b.config(state=nudge_state)
        # ペン下げ: tune_x_active かつ未押し付け時のみ
        pendown_state = (tk.NORMAL if connected_idle and self.tune_x_active
                         and not self.tune_x_pen_down else tk.DISABLED)
        self.btn_tune_pen_down.config(state=pendown_state)
        # ペン上げ: tune_x_active かつ押し付け中のみ
        lift_state = (tk.NORMAL if connected_idle and self.tune_x_active
                      and self.tune_x_pen_down else tk.DISABLED)
        self.btn_lift_pen.config(state=lift_state)

        # ===== 四つ角微調整ボタン =====
        ca_can_start = (connected_idle and not self.tune_x_active
                        and self.corner_adj_key is None)
        for b in self.btn_corner_adj.values():
            b.config(state=tk.NORMAL if ca_can_start else tk.DISABLED)
        ca_op_state = (tk.NORMAL if connected_idle
                        and self.corner_adj_key is not None
                        else tk.DISABLED)
        self.btn_corner_adj_confirm.config(state=ca_op_state)
        self.btn_corner_adj_cancel.config(state=ca_op_state)
        if self.corner_adj_key is not None:
            jp = {"tl": "左上", "tr": "右上", "br": "右下",
                  "bl": "左下"}.get(self.corner_adj_key, "?")
            try:
                yv = float(self.var_corner_adj_y.get())
                zv = float(self.var_corner_adj_z.get())
            except Exception:
                yv, zv = 0.0, 0.0
            self.lbl_corner_adj_status.config(
                text=f"{jp} 微調整中 (Y={yv:.2f}, Z={zv:.2f})。 "
                     "Y/Z spinbox ▲▼ で追従、 確定で yaml 更新。",
                foreground="red")
        else:
            self.lbl_corner_adj_status.config(
                text="待機中 (隅ボタンで移動)", foreground="gray")

        # ===== Capture / Ready Pose (Task B) buttons + label =====
        # Record only valid while in master mode (joints come from
        # CandumpListener). Clear can run any time the GUI is alive --
        # it only edits the yaml file.
        record_state = (tk.NORMAL if is_recording else tk.DISABLED)
        self.btn_record_ready.config(state=record_state)
        clear_state = (tk.NORMAL
                       if self.calibrated_ready_pose is not None
                       and not self.busy and not self.gui_restart_required
                       else tk.DISABLED)
        self.btn_clear_ready.config(state=clear_state)
        if self.calibrated_ready_pose is not None:
            j = self.calibrated_ready_pose
            self.lbl_ready_pose.config(
                text=("登録済み: "
                      + " ".join(f"{v:+7.2f}" for v in j)),
                foreground="green")
        else:
            self.lbl_ready_pose.config(
                text=f"デフォルト (未登録): {READY_POSE_V2_DEG}",
                foreground="gray")

        # ===== Section 5 (full_dev): 生成画像描画 =====
        # panel 変換は busy/restart 時以外いつでも可
        strokes_idle = (not self.busy and not self.gui_restart_required)
        self.btn_panel_convert.config(
            state=tk.NORMAL if strokes_idle else tk.DISABLED)
        # 描画開始: ストロークファイル指定済み AND busy でない
        has_path = bool(self.var_strokes_json_path.get())
        self.btn_strokes_draw.config(
            state=tk.NORMAL if (strokes_idle and has_path) else tk.DISABLED)
        # 再開: 中断状態 (last_completed_idx >= 0) かつ アイドル の時
        can_resume = (strokes_idle and has_path
                      and self.strokes_last_completed_idx >= 0)
        self.btn_strokes_resume.config(
            state=tk.NORMAL if can_resume else tk.DISABLED)
        # 中止: 描画中(busy)のみ enable
        self.btn_strokes_abort.config(
            state=tk.NORMAL if self.busy else tk.DISABLED)

        self.lbl_connected.config(
            text="● 接続中" if self.connected else "● 未接続",
            foreground="green" if self.connected else "gray")
        self.lbl_master.config(
            text="マスターモード中" if self.in_master else "(通常)",
            foreground="red" if self.in_master else "gray")
        if self.gui_restart_required:
            pc_text = "⚠ GUI 再起動が必要 (アーム電源リセット → 再起動)"
            pc_color = "red"
        elif self.power_cycle_needed:
            pc_text = "⚠ アーム電源リセットが必要"
            pc_color = "red"
        elif self.tune_x_active:
            tag = "ペン下げ中" if self.tune_x_pen_down else "ペン上げ中"
            pc_text = f"中央調整中 ({tag})"
            pc_color = "blue"
        else:
            pc_text = ""
            pc_color = "gray"
        self.lbl_powercycle.config(text=pc_text, foreground=pc_color)

    def _refresh_buttons_safe(self):
        self.root.after(0, self._refresh_buttons)

    # ------------------------------------------------------------------
    # live status poll
    # ------------------------------------------------------------------
    def _schedule_status_poll(self):
        self._poll_status()
        self.root.after(500, self._schedule_status_poll)

    def _poll_status(self):
        try:
            if self.in_master and self.listener is not None:
                j = self.listener.get_joints_deg()
                counts, total = self.listener.get_counts()
                ok = all(counts[c] > 0 for c in (0x155, 0x156, 0x157))
                tag = ("マスター" if ok
                       else "マスター (アームを揺らして待機解除)")
                self.lbl_joints.config(
                    text=f"関節 ({tag}): "
                         + " ".join(f"{v:+7.2f}" for v in j))
            elif self.connected and self.piper is not None and not self.busy:
                js = self.piper.GetArmJointMsgs().joint_state
                j = tuple(getattr(js, f"joint_{i}") / 1000.0
                          for i in range(1, 7))
                self.lbl_joints.config(
                    text="関節: " + " ".join(f"{v:+7.2f}" for v in j))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # background-thread guard
    # ------------------------------------------------------------------
    def _run_in_thread(self, fn, *args):
        if self.gui_restart_required:
            messagebox.showerror("⚠ GUI 再起動が必要",
                "GUI がアームを正常に制御できなくなりました "
                "(マスター後の状態など)。\n\n"
                "① 必要ならアームの電源リセット\n"
                "② 必要ならターミナルで CAN を up:\n"
                "    sudo ip link set can0 type can bitrate 1000000\n"
                "    sudo ip link set can0 up\n"
                "③ 右上の 「GUI 再起動」 ボタンで新プロセスを起動")
            return
        with self.busy_lock:
            if self.busy:
                messagebox.showwarning("ビジー",
                    "別の操作が実行中です。 終わるまでお待ちください。")
                return
            self.busy = True
        self._refresh_buttons()

        def wrapper():
            try:
                fn(*args)
            except Exception as e:
                self.log_safe(f"ERROR: {e}")
                import traceback
                self.log_safe(traceback.format_exc())
            finally:
                with self.busy_lock:
                    self.busy = False
                self._refresh_buttons_safe()

        threading.Thread(target=wrapper, daemon=True).start()

    # ------------------------------------------------------------------
    # low-level arm helpers
    # ------------------------------------------------------------------
    def _speed_joint(self):
        try:
            v = int(self.var_speed_joint.get())
        except Exception:
            v = SPEED_JOINT_DEFAULT
        return max(SPEED_MIN, min(SPEED_MAX, v))

    def _movel_factor(self):
        """Return current MOVE L factor (0.1-1.0), clipped to safe range."""
        try:
            v = float(self.var_movel_factor.get())
        except Exception:
            v = 0.4
        return max(0.1, min(1.0, v))

    def _chunk_mm_setting(self):
        """Return user-set chunk_mm (0 = no chunking) for positioning moves."""
        try:
            v = int(self.var_chunk_mm.get())
        except Exception:
            v = 0
        return max(0, min(30, v))

    @staticmethod
    def _settle_seconds_for(speed, base=6.0):
        # at speed 5 -> base (6s); at slower speeds wait proportionally longer
        return max(base, 30.0 / max(1, speed))

    def _wait_until_settled(self, tx, ty, tz, max_wait_s,
                             min_wait_s=0.5, stable_tol_mm=0.5,
                             stable_period_s=0.6, sample_dt=0.25):
        """アームの停止を検知して早期 return。 上限 max_wait_s。

        低速時の adaptive_floor (例 spd=1 で 30 秒) は SDK MOVE L 速度フロア
        と実動作時間の乖離を吸収する保険値だが、 実際にはアームはとっくに
        着いているのに固定 sleep で UI が長時間グレーアウトする副作用が
        あった。 ポーリングで 「pose 変化が stable_tol_mm 未満を
        stable_period_s 維持」 を見て早期復帰する。
        """
        deadline = time.time() + max_wait_s
        min_until = time.time() + min_wait_s
        last_pose = None
        stable_since = None
        while time.time() < deadline:
            time.sleep(sample_dt)
            try:
                cur = self._read_endpose()[:3]
            except Exception:
                continue
            if time.time() < min_until:
                last_pose = cur
                continue
            if last_pose is not None:
                mvt = max(abs(cur[i] - last_pose[i]) for i in range(3))
                if mvt < stable_tol_mm:
                    if stable_since is None:
                        stable_since = time.time()
                    elif time.time() - stable_since >= stable_period_s:
                        return  # 停止検知 → 早期復帰
                else:
                    stable_since = None
            last_pose = cur
        # max_wait_s 経過 → 通常通り fall through、 caller の許容誤差判定へ

    def _read_joints(self):
        js = self.piper.GetArmJointMsgs().joint_state
        return tuple(getattr(js, f"joint_{i}") / 1000.0 for i in range(1, 7))

    def _read_endpose(self):
        ep = self.piper.GetArmEndPoseMsgs().end_pose
        return (ep.X_axis / 1000.0, ep.Y_axis / 1000.0, ep.Z_axis / 1000.0,
                ep.RX_axis / 1000.0, ep.RY_axis / 1000.0, ep.RZ_axis / 1000.0)

    def _at_pose(self, target_deg, tol_deg=READY_JOINT_TOL_DEG):
        try:
            cur = self._read_joints()
        except Exception:
            return False
        return all(abs(c - r) <= tol_deg for c, r in zip(cur, target_deg))

    def _at_ready_pose(self, tol_deg=READY_JOINT_TOL_DEG):
        return self._at_pose(self._active_ready_pose(), tol_deg)

    def _move_joints(self, target_deg, settle_s=None,
                     verify_tol=MOVE_J_VERIFY_TOL_DEG, chained=False):
        """MOVE J で target_deg まで移動。

        chained=True なら ModeCtrl + 1.0s ホールド sleep をスキップ
        (連続 IK 描画など、 直前 ModeCtrl が MOVE J 0x01 で確定して
        いる場合)。 起動時の 1.3s オーバーヘッドが消えるので連続点
        描画が高速化。
        """
        speed = self._speed_joint()
        if settle_s is None:
            settle_s = self._settle_seconds_for(speed, base=6.0)
        if not chained:
            # ModeCtrl を 3 回 連送して SDK の mode を確実に MOVE J に
            # 設定 (gripper 操作等で混乱した CAN bus 状態をリセット)。
            for _ in range(3):
                self.piper.ModeCtrl(0x01, 0x01, speed, 0x00)
                time.sleep(0.1)
            time.sleep(0.2)
            js = self.piper.GetArmJointMsgs().joint_state
            self.piper.JointCtrl(js.joint_1, js.joint_2, js.joint_3,
                                 js.joint_4, js.joint_5, js.joint_6)
            time.sleep(1.0)
        self.piper.JointCtrl(*(int(round(a * 1000.0)) for a in target_deg))
        start_joints = list(self._read_joints())
        prev = list(start_joints)
        settled = False
        movement_started = False
        consecutive_still = 0
        # iterations: enough room to cover settle_s, plus a small grace.
        max_iter = int(settle_s / 0.25) + 8
        for _ in range(max_iter):
            time.sleep(0.25)
            cur = self._read_joints()
            moved_total = max(abs(cur[i] - start_joints[i])
                               for i in range(6))
            still = max(abs(cur[i] - prev[i]) for i in range(6)) < 0.05
            if not movement_started:
                # 動き始めていない (gripper 命令後等で SDK が JointCtrl を
                # 受理してないケース)。 まず 0.5° 以上動くまで待つ。
                if moved_total > 0.5:
                    movement_started = True
            else:
                if still:
                    consecutive_still += 1
                    if consecutive_still >= 3:  # 0.75s 連続静止
                        settled = True
                        break
                else:
                    consecutive_still = 0
            prev = cur
        cur = self._read_joints()
        err = max(abs(cur[i] - target_deg[i]) for i in range(6))
        if err > verify_tol:
            if not settled:
                # arm is still moving; not a real failure -- just slow.
                self.log_safe(f"  WARNING: MOVE J still in motion at "
                              f"timeout (err {err:.2f}°). Waiting extra ...")
                # wait extra to let it complete
                deadline2 = time.time() + 15.0
                while time.time() < deadline2:
                    time.sleep(0.5)
                    cur2 = self._read_joints()
                    if max(abs(cur2[i] - cur[i]) for i in range(6)) < 0.05:
                        break
                    cur = cur2
                err = max(abs(cur[i] - target_deg[i]) for i in range(6))
            if err > verify_tol:
                self.gui_restart_required = True
                raise RuntimeError(
                    f"MOVE J failed: settled at err {err:.2f}° > "
                    f"{verify_tol}°. Arm did not reach target. "
                    "Likely post-master-mode state. Power-cycle, re-up "
                    "CAN, then click 'Restart GUI' (top-right).")
        return cur

    def _build_conn_section(self, parent):
        """接続セクション (タブ外、 常時表示)。"""
        conn_r1 = ttk.Frame(parent)
        conn_r1.pack(fill=tk.X)
        self.btn_can_up = tk.Button(conn_r1, text="🔴 CAN 起動(管理者)",
            bg="#fdd", fg="#a00",
            activebackground="#faa", activeforeground="#800",
            font=("Monaco", 10, "bold"),
            command=self.on_can_up, width=16)
        self.btn_can_up.pack(side=tk.LEFT, padx=(0, 8))
        ttk.Label(conn_r1, text="関節速度(%):").pack(side=tk.LEFT)
        make_spinbox(conn_r1, self.var_speed_joint, SPEED_MIN, SPEED_MAX,
                     1, width=4, fmt="%.0f").pack(side=tk.LEFT, padx=(2, 6))
        ttk.Label(conn_r1, text="直線移動 倍率:").pack(side=tk.LEFT)
        make_spinbox(conn_r1, self.var_movel_factor, 0.1, 1.0, 0.1,
                     width=4, fmt="%.1f").pack(side=tk.LEFT, padx=(2, 8))
        ttk.Label(conn_r1, text="分割幅(mm):").pack(side=tk.LEFT)
        make_spinbox(conn_r1, self.var_chunk_mm, 0, 30, 5, width=4,
                     fmt="%.0f").pack(side=tk.LEFT, padx=(2, 8))
        conn_r2 = ttk.Frame(parent)
        conn_r2.pack(fill=tk.X, pady=(4, 0))
        self.btn_connect = tk.Button(conn_r2, text="🟢 接続",
            bg="#dfd", fg="#060",
            activebackground="#afa", activeforeground="#040",
            font=("Monaco", 10, "bold"),
            command=self.on_connect, width=10)
        self.btn_connect.pack(side=tk.LEFT, padx=2)
        self.btn_recover = tk.Button(conn_r2, text="🏠 ホーム/撮影位置へ",
            bg="#fea", fg="#940",
            activebackground="#fc7", activeforeground="#820",
            font=("Monaco", 10, "bold"),
            command=self.on_recover, width=18)
        self.btn_recover.pack(side=tk.LEFT, padx=2)
        self.btn_storage = tk.Button(conn_r2, text="📦 収納ポーズへ",
            bg="#fea", fg="#940",
            activebackground="#fc7", activeforeground="#820",
            font=("Monaco", 10, "bold"),
            command=self.on_storage, width=14)
        self.btn_storage.pack(side=tk.LEFT, padx=2)
        self.btn_pen_exchange = tk.Button(conn_r2, text="🖋 ペン交換ポーズへ",
            bg="#fea", fg="#940",
            activebackground="#fc7", activeforeground="#820",
            font=("Monaco", 10, "bold"),
            command=self.on_pen_exchange, width=18)
        self.btn_pen_exchange.pack(side=tk.LEFT, padx=2)
        self.btn_grip_home = ttk.Button(conn_r2,
            text="⚙ ホーミング (現位置→0)",
            command=self.on_grip_home, width=22)
        self.btn_grip_home.pack(side=tk.LEFT, padx=2)
        self.btn_grip_strong = ttk.Button(conn_r2, text="🔒 強く掴む",
            command=self.on_grip_strong, width=12)
        self.btn_grip_strong.pack(side=tk.LEFT, padx=2)
        self.btn_grip_release = ttk.Button(conn_r2, text="🔓 ゆるめる",
            command=self.on_grip_release, width=12)
        self.btn_grip_release.pack(side=tk.LEFT, padx=2)
        self.btn_disconnect = tk.Button(conn_r2, text="🔴 切断",
            bg="#fdd", fg="#a00",
            activebackground="#faa", activeforeground="#800",
            font=("Monaco", 10, "bold"),
            command=self.on_disconnect, width=10)
        self.btn_disconnect.pack(side=tk.LEFT, padx=2)
        self.btn_recover_conn = tk.Button(conn_r2, text="🔄 接続をリセット",
            bg="#fdd", fg="#a00",
            activebackground="#faa", activeforeground="#800",
            font=("Monaco", 10, "bold"),
            command=self.on_recover_connection, width=16)
        self.btn_recover_conn.pack(side=tk.LEFT, padx=2)
        # 「操作の流れを開く」 ボタンを接続の右端に配置
        ttk.Button(conn_r2, text="📖 操作の流れを開く",
            command=self._show_help_window, width=22
        ).pack(side=tk.RIGHT, padx=2)

    def _show_motor_diagram(self):
        """モーター番号とアーム関節の対応 + 各モーターの回転方向説明。"""
        if hasattr(self, "_motor_window") and self._motor_window is not None:
            try:
                self._motor_window.lift()
                return
            except Exception:
                self._motor_window = None
        w = tk.Toplevel(self.root)
        w.title("モーター配置 - Piper")
        w.geometry("640x600")
        st = scrolledtext.ScrolledText(w, font=("Monaco", 10),
                                        wrap=tk.WORD, padx=10, pady=10)
        st.pack(fill=tk.BOTH, expand=True)
        body = self._motor_diagram_text() + "\n\n"
        body += "▶ 各モーターの回転方向 (限界張付からの脱出に使う):\n\n"
        for i in range(1, 7):
            info = self._JOINT_INFO.get(i, {})
            body += (f"  モーター {i}: {info.get('name', '?')}\n"
                     f"    位置: {info.get('axis', '?')}\n"
                     f"    + 方向 (角度を増やす) = "
                     f"{info.get('ccw_view', '?')}\n"
                     f"    − 方向 (角度を減らす) = "
                     f"{info.get('cw_view', '?')}\n\n")
        st.insert("1.0", body)
        st.config(state=tk.DISABLED)

        def on_close():
            self._motor_window = None
            w.destroy()
        w.protocol("WM_DELETE_WINDOW", on_close)
        self._motor_window = w

    def _show_help_window(self):
        """操作の流れ を Toplevel 別ウィンドウで表示。"""
        if hasattr(self, "_help_window") and self._help_window is not None:
            try:
                self._help_window.lift()
                return
            except Exception:
                self._help_window = None
        w = tk.Toplevel(self.root)
        w.title("操作の流れ - draw_piper")
        w.geometry("720x600")
        st = scrolledtext.ScrolledText(w, font=("Monaco", 10),
                                        wrap=tk.WORD, padx=10, pady=10)
        st.pack(fill=tk.BOTH, expand=True)
        help_text = (
            "【A. 初回 or キャリブやり直し】 (マスターモード使用、 power "
            "cycle が要る)\n"
            "  ① 接続セクション → 「ホーム/撮影位置へ」\n"
            "  ② (任意) ① リーチ確認: アームがキャンバスに届くか 4 隅を試す\n"
            "  ③ ② キャリブ → 「ティーチ開始」 → アームを手動で動かして:\n"
            "      四隅記録 → 外周なぞり → 対角線 2 本 → (任意 内側ジグザグ)\n"
            "      → 「現在の姿勢を撮影/ホームに登録」 → 「保存して終了」\n"
            "  ④ 電源 OFF → 30秒 → ON → CAN up → 「GUI 再起動」\n"
            "  ⑤ 起動後 接続 → ③ 中央調整 (視覚中心へ):\n"
            "      「対角線交点へ」 → Y/Z 微調整で視覚中心へ → "
            "「ここを中央として確定」\n"
            "  ⑥ ④ 描画 → 「panel_frame.yaml を更新」 (描画準備完了)\n"
            "\n"
            "【B. 描画する】 (既にキャリブ済み)\n"
            "  ① 接続 → 「ホーム/撮影位置へ」\n"
            "  ② ④ 描画 → 生成画像描画 → 「選択...」 で strokes.json\n"
            "  ③ 「実機で描画」 OFF (mock) で 「描画開始」 → base 座標が "
            "範囲内か log で確認\n"
            "  ④ 良ければ 「実機で描画」 ON + 最大本数 5 で smoke 描画\n"
            "  ⑤ 問題なければ 最大本数 0 (全部) で本番描画\n"
            "\n"
            "【C. 動作テストだけしたい】\n"
            "  ① 接続 → 「ホーム/撮影位置へ」 → ③ 中央調整\n"
            "  ② ④ 描画 → 「中心に正方形」 で 30mm 正方形 を描く\n"
            "\n"
            "⚠ 四隅記録時 margin 0° の限界張付があったら 「1点取消」 で "
            "取り直し推奨\n"
            "⚠ 接続できない時は 「接続をリセット」 → 失敗なら USB-CAN "
            "抜き差し\n"
            "\n"
            "▶ 速度: 関節速度 (%) を下げると、 IK + MOVE J 経路は実際に "
            "ゆっくり動きます。 直線描画 (MOVE L、 Section 5 のストローク) は\n"
            "  SDK 仕様で低速設定が効きにくいので注意。\n"
            "\n"
            "▶ ストロークJSON は ~/draw_piper/logs/vlm_to_image_*/cycle_NN/"
            "strokes.json に生成されます (VLM パイプライン側で)。\n"
        )
        st.insert("1.0", help_text)
        st.config(state=tk.DISABLED)

        def on_close():
            self._help_window = None
            w.destroy()
        w.protocol("WM_DELETE_WINDOW", on_close)
        self._help_window = w

    def _load_calib_corner_joints(self):
        """canvas_calibration.yaml の 4 隅の joints_deg を再読込。
        戻り値: {'tl': [j1..j6], ...} or {} (失敗時)。"""
        try:
            parsed = read_calibration(OUTPUT_YAML)
        except Exception:
            return {}
        return parsed.get("whiteboard_corners_joints_deg") or {}

    def _calib_seeds_for_target(self, y, z):
        """目標 (y, z) に近い 順 で キャリブ済み隅 joints を返す。
        さらに 4 隅平均も末尾に追加。 yaml なし時 空 list。
        """
        try:
            parsed = read_calibration(OUTPUT_YAML)
        except Exception:
            return []
        wb = parsed.get("whiteboard_corners_mm") or {}
        joints_map = parsed.get("whiteboard_corners_joints_deg") or {}
        if not wb or not joints_map:
            return []
        scored = []
        for k in ("tl", "tr", "br", "bl"):
            if k in wb and k in joints_map:
                dy = wb[k][1] - y
                dz = wb[k][2] - z
                d = (dy * dy + dz * dz) ** 0.5
                scored.append((d, tuple(joints_map[k])))
        scored.sort(key=lambda t: t[0])
        seeds = [q for _, q in scored]
        # 4 隅平均を追加
        if len(scored) == 4:
            import statistics
            try:
                avg = tuple(statistics.fmean(
                    joints_map[k][j] for k in ("tl", "tr", "br", "bl")
                ) for j in range(6))
                seeds.append(avg)
            except Exception:
                pass
        return seeds

    def _ik_candidates(self, x, y, z, ik_iters=600, dedupe_deg=5.0):
        """目標 (x,y,z) に対して 多通りの初期値で IK を解き、 重複を
        除外した候補リスト (位置誤差で sort) を返す。
        初期値:
          ・ 現在姿勢
          ・ キャリブ済 4 隅 joints (target に近い順) + 4 隅平均
          ・ wall_facing_ik の generic INITIAL_GUESSES (6 種)
        """
        import numpy as np
        cur_q = self._read_joints()
        _, R_cur = fk(np.array(cur_q, dtype=float))
        desired_z = R_cur[:, 2]
        guesses = [np.array(cur_q, dtype=float)]
        # キャリブ済隅 joints (target Y/Z に近い順) を優先
        for seed in self._calib_seeds_for_target(y, z):
            guesses.append(np.array(seed, dtype=float))
        for g in IK_INITIAL_GUESSES:
            guesses.append(np.array(g, dtype=float))
        target_pos = np.array([x, y, z], dtype=float)
        results = []
        for q0 in guesses:
            q_sol, pe, oe = solve_ik(target_pos, desired_z, q0,
                                       iters=ik_iters)
            q_tuple = tuple(float(v) for v in q_sol)
            results.append({"q": q_tuple, "pe": float(pe), "oe": float(oe)})
        # dedupe (5° 以内の差は同一解扱い)
        uniq = []
        for r in results:
            is_dup = False
            for u in uniq:
                if max(abs(r["q"][i] - u["q"][i]) for i in range(6)) < dedupe_deg:
                    if r["pe"] < u["pe"]:
                        u.update(r)  # より良い解で上書き
                    is_dup = True
                    break
            if not is_dup:
                uniq.append(dict(r))
        # 位置誤差で sort
        uniq.sort(key=lambda r: r["pe"])
        return uniq

    def _show_ik_candidates_dialog(self, x, y, z, label, on_select):
        """IK 候補を一覧表示する modal popup。 ユーザがクリックした
        候補に対して on_select(q_tuple) を呼ぶ。"""
        candidates = self._ik_candidates(x, y, z)
        if not candidates:
            messagebox.showerror("IK 候補なし", "解が見つかりません。")
            return
        win = tk.Toplevel(self.root)
        win.title(f"IK 候補 - {label}")
        win.geometry("760x460")
        # キャリブ最寄り隅 joints を取得 (各候補との比較用)
        calib_seeds = self._calib_seeds_for_target(y, z)
        calib_ref = calib_seeds[0] if calib_seeds else None
        ttk.Label(win,
            text=(f"目標: X={x:.1f}, Y={y:.1f}, Z={z:.1f}\n"
                  "下の候補から選択してください。 "
                  "「キャリブ差」 が小さいものは キャリブ時 (= M5 が"
                  "外向きで成功した姿勢) に近く、 描画に適してます。"),
            font=("Monaco", 9), justify=tk.LEFT, wraplength=720
        ).pack(padx=10, pady=(8, 4), anchor=tk.W)
        # ヘッダ
        hdr = ttk.Frame(win)
        hdr.pack(fill=tk.X, padx=10)
        for i, txt in enumerate(("候補", "位置誤差", "キャリブ差",
                                  "M1", "M2", "M3", "M4", "M5", "M6", "")):
            ttk.Label(hdr, text=txt, font=("Monaco", 9, "bold"),
                      width=10 if i == 2 else (8 if i == 0 else 7)
                      ).pack(side=tk.LEFT, padx=2)
        # 候補リスト
        list_frame = ttk.Frame(win)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)
        for idx, c in enumerate(candidates):
            row = ttk.Frame(list_frame)
            row.pack(fill=tk.X, pady=1)
            q = c["q"]
            ttk.Label(row, text=f"#{idx+1}", width=8,
                      font=("Monaco", 9)).pack(side=tk.LEFT, padx=2)
            err_color = "red" if c["pe"] > 10.0 else \
                        ("orange" if c["pe"] > 3.0 else "green")
            ttk.Label(row, text=f"{c['pe']:.1f}mm", width=7,
                      font=("Monaco", 9), foreground=err_color
                      ).pack(side=tk.LEFT, padx=2)
            # キャリブ差 (最大関節差 deg)
            if calib_ref is not None:
                cal_diff = max(abs(q[j] - calib_ref[j]) for j in range(6))
                cal_color = "green" if cal_diff < 15.0 else \
                            ("orange" if cal_diff < 45.0 else "red")
                cal_txt = f"{cal_diff:.0f}°"
            else:
                cal_txt = "-"
                cal_color = "gray"
            ttk.Label(row, text=cal_txt, width=10,
                      font=("Monaco", 9), foreground=cal_color
                      ).pack(side=tk.LEFT, padx=2)
            for j in range(6):
                # M5 ハイライト
                fg = "blue" if j == 4 else "black"
                ttk.Label(row, text=f"{q[j]:+.1f}°", width=7,
                          font=("Monaco", 9), foreground=fg
                          ).pack(side=tk.LEFT, padx=2)
            def make_cb(q_tuple=q):
                def cb():
                    win.destroy()
                    on_select(q_tuple)
                return cb
            ttk.Button(row, text="この姿勢で移動",
                       command=make_cb()).pack(side=tk.LEFT, padx=4)
        # キャンセル
        ttk.Button(win, text="キャンセル",
            command=win.destroy, width=14
        ).pack(pady=(4, 10))

    def _move_xyz_via_ik(self, x, y, z, max_pos_err_mm=5.0, ik_iters=600,
                          warm_start_q=None, settle_s=None, chained=False,
                          try_multi_guess=False):
        """IK で関節角を解いて MOVE J で移動。

        warm_start_q 未指定時はキャリブ済 4 隅で最も近い隅の joints を
        初期値に使う (M5 の符号が known-good になりやすい)。
        try_multi_guess=True: warm_start + 現在姿勢 + キャリブ 4 隅 +
          generic 6 通り を全部試し、 最も低い位置誤差の解を採用。
        chained=True: 連続描画用 (ModeCtrl/ホールド sleep を省略)
        """
        import numpy as np
        cur_q = self._read_joints()
        # 姿勢は現在の FK から
        _, R_cur = fk(np.array(cur_q, dtype=float))
        desired_z = R_cur[:, 2]
        # 初期値の候補
        guesses = []
        if warm_start_q is not None:
            guesses.append(np.array(warm_start_q, dtype=float))
        else:
            # キャリブ済隅 joints を最寄り順に追加 (M5 known-good seed)
            for seed in self._calib_seeds_for_target(y, z):
                guesses.append(np.array(seed, dtype=float))
        guesses.append(np.array(cur_q, dtype=float))
        if try_multi_guess:
            # 既にキャリブ seeds 追加してない (warm_start 指定時) なら追加
            if warm_start_q is not None:
                for seed in self._calib_seeds_for_target(y, z):
                    guesses.append(np.array(seed, dtype=float))
            for g in IK_INITIAL_GUESSES:
                guesses.append(np.array(g, dtype=float))
        # 全候補を試し、 最も低い pe を採用
        target_pos = np.array([x, y, z], dtype=float)
        best_q = None
        best_pe = float("inf")
        for q0 in guesses:
            q_sol, pe, oe = solve_ik(target_pos, desired_z, q0,
                                       iters=ik_iters)
            if pe < best_pe:
                best_q = q_sol
                best_pe = pe
            if pe < max_pos_err_mm:
                break  # 十分小さい誤差で見つかったら抜ける
        if best_pe > max_pos_err_mm:
            raise RuntimeError(
                f"IK 失敗: 位置誤差 {best_pe:.1f}mm > 許容 "
                f"{max_pos_err_mm}mm (目標 X={x:.1f} Y={y:.1f} Z={z:.1f})。 "
                f"{len(guesses)} 通りの初期値で最も近い解。 "
                "アームのリーチ外か関節限界の可能性。")
        q_target = tuple(float(v) for v in best_q)
        self._move_joints(q_target, settle_s=settle_s, chained=chained)
        return q_target  # 次回 warm_start 用

    def _move_xyz_via_ik_stream(self, x, y, z, warm_start_q,
                                  ik_iters=120, inter_point_s=0.08):
        """ストリーミング用 IK + JointCtrl。 settle を待たず brief sleep
        のみで次の点へ。 連続短セグメント (曲線描画) の高速化に。
        ModeCtrl は事前に MOVE J に設定済の前提 (chained と同じ)。
        失敗時は例外を握りつぶしてスキップ (描画継続優先)。
        """
        import numpy as np
        cur_q = self._read_joints()
        _, R_cur = fk(np.array(cur_q, dtype=float))
        desired_z = R_cur[:, 2]
        q0 = np.array(warm_start_q if warm_start_q is not None else cur_q,
                       dtype=float)
        target_pos = np.array([x, y, z], dtype=float)
        try:
            q_sol, pe, _ = solve_ik(target_pos, desired_z, q0,
                                      iters=ik_iters)
        except Exception:
            return warm_start_q
        if pe > 8.0:  # too far, skip
            return warm_start_q
        try:
            self.piper.JointCtrl(
                *(int(round(float(v) * 1000.0)) for v in q_sol))
        except Exception:
            pass
        if inter_point_s > 0:
            time.sleep(inter_point_s)
        return tuple(float(v) for v in q_sol)

    def _move_endpose_chained(self, x, y, z, rpy, max_wait_s=3.0,
                                tol_mm=2.0, poll_dt=0.05):
        """連続描画用: EndPoseCtrl を送り、 アームが目標近傍に到達する
        まで高頻度ポーリング待ち。 _move_endpose の min_wait_s や
        stable_period_s のオーバーヘッドを避けて短いセグメントを連続描画
        するのに使う。 caller は事前に ModeCtrl(0x01, 0x01 or 0x02, ...) を
        セット済みである前提 (mode 切替コストを払わない)。
        """
        self.piper.EndPoseCtrl(
            int(round(x * 1000)), int(round(y * 1000)), int(round(z * 1000)),
            int(round(rpy[0] * 1000)), int(round(rpy[1] * 1000)),
            int(round(rpy[2] * 1000)))
        deadline = time.time() + max_wait_s
        while time.time() < deadline:
            time.sleep(poll_dt)
            try:
                ax, ay, az, _, _, _ = self._read_endpose()
            except Exception:
                continue
            err = max(abs(ax - x), abs(ay - y), abs(az - z))
            if err < tol_mm:
                return
        # timeout — caller can detect via _read_endpose if needed

    def _move_endpose(self, x, y, z, rpy, settle_s=None,
                      tol_mm=ENDPOSE_TOL_MM, escalate=True,
                      speed_factor=1.0, chunk_mm=None,
                      force_move_l=False):
        """Cartesian MOVE L.

        chunk_mm: if set, split a long move into chunks of that size and
          command each chunk separately. Useful when the SDK's `spd`
          parameter has a high floor (we observed MOVE L barely slowing
          down between spd=1 and spd=5). Chunking trades a single long
          move for a sequence of short ones, each with the arm having
          to accel + decel -- net visual velocity is much lower. Use
          for positioning (Reach Probe / Go to Center / B4). DO NOT use
          for drawing strokes -- they should remain continuous lines.
        """
        base = self._speed_joint() * self._movel_factor() * speed_factor
        speed = max(SPEED_MIN, min(SPEED_MAX, int(round(base))))
        # settle_s logic:
        #   Single-shot moves: apply adaptive_floor (long waits at slow
        #     speeds) so the arm has time to finish ONE big move.
        #   Chunked moves: skip adaptive_floor -- chunking already
        #     throttles command rate, and the final chunk is small.
        #     This was the root cause of "GUI grayed out forever even
        #     though arm finished moving fast".
        if chunk_mm and chunk_mm > 0:
            if settle_s is None:
                settle_s = 2.0
            else:
                settle_s = max(float(settle_s), 1.5)
        else:
            adaptive_floor = self._settle_seconds_for(speed, base=3.0)
            if settle_s is None:
                settle_s = adaptive_floor
            else:
                settle_s = max(float(settle_s), adaptive_floor)
        # MOVE L (mode_ctrl=0x02) を既定とする。 EndPoseCtrl は MOVE L
        # mode でのみ正しく実行される (MOVE J mode + EndPoseCtrl では
        # SDK がコマンドを実行しない事を 2026-05-26 のテストで確認)。
        # SDK MOVE L mode の速度パラメータは高い floor を持ち低速設定が
        # 効きにくいが、 これは SDK 仕様。 真に低速が必要なら IK +
        # JointCtrl 経由 (TODO) で対処する。
        self.piper.ModeCtrl(0x01, 0x02, speed, 0x00)

        # ---- Chunked path (positioning) vs single-shot (drawing) ----
        if chunk_mm and chunk_mm > 0:
            cur = self._read_endpose()
            cx, cy_, cz = cur[0], cur[1], cur[2]
            dx, dy, dz = x - cx, y - cy_, z - cz
            dist = (dx * dx + dy * dy + dz * dz) ** 0.5
            if dist > chunk_mm and dist > 1.0:
                n_chunks = max(2, int(round(dist / chunk_mm)))
                # Inter-chunk dwell: scales with inverse speed so slower
                # settings give longer pauses between chunks (= more
                # visible deceleration). Range 0.3-1.5s.
                chunk_dwell = max(0.3, min(1.5, 1.0 / max(1, speed)))
                self.log_safe(
                    f"  分割 MOVE L: {dist:.0f}mm を "
                    f"{n_chunks} 分割 (約 {chunk_mm}mm 刻み、 "
                    f"間隔 {chunk_dwell:.2f}s)")
                for i in range(1, n_chunks):
                    t = i / n_chunks
                    ix = cx + dx * t
                    iy = cy_ + dy * t
                    iz = cz + dz * t
                    self.piper.EndPoseCtrl(
                        int(round(ix * 1000)), int(round(iy * 1000)),
                        int(round(iz * 1000)),
                        int(round(rpy[0] * 1000)),
                        int(round(rpy[1] * 1000)),
                        int(round(rpy[2] * 1000)))
                    time.sleep(chunk_dwell)
        # Final / single-shot target
        self.piper.EndPoseCtrl(
            int(round(x * 1000)), int(round(y * 1000)), int(round(z * 1000)),
            int(round(rpy[0] * 1000)), int(round(rpy[1] * 1000)),
            int(round(rpy[2] * 1000)))
        # 固定 sleep ではなく停止検知ポーリングで早期復帰 (上限 settle_s)
        self._wait_until_settled(x, y, z, max_wait_s=settle_s)

        ax, ay, az, _, _, _ = self._read_endpose()
        err = max(abs(ax - x), abs(ay - y), abs(az - z))
        chunk_tag = f" 分割={chunk_mm}mm" if chunk_mm else ""
        self.log_safe(
            f"  → 指示 ({x:6.1f},{y:6.1f},{z:6.1f}) "
            f"実位置 ({ax:6.1f},{ay:6.1f},{az:6.1f}) "
            f"誤差 {err:.1f}mm [MOVE L 速度={speed} "
            f"= {self._speed_joint()}×{self._movel_factor():.1f}"
            + (f"×{speed_factor:.1f}" if speed_factor != 1.0 else "")
            + f"  待機 {settle_s:.1f}s{chunk_tag}]")
        if speed < 10:
            time.sleep(0.4)
        if err > tol_mm:
            if escalate and err > 30.0:
                self.gui_restart_required = True
                raise RuntimeError(
                    f"EndPose 失敗: 誤差 {err:.1f}mm。 アームが指令を "
                    "実行しなかった可能性。 マスターモード後の状態かも。 "
                    "電源リセット → CAN up → 「GUI 再起動」")
            raise RuntimeError(
                f"位置誤差 {err:.1f}mm > 許容 {tol_mm}mm -- 中断")

    def _ensure_wall_facing(self):
        if not self._at_pose(WALL_FACING_JOINTS_DEG, tol_deg=1.0):
            # 関節大移動中に pen が canvas を擦らないように、 まず
            # 安全 X (= SAFE_TRAVEL_X_MM) まで退避してから MOVE J。
            try:
                ep = self._read_endpose()
                if ep[0] > SAFE_TRAVEL_X_MM:
                    self.log_safe(
                        f"  pen 安全退避 X={ep[0]:.1f} → "
                        f"{SAFE_TRAVEL_X_MM:.0f}mm (擦り防止)")
                    self._move_xyz_via_ik(
                        SAFE_TRAVEL_X_MM, ep[1], ep[2],
                        max_pos_err_mm=20.0, try_multi_guess=True)
            except Exception as e:
                self.log_safe(f"  安全退避 警告 (続行): {e}")
            self.log_safe("  MOVE J to wall-facing ...")
            self._move_joints(WALL_FACING_JOINTS_DEG)
        x, y, z, rx, ry, rz = self._read_endpose()
        self.cached_wall_rpy = (rx, ry, rz)
        self.cached_wall_xyz = (x, y, z)
        return self.cached_wall_rpy

    def _xoff(self):
        try:
            return float(self.var_xoff.get())
        except Exception:
            return 0.0

    def _draw_x(self):
        return self.contact_x_mm + MAX_PUSH_MM + self._xoff()

    def _pen_up_x(self):
        return self.contact_x_mm - PEN_UP_CLEAR_MM + self._xoff()

    # ------------------------------------------------------------------
    # Connect / Disconnect / Recover / Storage / Quit
    # ------------------------------------------------------------------
    def on_quit(self):
        if self.in_master:
            if not messagebox.askyesno("Quit while in MASTER MODE?",
                    "Master mode is active. Quitting now will NOT send "
                    "MasterSlaveConfig(0xFC) cleanly.\nContinue anyway?"):
                return
        try:
            if self.piper:
                try:
                    self.piper.ClosePort()
                except Exception:
                    pass
        finally:
            self.root.quit()

    def on_restart_gui(self):
        """Spawn a fresh GUI process and quit this one."""
        if self.in_master:
            messagebox.showerror("⚠ マスターモード中は再起動不可",
                "先にティーチを保存 or 中止してください。")
            return
        if not messagebox.askyesno("GUI 再起動",
                "GUI を再起動しますか?\n\n"
                "ティーチから来た場合は事前に:\n"
                "  ・ アームの電源リセット (OFF → 30秒 → ON)\n"
                "  ・ ターミナルで CAN を up し直す:\n"
                "      sudo ip link set can0 type can bitrate 1000000\n"
                "      sudo ip link set can0 up\n\n"
                "新しい GUI プロセスを起動し、 この GUI は閉じます。"):
            return
        self._restart_gui_now()

    def _restart_gui_now(self):
        try:
            if self.piper:
                self.piper.ClosePort()
        except Exception:
            pass
        python = sys.executable
        script = os.path.abspath(__file__)
        try:
            subprocess.Popen(
                [python, script],
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
        except Exception as e:
            messagebox.showerror("再起動失敗",
                f"GUI プロセスを起動できませんでした:\n{e}\n\n"
                "ターミナルから手動で起動してください。")
            return
        self.root.quit()

    # ------------------------------------------------------------------
    # CAN status / CAN up (via pkexec)
    # ------------------------------------------------------------------
    def _check_can_status(self):
        """Legacy state-only check (kept for callers that don't want stats)."""
        stats = self._read_can_counters()
        if stats is None:
            return ("error", "ip command failed")
        return (stats.get("state", "?"), stats.get("_raw", ""))

    def _read_can_counters(self):
        """Parse `ip -s -d link show can0` for state + live counters.

        Returns dict with keys: state ('UP'/'DOWN'/'missing'/'?'),
        rx_packets, tx_packets, error_warn, error_passive, bus_off,
        _raw (full stdout). Returns None only on subprocess failure.
        """
        try:
            r = subprocess.run(
                ["ip", "-s", "-d", "link", "show", "can0"],
                capture_output=True, text=True, timeout=2)
        except Exception:
            return None
        if r.returncode != 0:
            return {"state": "missing", "rx_packets": 0, "tx_packets": 0,
                    "error_warn": 0, "error_passive": 0, "bus_off": 0,
                    "_raw": r.stderr.strip()}
        out = r.stdout
        if "state UP" in out or "ERROR-ACTIVE" in out:
            state = "UP"
        elif "state DOWN" in out:
            state = "DOWN"
        else:
            state = "?"
        rx_pkts = tx_pkts = 0
        err_warn = err_pass = bus_off = 0
        lines = out.splitlines()
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("RX:") and "packets" in stripped:
                if i + 1 < len(lines):
                    parts = lines[i + 1].split()
                    if len(parts) >= 2:
                        try: rx_pkts = int(parts[1])
                        except ValueError: pass
            elif stripped.startswith("TX:") and "packets" in stripped:
                if i + 1 < len(lines):
                    parts = lines[i + 1].split()
                    if len(parts) >= 2:
                        try: tx_pkts = int(parts[1])
                        except ValueError: pass
            elif "bus-errors" in stripped and "arbit-lost" in stripped:
                if i + 1 < len(lines):
                    parts = lines[i + 1].split()
                    if len(parts) >= 6:
                        try:
                            err_warn = int(parts[3])
                            err_pass = int(parts[4])
                            bus_off = int(parts[5])
                        except ValueError:
                            pass
        return {"state": state, "rx_packets": rx_pkts, "tx_packets": tx_pkts,
                "error_warn": err_warn, "error_passive": err_pass,
                "bus_off": bus_off, "_raw": out.strip()}

    def _poll_can(self):
        stats = self._read_can_counters()
        if stats is None:
            self.lbl_can.config(text="CAN: ● ?", foreground="orange")
        else:
            state = stats["state"]
            if state == "UP":
                tx = stats["tx_packets"]
                rx = stats["rx_packets"]
                err = stats["error_passive"] + stats["bus_off"]
                # Red if errors or TX stuck while RX moving (= TX dead).
                tx_dead = (tx == 0 and rx > 100)
                color = ("red" if (err > 0 or tx_dead)
                         else "green")
                marker = "TX-DEAD" if tx_dead else "UP"
                self.lbl_can.config(
                    text=f"CAN:●{marker} TX:{tx} RX:{rx} err:{err}",
                    foreground=color)
            elif state == "DOWN":
                self.lbl_can.config(text="CAN:●DOWN (CAN up が必要)",
                                    foreground="red")
            elif state == "missing":
                self.lbl_can.config(
                    text="CAN:●missing (USB-CAN 未接続?)",
                    foreground="red")
            else:
                self.lbl_can.config(text=f"CAN:●{state}",
                                    foreground="orange")
        self.root.after(2000, self._poll_can)

    def on_can_up(self):
        which = subprocess.run(["which", "pkexec"], capture_output=True)
        if which.returncode != 0:
            messagebox.showerror("❌ pkexec not found",
                "pkexec (PolicyKit) is not installed. Either:\n"
                "  sudo apt install policykit-1\n"
                "or run CAN up manually in a terminal:\n"
                "  sudo ip link set can0 type can bitrate 1000000\n"
                "  sudo ip link set can0 up")
            return
        if not messagebox.askyesno("Bring CAN up",
                "Run as root via pkexec:\n"
                "  ip link set can0 type can bitrate 1000000\n"
                "  ip link set can0 up\n\n"
                "A graphical password prompt will appear."):
            return
        self._run_in_thread(self._do_can_up)

    def _do_can_up(self):
        self.log_safe("Bringing CAN up (pkexec) ...")
        # `|| true` keeps going even if bitrate is already set (busy error).
        cmd = ["pkexec", "sh", "-c",
               "ip link set can0 type can bitrate 1000000 2>&1 || true; "
               "ip link set can0 up 2>&1 || true; exit 0"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=60)
        except subprocess.TimeoutExpired:
            self.log_safe("  pkexec timed out (60s) -- polkit agent may be off")
            return
        out = (r.stdout + r.stderr).strip()
        if r.returncode == 126:
            self.log_safe("  pkexec cancelled (dialog dismissed)")
            return
        if r.returncode == 127:
            self.log_safe("  pkexec auth failed (returncode 127)")
            return
        if out:
            self.log_safe(f"  pkexec output: {out}")
        # verify
        state, raw = self._check_can_status()
        if state == "UP":
            self.log_safe("  CAN status: UP ✓")
        else:
            self.log_safe(f"  CAN status: {state}")
            if raw:
                self.log_safe(f"  detail: {raw.splitlines()[0]}")

    def on_connect(self):
        if self.gui_restart_required:
            messagebox.showerror("⚠ GUI 再起動が必要",
                "この GUI は以前マスターモードに入った状態です。 "
                "SDK が安定して再接続できません。 「GUI 終了」 して "
                "再起動してください。")
            return
        if self.power_cycle_needed:
            if not messagebox.askyesno("Power-cycle needed?",
                    "GUI thinks a power cycle is still needed.\n"
                    "Did you actually power-cycle the arm? Continue anyway?"):
                return
        self._run_in_thread(self._do_connect)

    def _do_connect(self):
        if C_PiperInterface_V2 is None:
            self.log_safe("piper_sdk not importable.")
            return
        # ---- Pre-flight CAN check (avoid the user diving into terminal) ----
        stats_before = self._read_can_counters() or {}
        state = stats_before.get("state", "?")
        if state == "missing":
            self.log_safe("PRE-FLIGHT: can0 device not found.")
            self.root.after(0, lambda: messagebox.showerror(
                "USB-CAN not detected",
                "can0 device is missing.\n\n"
                "Recovery:\n"
                "  1. Plug in the USB-CAN adapter\n"
                "  2. Wait 5 seconds\n"
                "  3. Click 'CAN up (sudo)'\n"
                "  4. Click Connect"))
            return
        if state != "UP":
            self.log_safe(f"PRE-FLIGHT: can0 state = {state}")
            self.root.after(0, lambda s=state: messagebox.showerror(
                "CAN not up",
                f"can0 state is '{s}'. Click 'CAN up (sudo)' first."))
            return
        rx_before = stats_before.get("rx_packets", 0)
        tx_before = stats_before.get("tx_packets", 0)
        self.log_safe(f"Pre-flight: CAN UP, RX={rx_before} TX={tx_before}")
        # Sniff 1 s and see if the arm is actually broadcasting feedback.
        time.sleep(1.0)
        stats_sniff = self._read_can_counters() or {}
        rx_sniff_delta = stats_sniff.get("rx_packets", 0) - rx_before
        if rx_sniff_delta == 0:
            self.log_safe(f"PRE-FLIGHT WARN: RX delta = 0 in 1s "
                          "(arm not transmitting).")
            self.root.after(0, lambda: messagebox.showwarning(
                "Arm not transmitting",
                "No CAN frames received from arm in 1 second.\n\n"
                "Likely causes:\n"
                "  - Arm power is OFF (look for motor hum / LED)\n"
                "  - CAN cable disconnected on the arm side\n\n"
                "Connect will proceed anyway but will likely time out."))
        # ---- The actual SDK Connect ----
        self.log_safe("Connecting (ConnectPort + EnablePiper) ...")
        piper = C_PiperInterface_V2("can0")
        piper.ConnectPort()
        time.sleep(1.0)
        piper.ArmParamEnquiryAndConfig(0x01, 0x02, 0, 0, 0x02)
        time.sleep(0.5)
        piper.SearchAllMotorMaxAngleSpd()
        time.sleep(0.3)
        t0 = time.time()
        enabled = False
        while not piper.EnablePiper():
            time.sleep(0.01)
            if time.time() - t0 > 6.0:
                break
        else:
            enabled = True
        if not enabled:
            # Diagnose failure before raising.
            stats_after = self._read_can_counters() or {}
            tx_delta = stats_after.get("tx_packets", 0) - tx_before
            rx_delta = stats_after.get("rx_packets", 0) - rx_before
            err_count = (stats_after.get("error_passive", 0)
                         + stats_after.get("bus_off", 0))
            self.log_safe(f"EnablePiper timeout. "
                          f"TX delta={tx_delta}  RX delta={rx_delta}  "
                          f"err={err_count}")
            try:
                piper.ClosePort()
            except Exception:
                pass
            self._diagnose_connect_failure(tx_delta, rx_delta, err_count)
            raise RuntimeError(
                "EnablePiper timeout (see dialog for recovery steps).")
        time.sleep(0.5)
        self.piper = piper
        self.connected = True
        self.power_cycle_needed = False
        self.tune_x_active = False
        self.tune_x_pen_down = False
        self.log_safe("Connected. Config Init done, motors enabled.")

    def _diagnose_connect_failure(self, tx_delta, rx_delta, err_count):
        """Show an actionable error dialog based on CAN statistics observed
        during the failed Connect attempt. Maps known failure modes to
        copy-pasteable recovery steps so users don't dive into terminal."""
        if tx_delta == 0 and rx_delta > 0:
            title = "USB-CAN TX dead (known issue)"
            msg = (f"CAN diagnostics: host received {rx_delta} frames from "
                   "the arm, but sent 0.\n\n"
                   "Known issue: USB-CAN adapter TX silently failing.\n\n"
                   "Recovery (in order):\n"
                   "  1. Arm power OFF\n"
                   "  2. UNPLUG the USB-CAN adapter from the PC\n"
                   "  3. Wait 10 seconds\n"
                   "  4. PLUG IT BACK IN\n"
                   "  5. Click 'CAN up (sudo)'\n"
                   "  6. Arm power ON (verify motor hum / LED)\n"
                   "  7. Click 'Restart GUI'\n"
                   "  8. Click Connect")
        elif rx_delta == 0:
            title = "Arm not responding"
            msg = ("CAN diagnostics: 0 RX frames in the 1s pre-flight + "
                   "the 6s EnablePiper window.\n\n"
                   "Recovery:\n"
                   "  1. Verify arm power is ON (motor hum / LED)\n"
                   "  2. Check CAN cable between USB-CAN and arm\n"
                   "  3. Full arm power-cycle: OFF → 30s wait → ON\n"
                   "  4. (If CAN went DOWN) 'CAN up (sudo)'\n"
                   "  5. Click Connect again")
        elif err_count > 0:
            title = "CAN bus errors"
            msg = (f"CAN diagnostics: {err_count} bus errors (error-passive "
                   "or bus-off) seen during Connect.\n\n"
                   "USB-CAN likely entered bus-off state.\n\n"
                   "Recovery:\n"
                   "  1. UNPLUG + REPLUG USB-CAN adapter\n"
                   "  2. 'CAN up (sudo)' to re-init\n"
                   "  3. 'Restart GUI'\n"
                   "  4. Connect")
        else:
            title = "Connect failed (residual state)"
            msg = ("CAN looks healthy (TX/RX both moving, no errors) but "
                   "EnablePiper timed out -- likely residual SDK state from "
                   "a prior session.\n\n"
                   "Recovery:\n"
                   "  1. Click 'Recover Connection' (sends 0xFC release)\n"
                   "  2. If that fails, 'Restart GUI' then Connect\n"
                   "  3. If still fails, full arm power-cycle then Connect")
        self.root.after(0, lambda t=title, m=msg:
                        messagebox.showerror(t, m))

    def on_recover_connection(self):
        """Send MasterSlaveConfig(0xFC) to release any residual master mode
        and close the port. Useful when a previous session left the arm or
        SDK in a weird state and Connect won't go through."""
        if not messagebox.askyesno("Recover Connection",
                "Send MasterSlaveConfig(0xFC, 0, 0, 0) to release any "
                "residual master-mode state, then close the port.\n\n"
                "After this, click 'Restart GUI' to spawn a fresh process, "
                "then Connect.\n\n"
                "Use this when EnablePiper times out repeatedly with "
                "no CAN errors. Continue?"):
            return
        self._run_in_thread(self._do_recover_connection)

    def _do_recover_connection(self):
        self.log_safe("Recover Connection: opening a fresh CAN port ...")
        try:
            p = C_PiperInterface_V2("can0")
            p.ConnectPort()
            time.sleep(0.5)
            self.log_safe("  sending MasterSlaveConfig(0xFC, 0, 0, 0) ...")
            p.MasterSlaveConfig(0xFC, 0, 0, 0)
            time.sleep(0.5)
            try:
                p.ClosePort()
            except Exception:
                pass
            self.log_safe("  done. Click 'Restart GUI' for a fresh "
                          "process, then Connect.")
        except Exception as e:
            self.log_safe(f"  Recover failed: {e}")

    def on_disconnect(self):
        self._run_in_thread(self._do_disconnect)

    def _do_disconnect(self):
        if self.piper is not None:
            try:
                self.piper.ClosePort()
            except Exception:
                pass
        self.piper = None
        self.connected = False
        self.tune_x_active = False
        self.tune_x_pen_down = False
        self.probe_active = False
        self.probe_pen_down = False
        self.log_safe("Disconnected.")

    def on_recover(self):
        if not messagebox.askyesno("ホーム/撮影位置へ",
                f"アームを ホーム/撮影位置 へ移動しますか?\n"
                f"  関節速度 {self._speed_joint()}% (MOVE J)\n"
                "ペンがキャンバスに近い場合は先に X=130 まで退避します。"):
            return
        self._run_in_thread(self._do_recover)

    def _do_recover(self):
        self.log_safe("ホーム/撮影位置 へ復帰 ...")
        ep = self.piper.GetArmEndPoseMsgs().end_pose
        x = ep.X_axis / 1000.0
        y = ep.Y_axis / 1000.0
        z = ep.Z_axis / 1000.0
        if x > 180.0:
            # 旧版は MOVE L で退避してから MOVE J に切替えていたが、
            # MOVE J → MOVE L → MOVE J の mode 切替で SDK が混乱し
            # err 70°+ で固まる現象を複数回確認。 IK + MOVE J で退避する
            # 事で mode 切替を完全に排除する。
            self.log_safe(f"  ペン X={x:.1f} > 180、 "
                          "IK + MOVE J で X=130 へ退避 (mode 切替なし) ...")
            try:
                self._move_xyz_via_ik(130.0, y, z,
                                       max_pos_err_mm=20.0,
                                       try_multi_guess=True)
            except Exception as e:
                self.log_safe(f"  退避警告 (続行): {e}")
        target = self._active_ready_pose()
        tag = ("カスタム撮影位置" if self.calibrated_ready_pose
               else "ready pose v2 デフォルト")
        self.log_safe(f"  MOVE J で {tag} へ ...")
        # gripper 操作後など SDK 状態混乱で MOVE J が settled at large
        # err で失敗する case あり。 verify_tol 緩和 + 失敗時は gui lock
        # しないで再試行を促す。
        try:
            cur = self._move_joints(target, verify_tol=15.0)
            err = max(abs(c - r) for c, r in zip(cur, target))
            self.log_safe(f"  到着。 最大関節誤差 {err:.2f}°")
        except Exception as e:
            self.log_safe(f"  ❌ MOVE J 失敗: {e}")
            self.log_safe(
                "  → 再度 「ホーム/撮影位置へ」 を押すと改善することが "
                "あります。 駄目なら 「収納ポーズへ」 で安全姿勢に。")
            # GUI lock 解除して操作続行可能に
            self.gui_restart_required = False
            self._refresh_buttons_safe()
        self.tune_x_active = False
        self.tune_x_pen_down = False
        self.probe_active = False
        self.probe_pen_down = False

    def on_storage(self):
        if not messagebox.askyesno("収納ポーズへ",
                f"アームを収納ポーズに移動しますか?\n"
                f"  関節速度 {self._speed_joint()}%"):
            return
        self._run_in_thread(self._do_storage)

    def _do_storage(self):
        self.log_safe("収納ポーズへ移動中 ...")
        # 寛容モード: 安全姿勢への退避なので 5° 精度は不要。 失敗しても
        # GUI lock しない。
        try:
            self._move_joints(STORAGE_POSE_DEG, settle_s=8.0,
                                verify_tol=20.0)
            self.log_safe("  到着。 電源 OFF 可。")
        except Exception as e:
            self.log_safe(f"  収納ポーズ 部分到達 ({e})")
            self.log_safe(
                "  → 物理的に安全な位置にあれば電源 OFF 可能。 "
                "再試行する場合はもう一度ボタンを押してください。")
            self.gui_restart_required = False
            self._refresh_buttons_safe()
        self.tune_x_active = False
        self.tune_x_pen_down = False
        self.probe_active = False
        self.probe_pen_down = False

    def on_pen_exchange(self):
        if not messagebox.askyesno("ペン交換ポーズへ",
                f"アームをペン交換ポーズ (ペン先が真上を向く) に "
                f"移動しますか?\n"
                f"  関節速度 {self._speed_joint()}%\n"
                f"  関節角度: J1=0, J2=10, J3=-100, J4=0, J5=70, J6=0\n\n"
                "アーム周囲に十分なスペースがあることを確認してください。"):
            return
        self._run_in_thread(self._do_pen_exchange)

    def _do_pen_exchange(self):
        self.log_safe("ペン交換ポーズへ移動中 ...")
        # ペン交換は精度を要求しない (≒ 視覚的に概ねその姿勢でよい)
        # ので verify_tol を大きく取って partial reach を許容する。
        # 失敗しても gui_restart_required にしない (= 例外を握りつぶす)。
        try:
            self._move_joints(PEN_EXCHANGE_POSE_DEG, settle_s=8.0,
                                verify_tol=20.0)
            self.log_safe("  到着。 「🔓 ゆるめる」 でペンを抜く、 新しい"
                          "ペンを差してから 「🔒 強く掴む」、 「ホーム/撮影"
                          "位置へ」 で復帰。")
        except Exception as e:
            # 大きく外れた場合: gui_restart_required をリセットして
            # ユーザが復帰操作を続けられるようにする
            self.log_safe(f"  ペン交換ポーズ 部分到達 ({e})")
            self.log_safe("  → 物理的に問題なければそのまま使用、 "
                          "問題あれば 「ホーム/撮影位置へ」 で戻る。")
            self.gui_restart_required = False
            self._refresh_buttons_safe()
        self.tune_x_active = False
        self.tune_x_pen_down = False
        self.probe_active = False
        self.probe_pen_down = False

    def _gripper_state(self):
        """現在のグリッパー状態を読んで dict 返却 (失敗時 None)。
        status_code から homed / enabled も解釈して含める。
        """
        try:
            msg = self.piper.GetArmGripperMsgs()
            gs = msg.gripper_state
            sc = int(getattr(gs, "status_code", 0))
            return {
                "angle": getattr(gs, "grippers_angle", None),
                "effort": getattr(gs, "grippers_effort", None),
                "status_code": sc,
                "enabled": bool(sc & (1 << 6)),
                "homed": bool(sc & (1 << 7)),
                "errors": [
                    name for name, bit in [
                        ("voltage_low", 0),
                        ("motor_overtemp", 1),
                        ("overcurrent", 2),
                        ("driver_overtemp", 3),
                        ("sensor_err", 4),
                        ("driver_err", 5),
                    ] if (sc & (1 << bit))
                ],
            }
        except Exception as e:
            return None

    def on_grip_home(self):
        """グリッパーの現在位置を 0 点として登録 (ホーミング)。
        通常は手動でグリッパーを完全に閉じた状態で押す。 これ以降、
        angle=0 = 閉じ位置として扱われる。
        """
        if self.piper is None:
            messagebox.showerror("❌ 未接続",
                "アームに接続されていません。\n\n"
                "対処: 上部の 「🟢 接続」 ボタンを押してから再試行してください。")
            return
        if not messagebox.askyesno("グリッパー ホーミング",
                "現在のグリッパー位置を 「0」 (= 完全に閉じた位置) として "
                "登録します。\n\n"
                "推奨手順:\n"
                "  ① グリッパーを手動で完全に閉じる (ペンを挟むなら "
                "そのペンを挟んだ状態で)\n"
                "  ② このボタンを押す\n"
                "  ③ 以降は 「強く掴む」 = 現在位置で固定、 "
                "「ゆるめる」 = 開く になる\n\n"
                "続行?"):
            return
        self._run_in_thread(self._do_grip_home)

    def _do_grip_home(self):
        state_before = self._gripper_state()
        self.log_safe(f"⚙ ホーミング 開始 (before: {state_before})")
        try:
            # set_zero=0xAE で現在位置を 0 点に。 angle/effort は無視される
            self.piper.GripperCtrl(0, 0, 0x01, 0xAE)
            time.sleep(0.3)
            # 念のため通常 enable も再送
            self.piper.GripperCtrl(0, 1000, 0x01, 0)
            time.sleep(0.5)
        except Exception as e:
            self.log_safe(f"  ❌ GripperCtrl 失敗: {e}")
            return
        state_after = self._gripper_state()
        self.log_safe(f"  ✓ ホーミング完了 (after: {state_after})")
        if state_after and not state_after.get("homed"):
            self.log_safe(
                "  ⚠ status_code に homed bit が立っていません。 "
                "ハードウェア側で homing が完了してないかも。")

    def _send_gripper(self, angle, effort, code=0x03, repeats=3,
                        interval_s=0.1):
        """GripperCtrl を複数回送信して確実に届ける。
        code=0x03 = enable + clear error。
        """
        for i in range(repeats):
            try:
                self.piper.GripperCtrl(int(angle), int(effort),
                                         int(code), 0)
            except Exception as e:
                self.log_safe(f"GripperCtrl 送信失敗 (try {i+1}): {e}")
            if i < repeats - 1:
                time.sleep(interval_s)

    def on_grip_strong(self):
        """グリッパーを最大トルクで閉じる (ペンを強く掴む)。"""
        if self.piper is None:
            messagebox.showerror("❌ 未接続",
                "アームに接続されていません。\n\n"
                "対処: 上部の 「🟢 接続」 ボタンを押してから再試行してください。")
            return
        self._run_in_thread(self._do_grip_strong)

    def _do_grip_strong(self):
        state_before = self._gripper_state()
        self.log_safe(f"🔒 強く掴む 開始 (before: {state_before})")
        # angle=0 (閉), effort=3000 (= 3 N/m、 SDK max 5N の 60%、 強め)
        # code=0x01 (enable のみ、 clear_error は使わない: 2026-05-27 の
        # 検証で 0x03 だと後続 MOVE J が混乱する事を確認)
        self._send_gripper(angle=0, effort=3000, code=0x01)
        time.sleep(0.5)
        state_after = self._gripper_state()
        self.log_safe(f"  ✓ 完了 (after: {state_after})")

    def on_grip_release(self):
        """グリッパーを開く (ペンを離す)。"""
        if self.piper is None:
            messagebox.showerror("❌ 未接続",
                "アームに接続されていません。\n\n"
                "対処: 上部の 「🟢 接続」 ボタンを押してから再試行してください。")
            return
        self._run_in_thread(self._do_grip_release)

    def _do_grip_release(self):
        state_before = self._gripper_state()
        self.log_safe(f"🔓 ゆるめる 開始 (before: {state_before})")
        # angle=70000 (= 70mm 開、 piper の最大開閉に近い)
        # effort=1000 (弱め、 物が抜けやすく)
        # code=0x01 (enable のみ、 clear_error 不使用)
        self._send_gripper(angle=70000, effort=1000, code=0x01)
        time.sleep(0.5)
        state_after = self._gripper_state()
        self.log_safe(f"  ✓ 完了 (after: {state_after})")

    # ------------------------------------------------------------------
    # Reach Probe (pre-calibration: drive arm to 4 reach-corner
    # candidates and mark each with a pen dot, so the user can see the
    # safe drawing region before mounting/calibrating the canvas)
    # ------------------------------------------------------------------
    # Candidate corners (Y, Z) in robot base_link mm. Inside M10's reach
    # polygon (Y[-97, +77] x Z[157, 464]) with ~10mm safety margin.
    PROBE_CORNERS = (
        ("TL", +75.0, +440.0),
        ("TR", -90.0, +440.0),
        ("BR", -90.0, +180.0),
        ("BL", +75.0, +180.0),
    )
    PROBE_DOT_HOLD_SEC = 1.0

    def on_probe_corner(self, name):
        """Probe ONE reach-corner candidate. Caller picks which corner.

        Does NOT auto-return to ready -- arm stays at pen-up over the
        target so the next button click can travel directly. After all
        desired corners are probed, click Recover to Ready.
        """
        target = next((c for c in self.PROBE_CORNERS if c[0] == name), None)
        if target is None:
            messagebox.showerror("未知のコーナー",
                                  f"コーナー名 {name} は登録されてません")
            return
        _, y, z = target
        # Allow from ready pose OR while a probe chain is active.
        # IK solutions for different corners give wildly different joint
        # configurations even with the same cartesian pose, so a strict
        # joint-pose check is too restrictive once chaining has started.
        if not self._at_ready_pose() and not self.probe_active:
            messagebox.showwarning("⚠ ホーム位置未到達",
                "リーチ確認を始める前に 「ホーム/撮影位置へ」 を "
                "押してください。")
            return
        jp_map = {"TL": "左上", "TR": "右上", "BR": "右下", "BL": "左下"}
        if not messagebox.askyesno(f"リーチ確認 {jp_map.get(name, name)}",
                f"アームを {jp_map.get(name, name)} ({name}) = "
                f"(Y={y:+.0f}, Z={z:+.0f}) へ移動して印を付けます。\n\n"
                f"アプローチ X = 接触深さ ({self.contact_x_mm:.1f}) + "
                f"{MAX_PUSH_MM}mm 押し付け。\n"
                "キャンバスとペンの装着を確認してください。 続行?"):
            return
        self._run_in_thread(self._do_probe_corner, name, y, z)

    def _do_probe_corner_with_q(self, name, y, z, q_target):
        """ユーザが選んだ IK 候補 (q_target) で移動して、 移動後の関節
        余裕などを通常通り log する。 _do_probe_corner と同じく
        probe_pen_down 状態の場合は retract。
        """
        self._ensure_wall_facing()
        self.probe_active = True
        pen_up_x = self._pen_up_x()
        if self.probe_pen_down:
            self.log_safe("  ペン retract (IK 低速)")
            actual = self._read_endpose()
            try:
                self._move_xyz_via_ik(pen_up_x, actual[1], actual[2],
                                       try_multi_guess=True)
            except Exception as e:
                self.log_safe(f"  retract 警告: {e}")
            self.probe_pen_down = False
        self.log_safe(f"リーチ確認 {name}: 選択した候補で移動")
        try:
            self._move_joints(q_target)
            joints = self._read_joints()
            margin = min(min(joints[i] - JOINT_LIMITS_DEG[i][0],
                             JOINT_LIMITS_DEG[i][1] - joints[i])
                         for i in range(6))
            tag = "限界" if margin < 5.0 else "OK"
            self.log_safe(f"  関節余裕 {margin:.1f}° [{tag}]")
            actual = self._read_endpose()
            err = max(abs(actual[1] - y), abs(actual[2] - z))
            self.log_safe(
                f"  実位置 ({actual[0]:.1f}, {actual[1]:.1f}, "
                f"{actual[2]:.1f})  目標 (-, {y:.1f}, {z:.1f})  "
                f"YZ 誤差 {err:.1f}mm")
        except Exception as e:
            self.log_safe(f"  ❌ リーチ確認 {name}: 移動失敗 ({e})")
        self._refresh_buttons_safe()

    def _do_probe_corner(self, name, y, z):
        first_probe = (not self.probe_active or self.cached_wall_rpy is None)
        if first_probe:
            self._ensure_wall_facing()
        else:
            self.log_safe("  (連続リーチ確認: 既存の wall_rpy を再利用)")
        self.probe_active = True
        pen_up_x = self._pen_up_x()
        # ペン下げ中なら最初に retract (IK 低速)
        if self.probe_pen_down:
            self.log_safe("  ペン retract (IK 低速)")
            actual = self._read_endpose()
            try:
                self._move_xyz_via_ik(pen_up_x, actual[1], actual[2])
            except Exception as e:
                self.log_safe(f"  retract failed: {e}")
            self.probe_pen_down = False
        self.log_safe(f"リーチ確認 {name}: ペン上げで "
                      f"(Y={y:.1f}, Z={z:.1f}, X={pen_up_x:.1f}) へ "
                      "[IK 低速 + multi-guess]")
        # IK 候補選択モード ON ならポップアップで選ばせる
        if bool(self.var_probe_ik_select.get()):
            jp_map = {"TL": "左上", "TR": "右上",
                      "BR": "右下", "BL": "左下"}
            def on_pick(q_tuple, name=name, y=y, z=z):
                self._run_in_thread(self._do_probe_corner_with_q,
                                     name, y, z, q_tuple)
            self.root.after(0, lambda:
                self._show_ik_candidates_dialog(
                    pen_up_x, y, z,
                    f"{jp_map.get(name, name)} ({name})", on_pick))
            return
        try:
            # Probe はリーチ端を試すので multi-guess + 緩和した閾値で
            self._move_xyz_via_ik(pen_up_x, y, z,
                                   max_pos_err_mm=20.0,
                                   try_multi_guess=True)
            joints = self._read_joints()
            margin = min(min(joints[i] - JOINT_LIMITS_DEG[i][0],
                             JOINT_LIMITS_DEG[i][1] - joints[i])
                         for i in range(6))
            tag = "限界" if margin < 5.0 else "OK"
            self.log_safe(f"  関節余裕 {margin:.1f}° [{tag}]")
            actual = self._read_endpose()
            err = max(abs(actual[1] - y), abs(actual[2] - z))
            self.log_safe(
                f"  実位置 ({actual[0]:.1f}, {actual[1]:.1f}, "
                f"{actual[2]:.1f})  目標 (-, {y:.1f}, {z:.1f})  "
                f"YZ 誤差 {err:.1f}mm")
            if err > 5.0:
                self.log_safe(
                    f"  ⚠ 目標から {err:.1f}mm ずれて到着 "
                    "(リーチ限界の最寄り点)。 キャンバスをアームに近づけるか "
                    "サイズを調整してください。")
        except Exception as e:
            self.log_safe(f"  ❌ リーチ確認 {name}: 移動失敗 ({e})")
        self.log_safe(f"リーチ確認 {name}: ペン上げで到着。 「ペン下げ」 "
                      "で印を付けるか、 次のコーナーを選択。")
        self._refresh_buttons_safe()

    def on_probe_pen_down(self):
        if not self.probe_active or self.cached_wall_rpy is None:
            messagebox.showerror("リーチ確認未開始",
                "先にいずれかのコーナーボタン (左上 等) を押して "
                "移動してください。")
            return
        if self.probe_pen_down:
            messagebox.showinfo("既にペン下げ",
                "ペンは下げ状態です。 先に 「ペン上げ」 で戻してください。")
            return
        self._run_in_thread(self._do_probe_pen_down)

    def _do_probe_pen_down(self):
        actual = self._read_endpose()
        draw_x = self._draw_x()
        self.log_safe(f"✏ ペン下げ (IK 低速): X={draw_x:.1f}"
                      f"Y={actual[1]:.1f} Z={actual[2]:.1f} "
                      f"[X 補正 {self._xoff():+.1f}]")
        try:
            self._move_xyz_via_ik(draw_x, actual[1], actual[2])
            self.probe_pen_down = True
            self.log_safe("  ✓ ペン下げ完了。 印を付けたら 「⬆ ペン上げ」。")
        except Exception as e:
            self.log_safe(f"  ❌ ペン下げ失敗: {e}")
        self._refresh_buttons_safe()

    def on_probe_pen_up(self):
        if not self.probe_active or self.cached_wall_rpy is None:
            messagebox.showerror("リーチ確認未開始",
                "ペン上げはリーチ確認チェーン中にのみ使えます。")
            return
        if not self.probe_pen_down:
            messagebox.showinfo("既にペン上げ", "ペンは上げ状態です。")
            return
        self._run_in_thread(self._do_probe_pen_up)

    def _do_probe_pen_up(self):
        actual = self._read_endpose()
        pen_up_x = self._pen_up_x()
        self.log_safe(f"ペン上げ (IK 低速): X={pen_up_x:.1f} "
                      f"Y={actual[1]:.1f} Z={actual[2]:.1f}")
        try:
            self._move_xyz_via_ik(pen_up_x, actual[1], actual[2])
            self.probe_pen_down = False
            self.log_safe("  ✓ ペン上げ完了。")
        except Exception as e:
            self.log_safe(f"  ❌ ペン上げ失敗: {e}")
        self._refresh_buttons_safe()

    # ------------------------------------------------------------------
    # Drag-Teach
    # ------------------------------------------------------------------
    def on_start_drag(self):
        if not self._at_ready_pose():
            messagebox.showwarning("⚠ ホーム位置未到達",
                "ティーチを始める前に 「ホーム/撮影位置へ」 を "
                "押してください。")
            return
        # 既存 yaml に有効なデータがあれば、 引継ぎオプションを提示
        load_prev = False
        existing_summary = self._summarize_existing_calib()
        if existing_summary:
            choice = messagebox.askyesnocancel("ティーチ開始 確認",
                "既存のキャリブデータが見つかりました:\n"
                f"  {existing_summary}\n\n"
                "「はい」 = 既存データを引き継いで個別やり直し可能に\n"
                "「いいえ」 = 全部新規記録 (旧データは保存時に上書き)\n"
                "「キャンセル」 = ティーチ開始しない\n\n"
                "続行?")
            if choice is None:
                return
            load_prev = bool(choice)
        else:
            if not messagebox.askyesno("ティーチ開始 確認",
                    "ティーチを開始します:\n"
                    "  ① 壁向き姿勢へ MOVE J\n"
                    "  ② candump サブプロセス起動\n"
                    "  ③ マスターモード ON (MasterSlaveConfig 0xFA)\n\n"
                    "終了後はアームの電源リセット + GUI 再起動が "
                    "必要です。 続行?"):
                return
        self._run_in_thread(self._do_start_drag, load_prev)

    def _summarize_existing_calib(self):
        """canvas_calibration.yaml のデータ概要を str で返す (なければ None)。"""
        try:
            parsed = read_calibration(OUTPUT_YAML)
        except Exception:
            return None
        if parsed.get("schema_version") != 3:
            return None
        raw = parsed.get("raw") or {}
        wb = raw.get("whiteboard_corners_mm") or {}
        traces = raw.get("traces") or {}
        n_corners = sum(1 for k in ("tl", "tr", "br", "bl") if k in wb)
        n_perim = len(traces.get("perimeter") or [])
        n_tlbr = len(traces.get("diagonal_tl_br") or [])
        n_trbl = len(traces.get("diagonal_tr_bl") or [])
        n_surf = len(traces.get("surface") or [])
        if n_corners == 0 and n_perim == 0:
            return None
        return (f"四隅 {n_corners}/4 / 外周 {n_perim} / "
                f"対角 左上→右下 {n_tlbr} + 右上→左下 {n_trbl} / "
                f"内側 {n_surf} 点")

    def _load_existing_into_dt(self):
        """既存 canvas_calibration.yaml の corners + traces を dt_corners /
        dt_traces にロード。 個別やり直し可能にする。
        """
        try:
            parsed = read_calibration(OUTPUT_YAML)
        except Exception as e:
            self.log_safe(f"  ❌ 既存 yaml 読込失敗: {e}")
            return
        raw = parsed.get("raw") or {}
        wb_records = raw.get("whiteboard_corners_mm") or {}
        # whiteboard_corners_mm の各 entry は v3 で 「sample record」 dict。
        # _capture_point と互換な形式に整形 (joints_deg / xyz_mm / margin_deg)
        for k in ("tl", "tr", "br", "bl"):
            rec = wb_records.get(k)
            if not isinstance(rec, dict):
                continue
            ep = rec.get("end_pose_mm_deg") or []
            joints = rec.get("joints_deg") or []
            if len(ep) < 3 or len(joints) < 6:
                continue
            # margin 再計算
            import numpy as np
            margin = min(
                min(joints[i] - JOINT_LIMITS_DEG[i][0],
                    JOINT_LIMITS_DEG[i][1] - joints[i])
                for i in range(6))
            self.dt_corners[k] = {
                "joints_deg": list(joints),
                "xyz_mm": [float(ep[0]), float(ep[1]), float(ep[2])],
                "rpy_deg": [float(ep[3]) if len(ep) >= 4 else 0.0,
                            float(ep[4]) if len(ep) >= 5 else 0.0,
                            float(ep[5]) if len(ep) >= 6 else 0.0],
                "margin_deg": float(margin),
                "timestamp": rec.get("timestamp", ""),
            }
        self.dt_corner_idx = len(self.dt_corners)
        # traces
        traces = raw.get("traces") or {}
        for k in ("perimeter", "diagonal_tl_br", "diagonal_tr_bl",
                  "surface"):
            self.dt_traces[k] = list(traces.get(k) or [])
        # phase を corners 完了状態に
        if len(self.dt_corners) >= 4:
            self.dt_phase = "b2_idle"
        self.log_safe(f"  既存データ引継ぎ: 四隅 {len(self.dt_corners)}/4、 "
                      f"外周 {len(self.dt_traces['perimeter'])} 点、 "
                      f"対角 {len(self.dt_traces['diagonal_tl_br'])} + "
                      f"{len(self.dt_traces['diagonal_tr_bl'])}、 "
                      f"内側 {len(self.dt_traces['surface'])} 点")

    def _do_start_drag(self, load_prev=False):
        self.dt_corners = {}
        self.dt_corner_idx = 0
        self.redo_corner_key = None
        self.dt_traces = {"perimeter": [], "diagonal_tl_br": [],
                          "diagonal_tr_bl": [], "surface": []}
        self.sampling_thread = None
        self.dt_phase = "corners"
        self.probe_active = False
        self.probe_pen_down = False
        self.log_safe("Moving to wall-facing pose ...")
        self._move_joints(WALL_FACING_JOINTS_DEG)
        self.log_safe("Starting candump subprocess ...")
        self.listener = CandumpListener("can0")
        self.listener.start()
        time.sleep(0.5)
        _, total = self.listener.get_counts()
        self.log_safe(f"  candump alive, baseline frames in 0.5s: {total}")
        self.log_safe("Entering master mode (MasterSlaveConfig 0xFA) ...")
        self.piper.MasterSlaveConfig(0xFA, 0, 0, 0)
        self.in_master = True
        self.power_cycle_needed = True
        self._refresh_buttons_safe()
        self.log_safe("MASTER MODE ACTIVE.")
        # 既存データ引継ぎが選択されてた場合はここでロード
        if load_prev:
            self._load_existing_into_dt()
            self._refresh_buttons_safe()
        self.log_safe("*** WIGGLE THE ARM by hand to start 0x155-7 broadcast ***")
        if load_prev and self.dt_phase == "b2_idle":
            self.log_safe("✓ 既存データ引継ぎ完了。 個別やり直し ボタンで "
                          "特定の隅だけ再記録可能。 トレース系も既存値を "
                          "保持。 完了したら 「保存して終了」 で yaml 上書き。")
        else:
            self.log_safe("Phase B1: drag pen to each WHITEBOARD CORNER "
                          "and press Enter, in order TL -> TR -> BR -> BL.")
            self.log_safe("Phase B2: after B1, click 'Start B2 trace' and "
                          "drag the pen along the perimeter (auto-sampled).")
        deadline = time.time() + 30.0
        last = 0.0
        while time.time() < deadline:
            counts, total = self.listener.get_counts()
            if all(counts[c] > 0 for c in (0x155, 0x156, 0x157)):
                joints = self.listener.get_joints_deg()
                self.log_safe(f"  master broadcast active. "
                              + " ".join(f"{v:+7.2f}" for v in joints))
                return
            now = time.time()
            if now - last > 2.0:
                self.log_safe(f"  ...still waiting. counts={counts} "
                              f"total={total}")
                last = now
            time.sleep(0.2)
        self.log_safe("  (timeout waiting for broadcast; you can still Record "
                      "once joints become non-zero)")

    # 各モーターの物理的位置と回転方向 (Piper 6-DOF アーム)
    # ccw_view: lower 限界 (+方向に回すと余裕↑) の時、 どこから見て
    #           どっちに回すかの説明
    # cw_view:  upper 限界 (-方向に回すと余裕↑) の時の説明
    _JOINT_INFO = {
        1: {
            "name": "土台 (ベース) 回転",
            "axis": "アームの一番下、 土台を回す回転軸",
            "ccw_view": "上から見て 反時計回り (アームを向かって 左 へ振る)",
            "cw_view":  "上から見て 時計回り (アームを向かって 右 へ振る)",
        },
        2: {
            "name": "肩",
            "axis": "土台の上、 大きく動く肩関節",
            "ccw_view": "向かって右側から見て 反時計回り "
                        "(アームを起こす / 後ろへ反らす)",
            "cw_view":  "向かって右側から見て 時計回り "
                        "(アームを前に倒す / 下げる)",
        },
        3: {
            "name": "肘",
            "axis": "肩の先、 アームを折りたたむ肘関節",
            "ccw_view": "向かって右側から見て 反時計回り (肘を伸ばす方向)",
            "cw_view":  "向かって右側から見て 時計回り (肘を曲げる方向)",
        },
        4: {
            "name": "手首ロール (前腕の軸回転)",
            "axis": "前腕の長手方向の軸まわりに手首を回す",
            "ccw_view": "前腕の先端 (手首側) から見て 反時計回り",
            "cw_view":  "前腕の先端 (手首側) から見て 時計回り",
        },
        5: {
            "name": "手首ピッチ",
            "axis": "手首を上下に振る軸",
            "ccw_view": "向かって右側から見て 反時計回り (ペン先を上に向ける)",
            "cw_view":  "向かって右側から見て 時計回り (ペン先を下に向ける)",
        },
        6: {
            "name": "ペン軸ロール (末端回転)",
            "axis": "ペンが取り付いている末端の軸回転",
            "ccw_view": "ペン軸方向 (ペン先側) から見て 反時計回り",
            "cw_view":  "ペン軸方向 (ペン先側) から見て 時計回り",
        },
    }

    def _joint_limit_advice(self, joint_idx, side):
        """関節限界に張り付いた時、 どのモーターをどっちに回すか案内。
        side='lower' なら + 方向に動かすと余裕↑、 'upper' なら -。
        + 方向 = ccw_view、 - 方向 = cw_view と対応。
        """
        info = self._JOINT_INFO.get(int(joint_idx), {})
        name = info.get("name", "?")
        axis = info.get("axis", "")
        direction_text = info.get("ccw_view") if side == "lower" \
                         else info.get("cw_view")
        short = f"モーター {joint_idx} を {direction_text}"
        detail = (f"モーター {joint_idx} = {name}\n"
                  f"   位置: {axis}\n"
                  f"   回す方向: {direction_text}")
        return {"name": name, "short": short, "detail": detail}

    def _motor_diagram_text(self):
        """モーター番号とアームの位置関係の ASCII 図 (tooltip / help 用)。"""
        return (
            "Piper 6-DOF アーム モーター配置 (土台 → 先端の順)\n"
            "\n"
            "   モーター 6 ─ ペン軸ロール (ペン先側に近い末端の回転)\n"
            "    │\n"
            "   モーター 5 ─ 手首ピッチ (ペン先を上下に振る)\n"
            "    │\n"
            "   モーター 4 ─ 手首ロール (前腕の軸まわりに手首を回す)\n"
            "    │  前腕\n"
            "   モーター 3 ─ 肘 (アームを折りたたむ関節)\n"
            "    │  上腕\n"
            "   モーター 2 ─ 肩 (アームを上下に動かす大きな関節)\n"
            "    │\n"
            "   モーター 1 ─ 土台 (アーム全体を左右に振る回転)\n"
            "    ─ ベース ─\n"
            "\n"
            "公式ドキュメント / 画像: AgileX Robotics の Piper ページで "
            "確認できます。\n"
            "  https://global.agilex.ai/  → Products → Piper\n"
            "  または GitHub: https://github.com/agilexrobotics/piper_sdk"
        )

    def _capture_point(self):
        """Read current master joints and return a point dict, or None."""
        if self.listener is None:
            return None
        j = self.listener.get_joints_deg()
        if not any(abs(v) > 0.001 for v in j):
            return None
        pos_mm_arr, R = fk(list(j))
        pos_mm = pos_mm_arr.tolist()
        rpy = euler_zyx(R).tolist()
        # 各関節の lower/upper どちら側に近いか + 余裕を全部算出
        per_joint = []
        for i in range(6):
            lo_margin = j[i] - JOINT_LIMITS_DEG[i][0]
            hi_margin = JOINT_LIMITS_DEG[i][1] - j[i]
            if lo_margin < hi_margin:
                per_joint.append((i + 1, lo_margin, "lower"))
            else:
                per_joint.append((i + 1, hi_margin, "upper"))
        margin = min(m for _, m, _ in per_joint)
        # 最も限界に近い関節 (アドバイス用)
        tightest = min(per_joint, key=lambda x: x[1])
        return {
            "joints_deg": list(j),
            "xyz_mm": pos_mm,
            "rpy_deg": rpy,
            "margin_deg": margin,
            "tightest_joint": tightest[0],
            "tightest_side": tightest[2],  # "lower" or "upper"
            "timestamp": datetime.datetime.now().astimezone()
                .isoformat(timespec="milliseconds"),
        }

    # Joint margin threshold: corners with margin BELOW this are flagged
    # as "joint limit に張り付き" (the arm couldn't actually reach the
    # visual canvas corner; it stopped at its kinematic limit). Captures
    # below this trigger a visible warning + a confirm dialog on Save.
    MARGIN_TIGHT_DEG = 5.0

    def on_record_point(self):
        """Record one B1 corner. B2+ are auto-sampled (Start B2 button).
        個別やり直しモード (redo_corner_key 設定済) では指定隅を上書き。
        """
        if self.listener is None:
            return
        # 個別やり直しモード: 指定隅を上書き保存して redo モード解除
        if self.redo_corner_key is not None:
            p = self._capture_point()
            if p is None:
                messagebox.showwarning("⚠ 関節フィードバック未取得",
                    "アームの関節値がまだ全てゼロです。\n"
                    "アームを少し手で揺らしてから再度押してください。")
                return
            key = self.redo_corner_key
            corner_jp = {"tl": "左上", "tr": "右上",
                         "br": "右下", "bl": "左下"}
            jp = corner_jp.get(key, key.upper())
            self.dt_corners[key] = p
            pos = p["xyz_mm"]
            margin = p["margin_deg"]
            tight = (margin < self.MARGIN_TIGHT_DEG)
            tag = "⚠️ 関節限界張付" if tight else "OK"
            self.log(f"  [{jp} 再記録] X={pos[0]:6.1f} Y={pos[1]:6.1f} "
                     f"Z={pos[2]:6.1f}  関節余裕 {margin:.1f}° [{tag}]")
            self.redo_corner_key = None
            self._refresh_buttons()
            return
        # 通常モード: 順次記録
        if self.dt_phase != "corners":
            return
        p = self._capture_point()
        if p is None:
            messagebox.showwarning("⚠ 関節フィードバック未取得",
                "アームの関節値がまだ全てゼロです。\n"
                "アームを少し手で揺らして 0x155-7 ブロードキャストを "
                "起動してから記録してください。")
            return
        key = self.CORNER_ORDER[self.dt_corner_idx]
        corner_jp = {"tl": "左上", "tr": "右上", "br": "右下", "bl": "左下"}
        jp = corner_jp.get(key, key.upper())
        self.dt_corners[key] = p
        self.dt_corner_idx += 1
        pos = p["xyz_mm"]
        margin = p["margin_deg"]
        tight = (margin < self.MARGIN_TIGHT_DEG)
        tag = "⚠️ 関節限界張付" if tight else "OK"
        self.log(f"  [B1 {jp}] X={pos[0]:6.1f} Y={pos[1]:6.1f} "
                 f"Z={pos[2]:6.1f}  関節余裕 {margin:.1f}° [{tag}]")
        if tight:
            joint_idx = p.get("tightest_joint", 0)
            side = p.get("tightest_side", "lower")
            advice = self._joint_limit_advice(joint_idx, side)
            self.log_safe(f"  ⚠️ {jp} は関節限界 (余裕 {margin:.1f}°、 "
                          f"モーター {joint_idx} の {side} 側)。 "
                          f"アドバイス: {advice['short']}")
            self.root.after(0, lambda j=jp, m=margin, ji=joint_idx,
                              s=side, ad=advice:
                messagebox.showwarning(
                    f"{j} が関節限界",
                    f"{j} を関節余裕 {m:.1f}° で記録しました "
                    f"(モーター {ji} = {ad['name']} の限界に張付)。\n\n"
                    "5° 未満は 「アームが届く限界」 で止まっている状態。 "
                    "視覚的なキャンバスコーナーまで届いてない可能性大です。\n\n"
                    "▶ どのモーターをどっちに回すか:\n"
                    f"   {ad['detail']}\n\n"
                    "対策:\n"
                    f"  ① 「1点取消」 で取り消し\n"
                    f"  ② 上記の通り モーター {ji} を回してアームの姿勢を "
                    "変える\n"
                    f"  ③ ペン先がコーナーに当たるよう Y/Z も微調整\n"
                    f"  ④ 再度 「現在地を記録」\n\n"
                    "モーター番号とアームの位置関係は 「現在地を記録」 "
                    "ボタンの近くにマウスを置くと tooltip で確認できます。\n"
                    "(もしくはキャンバスを物理的にアームのリーチ内に移動 "
                    "するのも有効です。)"))
        if self.dt_corner_idx >= 4:
            self.dt_phase = "b2_idle"
            self.log("Phase B1 done. B2 perimeter trace is now available. "
                     "Click 'Start B2 trace' to begin drag-teach sampling.")
        self._refresh_buttons()

    def on_redo_corner(self, corner_key):
        """指定隅を個別に再記録モードへ。 次の 「現在地を記録」 押下で
        その隅が上書きされる。
        """
        if self.listener is None or not self.in_master:
            messagebox.showerror("⚠ マスターモード外",
                "個別やり直しはティーチ中 (マスターモード) のみ可。")
            return
        if corner_key not in self.CORNER_ORDER:
            return
        corner_jp = {"tl": "左上", "tr": "右上",
                     "br": "右下", "bl": "左下"}
        jp = corner_jp.get(corner_key, corner_key)
        if not messagebox.askyesno(f"{jp} を個別再記録",
                f"{jp} ({corner_key.upper()}) を再記録モードにします。\n\n"
                "手順:\n"
                "  ① アームを手で動かして 視覚的な {jp} 隅に持っていく\n"
                "  ② 「現在地を記録 [Enter]」 を押す\n"
                "  ③ 既存値が上書きされる (他の隅 / 外周 / 対角線 / "
                "内側ジグザグ はそのまま)\n\n"
                "続行?".format(jp=jp)):
            return
        self.redo_corner_key = corner_key
        self.log(f"  → {jp} ({corner_key.upper()}) 再記録モード。 "
                  "アームを動かして 「現在地を記録」 を押してください。")
        self._refresh_buttons()

    def on_undo_point(self):
        """Undo the most-recently recorded B1 corner. B2+ are auto-sampled
        so they are not undoable via this button (use Stop -> Start to
        discard in-progress segments, or Abort to discard all)."""
        if self.dt_phase == "corners" and self.dt_corner_idx > 0:
            self.dt_corner_idx -= 1
            key = self.CORNER_ORDER[self.dt_corner_idx]
            removed = self.dt_corners.pop(key, None)
            if removed is not None:
                self.log(f"  removed corner {self.CORNER_LABEL[key]}: "
                         f"{removed['xyz_mm']}")
        else:
            return
        self._refresh_buttons()

    def on_save_drag(self):
        if len(self.dt_corners) != 4:
            messagebox.showerror("⚠ 四隅が未完了",
                f"記録済み {len(self.dt_corners)}/4 隅。 B1 (四隅) を "
                "全て記録してから保存してください。")
            return
        if (self.sampling_thread is not None
                and self.sampling_thread.is_alive()):
            messagebox.showerror("外周なぞり実行中",
                "外周なぞりが まだ動いてます。 先に 「トレース停止」 を。")
            return
        # ジョイント限界張付チェック (Edit I)
        corner_jp = {"tl": "左上", "tr": "右上", "br": "右下", "bl": "左下"}
        tight = []
        for k in self.CORNER_ORDER:
            c = self.dt_corners.get(k)
            if c and c.get("margin_deg", 99) < self.MARGIN_TIGHT_DEG:
                tight.append((corner_jp[k], c["margin_deg"]))
        if tight:
            details = "\n".join(f"  ・ {n}: 余裕 {m:.1f}°"
                                 for n, m in tight)
            if not messagebox.askyesno("⚠️ 関節限界張付のコーナーあり",
                    f"以下のコーナーが関節限界 (5° 未満) で記録されて "
                    "います:\n\n"
                    f"{details}\n\n"
                    "これは アームが届く限界 で止まっているだけで、 視覚的な "
                    "キャンバスコーナーまで届いていない可能性が大きいです。\n"
                    "描画時にズレや関節飛びが起きやすくなります。\n\n"
                    "推奨:\n"
                    "  ・ 中止して 1点取消 → 別の位置で取り直し\n"
                    "  ・ または キャンバスを アームのリーチ内に移動\n\n"
                    "それでも このまま保存しますか?"):
                return
        n_perim = len(self.dt_traces["perimeter"])
        if n_perim == 0:
            if not messagebox.askyesno("外周なぞり未実施",
                    "外周なぞりが 0 点です。 四隅だけで保存しますか?\n"
                    "(平面 fit が 4 点だけになり精度が落ちます)"):
                return
        self._run_in_thread(self._do_save_drag)

    def _do_save_drag(self):
        self._exit_master()
        self._fit_and_save()
        # Try to send the arm to storage pose for safer power-off. Master
        # mode exit can leave SDK in a fragile state (see N4); if the MOVE
        # J fails, warn the user and proceed with the standard power-cycle
        # modal so they manually support the arm.
        try:
            self.log_safe("Moving to storage pose for safe power-off ...")
            self._move_joints(STORAGE_POSE_DEG, settle_s=8.0)
            self.log_safe("  reached storage pose.")
        except Exception as e:
            err_msg = str(e)
            self.log_safe(f"  MOVE J to storage FAILED: {err_msg}")
            self.root.after(0, lambda msg=err_msg: messagebox.showwarning(
                "Storage pose move failed",
                "Could not move arm to storage pose after save:\n"
                f"  {msg}\n\n"
                "Manually SUPPORT the arm before powering off "
                "(it may stay where it was)."))
        self.gui_restart_required = True
        self._refresh_buttons_safe()
        self._show_master_exit_dialog()

    def on_abort_drag(self):
        n_perim = len(self.dt_traces["perimeter"])
        if (self.sampling_thread is not None
                and self.sampling_thread.is_alive()):
            n_perim += self.sampling_thread.get_point_count()
        n_total = len(self.dt_corners) + n_perim
        if not messagebox.askyesno("ティーチ中止",
                f"保存せずに中止しますか?\n"
                f"  記録済み: {len(self.dt_corners)}/4 隅 + "
                f"{n_perim} 外周点 (合計 {n_total} 点)"):
            return
        self._run_in_thread(self._do_abort_drag)

    def _do_abort_drag(self):
        self._exit_master()
        self.log_safe("Exited master mode without saving.")
        self.gui_restart_required = True
        self._refresh_buttons_safe()
        self._show_master_exit_dialog()

    # ------------------------------------------------------------------
    # B2 perimeter trace (v3)
    # ------------------------------------------------------------------
    # Helper: per-trace UI label + phase mapping.
    _TRACE_LABELS = {
        "perimeter":      ("外周なぞり",        "b2_recording"),
        "diagonal_tl_br": ("対角線 左上→右下",  "b3_tlbr_recording"),
        "diagonal_tr_bl": ("対角線 右上→左下",  "b3_trbl_recording"),
        "surface":        ("内側ジグザグ",      "b5_recording"),
    }

    def _start_trace(self, target):
        """Start a DragSamplingThread that fills dt_traces[target]."""
        if self.listener is None or not self.in_master:
            messagebox.showerror("⚠ マスターモード外",
                "先に 「ティーチ開始」 でマスターモードに入って "
                "ください。")
            return
        if len(self.dt_corners) < 4:
            messagebox.showerror("⚠ 四隅未完了",
                f"先に 4 隅を全部記録してください "
                f"(現在 {len(self.dt_corners)}/4)。")
            return
        if (self.sampling_thread is not None
                and self.sampling_thread.is_alive()):
            messagebox.showwarning("既に記録中",
                "実行中のなぞりを先に停止してください。")
            return
        try:
            interval = float(self.var_sampling_interval.get())
            if interval <= 0:
                raise ValueError("must be > 0")
        except Exception as e:
            messagebox.showerror("サンプリング間隔 不正",
                f"間隔が不正です: {e}")
            return
        label, phase = self._TRACE_LABELS[target]
        self.recording_target = target
        self.sampling_thread = DragSamplingThread(
            listener=self.listener,
            fk_fn=fk,
            euler_fn=euler_zyx,
            sampling_interval_mm=interval,
            poll_interval_s=0.1,
            on_point=self._on_trace_point_added,
            on_warning=self._on_trace_joint_warning,
            on_error=self._on_trace_error,
        )
        self.sampling_thread.start()
        self.dt_phase = phase
        self.log_safe(f"{label} trace started "
                      f"(interval {interval:.0f} mm)")
        if target == "perimeter":
            self.log_safe("  drag pen along the whiteboard perimeter: "
                          "TL→TR→BR→BL→TL")
        elif target == "diagonal_tl_br":
            self.log_safe("  drag pen TL → BR along the diagonal")
        elif target == "diagonal_tr_bl":
            self.log_safe("  drag pen TR → BL along the diagonal")
        elif target == "surface":
            self.log_safe("  drag pen in a zigzag across the interior")
        self._refresh_buttons()

    def on_b2_start_trace(self):
        self._start_trace("perimeter")

    def on_b3_tlbr_start(self):
        self._start_trace("diagonal_tl_br")

    def on_b3_trbl_start(self):
        self._start_trace("diagonal_tr_bl")

    def on_b5_start_trace(self):
        # B5 surface zigzag -- optional interior coverage.
        self._start_trace("surface")

    # ------------------------------------------------------------------
    # Capture/Ready pose recording (Task B)
    # ------------------------------------------------------------------
    def on_record_ready_pose(self):
        """Snapshot current master-mode joints as the ready/capture pose
        and persist to panel_frame.yaml's panel.ready_pose_deg."""
        if not self.in_master or self.listener is None:
            messagebox.showerror("⚠ マスターモード外",
                "撮影/ホーム位置の記録には マスターモード "
                "(ティーチ中) が必要です。\n\n"
                "先に 「ティーチ開始」 でマスターモードに入り、 "
                "アームを撮影位置まで動かしてから このボタンを "
                "押してください。")
            return
        joints = self.listener.get_joints_deg()
        if not any(abs(v) > 0.001 for v in joints):
            messagebox.showwarning("マスター情報未受信",
                "関節値がまだ全部 0 です。 アームを少し動かして "
                "0x155-7 ブロードキャストを開始させてください。")
            return
        joints = tuple(float(v) for v in joints)
        if not messagebox.askyesno("撮影/ホーム位置を記録",
                "現在の姿勢を撮影/ホーム位置として保存しますか?\n\n"
                "  関節 (deg) = "
                + ", ".join(f"{v:+7.2f}" for v in joints) + "\n\n"
                "panel_frame.yaml の panel.ready_pose_deg に書き込みます。 "
                "以降の 「ホーム/撮影位置へ」 はこの姿勢を使います "
                "(組込みデフォルトの代わりに)。"):
            return
        try:
            self._save_ready_pose_to_panel_yaml(joints)
        except Exception as e:
            messagebox.showerror("保存失敗",
                f"panel_frame.yaml への書き込み失敗:\n{e}")
            return
        self.calibrated_ready_pose = joints
        self.log(f"Recorded ready/capture pose -> panel_frame.yaml: "
                 + " ".join(f"{v:+7.2f}" for v in joints))
        self._refresh_buttons()

    def on_clear_ready_pose(self):
        """Remove the calibrated ready_pose -- revert to default."""
        if self.calibrated_ready_pose is None:
            messagebox.showinfo("既にデフォルト",
                "カスタム ホーム位置 は現在設定されてません。")
            return
        if not messagebox.askyesno("ホーム位置をクリア",
                "panel_frame.yaml の panel.ready_pose_deg を削除しますか?\n\n"
                "以降の 「ホーム/撮影位置へ」 は組込みデフォルト "
                f"{READY_POSE_V2_DEG} を使うようになります。"):
            return
        try:
            self._clear_ready_pose_from_panel_yaml()
        except Exception as e:
            messagebox.showerror("クリア失敗",
                f"panel_frame.yaml の編集失敗:\n{e}")
            return
        self.calibrated_ready_pose = None
        self.log("Calibrated ready_pose removed; reverting to default.")
        self._refresh_buttons()

    def _clear_ready_pose_from_panel_yaml(self):
        """Remove panel.ready_pose_deg from panel_frame.yaml (preserves
        all other keys including the rest of the panel: block)."""
        path = self._PANEL_YAML_PATH
        if not os.path.exists(path):
            return  # nothing to clear
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        panel = data.get("panel")
        if isinstance(panel, dict) and "ready_pose_deg" in panel:
            panel["ready_pose_deg"] = None
            data["panel"] = panel
            with open(path, "w") as f:
                yaml.safe_dump(data, f, sort_keys=False,
                               default_flow_style=False)

    def on_trace_stop(self):
        if self.sampling_thread is None:
            return
        target = self.recording_target
        self.sampling_thread.stop(join_timeout=2.0)
        new_points = self.sampling_thread.get_points()
        if target is not None:
            self.dt_traces[target].extend(new_points)
            n = len(self.dt_traces[target])
            label = self._TRACE_LABELS[target][0]
            self.log_safe(f"{label} trace stopped. "
                          f"new in this segment: {len(new_points)}; "
                          f"total {target}: {n}")
        self.sampling_thread = None
        self.recording_target = None
        # Pick a "done" phase that reflects what was last filled.
        if target == "perimeter":
            self.dt_phase = "b2_done"
        elif target in ("diagonal_tl_br", "diagonal_tr_bl"):
            self.dt_phase = "b3_done"
        elif target == "surface":
            self.dt_phase = "b5_done"
        else:
            self.dt_phase = "b2_done"  # generic fallback
        self._refresh_buttons()

    def _on_trace_point_added(self, record):
        """Sampling-thread callback. Tk-safe via root.after()."""
        if self.sampling_thread is None:
            return
        # Update will be picked up by _refresh_buttons label rendering.
        self.root.after(0, self._refresh_buttons)

    def _on_trace_joint_warning(self, idx, val, msg):
        target = self.recording_target or "?"
        self.log_safe(f"  ⚠ joint warning ({target}): {msg}")

    def _on_trace_error(self, e):
        target = self.recording_target or "?"
        self.log_safe(f"  ⚠ sampling error ({target}): {e}")

    def _stop_sampling_thread_if_running(self):
        """Stop the active sampling thread (used by Save / Abort / Exit)."""
        if self.sampling_thread is None:
            return
        target = self.recording_target
        try:
            self.sampling_thread.stop(join_timeout=2.0)
            new_points = self.sampling_thread.get_points()
            if target is not None:
                self.dt_traces[target].extend(new_points)
                self.log_safe(f"  trace ({target}) halted. "
                              f"accumulated: "
                              f"{len(self.dt_traces[target])}")
        except Exception as e:
            self.log_safe(f"  trace stop failed: {e}")
        finally:
            self.sampling_thread = None
            self.recording_target = None

    @staticmethod
    def _fit_line_y_vs_z(points):
        """Fit Z = m*Y + c to a list of v3 sample records.

        Each record has pen_yz_mm = [y, z]. Returns (m, c) tuple, or None
        if fewer than 2 points (line undetermined).
        """
        yzs = [p["pen_yz_mm"] for p in points
               if isinstance(p, dict) and "pen_yz_mm" in p]
        if len(yzs) < 2:
            return None
        Y = np.array([yz[0] for yz in yzs], dtype=float)
        Z = np.array([yz[1] for yz in yzs], dtype=float)
        A = np.vstack([Y, np.ones_like(Y)]).T
        m, c = np.linalg.lstsq(A, Z, rcond=None)[0]
        return float(m), float(c)

    def _diagonal_intersection_yz(self):
        """Intersect the two B3 diagonal line fits. Returns (y, z) or None.

        Returns None if either diagonal has fewer than 2 points, or if
        the two lines are near-parallel (degenerate).
        """
        l1 = self._fit_line_y_vs_z(self.dt_traces["diagonal_tl_br"])
        l2 = self._fit_line_y_vs_z(self.dt_traces["diagonal_tr_bl"])
        if l1 is None or l2 is None:
            return None
        m1, c1 = l1
        m2, c2 = l2
        if abs(m1 - m2) < 1e-6:
            return None  # near-parallel
        y = (c2 - c1) / (m1 - m2)
        z = m1 * y + c1
        return (float(y), float(z))

    def _exit_master(self):
        # Stop the B2 sampling thread first (it reads the listener).
        self._stop_sampling_thread_if_running()
        self.log_safe("Sending MasterSlaveConfig(0xFC, 0, 0, 0) ...")
        try:
            self.piper.MasterSlaveConfig(0xFC, 0, 0, 0)
            time.sleep(0.5)
        except Exception as e:
            self.log_safe(f"  exit 0xFC failed: {e}")
        if self.listener is not None:
            self.listener.stop()
            self.listener = None
        self.in_master = False

    def _fit_and_save(self):
        # Combined point set for plane fit: 4 corners + plane extras.
        # ---- Build v3 records ----
        # Legacy capture dict has joints_deg + xyz_mm + rpy_deg + timestamp.
        # v3 record needs pen_yz_mm + joints_deg + end_pose_mm_deg + timestamp.
        # B1 corners use the capture-dict shape; B2 perimeter samples are
        # already in v3 format (DragSamplingThread emits them directly).
        def _capture_to_v3(p):
            return make_point(
                pen_yz_mm=(p["xyz_mm"][1], p["xyz_mm"][2]),
                joints_deg=p["joints_deg"],
                end_pose_mm_deg=list(p["xyz_mm"]) + list(p["rpy_deg"]),
                timestamp=p.get("timestamp"),
            )

        # Collect XYZ for plane fit: corners + perimeter (+ future b3/b5).
        def _xyz_of(rec_or_capture):
            if "xyz_mm" in rec_or_capture:
                return rec_or_capture["xyz_mm"]
            ep = rec_or_capture["end_pose_mm_deg"]
            return [float(ep[0]), float(ep[1]), float(ep[2])]

        all_fit_pts = ([self.dt_corners[k] for k in self.CORNER_ORDER]
                       + list(self.dt_traces["perimeter"])
                       + list(self.dt_traces["diagonal_tl_br"])
                       + list(self.dt_traces["diagonal_tr_bl"])
                       + list(self.dt_traces["surface"]))
        pts = np.array([_xyz_of(p) for p in all_fit_pts])
        centroid = pts.mean(axis=0)
        centered = pts - centroid
        _, _, vh = np.linalg.svd(centered, full_matrices=False)
        normal = vh[-1] / np.linalg.norm(vh[-1])
        if normal[0] > 0:
            normal = -normal
        distances = centered @ normal
        rms = float(np.sqrt(np.mean(distances ** 2)))

        # whiteboard center = mean of 4 corners
        c_arr = np.array([self.dt_corners[k]["xyz_mm"]
                          for k in self.CORNER_ORDER])
        wb_center = c_arr.mean(axis=0)
        # whiteboard size (mean of opposite edge lengths along Y and Z)
        tl = c_arr[0]; tr = c_arr[1]; br = c_arr[2]; bl = c_arr[3]
        width_top    = abs(tr[1] - tl[1])
        width_bottom = abs(br[1] - bl[1])
        height_left  = abs(tl[2] - bl[2])
        height_right = abs(tr[2] - br[2])
        width_mm  = (width_top + width_bottom) / 2.0
        height_mm = (height_left + height_right) / 2.0

        n_corners = len(self.dt_corners)
        n_perimeter = len(self.dt_traces["perimeter"])
        n_diag_tlbr = len(self.dt_traces["diagonal_tl_br"])
        n_diag_trbl = len(self.dt_traces["diagonal_tr_bl"])
        n_surface = len(self.dt_traces["surface"])
        n_total = n_corners + n_perimeter + n_diag_tlbr + n_diag_trbl + n_surface

        corners_v3 = {k: _capture_to_v3(self.dt_corners[k])
                      for k in self.CORNER_ORDER}
        # B2 perimeter (and B3/B5 in future steps) are already v3-shaped.
        traces_v3 = {k: list(self.dt_traces[k])
                     for k in ("perimeter", "diagonal_tl_br",
                               "diagonal_tr_bl", "surface")}
        corner_avg_yz = [round(float(wb_center[1]), 2),
                         round(float(wb_center[2]), 2)]
        computed_v3 = {
            # B4 user-confirmation comes in Step 6. Until then the center
            # is the four-corner average (same as v2 behaviour).
            "center_mm": list(corner_avg_yz),
            "center_corner_avg_mm": list(corner_avg_yz),
            "width_mm": round(float(width_mm), 2),
            "height_mm": round(float(height_mm), 2),
        }
        # B3 diagonal intersection (if both diagonals have data)
        center_calc = self._diagonal_intersection_yz()
        if center_calc is not None:
            computed_v3["center_calc_mm"] = [round(center_calc[0], 2),
                                              round(center_calc[1], 2)]
            computed_v3["center_adjustment_mm"] = [
                round(center_calc[0] - corner_avg_yz[0], 2),
                round(center_calc[1] - corner_avg_yz[1], 2),
            ]
            self.log_safe(f"  center_calc (B3 intersection): "
                          f"Y={center_calc[0]:.1f} Z={center_calc[1]:.1f}  "
                          f"vs corner_avg "
                          f"Y={corner_avg_yz[0]:.1f} "
                          f"Z={corner_avg_yz[1]:.1f}")
        plane_fit_v3 = {
            "normal": [round(float(normal[i]), 4) for i in range(3)],
            "centroid_mm": [round(float(centroid[i]), 2) for i in range(3)],
            "rms_residual_mm": round(rms, 3),
            "n_points": int(pts.shape[0]),
            "source_breakdown": {
                "corners": n_corners,
                "perimeter": n_perimeter,
                "diagonal_tl_br": n_diag_tlbr,
                "diagonal_tr_bl": n_diag_trbl,
                "surface": n_surface,
                "center": 0,
            },
        }
        write_v3(
            OUTPUT_YAML,
            corners=corners_v3,
            traces=traces_v3,
            computed=computed_v3,
            plane_fit=plane_fit_v3,
        )
        self.log_safe(f"SAVED: {OUTPUT_YAML} (schema v3)")
        self.log_safe(f"  corners: {n_corners}/4, perimeter: "
                      f"{n_perimeter}, surface: {n_surface}, "
                      f"total: {n_total}, RMS: {rms:.2f} mm")
        self.log_safe(f"  whiteboard center: X={centroid[0]:.1f} "
                      f"Y={wb_center[1]:.1f} Z={wb_center[2]:.1f}")
        self.log_safe(f"  whiteboard size: {width_mm:.1f} x {height_mm:.1f} mm")
        self.log_safe(f"  plane normal: [{normal[0]:+.3f} {normal[1]:+.3f} "
                      f"{normal[2]:+.3f}]")

    def _show_master_exit_dialog(self):
        def show():
            do_restart = messagebox.askokcancel(
                "電源リセット + GUI 再起動が必要",
                "マスターモードを終了しました。 アームを復帰させるには:\n\n"
                "  ① アームを支えてください (今いる位置で停止)\n"
                "  ② アームの電源を OFF → 30 秒待機 → ON\n"
                "  ③ ターミナルで CAN を up し直す:\n"
                "     sudo ip link set can0 type can bitrate 1000000\n"
                "     sudo ip link set can0 up\n\n"
                "上記 ①②③ が完了してから [OK] を押すと "
                "GUI を自動再起動します。\n"
                "(まだ電源リセットが終わっていないなら [キャンセル] で "
                "ここに留まり、 後で右上の 「GUI 再起動」 ボタンを押す)",
                default=messagebox.CANCEL)
            if do_restart:
                self._restart_gui_now()
        self.root.after(0, show)

    # ------------------------------------------------------------------
    # Tune X
    # ------------------------------------------------------------------
    def _check_at_home_or_warn(self, action_name="この操作"):
        """ホーム/wall-facing 姿勢でなければ popup 警告して False を返す。
        tune_x_active 中 (B4 含む) は中央に押し付け中なので例外扱い。
        """
        if self.tune_x_active:
            return True
        if self._at_ready_pose():
            return True
        if self._at_pose(WALL_FACING_JOINTS_DEG, tol_deg=2.0):
            return True
        messagebox.showwarning("ホーム位置未到達",
            f"{action_name} の前に 「ホーム/撮影位置へ」 を押して "
            "アームをホーム姿勢にしてください。")
        return False

    def on_go_center(self):
        try:
            cy = float(self.var_cy.get())
            cz = float(self.var_cz.get())
        except Exception:
            messagebox.showerror("入力不正", "中央 Y/Z は数値で入力して "
                                           "ください。")
            return
        if not self._check_at_home_or_warn("中央へ移動"):
            return
        movel_eff = int(round(self._speed_joint() * self._movel_factor()))
        if not messagebox.askyesno("中央へ移動",
                f"中央 (Y={cy:.1f}, Z={cz:.1f}) へ移動しますか?\n"
                f"  関節速度 {self._speed_joint()}%\n"
                f"  直線 (MOVE L) 実効速度 {movel_eff}% = "
                f"{self._speed_joint()}% × {self._movel_factor():.1f}\n"
                f"  ペン上げ X = {self._pen_up_x():.1f} mm"):
            return
        self._run_in_thread(self._do_go_center, cy, cz)

    def _do_go_center(self, cy, cz):
        self._ensure_wall_facing()
        pen_up_x = self._pen_up_x()
        self.log_safe(f"中央へ移動 (ペン上げ、 IK 低速): "
                      f"X={pen_up_x:.1f} Y={cy:.1f} Z={cz:.1f}")
        q_target = self._move_xyz_via_ik(pen_up_x, cy, cz)
        self.tune_x_active = True
        self.tune_x_pen_down = False
        self.tune_y = cy
        self.tune_z = cz
        self.tune_warm_q = q_target
        self.log_safe("  → 中央に到着 (ペン上げ状態)。 "
                      "「ペン下げ」 で接触、 X/Y/Z spinbox で微調整。")

    def on_tune_pen_down(self):
        """Section 3: 「ペン下げ」 button. Lowers pen at current Y/Z."""
        if not self.tune_x_active:
            messagebox.showerror("中央未到着",
                "先に 「中央へ移動」 を押してください。")
            return
        if self.tune_x_pen_down:
            messagebox.showinfo("既にペン下げ済み",
                "すでにペン下げ状態です。")
            return
        self._run_in_thread(self._do_tune_pen_down)

    def _do_tune_pen_down(self):
        draw_x = self._draw_x()
        self.log_safe(f"✏ ペン下げ (IK 低速): X={draw_x:.1f}(押し付け量 "
                      f"{MAX_PUSH_MM}mm + 補正 {self._xoff():+.1f}mm)")
        q = self._move_xyz_via_ik(draw_x, self.tune_y, self.tune_z,
                                   warm_start_q=self.tune_warm_q)
        self.tune_warm_q = q
        self.tune_x_pen_down = True

    def on_nudge_x(self, step):
        if not self.tune_x_active:
            messagebox.showerror("中央調整 未開始",
                "先に 「中央へ移動」 を押してください。")
            return
        new = self._xoff() + step
        if new < -20.0 or new > 20.0:
            messagebox.showwarning("範囲外",
                f"X 補正 {new:+.1f} mm は [-20, +20] mm 安全範囲外。")
            return
        self._run_in_thread(self._do_nudge_x, step)

    def _on_tune_x_spin(self):
        """X 押し付け補正 spinbox の ▲▼ クリックで発火 → アーム追従。"""
        if not self.tune_x_active or self.busy:
            return
        self._run_in_thread(self._do_tune_x_update)

    def _do_tune_x_update(self):
        new_xoff = self._xoff()
        x = (self.contact_x_mm + MAX_PUSH_MM + new_xoff) \
            if self.tune_x_pen_down \
            else (self.contact_x_mm - PEN_UP_CLEAR_MM + new_xoff)
        tag = "pen-down" if self.tune_x_pen_down else "pen-up"
        self.log_safe(f"  X 補正更新 {new_xoff:+.2f} → X={x:.1f} ({tag})"
                      f" [IK 低速]")
        q = self._move_xyz_via_ik(x, self.tune_y, self.tune_z,
                                   warm_start_q=self.tune_warm_q,
                                   ik_iters=200, settle_s=1.5)
        self.tune_warm_q = q

    def _on_tune_y_spin(self):
        if not self.tune_x_active or self.busy:
            return
        self._run_in_thread(self._do_tune_yz_update)

    def _on_tune_z_spin(self):
        if not self.tune_x_active or self.busy:
            return
        self._run_in_thread(self._do_tune_yz_update)

    def _do_tune_yz_update(self):
        try:
            self.tune_y = float(self.var_cy.get())
            self.tune_z = float(self.var_cz.get())
        except Exception:
            return
        xoff = self._xoff()
        x = (self.contact_x_mm + MAX_PUSH_MM + xoff) \
            if self.tune_x_pen_down \
            else (self.contact_x_mm - PEN_UP_CLEAR_MM + xoff)
        tag = "pen-down" if self.tune_x_pen_down else "pen-up"
        self.log_safe(f"  中央 Y/Z 更新 ({self.tune_y:.1f}, "
                      f"{self.tune_z:.1f}) ({tag}) [IK 低速]")
        q = self._move_xyz_via_ik(x, self.tune_y, self.tune_z,
                                   warm_start_q=self.tune_warm_q,
                                   ik_iters=200, settle_s=1.5)
        self.tune_warm_q = q

    def _do_nudge_x(self, step):
        new = self._xoff() + step
        self.root.after(0, lambda v=new: self.var_xoff.set(round(v, 2)))
        x = (self.contact_x_mm + MAX_PUSH_MM + new) if self.tune_x_pen_down \
            else (self.contact_x_mm - PEN_UP_CLEAR_MM + new)
        tag = "pen-down" if self.tune_x_pen_down else "pen-up"
        self.log_safe(f"  X 微調整 {step:+.1f} → 補正 {new:+.2f} → "
                      f"X={x:.1f} ({tag}) [IK 低速]")
        q = self._move_xyz_via_ik(x, self.tune_y, self.tune_z,
                                   warm_start_q=self.tune_warm_q)
        self.tune_warm_q = q

    def on_lift_pen(self):
        if not self.tune_x_active or not self.tune_x_pen_down:
            return
        self._run_in_thread(self._do_lift_pen)

    def _do_lift_pen(self):
        pen_up_x = self._pen_up_x()
        self.log_safe(f"ペン上げ (IK 低速): X={pen_up_x:.1f}")
        q = self._move_xyz_via_ik(pen_up_x, self.tune_y, self.tune_z,
                                   warm_start_q=self.tune_warm_q)
        self.tune_warm_q = q
        self.tune_x_pen_down = False

    # ------------------------------------------------------------------
    # ===== Step 6 (B4) Center Adjustment =====
    # Runs OUTSIDE master mode after Save+restart. Reads center candidates
    # from the loaded yaml, lets the user MOVE L to one of them pen-down,
    # nudges Y/Z to visually match the canvas center, then writes the
    # confirmed value back to yaml.whiteboard_computed.center_mm.
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 四つ角微調整 (旧 B4 中央調整の代替): yaml の 4 隅を 1 つずつ
    # ペン下げで訪問し、 Y/Z spinbox で位置を微調整して上書き保存。
    # ------------------------------------------------------------------
    def on_corner_adj_goto(self, corner_key):
        """corner_key in {'tl','tr','br','bl'} の保存済み隅へペン下げで移動。"""
        try:
            calib = read_calibration(OUTPUT_YAML)
        except Exception as e:
            messagebox.showerror("キャリブ未確定", f"yaml 読込失敗: {e}")
            return
        wb = calib.get("whiteboard_corners_mm") or {}
        if corner_key not in wb:
            messagebox.showerror("隅データなし",
                f"yaml に {corner_key.upper()} がありません。 "
                "先にキャリブで 4 隅を記録してください。")
            return
        if not self._check_at_home_or_warn(f"{corner_key.upper()} へ移動"):
            return
        y = float(wb[corner_key][1])
        z = float(wb[corner_key][2])
        self.root.after(0, lambda v=y: self.var_corner_adj_y.set(round(v, 2)))
        self.root.after(0, lambda v=z: self.var_corner_adj_z.set(round(v, 2)))
        self.corner_adj_key = corner_key
        self.corner_adj_warm_q = None
        self._run_in_thread(self._do_corner_adj_goto, corner_key, y, z)

    def _do_corner_adj_goto(self, corner_key, y, z):
        self._ensure_wall_facing()
        draw_x = self._draw_x()
        jp = {"tl": "左上", "tr": "右上", "br": "右下",
              "bl": "左下"}.get(corner_key, corner_key)
        self.log_safe(f"{jp} ({corner_key.upper()}) へペン下げで移動 "
                      f"(IK 低速): X={draw_x:.1f} Y={y:.2f} Z={z:.2f}")
        try:
            q = self._move_xyz_via_ik(draw_x, y, z)
            self.corner_adj_warm_q = q
            self.log_safe("  到着。 Y/Z spinbox で微調整 → 「yaml 更新」。")
        except Exception as e:
            self.log_safe(f"  ❌ 移動失敗: {e}")
            self.corner_adj_key = None
        self._refresh_buttons_safe()

    def _on_corner_adj_spin(self):
        if self.corner_adj_key is None or self.busy:
            return
        self._run_in_thread(self._do_corner_adj_follow)

    def _do_corner_adj_follow(self):
        try:
            y = float(self.var_corner_adj_y.get())
            z = float(self.var_corner_adj_z.get())
        except Exception:
            return
        draw_x = self._draw_x()
        self.log_safe(f"  四つ角微調整 spinbox → (Y={y:.2f}, Z={z:.2f}, "
                      f"X={draw_x:.1f}) [IK 低速]")
        try:
            q = self._move_xyz_via_ik(draw_x, y, z,
                                       warm_start_q=self.corner_adj_warm_q,
                                       ik_iters=200, settle_s=1.5,
                                       chained=True)
            self.corner_adj_warm_q = q
        except Exception as e:
            self.log_safe(f"  follow 失敗: {e}")

    def on_corner_adj_confirm(self):
        if self.corner_adj_key is None:
            messagebox.showerror("移動未実行",
                "先に 左上/右上/右下/左下 のボタンで隅へ移動してください。")
            return
        try:
            new_y = float(self.var_corner_adj_y.get())
            new_z = float(self.var_corner_adj_z.get())
        except Exception as e:
            messagebox.showerror("入力不正", f"Y/Z 不正: {e}")
            return
        jp = {"tl": "左上", "tr": "右上", "br": "右下",
              "bl": "左下"}.get(self.corner_adj_key, "?")
        if not messagebox.askyesno("yaml 更新",
                f"{jp} ({self.corner_adj_key.upper()}) の隅位置を "
                f"(Y={new_y:.2f}, Z={new_z:.2f}) で yaml に上書き保存 "
                "しますか?\n\n"
                "(X は元の値を保持、 関節角度などは取り直しになるので "
                "近似値が入ります)"):
            return
        self._run_in_thread(self._do_corner_adj_confirm, new_y, new_z)

    def _do_corner_adj_confirm(self, new_y, new_z):
        try:
            self._persist_corner(self.corner_adj_key, new_y, new_z)
            self.log_safe(f"  {self.corner_adj_key.upper()} 隅を yaml 更新: "
                          f"(Y={new_y:.2f}, Z={new_z:.2f})")
        except Exception as e:
            self.log_safe(f"  yaml 更新失敗: {e}")
            self.root.after(0, lambda m=str(e): messagebox.showerror(
                "保存失敗", f"yaml に書き込めません:\n{m}"))
            return
        # pen-up して モード終了
        try:
            pen_up_x = self._pen_up_x()
            self._move_xyz_via_ik(pen_up_x, new_y, new_z,
                                    warm_start_q=self.corner_adj_warm_q,
                                    chained=True)
        except Exception as e:
            self.log_safe(f"  pen-up 失敗: {e}")
        self.corner_adj_key = None
        self.corner_adj_warm_q = None
        self._refresh_buttons_safe()

    def on_corner_adj_cancel(self):
        if self.corner_adj_key is None:
            return
        self._run_in_thread(self._do_corner_adj_cancel)

    def _do_corner_adj_cancel(self):
        try:
            y = float(self.var_corner_adj_y.get())
            z = float(self.var_corner_adj_z.get())
            pen_up_x = self._pen_up_x()
            self._move_xyz_via_ik(pen_up_x, y, z,
                                    warm_start_q=self.corner_adj_warm_q,
                                    chained=True)
        except Exception as e:
            self.log_safe(f"  cancel pen-up 失敗: {e}")
        self.corner_adj_key = None
        self.corner_adj_warm_q = None
        self._refresh_buttons_safe()

    def _persist_corner(self, corner_key, new_y, new_z):
        """yaml の whiteboard_corners_mm[corner_key] を Y/Z だけ上書き保存。
        X / 関節角度は元の値を保持 (関節角度は本来再 IK が必要だが、
        小さな差異なら元の値で実用可)。
        """
        parsed = read_calibration(OUTPUT_YAML)
        if parsed.get("schema_version") != 3:
            raise RuntimeError(
                f"yaml v{parsed.get('schema_version')} は未対応。 v3 で再保存"
                "してから試してください。")
        raw = parsed["raw"]
        wb = raw.get("whiteboard_corners_mm") or {}
        if corner_key not in wb:
            raise RuntimeError(f"yaml に {corner_key} がありません")
        rec = dict(wb[corner_key])
        # end_pose_mm_deg = [x, y, z, rx, ry, rz]
        ep = list(rec.get("end_pose_mm_deg") or [200.0, 0.0, 300.0,
                                                  0.0, 0.0, 0.0])
        ep[1] = round(float(new_y), 4)
        ep[2] = round(float(new_z), 4)
        rec["end_pose_mm_deg"] = ep
        rec["pen_yz_mm"] = [round(float(new_y), 4),
                             round(float(new_z), 4)]
        wb[corner_key] = rec
        traces = raw.get("traces") or {}
        traces = {k: list(traces.get(k) or [])
                  for k in ("perimeter", "diagonal_tl_br",
                            "diagonal_tr_bl", "surface")}
        write_v3(
            OUTPUT_YAML,
            corners=wb,
            traces=traces,
            computed=raw.get("whiteboard_computed"),
            plane_fit=raw.get("plane_fit"),
            joint_map=raw.get("joint_map"),
        )

    # ------------------------------------------------------------------
    # Reset corner deltas
    # ------------------------------------------------------------------
    def on_save_tune_values(self):
        """中央調整 (X 押し付け補正 / 中央 Y / 中央 Z) を yaml に保存。
        center_mm は whiteboard_computed.center_mm に、 X 補正は
        whiteboard_computed.contact_x_offset_mm として保存。
        """
        try:
            xoff = float(self.var_xoff.get())
            cy = float(self.var_cy.get())
            cz = float(self.var_cz.get())
        except Exception as e:
            messagebox.showerror("入力不正", f"値が数値ではありません: {e}")
            return
        if not messagebox.askyesno("中央調整 保存",
                f"現在の中央調整値を canvas_calibration.yaml に保存しますか?\n\n"
                f"  X 押し付け補正: {xoff:+.2f} mm\n"
                f"  中央 Y: {cy:.2f} mm\n"
                f"  中央 Z: {cz:.2f} mm\n\n"
                "次回 GUI 起動時に自動で読み込まれます。"):
            return
        self._run_in_thread(self._do_save_tune_values, xoff, cy, cz)

    def _do_save_tune_values(self, xoff, cy, cz):
        try:
            parsed = read_calibration(OUTPUT_YAML)
            if parsed.get("schema_version") != 3:
                raise RuntimeError(
                    f"yaml v{parsed.get('schema_version')} は未対応。 "
                    "v3 で再保存してから試してください。")
            raw = parsed["raw"]
            computed = dict(raw.get("whiteboard_computed") or {})
            # center_mm を更新 (旧 B4 と同じ)
            corner_avg = computed.get("center_corner_avg_mm",
                                       computed.get("center_mm",
                                                     [0.0, 0.0]))
            if len(corner_avg) < 2:
                corner_avg = [0.0, 0.0]
            computed["center_mm"] = [round(float(cy), 2),
                                      round(float(cz), 2)]
            computed["center_adjustment_mm"] = [
                round(float(cy) - float(corner_avg[0]), 2),
                round(float(cz) - float(corner_avg[1]), 2),
            ]
            # X 押し付け補正を追加保存
            computed["contact_x_offset_mm"] = round(float(xoff), 2)
            # traces 互換性 (write_v3 が要求するキー全部入り)
            traces = raw.get("traces") or {}
            traces = {k: list(traces.get(k) or [])
                      for k in ("perimeter", "diagonal_tl_br",
                                "diagonal_tr_bl", "surface")}
            write_v3(
                OUTPUT_YAML,
                corners=raw["whiteboard_corners_mm"],
                traces=traces,
                computed=computed,
                plane_fit=raw.get("plane_fit"),
                joint_map=raw.get("joint_map"),
            )
            self.log_safe(f"中央調整 保存: xoff={xoff:+.2f}, "
                          f"center=({cy:.2f}, {cz:.2f})")
        except Exception as e:
            self.log_safe(f"❌ 中央調整 保存失敗: {e}")
            self.root.after(0, lambda m=str(e): messagebox.showerror(
                "保存失敗", f"yaml 書き込み失敗:\n{m}"))

    def on_reset_corners(self):
        for v in self.var_dy + self.var_dz:
            v.set(0.0)
        self.log("Corner ΔΥΔΖ reset to 0.")

    # ------------------------------------------------------------------
    # Draw Square
    # ------------------------------------------------------------------
    def _build_corners(self):
        cy = float(self.var_cy.get())
        cz = float(self.var_cz.get())
        side = float(self.var_side.get())
        h = side / 2.0
        nominal = [
            (cy - h, cz - h),
            (cy + h, cz - h),
            (cy + h, cz + h),
            (cy - h, cz + h),
        ]
        return [
            (nominal[i][0] + float(self.var_dy[i].get()),
             nominal[i][1] + float(self.var_dz[i].get()))
            for i in range(4)
        ]

    def on_draw_square(self):
        try:
            corners = self._build_corners()
        except Exception as e:
            messagebox.showerror("入力不正", f"パラメータ不正: {e}")
            return
        if not self._check_at_home_or_warn("正方形描画"):
            return
        if self.tune_x_active:
            if not messagebox.askyesno("微調整中",
                    "アームがキャンバス押し付け中です。 描画前に "
                    "壁向きへ MOVE J して戻ります。 続行?"):
                return
        xoff = self._xoff()
        draw_x = self.contact_x_mm + MAX_PUSH_MM + xoff
        pen_up_x = self.contact_x_mm - PEN_UP_CLEAR_MM + xoff
        msg = (f"中心に正方形 を描画します\n"
               f"  関節速度 {self._speed_joint()}% (IK + MOVE J 低速描画)\n"
               f"  X 押し付け {xoff:+.2f} → draw X={draw_x:.1f}, "
               f"pen-up X={pen_up_x:.1f}\n"
               "  4 隅 (Y, Z):\n"
               + "\n".join(f"    C{i+1}: ({c[0]:+.2f}, {c[1]:+.2f})"
                           for i, c in enumerate(corners))
               + "\n\n続行?")
        if not messagebox.askyesno("正方形描画 確認", msg):
            return
        self._run_in_thread(self._do_draw_square, corners, xoff)

    def _do_draw_square(self, corners, xoff):
        self.log_safe(f"▶ 正方形描画 (IK + MOVE J 低速): "
                      f"関節速度 {self._speed_joint()}%, X offset {xoff:+.2f}")
        self._ensure_wall_facing()
        pen_up_x = self.contact_x_mm - PEN_UP_CLEAR_MM + xoff
        draw_x = self.contact_x_mm + MAX_PUSH_MM + xoff
        self.log_safe(f"  pen_up X={pen_up_x:.1f}  draw X={draw_x:.1f}")
        y0, z0 = corners[0]
        warm = self._move_xyz_via_ik(pen_up_x, y0, z0)
        warm = self._move_xyz_via_ik(draw_x, y0, z0, warm_start_q=warm)
        for i in [1, 2, 3, 0]:
            y, z = corners[i]
            warm = self._move_xyz_via_ik(draw_x, y, z, warm_start_q=warm)
        self._move_xyz_via_ik(pen_up_x, corners[0][0], corners[0][1],
                               warm_start_q=warm)
        target = self._active_ready_pose()
        tag = ("calibrated capture pose" if self.calibrated_ready_pose
               else "ready pose v2 default")
        self.log_safe(f"  MOVE J back to {tag} ...")
        self._move_joints(target)
        self.log_safe("  done.")
        self.tune_x_active = False
        self.tune_x_pen_down = False

    # ------------------------------------------------------------------
    # 試し書きモード: 中心に丸 / 三角 / 四隅合わせ正方形
    # ------------------------------------------------------------------
    def _canvas_local_axes_yz(self):
        """canvas_calibration.yaml の 4 隅から canvas 平面の orthonormal な
        U/V 軸を YZ で返す (panel_frame.yaml に依存しない簡易版)。

        u = 横方向 (BL→BR 等の平均)、 v = 縦方向 (u に直交、 上向き寄り)。
        Gram-Schmidt で u と v を直交化するので、 (du, dv) → (Y, Z) の
        マッピングが 「キャンバス平面上での真円 ⇄ YZ 上での真円 (回転のみ)」
        になる。 4 隅が完全な矩形でない (calibration tilt) 場合の楕円描画
        を防ぐ。 失敗時 None。
        """
        import math
        try:
            calib = read_calibration(OUTPUT_YAML)
        except Exception:
            return None
        wb = calib.get("whiteboard_corners_mm") or {}
        if not all(k in wb for k in ("tl", "tr", "br", "bl")):
            return None
        tl = wb["tl"]; tr = wb["tr"]; br = wb["br"]; bl = wb["bl"]
        # 横方向 U = (BR+TR) - (BL+TL) の平均
        du_y = (br[1] + tr[1]) / 2.0 - (bl[1] + tl[1]) / 2.0
        du_z = (br[2] + tr[2]) / 2.0 - (bl[2] + tl[2]) / 2.0
        u_len = math.sqrt(du_y * du_y + du_z * du_z)
        if u_len < 1.0:
            return None
        u_yz = (du_y / u_len, du_z / u_len)
        # 縦方向 V (生) = (TL+TR) - (BL+BR) の平均
        dv_y = (tl[1] + tr[1]) / 2.0 - (bl[1] + br[1]) / 2.0
        dv_z = (tl[2] + tr[2]) / 2.0 - (bl[2] + br[2]) / 2.0
        v_len = math.sqrt(dv_y * dv_y + dv_z * dv_z)
        if v_len < 1.0:
            return None
        v_raw = (dv_y / v_len, dv_z / v_len)
        # Gram-Schmidt で u に直交化
        dot = v_raw[0] * u_yz[0] + v_raw[1] * u_yz[1]
        v_orth = (v_raw[0] - dot * u_yz[0], v_raw[1] - dot * u_yz[1])
        vo_len = math.sqrt(v_orth[0] ** 2 + v_orth[1] ** 2)
        if vo_len < 0.01:
            return None
        v_yz = (v_orth[0] / vo_len, v_orth[1] / vo_len)
        return u_yz, v_yz

    def _build_circle_points(self, segments=24):
        """中心 (cy, cz) を中心に半径 = side/2 の円周点列。 閉路。
        canvas frame があれば、 キャンバスの U/V 軸 (横/縦) で展開して
        canvas 上で真円に見えるようにする (Y/Z 軸が canvas と回転して
        いる場合の楕円補正)。
        """
        import math
        cy = float(self.var_cy.get())
        cz = float(self.var_cz.get())
        r = float(self.var_side.get()) / 2.0
        frame = self._canvas_local_axes_yz()
        points = []
        for i in range(segments):
            theta = 2 * math.pi * i / segments
            du = r * math.cos(theta)
            dv = r * math.sin(theta)
            if frame is None:
                # fallback: Y/Z 軸で素直に
                y = cy + du
                z = cz + dv
            else:
                u_yz, v_yz = frame
                y = cy + du * u_yz[0] + dv * v_yz[0]
                z = cz + du * u_yz[1] + dv * v_yz[1]
            points.append((y, z))
        return points

    def _build_triangle_points(self):
        """中心 (cy, cz) に内接する正三角形の 3 頂点 (上向き)。 閉路。
        canvas frame で U/V 軸展開。
        """
        import math
        cy = float(self.var_cy.get())
        cz = float(self.var_cz.get())
        side = float(self.var_side.get())
        r = side / math.sqrt(3.0)
        # canvas local: 上頂点 (0, +r)、 左下 (-side/2, -r/2)、 右下 (+side/2, -r/2)
        local = [(0.0, r),
                 (-side / 2.0, -r / 2.0),
                 (+side / 2.0, -r / 2.0)]
        frame = self._canvas_local_axes_yz()
        out = []
        for (du, dv) in local:
            if frame is None:
                out.append((cy + du, cz + dv))
            else:
                u_yz, v_yz = frame
                y = cy + du * u_yz[0] + dv * v_yz[0]
                z = cz + du * u_yz[1] + dv * v_yz[1]
                out.append((y, z))
        return out

    def _build_corner_aligned_square(self, corner_code):
        """キャンバスの corner_code 隅 (TL/TR/BR/BL) に正方形の対応角を
        一致させた 4 点 (C1=BL→C2=BR→C3=TR→C4=TL の順) を返す。
        """
        calib = read_calibration(OUTPUT_YAML)
        wb = calib.get("whiteboard_corners_mm") or {}
        records = calib.get("whiteboard_corners_records") or {}
        key_map = {"TL": "tl", "TR": "tr", "BR": "br", "BL": "bl"}
        k = key_map[corner_code]
        if k not in wb:
            raise RuntimeError(
                f"canvas_calibration.yaml に {corner_code} ({k}) が "
                "見つかりません。 先にキャリブで 4 隅を記録してください。")
        # joint limit 警告 (margin_deg < 5°)
        rec = records.get(k) if isinstance(records, dict) else None
        if isinstance(rec, dict):
            margin = rec.get("margin_deg")
            if margin is not None and float(margin) < 5.0:
                self.log_safe(f"⚠ {corner_code} 隅は関節限界張付 "
                              f"(margin {float(margin):.1f}°)。 "
                              "描画位置はキャリブ時点と同じになります "
                              "(本来のキャンバス隅より内側かもしれません)")
        cy = float(wb[k][1])
        cz = float(wb[k][2])
        side = float(self.var_side.get())
        # corner_code に応じて square の対応角を canvas 隅に合わせ、
        # 残りの 3 角を内側 (canvas 中央寄り) に展開
        if corner_code == "TL":  # square TL = canvas TL、 右下方向へ展開
            tl = (cy, cz)
            bl = (cy, cz - side)
            br = (cy + side, cz - side)
            tr = (cy + side, cz)
        elif corner_code == "TR":
            tr = (cy, cz)
            br = (cy, cz - side)
            bl = (cy - side, cz - side)
            tl = (cy - side, cz)
        elif corner_code == "BR":
            br = (cy, cz)
            tr = (cy, cz + side)
            tl = (cy - side, cz + side)
            bl = (cy - side, cz)
        elif corner_code == "BL":
            bl = (cy, cz)
            br = (cy + side, cz)
            tr = (cy + side, cz + side)
            tl = (cy, cz + side)
        else:
            raise ValueError(f"unknown corner_code: {corner_code}")
        # C1=BL → C2=BR → C3=TR → C4=TL の順
        return [bl, br, tr, tl]

    def _confirm_and_draw_path(self, points, label, settle_each,
                                stream_ms=None):
        """共通: ready pose チェック → 確認ダイアログ → 別スレッドで描画。

        stream_ms=None: 各セグメント settle_each 秒で MOVE L 完了待ち
        stream_ms=int:  Section 5 と同じく EndPoseCtrl を直接ストリーム
                        (点間 stream_ms ms)、 滑らかな曲線描画
        """
        if not self._check_at_home_or_warn("テスト描画"):
            return
        if self.tune_x_active:
            if not messagebox.askyesno("微調整中",
                    "アームがキャンバス押し付け中です。 描画前に "
                    "壁向きへ MOVE J して戻ります。 続行?"):
                return
        xoff = self._xoff()
        draw_x = self.contact_x_mm + MAX_PUSH_MM + xoff
        pen_up_x = self.contact_x_mm - PEN_UP_CLEAR_MM + xoff
        speed = self._speed_joint()
        preview = "\n".join(
            f"    P{i+1}: ({p[0]:+.2f}, {p[1]:+.2f})"
            for i, p in enumerate(points[:8]))
        if len(points) > 8:
            preview += f"\n    ... ({len(points)} 点)"
        msg = (f"{label} を描画します\n\n"
               f"  関節速度 {speed}% (MOVE J 連続描画)\n"
               f"  X 押し付け {xoff:+.2f} → draw X={draw_x:.1f}, "
               f"pen-up X={pen_up_x:.1f}\n"
               f"  経路点 ({len(points)} 点):\n"
               + preview + "\n\n続行?")
        if not messagebox.askyesno(f"{label} 描画確認", msg):
            return
        self._run_in_thread(self._do_draw_closed_path,
                            points, xoff, label, settle_each, stream_ms)

    def _do_draw_closed_path(self, points, xoff, label, settle_each,
                              stream_ms=None):
        """points (>=3) を閉路として IK + MOVE J で描画。 関節速度
        パラメータが honored なので低速描画可能。 近接点間は warm-start
        IK で高速収束。
        """
        speed = self._speed_joint()
        self.log_safe(f"▶ Drawing {label}: IK + MOVE J 低速描画 関節速度="
                      f"{speed}%, X offset {xoff:+.2f}, {len(points)} 点")
        self._ensure_wall_facing()
        pen_up_x = self.contact_x_mm - PEN_UP_CLEAR_MM + xoff
        draw_x = self.contact_x_mm + MAX_PUSH_MM + xoff
        y0, z0 = points[0]
        # pen-up で第 1 点へ → pen-down (初回は ModeCtrl 含む)
        warm = self._move_xyz_via_ik(pen_up_x, y0, z0)
        warm = self._move_xyz_via_ik(draw_x, y0, z0, warm_start_q=warm,
                                       chained=True)
        # 連続点: chained で ModeCtrl/ホールドをスキップ → 高速
        for i, (y, z) in enumerate(points[1:]):
            warm = self._move_xyz_via_ik(draw_x, y, z,
                                          warm_start_q=warm,
                                          ik_iters=120,
                                          settle_s=1.0,
                                          chained=True)
        # 閉じる: 第 1 点に戻る
        warm = self._move_xyz_via_ik(draw_x, y0, z0,
                                      warm_start_q=warm,
                                      ik_iters=120,
                                      settle_s=1.0,
                                      chained=True)
        # pen-up
        self._move_xyz_via_ik(pen_up_x, y0, z0, warm_start_q=warm,
                                chained=True)
        target = self._active_ready_pose()
        tag = ("calibrated capture pose" if self.calibrated_ready_pose
               else "ready pose v2 default")
        self.log_safe(f"  MOVE J back to {tag} ...")
        self._move_joints(target)
        self.log_safe("  done.")
        self.tune_x_active = False
        self.tune_x_pen_down = False

    def on_draw_circle(self):
        try:
            # 48 セグメント (直径 30mm で sagitta 0.03mm)
            points = self._build_circle_points(segments=48)
        except Exception as e:
            messagebox.showerror("Bad input", f"パラメータ不正: {e}")
            return
        self._confirm_and_draw_path(points, "中心に丸 (48 点)",
                                    settle_each=0.0)

    def on_draw_triangle(self):
        try:
            points = self._build_triangle_points()
        except Exception as e:
            messagebox.showerror("Bad input", f"パラメータ不正: {e}")
            return
        self._confirm_and_draw_path(points, "中心に三角",
                                    settle_each=0.0)

    def on_draw_square_at_corner(self, corner_code):
        try:
            points = self._build_corner_aligned_square(corner_code)
        except Exception as e:
            messagebox.showerror("キャリブ未完", str(e))
            return
        jp_map = {"TL": "左上", "TR": "右上", "BR": "右下", "BL": "左下"}
        self._confirm_and_draw_path(points,
            f"{jp_map[corner_code]} ({corner_code}) 隅合わせ正方形",
            settle_each=2.5)

    # ------------------------------------------------------------------
    # Section 5 (full_dev): 生成画像描画ハンドラ
    # ------------------------------------------------------------------
    def on_strokes_select_file(self):
        """ストローク選択。

        StrokePicker (draw_piper の logs/ カード一覧 GUI) が import 出来れば
        そちらで選択。 失敗時は従来通り filedialog にフォールバック。
        """
        path = None

        # 主経路: StrokePicker (生成画像 + strokes.png サムネ + 題材表示)
        if StrokePicker is not None:
            try:
                selected = StrokePicker.show(self.root)
            except Exception as e:
                self.log(f"StrokePicker error → filedialog にフォールバック: {e}")
                selected = "__fallback__"
            else:
                if selected is None:
                    # キャンセル
                    return
                sj = selected.get("strokes_json")
                if sj is None:
                    messagebox.showwarning("strokes.json なし",
                        f"{selected.get('cycle_dir')} に strokes.json が "
                        "ありません (古いログ / 生成途中) — 別のものを選んで "
                        "ください。")
                    return
                path = str(sj)
                self.log(f"strokes.json 選択 (picker): {path}  "
                         f"題材={selected.get('subject', '?')}  "
                         f"ts={selected.get('timestamp', '?')}")

        # フォールバック: 従来 filedialog
        if path is None:
            import glob
            candidates = sorted(
                glob.glob("/home/jizaiedev2026/draw_piper/logs/"
                           "vlm_to_image_*/cycle_*/strokes.json")
                + glob.glob("/home/jizaiedev2026/draw_piper/logs/"
                             "testStroke/*.json")
            )
            init_dir = "/home/jizaiedev2026/draw_piper/logs"
            if candidates:
                init_dir = os.path.dirname(candidates[-1])
            path = filedialog.askopenfilename(
                title="strokes.json (または panel_uv.json) を選択",
                filetypes=[
                    ("ストローク JSON", "strokes.json"),
                    ("panel_uv mm JSON", "*_panel_uv.json"),
                    ("JSON files", "*.json"),
                    ("All", "*.*")],
                initialdir=init_dir)
            if not path:
                return
            self.log(f"strokes.json 選択 (filedialog): {path}")

        # 共通: 軽い検証 ('strokes' キーがあるか) + state 更新
        try:
            import json as _json
            with open(path) as f:
                data = _json.load(f)
            if "strokes" not in data:
                if not messagebox.askyesno("ファイル形式 ?",
                        f"ファイルに 'strokes' キーがありません:\n{path}\n\n"
                        "ストロークファイルではない可能性大ですが、 "
                        "それでも選択しますか?"):
                    return
        except Exception as e:
            messagebox.showerror("読込失敗", f"JSON 解析失敗:\n{e}")
            return
        self.var_strokes_json_path.set(path)
        # ボタンを即時 enable
        self._refresh_buttons_safe()

    def on_panel_convert(self):
        """canvas_calibration.yaml の 4 隅 → panel_frame.yaml の panel: 更新"""
        if not messagebox.askyesno("panel_frame.yaml 更新",
                "canvas_calibration.yaml の 4 隅から panel_frame.yaml の "
                "panel: ブロックを再生成します。 (phase_a_calibration は保護)\n\n"
                "続行?"):
            return
        self._run_in_thread(self._do_panel_convert)

    def _do_panel_convert(self):
        try:
            block = ctp_dev.convert_canvas_yaml_to_panel()
            diag = block.get("_diagnostics", {})
            self.log_safe(f"panel_frame.yaml 更新完了:")
            self.log_safe(f"  原点 (BL) = {block['origin_mm']}")
            self.log_safe(f"  size_mm = {block['size_mm']}  "
                          f"(実測 {diag.get('captured_width_mm')}×"
                          f"{diag.get('captured_height_mm')} mm)")
            self.log_safe(f"  直交誤差 = "
                          f"{diag.get('non_perpendicular_err_deg')}°")
        except Exception as e:
            self.log_safe(f"panel 変換失敗: {e}")
            self.root.after(0, lambda msg=str(e):
                messagebox.showerror("変換失敗", msg))

    def on_strokes_draw(self):
        path = self.var_strokes_json_path.get()
        if not path:
            messagebox.showerror("❌ ファイル未指定",
                "「選択...」で strokes.json を指定してください。")
            return
        if not os.path.exists(path):
            messagebox.showerror("❌ ファイルなし",
                f"見つかりません:\n{path}\n\n"
                "対処: パスが正しいか、 ファイルが移動されていないか確認。")
            return
        if not self.connected:
            messagebox.showerror("未接続",
                "アームに接続されていません。 「接続」 してから再度。")
            return
        if not self._check_at_home_or_warn("ストローク描画"):
            return
        msg = (f"ストローク描画を開始します。\n\n"
               f"  入力      : {os.path.basename(path)}\n"
               f"  最大本数  : {self.var_strokes_max.get() or '全部'}\n"
               f"  関節速度  : {self._speed_joint()}% (IK + MOVE J)\n\n"
               "事前確認:\n"
               "  ☐ アームの可動範囲に人や障害物がない\n"
               "  ☐ panel に紙が貼られている\n"
               "  ☐ ペンが付いていて contact_x 調整済\n"
               "  ☐ 緊急停止ボタンが手元にある\n\n"
               "⚠ アームが動きます。 続けますか?")
        if not messagebox.askyesno("⚠️ ストローク描画 確認", msg):
            return
        self.strokes_abort_flag = False
        # 新規開始: 再開状態リセット
        self.strokes_last_completed_idx = -1
        self.strokes_current_idx = -1
        self._run_in_thread(self._do_strokes_draw, 0)

    def on_strokes_preview(self):
        """strokes.json を読み込み、 別ウィンドウに matplotlib で
        プレビュー表示。 ペン軌跡 + 開始/終点マーカー。"""
        path = self.var_strokes_json_path.get()
        if not path:
            messagebox.showerror("❌ ファイル未指定",
                "「選択...」 で strokes.json を指定してください。")
            return
        if not os.path.exists(path):
            messagebox.showerror("❌ ファイルなし",
                f"見つかりません:\n{path}\n\n"
                "対処: パスが正しいか、 ファイルが移動されていないか確認。")
            return
        try:
            image_shape, strokes_px, meta = dsw_dev.load_strokes_json(path)
        except Exception as e:
            messagebox.showerror("読込失敗", f"strokes.json 読込失敗:\n{e}")
            return
        # Toplevel に Canvas でプレビュー描画 (matplotlib なしで OK)
        h, w = image_shape
        win = tk.Toplevel(self.root)
        win.title(f"ストローク プレビュー ({len(strokes_px)} 本)")
        # キャンバスサイズを ~600 px に揃える
        max_dim = 600.0
        scale = max_dim / max(w, h)
        cw = int(w * scale)
        ch = int(h * scale)
        c = tk.Canvas(win, width=cw, height=ch, bg="white")
        c.pack(padx=8, pady=8)
        for stroke_px in strokes_px:
            if len(stroke_px) < 2:
                continue
            # Tk Canvas は flatten 引数 (x1,y1,x2,y2,...)
            flat = []
            for (xpx, ypx) in stroke_px:
                flat.extend([xpx * scale, ypx * scale])
            c.create_line(*flat, fill="black", width=1)
            # 開始点 (緑) と終点 (赤)
            x0, y0 = stroke_px[0]
            xL, yL = stroke_px[-1]
            r = 2
            c.create_oval(x0 * scale - r, y0 * scale - r,
                          x0 * scale + r, y0 * scale + r,
                          outline="green", fill="green")
            c.create_oval(xL * scale - r, yL * scale - r,
                          xL * scale + r, yL * scale + r,
                          outline="red", fill="red")
        info = (f"画像 {w}×{h} px、 {len(strokes_px)} ストローク、 "
                f"{meta.get('n_points', '?')} 点。 緑=始点、 赤=終点")
        ttk.Label(win, text=info, font=("Monaco", 9)).pack(pady=(0, 6))

    def on_strokes_abort(self):
        self.strokes_abort_flag = True
        self.log("⛔ 描画中止を要求 (現在のストロークの終了後に停止)")

    # ------------------------------------------------------------------
    # Frida Smooth Draw (PR #2, claude/frida-smoothness-20260527)
    # ------------------------------------------------------------------
    def on_strokes_draw_smooth(self):
        """Frida-inspired multi-stroke smooth draw を別 Robot で実行する。

        既存 'on_strokes_draw' (IK + MOVE J chained) とは独立。 別 Robot
        インスタンスで draw_strokes_panel_smooth() を呼ぶ:
          - TSP greedy で stroke 順最適化
          - 曲率連動の 3-region 速度プロファイル (直線部加速 含む)
          - look-ahead descent height (近い stroke 間は pen-up 浅く)

        既存 GUI の SDK 接続と CAN bus を共有するので、 描画中に他のボタンを
        押さないこと。 安全のため確認ダイアログを出す。
        """
        if _DPRobot is None:
            messagebox.showerror("Frida 無効",
                "draw_piper の Robot import 失敗。 起動ログを確認してください。")
            return
        path = self.var_strokes_json_path.get()
        if not path:
            messagebox.showerror("❌ ファイル未指定",
                "「選択...」 で strokes.json を指定してください。")
            return
        if not os.path.exists(path):
            messagebox.showerror("❌ ファイルなし",
                f"見つかりません:\n{path}\n\n"
                "対処: パスが正しいか、 ファイルが移動されていないか確認。")
            return
        if not self._check_at_home_or_warn("Frida Smooth Draw"):
            return
        if not messagebox.askyesno(
            "Frida Smooth Draw",
            "Frida 拡張 (TSP + 曲率連動速度 + look-ahead descent) で\n"
            "別 Robot 経由で実機描画します。\n\n"
            "既存 GUI の SDK 接続と CAN bus を共有するので、 描画中は\n"
            "GUI の他のボタンを押さないでください。\n\n続けますか?"):
            return
        self.strokes_abort_flag = False
        self._run_in_thread(self._do_strokes_draw_smooth)

    def _do_strokes_draw_smooth(self):
        """Worker: draw_piper.Robot.draw_strokes_panel_smooth で描画。"""
        path = self.var_strokes_json_path.get()
        # 1. strokes.json をロード (既存 dsw_dev 経由、 px 座標)
        try:
            image_shape, strokes_px, meta = dsw_dev.load_strokes_json(path)
        except Exception as e:
            self.log_safe(f"[frida] strokes.json 読み込み失敗: {e}")
            return
        # 2. panel_frame.yaml から PanelFrame (draw_piper 版)
        try:
            panel = _DPPanelFrame.from_yaml(self._PANEL_YAML_PATH)
        except Exception as e:
            self.log_safe(f"[frida] panel_frame.yaml 読み込み失敗: {e}")
            return
        # 3. px → uv mm 変換 (Vectorizer.vectorize_to_panel と同じロジック、
        #    画像左上原点 / panel 左下原点で Y 反転)
        h, w = image_shape
        wu, hv = panel.size_mm[0], panel.size_mm[1]
        scale_u = wu / w
        scale_v = hv / h
        strokes_mm = []
        for stroke_px in strokes_px:
            s_uv = []
            for (x_px, y_px) in stroke_px:
                u = x_px * scale_u
                v = (h - y_px) * scale_v
                if panel.in_bounds(u, v):
                    s_uv.append((u, v))
            if len(s_uv) >= 2:
                strokes_mm.append(s_uv)
        self.log_safe(
            f"[frida] {len(strokes_mm)} strokes after px->mm + bounds filter "
            f"(input {len(strokes_px)}, image {w}x{h}, "
            f"panel {wu:.0f}x{hv:.0f}mm)")
        if not strokes_mm:
            self.log_safe("[frida] no strokes — abort")
            return
        # 4. Robot 接続 → 描画 → 切断
        robot = _DPRobot(mock=False, panel_frame=panel,
                          use_feedback_workaround=True)
        try:
            robot.connect(enable_motors=True)
            self.log_safe(
                "[frida] Robot connected (別 SDK instance、 既存接続と並存)")
            self.log_safe("[frida] moving to ready pose ...")
            robot.goto_ready_pose(speed_pct=15, settle_s=10.0)
            self.log_safe(
                f"[frida] draw_strokes_panel_smooth start "
                f"({len(strokes_mm)} strokes) ...")
            import time as _t
            t0 = _t.time()
            diag = robot.draw_strokes_panel_smooth(
                strokes_mm,
                draw_speed_base=30, draw_speed_min=10, draw_speed_max=50,
                travel_speed=60, near_threshold_mm=15.0, step_mm=2.0,
                reorder=True,
                merge_threshold_mm=0.0,   # 接続線描画は default OFF
                merge_pen_lift_mm=0.0,    # merge ON 時の pen 浮かしも default 0
                settle_s=1.0, arrival_tol_mm=2.0, arrival_timeout_s=15.0,
            )
            elapsed = _t.time() - t0
            self.log_safe(f"[frida] done in {elapsed:.1f}s")
            self.log_safe(
                f"[frida]   n_arcs           = {diag.get('n_arcs')}")
            self.log_safe(
                f"[frida]   travel saved (mm)= "
                f"{diag.get('travel_saved_mm', 0):.1f}")
            self.log_safe(
                f"[frida]   speed mean (pct) = "
                f"{diag.get('speed_mean_pct', 0):.1f} "
                f"(range {diag.get('speed_min_pct')}-"
                f"{diag.get('speed_max_pct')})")
            self.log_safe("[frida] returning to ready pose ...")
            robot.goto_ready_pose(speed_pct=15, settle_s=6.0)
            self.log_safe("[frida] ✅ 完了")
        except Exception as e:
            self.log_safe(f"[frida] ❌ ERROR: {e}")
        finally:
            try:
                robot.disconnect()
                self.log_safe("[frida] Robot disconnected")
            except Exception:
                pass

    def on_strokes_live_preview(self):
        """ストローク全体プレビュー + 現在描画中のストロークをハイライト。
        500ms 毎に self.strokes_current_idx を読んで再描画。
        """
        path = self.var_strokes_json_path.get()
        if not path or not os.path.exists(path):
            messagebox.showerror("❌ ファイル未指定",
                "「選択...」 で strokes.json を指定してください。")
            return
        try:
            data = json.loads(open(path).read())
        except Exception as e:
            messagebox.showerror("読込失敗", str(e))
            return
        strokes = data.get("strokes") or []
        if not strokes:
            messagebox.showerror("strokes なし", "ストロークがありません。")
            return
        meta = data.get("meta") or {}
        is_panel_uv = meta.get("coordinate_system") == "panel_uv_mm"
        if is_panel_uv:
            psize = (meta.get("panel_size_mm")
                      or data.get("panel_size_mm") or [200.0, 200.0])
            w_u, h_u = float(psize[0]), float(psize[1])
        else:
            ish = data.get("image_shape") or [1024, 1024]
            h_u, w_u = float(ish[0]), float(ish[1])
        max_dim = 600.0
        scale = max_dim / max(w_u, h_u)
        cw, ch = int(w_u * scale), int(h_u * scale)
        win = tk.Toplevel(self.root)
        win.title(f"ストローク 進捗プレビュー ({len(strokes)} 本)")
        c = tk.Canvas(win, width=cw, height=ch, bg="white")
        c.pack(padx=8, pady=8)
        lbl = ttk.Label(win, text="(未開始)",
                        font=("Monaco", 9))
        lbl.pack(pady=(0, 4))

        def render():
            if not win.winfo_exists():
                return
            c.delete("all")
            cur = int(getattr(self, "strokes_current_idx", -1))
            done = int(getattr(self, "strokes_last_completed_idx", -1))
            n_total = len(strokes)
            for i, stroke in enumerate(strokes):
                if len(stroke) < 2:
                    continue
                flat = []
                for pt in stroke:
                    x = pt[0] * scale
                    y = pt[1] * scale
                    if is_panel_uv:
                        y = ch - y
                    flat.extend([x, y])
                # 完了済 = 黒、 現在 = 赤太、 未来 = 灰
                if i <= done:
                    color, width = "black", 1
                elif i == cur:
                    color, width = "red", 3
                else:
                    color, width = "#ccc", 1
                c.create_line(*flat, fill=color, width=width)
            lbl.config(text=(
                f"現在: {cur + 1 if cur >= 0 else '-'} / 完了: "
                f"{done + 1 if done >= 0 else 0} / 全: {n_total}"))
            win.after(500, render)

        render()
        win.protocol("WM_DELETE_WINDOW", win.destroy)

    def on_strokes_resume(self):
        """中断した描画を続きから再開。"""
        path = self.var_strokes_json_path.get()
        if not path:
            messagebox.showerror("❌ ファイル未指定",
                "「選択...」 で strokes.json を指定してください。")
            return
        idx = int(self.strokes_last_completed_idx)
        if idx < 0:
            messagebox.showerror("再開不可",
                "再開できる中断状態がありません。 「描画開始」 から。")
            return
        start_from = idx + 1
        if not self._check_at_home_or_warn("ストローク描画 再開"):
            return
        if not messagebox.askyesno("再開",
                f"ストローク {start_from + 1} 本目から再開しますか?\n"
                f"前回 {idx + 1} 本まで完了。"):
            return
        self.strokes_abort_flag = False
        self._run_in_thread(self._do_strokes_draw, start_from)

    def _do_strokes_draw(self, start_from=0):
        """Section 5: 生成画像描画。 IK + MOVE J chained で低速描画、
        X 位置はユーザ調整済の contact_x + MAX_PUSH + xoff (= テスト描画
        と同じ) を使う。 panel 平面の Y/Z 軸方向は panel_frame.yaml から
        取得して、 ストロークの (u, v) を Y/Z にマップする。

        start_from: ストローク開始 index (0=最初から、 N=N 本目から再開)。
        中断後の 「再開」 用。
        """
        path = self.var_strokes_json_path.get()
        # 1. strokes.json をロード
        try:
            image_shape, strokes_px, meta = dsw_dev.load_strokes_json(path)
        except Exception as e:
            self.log_safe(f"strokes.json 読み込み失敗: {e}")
            return
        self.log_safe(f"strokes.json: 画像 {image_shape}、 "
                      f"{len(strokes_px)} ストローク、 "
                      f"{meta.get('n_points', '?')} 点")
        # 2. panel_frame.yaml から U/V 軸 + size を読む (Y/Z への mapping 用)
        try:
            with open(self._PANEL_YAML_PATH) as f:
                panel_data = yaml.safe_load(f) or {}
        except Exception as e:
            self.log_safe(f"panel_frame.yaml 読み込み失敗: {e}")
            return
        panel = panel_data.get("panel") or {}
        if not panel.get("calibrated"):
            if not messagebox.askyesno("panel 未確定",
                    "panel_frame.yaml の panel: が未確定 です。 "
                    "「panel_frame.yaml を更新」 を押してから再実行を "
                    "推奨。 このまま続行しますか?"):
                return
        origin = panel.get("origin_mm", [200.0, 0.0, 200.0])
        u_axis = panel.get("u_axis", [0.0, -1.0, 0.0])
        v_axis = panel.get("v_axis", [0.0, 0.0, 1.0])
        size_mm = panel.get("size_mm", [200.0, 250.0])
        size_w, size_h = float(size_mm[0]), float(size_mm[1])
        # 3. ストローク → UV mm 変換
        is_panel_uv = bool(meta.get("is_panel_uv"))
        strokes_uv = []
        n_dropped = 0
        if is_panel_uv:
            # 形式 B: panel_uv_mm。 JSON の panel size と実 canvas の size
            # が不一致の場合は アスペクト比を維持しつつ 実 canvas に
            # フィットするようスケール + 中心合わせ。
            json_psize = meta.get("panel_size_mm") or [size_w, size_h]
            json_w, json_h = float(json_psize[0]), float(json_psize[1])
            if abs(json_w - size_w) > 1.0 or abs(json_h - size_h) > 1.0:
                # アスペクト比維持で 縮小スケール (実 canvas に収まる範囲)
                scale = min(size_w / json_w, size_h / json_h)
                # 中心合わせ オフセット
                shift_u = (size_w - json_w * scale) / 2.0
                shift_v = (size_h - json_h * scale) / 2.0
                self.log_safe(
                    f"panel_size 不一致: JSON [{json_w:.1f}, {json_h:.1f}] "
                    f"vs canvas [{size_w:.1f}, {size_h:.1f}]。 "
                    f"x{scale:.3f} スケール + 中心配置 "
                    f"({shift_u:+.1f}, {shift_v:+.1f}) で fit。")
            else:
                scale = 1.0
                shift_u = 0.0
                shift_v = 0.0
            for stroke in strokes_px:
                uv = []
                for (u_mm, v_mm) in stroke:
                    u_fit = u_mm * scale + shift_u
                    v_fit = v_mm * scale + shift_v
                    if not (0.0 <= u_fit <= size_w
                            and 0.0 <= v_fit <= size_h):
                        n_dropped += 1
                        continue
                    uv.append((u_fit, v_fit))
                if len(uv) >= 2:
                    strokes_uv.append(uv)
            self.log_safe(
                f"panel_uv_mm 形式: {len(strokes_uv)} 本 "
                f"({n_dropped} 点が canvas 外で削除)")
        else:
            # 形式 A: px → uv mm
            h, w = image_shape
            for stroke_px in strokes_px:
                uv = []
                for (xpx, ypx) in stroke_px:
                    u_mm = xpx / w * size_w
                    v_mm = (h - ypx) / h * size_h
                    if not (0.0 <= u_mm <= size_w
                            and 0.0 <= v_mm <= size_h):
                        n_dropped += 1
                        continue
                    uv.append((u_mm, v_mm))
                if len(uv) >= 2:
                    strokes_uv.append(uv)
            self.log_safe(
                f"px→UV 変換: {len(strokes_uv)} 本の有効ストローク "
                f"(範囲外で {n_dropped} 点削除)")
        max_n = int(self.var_strokes_max.get() or 0)
        if max_n > 0:
            strokes_uv = strokes_uv[:max_n]
            self.log_safe(f"  先頭 {max_n} 本に制限")
        if not strokes_uv:
            self.log_safe("描く対象なし。 終了。")
            return
        # 4. UV → Y/Z 変換ヘルパ (X はテスト描画と同じ contact_x + push + xoff)
        # origin の Y, Z 成分 + u*u_axis[1,2] + v*v_axis[1,2] で平面内マッピング
        def uv_to_yz(u, v):
            y = origin[1] + u * u_axis[1] + v * v_axis[1]
            z = origin[2] + u * u_axis[2] + v * v_axis[2]
            return y, z
        # 5. X 位置はユーザ調整済み contact_x_mm + MAX_PUSH_MM + xoff (テスト
        #    描画と同じ)。 panel 平面の wall_offset は無視 = キャンバス面に
        #    確実に到達。
        xoff = self._xoff()
        draw_x = self.contact_x_mm + MAX_PUSH_MM + xoff
        pen_up_x = self.contact_x_mm - PEN_UP_CLEAR_MM + xoff
        self.log_safe(f"X: draw={draw_x:.1f}, pen-up={pen_up_x:.1f} "
                      f"(contact_x={self.contact_x_mm:.1f}, "
                      f"push={MAX_PUSH_MM}, xoff={xoff:+.1f})")
        # 6. 実機準備
        if not self._at_ready_pose():
            self.log_safe("ホーム位置にないので先に ホーム/撮影位置 へ "
                          "戻します")
            self._move_joints(self._active_ready_pose())
        self._ensure_wall_facing()
        # 7. ストロークを 1 本ずつ描く (IK + MOVE J chained, テスト描画
        #    と同じ低速描画)
        n_total = len(strokes_uv)
        t0 = time.time()
        warm = None
        last_completed_idx = start_from - 1  # 再開時の起点
        if start_from > 0:
            self.log_safe(f"▶ 再開: ストローク {start_from + 1}/{n_total} から")
        for i in range(start_from, n_total):
            stroke_uv = strokes_uv[i]
            if self.strokes_abort_flag:
                self.log_safe(f"⛔ 中止: {i}/{n_total} 本まで描画済み")
                break
            self.root.after(0, lambda i=i, n=n_total:
                self.lbl_strokes_progress.config(
                    text=f"進捗: {i}/{n} 本", foreground="blue"))
            # 現在描画中のストロークを記録 (再開 + プレビュー用)
            self.strokes_current_idx = i
            u0, v0 = stroke_uv[0]
            u_last, v_last = stroke_uv[-1]
            self.log_safe(
                f"  [{i+1}/{n_total}] {len(stroke_uv)} 点 "
                f"u[{u0:.1f}→{u_last:.1f}] v[{v0:.1f}→{v_last:.1f}]")
            y0, z0 = uv_to_yz(u0, v0)
            # 7a. 安全 X (SAFE_TRAVEL_X_MM) で次の点 YZ へ travel
            # (擦り防止: 直前のストローク端や wall_facing から離れた位置で
            # 移動してから approach する)
            try:
                warm = self._move_xyz_via_ik(SAFE_TRAVEL_X_MM, y0, z0,
                                              warm_start_q=warm,
                                              max_pos_err_mm=15.0)
            except Exception as e:
                self.log_safe(f"     safe travel 失敗: {e}")
                continue
            # 7b. pen-up X へ approach (まだキャンバスから離れた位置)
            try:
                warm = self._move_xyz_via_ik(pen_up_x, y0, z0,
                                              warm_start_q=warm,
                                              chained=True)
            except Exception as e:
                self.log_safe(f"     pen-up approach 失敗: {e}")
                continue
            # 7c. pen-down to first point
            try:
                warm = self._move_xyz_via_ik(draw_x, y0, z0,
                                              warm_start_q=warm,
                                              chained=True)
            except Exception as e:
                self.log_safe(f"     pen-down 失敗: {e}")
                continue
            # 7d. trace remaining points (ストリーミング = settle 待たず
            # JointCtrl 連送、 曲線が段階的に見える問題を解消)
            for (u, v) in stroke_uv[1:]:
                if self.strokes_abort_flag:
                    break
                yy, zz = uv_to_yz(u, v)
                warm = self._move_xyz_via_ik_stream(
                    draw_x, yy, zz, warm_start_q=warm,
                    ik_iters=120, inter_point_s=0.08)
            # 7e. 最後の点で短い settle (arm 完全停止確認)
            try:
                warm = self._move_xyz_via_ik(
                    draw_x, uv_to_yz(u_last, v_last)[0],
                    uv_to_yz(u_last, v_last)[1],
                    warm_start_q=warm, ik_iters=120,
                    settle_s=0.5, chained=True)
            except Exception:
                pass
            # 7f. pen-up at last point
            y_l, z_l = uv_to_yz(u_last, v_last)
            try:
                warm = self._move_xyz_via_ik(pen_up_x, y_l, z_l,
                                              warm_start_q=warm,
                                              chained=True)
            except Exception as e:
                self.log_safe(f"     pen-up 失敗: {e}")
            last_completed_idx = i  # 完了したのでカウントアップ
        # 中断 / 完了 後の状態を記録 (再開用)
        self.strokes_last_completed_idx = last_completed_idx
        elapsed = time.time() - t0
        n_drawn = last_completed_idx + 1
        if self.strokes_abort_flag:
            self.log_safe(f"中断: {n_drawn}/{n_total} 本完了、 "
                          f"経過 {elapsed:.1f}s。 「再開」 で続行可能。")
        else:
            self.log_safe(f"描画完了: {n_drawn}/{n_total} 本、 "
                          f"経過 {elapsed:.1f}s")
        self.root.after(0, lambda n=n_drawn, t=n_total:
            self.lbl_strokes_progress.config(
                text=f"進捗: {n}/{t} 本 "
                     + ("中断" if self.strokes_abort_flag else "完了"),
                foreground="orange" if self.strokes_abort_flag else "green"))
        # 7. 最後にホーム/撮影位置へ戻す
        try:
            self._move_joints(self._active_ready_pose())
            self.log_safe("ホーム/撮影位置 に復帰")
        except Exception as e:
            self.log_safe(f"  復帰失敗: {e}")


def main():
    root = tk.Tk()
    gui = WallDrawingGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
