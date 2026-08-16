"""scripts/calibrate_panel.py

Phase A キャリブ: カメラ画像上で 4 隅 (TL→TR→BR→BL の順) をクリックして、
calibration/panel_frame.yaml の `phase_a_calibration` ブロックに保存する。

既存の `panel:` ブロックには手を出さない (Phase B 側が使う)。

Usage:
    python scripts/calibrate_panel.py
    python scripts/calibrate_panel.py --panel-width-mm 230 --panel-height-mm 300
    python scripts/calibrate_panel.py --input-image /path/to/test.png  # カメラなしテスト

ステージ:
    Stage 1: capture (median)
    Stage 2: rotation preview        ← 新規。回転を GUI で決める
    Stage 3: 4-corner click          ← 回転後の画像でクリック
    Stage 4: warp preview            ← warp 結果を確認して保存判断

Keys:

  Stage 2 (rotation preview):
    r:        90 度ずつ回転 (0 → 90 → 180 → 270 → 0)
    Enter/Space: 現在の回転で確定 → Stage 3 へ
    q / ESC:  中断

  Stage 3 (click):
    左クリック: 点を追加 (WHITEBOARD 4 隅、TL → TR → BR → BL の順)
    u:        undo (直前の点を取り消し)
    d:        全点クリア
    Enter/s:  4 点揃ったら確定 → Stage 4
    q / ESC:  中断

  Stage 4 (warp preview):
    s:        保存して終了
    d:        Stage 3 (クリック) からやり直し
    r:        Stage 2 (回転) からやり直し
    q / ESC:  中断 (保存せず終了)

座標規約:
  panel mm 系の (0, 0) はホワイトボードの左下隅 (= +v は上向き)。
  panel UV (warp 出力) は 1024x1024 で、画像 (0, 0) が panel_mm (0, H)、
  画像 (W, H) が panel_mm (W, 0)。これは「ホワイトボードを正対して見た
  向き」と一致する。カメラを 90 度回しても rotation_deg で正対向きに
  揃えてから click するので、UI と panel mm の対応は常に直感的。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import logging
import sys
from pathlib import Path

import cv2
import numpy as np
import yaml

# allow running as `python scripts/calibrate_panel.py`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.camera import Camera, VALID_ROTATION_DEGS  # noqa: E402
from modules.panel_geometry import (   # noqa: E402
    parse_resolution,
    select_sdxl_bucket,
)


def _resolve_panel_image_size(
    arg: str | None, panel_w_mm: float, panel_h_mm: float,
) -> tuple[int, int]:
    """warp 出力サイズ (W, H) を決める。

    - None / "auto" : panel 実寸の aspect (w_mm/h_mm) に最も近い SDXL bucket。
      これで warp 出力が物理キャンバスと同じ縦横比になり (= 歪み無し)、
      かつ画像生成 (auto_from_panel) と同一サイズなので resize/stretch ゼロ。
    - "WxH"         : 明示指定 (縦長等)。
    - "N"           : 正方形 (N, N) (後方互換)。
    """
    if arg is None or str(arg).strip().lower() in ("", "auto"):
        (w, h), _ = select_sdxl_bucket(panel_w_mm / panel_h_mm)
        return (int(w), int(h))
    wh = parse_resolution(arg)
    if wh is None:
        (w, h), _ = select_sdxl_bucket(panel_w_mm / panel_h_mm)
        return (int(w), int(h))
    return wh

log = logging.getLogger(__name__)


# --- click UI ---
CORNER_LABELS = ["tl", "tr", "br", "bl"]
CORNER_DESC = {
    "tl": "TOP-LEFT of WHITEBOARD     (panel mm: (0, H))",
    "tr": "TOP-RIGHT of WHITEBOARD    (panel mm: (W, H))",
    "br": "BOTTOM-RIGHT of WHITEBOARD (panel mm: (W, 0))",
    "bl": "BOTTOM-LEFT of WHITEBOARD  (panel mm: (0, 0))",
}
COLORS = [
    (60, 60, 220),    # TL: red
    (60, 200, 220),   # TR: yellow
    (60, 220, 60),    # BR: green
    (220, 120, 60),   # BL: blue
]


# ============================================================================
# Stage 2: rotation preview
# ============================================================================

def _apply_rotation(image: np.ndarray, deg: int) -> np.ndarray:
    """deg ∈ {0, 90, 180, 270} で画像を回転して返す。"""
    if deg == 0:
        return image
    elif deg == 90:
        return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    elif deg == 180:
        return cv2.rotate(image, cv2.ROTATE_180)
    elif deg == 270:
        return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
    raise ValueError(f"rotation deg must be in {VALID_ROTATION_DEGS}, got {deg}")


def _draw_rotation_overlay(
    image: np.ndarray,
    rotation_deg: int,
    panel_w_mm: float,
    panel_h_mm: float,
) -> np.ndarray:
    """rotation preview 用の文字オーバーレイ。"""
    img = image.copy()
    h, w = img.shape[:2]
    text_lines = [
        f"Stage 2/4: rotation preview  ({rotation_deg} deg)",
        f"image size after rotation: {w} x {h}",
        f"panel size (whiteboard): {panel_w_mm} x {panel_h_mm} mm",
        "Make the whiteboard appear UPRIGHT (top of board at top of screen)",
        "[r] rotate 90 deg  [Enter/Space] accept -> click  [q] quit",
    ]
    overlay = img.copy()
    box_h = 22 * len(text_lines) + 14
    cv2.rectangle(overlay, (0, 0), (w, box_h), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.55, img, 0.45, 0, img)
    for i, line in enumerate(text_lines):
        cv2.putText(
            img, line, (12, 22 + 22 * i),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (240, 240, 240), 1, cv2.LINE_AA,
        )
    return img


def choose_rotation(
    image_bgr: np.ndarray,
    initial_deg: int,
    panel_w_mm: float,
    panel_h_mm: float,
    window: str = "calibrate_panel_rotation",
) -> tuple[int, np.ndarray] | None:
    """rotation preview UI。確定なら (deg, rotated_image)、中断なら None。

    画面上では「ホワイトボードが正対向きになるまで `r` で 90 度ずつ回し、
    Enter で確定」というフロー。確定後の画像が後続ステージで使われる。
    """
    deg = initial_deg
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)

    while True:
        rotated = _apply_rotation(image_bgr, deg)
        disp = _draw_rotation_overlay(rotated, deg, panel_w_mm, panel_h_mm)
        cv2.imshow(window, disp)

        key = cv2.waitKey(20) & 0xFF
        if key == 255:
            continue
        if key in (ord("q"), 27):  # q / ESC
            cv2.destroyWindow(window)
            return None
        if key == ord("r"):
            deg = (deg + 90) % 360
            log.info("rotation -> %d deg", deg)
        elif key in (13, ord(" ")):  # Enter / Space
            cv2.destroyWindow(window)
            return deg, rotated


# ============================================================================
# Stage 3: click UI
# ============================================================================

def _draw_click_overlay(
    base: np.ndarray,
    points: list[tuple[int, int]],
    panel_w_mm: float,
    panel_h_mm: float,
) -> np.ndarray:
    """既にクリックされた点と、次にクリックすべき隅のヒントを描画する。"""
    img = base.copy()
    h, w = img.shape[:2]

    # クリック済みの点
    for i, (px, py) in enumerate(points):
        color = COLORS[i]
        cv2.circle(img, (px, py), 8, color, 2)
        cv2.circle(img, (px, py), 2, color, -1)
        cv2.putText(
            img, f"{i+1}:{CORNER_LABELS[i]}", (px + 12, py - 8),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2, cv2.LINE_AA,
        )

    # 線で結ぶ (順番が見えるように)
    if len(points) >= 2:
        for i in range(len(points) - 1):
            cv2.line(img, points[i], points[i + 1], (180, 180, 180), 1, cv2.LINE_AA)
        if len(points) == 4:
            cv2.line(img, points[3], points[0], (180, 180, 180), 1, cv2.LINE_AA)

    # 次のヒント
    if len(points) < 4:
        next_label = CORNER_LABELS[len(points)]
        text_lines = [
            f"Stage 3/4: click {len(points)+1}/4 - {CORNER_DESC[next_label]}",
            f"panel size: {panel_w_mm} x {panel_h_mm} mm (whiteboard)",
            "[u] undo  [d] reset all  [q] quit",
        ]
    else:
        text_lines = [
            "Stage 3/4: all 4 corners set.",
            "[Enter/s] preview  [u] undo  [d] reset all  [q] quit",
        ]

    # 半透明背景
    overlay = img.copy()
    box_h = 22 * len(text_lines) + 14
    cv2.rectangle(overlay, (0, 0), (w, box_h), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.55, img, 0.45, 0, img)
    for i, line in enumerate(text_lines):
        cv2.putText(
            img, line, (12, 22 + 22 * i),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (240, 240, 240), 1, cv2.LINE_AA,
        )
    return img


class _ClickState:
    def __init__(self):
        self.points: list[tuple[int, int]] = []
        self.dirty = True
        self.last_xy: tuple[int, int] | None = None

    def on_mouse(self, event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN and len(self.points) < 4:
            self.points.append((x, y))
            self.dirty = True
        elif event == cv2.EVENT_MOUSEMOVE:
            self.last_xy = (x, y)


def collect_corner_clicks(
    image_bgr: np.ndarray,
    panel_w_mm: float,
    panel_h_mm: float,
    window: str = "calibrate_panel_click",
) -> list[tuple[int, int]] | None:
    """4 点クリック UI。確定なら 4 点のリストを返す、中断なら None。"""
    state = _ClickState()
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(window, state.on_mouse)

    while True:
        if state.dirty:
            disp = _draw_click_overlay(image_bgr, state.points, panel_w_mm, panel_h_mm)
            cv2.imshow(window, disp)
            state.dirty = False

        key = cv2.waitKey(20) & 0xFF
        if key == 255:
            continue
        if key in (ord("q"), 27):  # q / ESC
            cv2.destroyWindow(window)
            return None
        if key == ord("u"):
            if state.points:
                state.points.pop()
                state.dirty = True
        elif key == ord("d"):  # reset all (was "r" before)
            state.points.clear()
            state.dirty = True
        elif key in (ord("s"), 13):  # s / Enter
            if len(state.points) == 4:
                cv2.destroyWindow(window)
                return state.points
            else:
                log.warning("need 4 points (got %d)", len(state.points))


# ============================================================================
# Stage 4: warp preview
# ============================================================================

def _make_correspondences(
    points_px: list[tuple[int, int]],
    panel_w_mm: float,
    panel_h_mm: float,
) -> list[dict]:
    """4 点を click した順 (TL→TR→BR→BL) と panel_mm (左下原点、+v=上) を紐付ける。"""
    panel_mm = {
        "tl": [0.0, panel_h_mm],
        "tr": [panel_w_mm, panel_h_mm],
        "br": [panel_w_mm, 0.0],
        "bl": [0.0, 0.0],
    }
    out = []
    for label, (x, y) in zip(CORNER_LABELS, points_px):
        out.append({
            "camera_px": [float(x), float(y)],
            "panel_mm": panel_mm[label],
            "label": label,
        })
    return out


def _build_warp_preview(
    image_bgr: np.ndarray,
    correspondences: list[dict],
    panel_w_mm: float,
    panel_h_mm: float,
    panel_image_size: tuple[int, int],
) -> tuple[np.ndarray, dict]:
    """Preview として warp 結果と reprojection 誤差を計算。

    panel_image_size = (W_px, H_px)。 縦長キャンバスなら (704, 1472) 等の
    非正方形が渡る。
    """
    pw_px, ph_px = panel_image_size
    src = np.array(
        [c["camera_px"] for c in correspondences], dtype=np.float32,
    )
    dst_mm = np.array(
        [c["panel_mm"] for c in correspondences], dtype=np.float32,
    )
    # panel mm → uv (TL 原点 + v 反転)
    dst_uv = np.empty_like(dst_mm)
    dst_uv[:, 0] = dst_mm[:, 0] / panel_w_mm * pw_px
    dst_uv[:, 1] = (panel_h_mm - dst_mm[:, 1]) / panel_h_mm * ph_px

    H = cv2.getPerspectiveTransform(src, dst_uv)
    warped = cv2.warpPerspective(
        image_bgr, H, (pw_px, ph_px),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255),
    )
    # reprojection error
    src_h = np.concatenate([src, np.ones((4, 1))], axis=1)
    reproj = (H @ src_h.T).T
    reproj = reproj[:, :2] / reproj[:, 2:3]
    err = np.linalg.norm(reproj - dst_uv, axis=1)

    info = {
        "reproj_err_px_mean": float(err.mean()),
        "reproj_err_px_max": float(err.max()),
    }
    return warped, info


def _draw_warp_preview_overlay(warped: np.ndarray, info: dict) -> np.ndarray:
    img = warped.copy()
    h, w = img.shape[:2]
    text_lines = [
        f"Stage 4/4: warp preview (panel UV {w}x{h})",
        f"reproj err: mean={info['reproj_err_px_mean']:.2f}  "
        f"max={info['reproj_err_px_max']:.2f} px",
        "[s] save  [d] redo click  [r] redo rotation  [q] quit",
    ]
    overlay = img.copy()
    box_h = 22 * len(text_lines) + 14
    cv2.rectangle(overlay, (0, 0), (w, box_h), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.55, img, 0.45, 0, img)
    for i, line in enumerate(text_lines):
        cv2.putText(
            img, line, (12, 22 + 22 * i),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (240, 240, 240), 1, cv2.LINE_AA,
        )
    return img


def show_warp_preview(
    warped: np.ndarray,
    info: dict,
    window: str = "calibrate_panel_preview",
) -> str:
    """Warp preview window. 'save' / 'redo_click' / 'redo_rotation' / 'quit' を返す。"""
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    disp = _draw_warp_preview_overlay(warped, info)
    cv2.imshow(window, disp)
    while True:
        key = cv2.waitKey(20) & 0xFF
        if key == 255:
            continue
        if key in (ord("q"), 27):
            cv2.destroyWindow(window)
            return "quit"
        if key == ord("d"):  # redo click (was "r" before)
            cv2.destroyWindow(window)
            return "redo_click"
        if key == ord("r"):  # NEW: redo rotation
            cv2.destroyWindow(window)
            return "redo_rotation"
        if key in (ord("s"), 13):
            cv2.destroyWindow(window)
            return "save"


# ============================================================================
# yaml save
# ============================================================================

def save_calibration(
    yaml_path: Path,
    correspondences: list[dict],
    panel_w_mm: float,
    panel_h_mm: float,
    panel_image_size: tuple[int, int],
    camera_image_size: tuple[int, int],
    camera_rotation_deg: int,
    reproj_info: dict,
) -> None:
    """既存 yaml を読み、phase_a_calibration ブロックだけ更新して書き戻す。

    panel: ブロック (Phase B 側) には手を付けない。
    """
    data: dict = {}
    if yaml_path.exists():
        with yaml_path.open() as f:
            data = yaml.safe_load(f) or {}

    now = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    data["phase_a_calibration"] = {
        "calibrated": True,
        "calibrated_at": now,
        # 回転設定。Camera(rotation_deg=...) で適用される。
        # camera_image_size は「回転後」のサイズで、correspondences の
        # camera_px 座標もこの (回転後の) 画像系の値。
        "camera_rotation_deg": int(camera_rotation_deg),
        "camera_image_size": list(camera_image_size),
        "panel_image_size": [int(panel_image_size[0]), int(panel_image_size[1])],
        "panel_size_mm": [float(panel_w_mm), float(panel_h_mm)],
        "correspondences": correspondences,
        "reproj_error_px": {
            "mean": reproj_info["reproj_err_px_mean"],
            "max": reproj_info["reproj_err_px_max"],
        },
        "method": "manual_4pt_click",
    }
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    with yaml_path.open("w") as f:
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)


# ============================================================================
# main
# ============================================================================

def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--panel-yaml", default="calibration/panel_frame.yaml")
    ap.add_argument("--panel-width-mm", type=float, default=230.0,
                    help="ホワイトボードの横サイズ (mm)")
    ap.add_argument("--panel-height-mm", type=float, default=300.0,
                    help="ホワイトボードの縦サイズ (mm)")
    ap.add_argument("--panel-image-size", type=str, default=None,
                    help="warp 後の出力サイズ。 省略/'auto' で panel 実寸 aspect の "
                         "SDXL bucket (= 物理キャンバスと同じ縦横比、 画像生成と同一 "
                         "サイズ)。 'WxH' で明示、 'N' で正方形 (後方互換)。")
    # camera args (camera.py と揃える)
    ap.add_argument("--camera-device", type=int, default=0)
    ap.add_argument("--camera-width", type=int, default=1280)
    ap.add_argument("--camera-height", type=int, default=720)
    ap.add_argument("--camera-fps", type=int, default=30)
    ap.add_argument("--camera-n-frames", type=int, default=10,
                    help="median 合成のフレーム数")
    ap.add_argument("--camera-interval", type=float, default=0.2,
                    help="median 合成のフレーム間隔 (s)")
    ap.add_argument("--camera-countdown", type=float, default=2.0,
                    help="capture 前のカウントダウン (s)、 <= 0 でスキップ")
    ap.add_argument("--initial-rotation-deg", type=int, default=0,
                    choices=list(VALID_ROTATION_DEGS),
                    help="rotation preview の初期値 (Stage 2 でも r で変更可能)")
    # alt input
    ap.add_argument("--input-image", default=None,
                    help="カメラの代わりにこの画像を使う (テスト用)")
    ap.add_argument("--save-capture", default=None,
                    help="median capture をこのパスにも保存 (PNG、回転前)")
    args = ap.parse_args()

    yaml_path = Path(args.panel_yaml)

    # warp 出力サイズ (W, H) を解決。 省略時は panel 実寸 aspect の SDXL bucket。
    panel_image_size_wh = _resolve_panel_image_size(
        args.panel_image_size, args.panel_width_mm, args.panel_height_mm,
    )
    log.info(
        "panel_image_size (warp 出力) = %dx%d  (panel %.1fx%.1f mm, aspect %.3f)",
        panel_image_size_wh[0], panel_image_size_wh[1],
        args.panel_width_mm, args.panel_height_mm,
        args.panel_width_mm / args.panel_height_mm,
    )

    # --- 1. 画像取得 (回転は後段でかける) ---
    if args.input_image:
        log.info("loading test image: %s", args.input_image)
        image_raw = cv2.imread(args.input_image)
        if image_raw is None:
            log.error("failed to load %s", args.input_image)
            return 1
    else:
        log.info("opening camera...")
        log.info("panel size = %.1f x %.1f mm  (use --panel-width-mm / "
                 "--panel-height-mm to change)",
                 args.panel_width_mm, args.panel_height_mm)
        if args.camera_countdown > 0:
            log.info(
                "%.1fs countdown before capture; stand clear of the panel.",
                args.camera_countdown,
            )
            import time as _t
            for i in range(int(args.camera_countdown), 0, -1):
                print(f"  capturing in {i}...", flush=True)
                _t.sleep(1.0)
        # rotation_deg=0 でキャプチャ (回転は Stage 2 で別途決める)
        with Camera(
            device_id=args.camera_device,
            width=args.camera_width,
            height=args.camera_height,
            fps=args.camera_fps,
            rotation_deg=0,
            verbose=True,
        ) as cam:
            log.info(
                "capturing median of %d frames (interval %.2fs)...",
                args.camera_n_frames, args.camera_interval,
            )
            image_raw = cam.capture_median(
                n_frames=args.camera_n_frames,
                interval_s=args.camera_interval,
            )
        if args.save_capture:
            Path(args.save_capture).parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(args.save_capture, image_raw)
            log.info("saved median capture (raw, pre-rotation): %s", args.save_capture)

    # --- ループ: Stage 2 -> 3 -> 4 -> save  /  redo_click は 3 から、redo_rotation は 2 から ---
    current_rotation = args.initial_rotation_deg
    while True:
        # === Stage 2: rotation preview ===
        rot_result = choose_rotation(
            image_raw,
            initial_deg=current_rotation,
            panel_w_mm=args.panel_width_mm,
            panel_h_mm=args.panel_height_mm,
        )
        if rot_result is None:
            log.info("aborted at rotation preview.")
            return 1
        current_rotation, rotated_image = rot_result
        log.info("rotation confirmed: %d deg, image shape=%s",
                 current_rotation, rotated_image.shape)

        rotated_h, rotated_w = rotated_image.shape[:2]
        camera_image_size = (rotated_w, rotated_h)

        # === Stage 3 + 4: クリックと warp preview のループ ===
        # (Stage 4 で redo_rotation を選んだら Stage 2 へ戻る)
        go_back_to_rotation = False
        while not go_back_to_rotation:
            # Stage 3: click
            points = collect_corner_clicks(
                rotated_image,
                panel_w_mm=args.panel_width_mm,
                panel_h_mm=args.panel_height_mm,
            )
            if points is None:
                log.info("aborted at click.")
                return 1

            correspondences = _make_correspondences(
                points,
                panel_w_mm=args.panel_width_mm,
                panel_h_mm=args.panel_height_mm,
            )

            # Stage 4: warp preview
            warped, info = _build_warp_preview(
                rotated_image, correspondences,
                panel_w_mm=args.panel_width_mm,
                panel_h_mm=args.panel_height_mm,
                panel_image_size=panel_image_size_wh,
            )
            log.info(
                "reprojection err: mean=%.2f max=%.2f px",
                info["reproj_err_px_mean"], info["reproj_err_px_max"],
            )

            choice = show_warp_preview(warped, info)
            if choice == "save":
                save_calibration(
                    yaml_path=yaml_path,
                    correspondences=correspondences,
                    panel_w_mm=args.panel_width_mm,
                    panel_h_mm=args.panel_height_mm,
                    panel_image_size=panel_image_size_wh,
                    camera_image_size=camera_image_size,
                    camera_rotation_deg=current_rotation,
                    reproj_info=info,
                )
                log.info("saved phase_a_calibration to %s (rotation=%d deg)",
                         yaml_path, current_rotation)
                return 0
            elif choice == "redo_click":
                log.info("redoing click (same rotation %d deg)...", current_rotation)
                continue  # Stage 3 へ
            elif choice == "redo_rotation":
                log.info("redoing rotation...")
                go_back_to_rotation = True
                break  # 外側 while へ (Stage 2 に戻る)
            else:  # quit
                log.info("aborted (no save).")
                return 1


if __name__ == "__main__":
    sys.exit(main())
