"""modules/panel_crop.py

Phase A: camera image → panel UV (1024x1024) への perspective warp。

calibration/panel_frame.yaml の `phase_a_calibration` ブロックを読み、
correspondences (camera_px ↔ panel_mm) から homography を計算する。

Coordinate systems:
    - camera_px:  カメラ画像座標 (px, TL=原点, +y=下)
                  ※ phase_a_calibration.camera_rotation_deg が 0 でない場合、
                     correspondences の camera_px は「回転後」画像座標で記録されている。
                     warp() への入力は生画像でも回転済み画像でも OK
                     (shape を見て内部で揃える、下記 "rotation 二重適用防止" 参照)。
    - panel_uv:   SDXL/VLM 入力画像座標 (px, TL=原点, +y=下, 0..panel_image_size)
    - panel_mm:   panel 物理座標 (mm, BL=原点, +u=右, +v=上)
                  これは modules/robot.py PanelFrame の (u, v) と一致。

PanelCropper は yaml から
    H_cam2uv:  camera_px → panel_uv (warp 用、回転後座標系)
を作る。

panel_uv ↔ panel_mm の変換は線形 (panel_size_mm と panel_image_size から計算可能、
v 軸の向きが反転している点に注意)。

カメラ回転の取り扱い (Q-R1 (a)):
    yaml の phase_a_calibration.camera_rotation_deg を __init__ で読み、
    warp(image) の入り口で「image が生画像なら回転、回転済みならそのまま」
    を自動判定する。

    呼び出し側の推奨パターン:
        cam = Camera(rotation_deg=0)            # 生画像でカメラを開く
        raw = cam.capture_median()              # 生 shape: (cam_H, cam_W, 3)
        warped = cropper.warp(raw)              # 内部で回転 + warp

    呼び出し側が Camera(rotation_deg=yaml_value) で回転済みを渡しても、
    warp() 内の shape チェックで二重回転を防ぐ。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np
import yaml

log = logging.getLogger(__name__)

# rotation_deg → cv2.rotate のコード
_ROTATION_CODE = {
    0: None,
    90: cv2.ROTATE_90_CLOCKWISE,
    180: cv2.ROTATE_180,
    270: cv2.ROTATE_90_COUNTERCLOCKWISE,
}


def _apply_rotation_deg(image: np.ndarray, deg: int) -> np.ndarray:
    """deg ∈ {0, 90, 180, 270} で画像を回転して返す。"""
    code = _ROTATION_CODE.get(deg)
    if code is None:
        return image
    return cv2.rotate(image, code)


@dataclass
class Correspondence:
    """1 つの対応点。"""
    camera_px: tuple[float, float]
    panel_mm: tuple[float, float]
    label: str | None = None


class PanelCropper:
    """Camera image → panel UV image への perspective warp。

    Usage:
        cropper = PanelCropper("calibration/panel_frame.yaml")
        warped = cropper.warp(camera_image_bgr)  # shape: (1024, 1024, 3) BGR

    yaml の phase_a_calibration.camera_rotation_deg を読み、warp() の入り口で
    自動的に回転を適用する。呼び出し側は Camera(rotation_deg=0) で生画像を
    取って warp() に渡せばよい。
    """

    REQUIRED_KEYS = {
        "camera_image_size",
        "panel_image_size",
        "panel_size_mm",
        "correspondences",
    }

    def __init__(self, yaml_path: str | Path, verbose: bool = False):
        self.yaml_path = Path(yaml_path)
        self.verbose = verbose
        self._load()
        self._compute_homographies()

    # ---- loading ----
    def _load(self) -> None:
        if not self.yaml_path.exists():
            raise FileNotFoundError(
                f"panel yaml not found: {self.yaml_path}"
            )
        with self.yaml_path.open() as f:
            data = yaml.safe_load(f) or {}

        block = data.get("phase_a_calibration")
        if block is None:
            raise KeyError(
                f"{self.yaml_path} has no `phase_a_calibration` block. "
                "Run scripts/calibrate_panel.py first."
            )
        if not block.get("calibrated", False):
            raise RuntimeError(
                f"{self.yaml_path}: phase_a_calibration.calibrated is False. "
                "Run scripts/calibrate_panel.py to (re)calibrate."
            )

        missing = self.REQUIRED_KEYS - block.keys()
        if missing:
            raise KeyError(
                f"phase_a_calibration missing keys: {missing}"
            )

        # camera_image_size は「回転後」のサイズ (回転前画像の shape からは
        # _rotation_deg を使って復元する)
        self.camera_image_size: tuple[int, int] = tuple(block["camera_image_size"])
        self.panel_image_size: tuple[int, int] = tuple(block["panel_image_size"])
        self.panel_size_mm: tuple[float, float] = tuple(block["panel_size_mm"])

        # camera_rotation_deg は新規フィールド。古い yaml (フィールド無し) では
        # 0 として扱い、後方互換を保つ。
        rot_raw = block.get("camera_rotation_deg", 0)
        try:
            rot_int = int(rot_raw) % 360
        except (TypeError, ValueError) as e:
            raise ValueError(
                f"phase_a_calibration.camera_rotation_deg must be int, "
                f"got {rot_raw!r}"
            ) from e
        if rot_int not in _ROTATION_CODE:
            raise ValueError(
                f"phase_a_calibration.camera_rotation_deg must be one of "
                f"{tuple(_ROTATION_CODE.keys())}, got {rot_raw}"
            )
        self.camera_rotation_deg: int = rot_int

        # 回転前カメラサイズ (warp() の shape 判定で使う)
        # 0/180 のとき: 回転後と同じ
        # 90/270 のとき: 回転後の (w, h) を swap した値が回転前 (w, h)
        cam_w_rot, cam_h_rot = self.camera_image_size
        if self.camera_rotation_deg in (90, 270):
            self.camera_image_size_pre_rotation: tuple[int, int] = (cam_h_rot, cam_w_rot)
        else:
            self.camera_image_size_pre_rotation = (cam_w_rot, cam_h_rot)

        self.correspondences: list[Correspondence] = []
        for c in block["correspondences"]:
            self.correspondences.append(
                Correspondence(
                    camera_px=tuple(c["camera_px"]),
                    panel_mm=tuple(c["panel_mm"]),
                    label=c.get("label"),
                )
            )
        if len(self.correspondences) < 4:
            raise ValueError(
                f"need >= 4 correspondences, got {len(self.correspondences)}"
            )

        if self.verbose:
            log.info(
                "loaded %d correspondences from %s (rotation=%d deg)",
                len(self.correspondences), self.yaml_path,
                self.camera_rotation_deg,
            )

    # ---- homography ----
    def _compute_homographies(self) -> None:
        """camera_px → panel_uv と camera_px → panel_mm を計算。

        camera_px は yaml に「回転後画像座標」で記録されている前提。
        したがって H_cam2uv も「回転後画像 → panel UV」の homography。
        warp() の入り口で必要に応じて生画像を回転してから適用する。
        """
        pw_px, ph_px = self.panel_image_size
        pw_mm, ph_mm = self.panel_size_mm

        src = np.array(
            [c.camera_px for c in self.correspondences],
            dtype=np.float32,
        )  # (N, 2) camera px (回転後座標系)
        dst_mm = np.array(
            [c.panel_mm for c in self.correspondences],
            dtype=np.float32,
        )  # (N, 2) panel mm (BL 原点、+u=右、+v=上)

        # panel mm → panel uv (TL 原点、+y=下)
        #   u_px = u_mm / pw_mm * pw_px
        #   v_px = (ph_mm - v_mm) / ph_mm * ph_px       # v 軸反転
        dst_uv = np.empty_like(dst_mm)
        dst_uv[:, 0] = dst_mm[:, 0] / pw_mm * pw_px
        dst_uv[:, 1] = (ph_mm - dst_mm[:, 1]) / ph_mm * ph_px

        if len(self.correspondences) == 4:
            self.H_cam2uv = cv2.getPerspectiveTransform(src, dst_uv)
            self.H_cam2mm = cv2.getPerspectiveTransform(src, dst_mm)
            inlier_mask = np.ones(4, dtype=bool)
            method_name = "getPerspectiveTransform (4 pts exact)"
        else:
            H_uv, mask_uv = cv2.findHomography(src, dst_uv, method=cv2.LMEDS)
            H_mm, mask_mm = cv2.findHomography(src, dst_mm, method=cv2.LMEDS)
            if H_uv is None or H_mm is None:
                raise RuntimeError("findHomography failed (degenerate points?)")
            self.H_cam2uv = H_uv
            self.H_cam2mm = H_mm
            inlier_mask = mask_uv.flatten().astype(bool)
            n_in = int(inlier_mask.sum())
            method_name = f"findHomography LMEDS ({n_in}/{len(src)} inliers)"

        # 逆変換も用意 (panel_uv → camera_px、デバッグ用)
        self.H_uv2cam = np.linalg.inv(self.H_cam2uv)

        # reprojection error
        reproj_uv = self._apply_h(self.H_cam2uv, src)
        err = np.linalg.norm(reproj_uv - dst_uv, axis=1)
        self.reproj_error_uv_px = err
        self.reproj_method = method_name
        self.inlier_mask = inlier_mask

        if self.verbose:
            log.info("homography: %s", method_name)
            log.info(
                "reprojection error (panel uv px): "
                "mean=%.2f max=%.2f",
                err.mean(), err.max(),
            )

    @staticmethod
    def _apply_h(H: np.ndarray, pts: np.ndarray) -> np.ndarray:
        """Apply 3x3 homography to (N, 2) points."""
        n = len(pts)
        homog = np.concatenate([pts, np.ones((n, 1), dtype=pts.dtype)], axis=1)  # (N, 3)
        out = (H @ homog.T).T  # (N, 3)
        out = out[:, :2] / out[:, 2:3]
        return out

    # ---- rotation handling ----
    def _normalize_input_image(self, image_bgr: np.ndarray) -> np.ndarray:
        """warp() の入力画像を「回転後座標系」に揃える。

        Logic:
            shape == camera_image_size               → 回転済み画像、そのまま
            shape == camera_image_size_pre_rotation  → 生画像、回転を適用
            それ以外                                 → 警告して回転を適用 (best effort)

        camera_rotation_deg=0 のときは _ROTATION_CODE が None なので、両分岐で
        同じ画像が返る (=実質ノーオペレーション、後方互換)。
        """
        h, w = image_bgr.shape[:2]
        in_wh = (w, h)
        exp_post = self.camera_image_size                # 回転後 (yaml の値)
        exp_pre = self.camera_image_size_pre_rotation    # 回転前 (推定)

        if in_wh == exp_post:
            # 既に回転済み (= 呼び出し側が Camera(rotation_deg=yaml) を使っている)
            return image_bgr
        elif in_wh == exp_pre:
            # 生画像 (= 呼び出し側が Camera(rotation_deg=0) を使っている)
            if self.camera_rotation_deg != 0 and self.verbose:
                log.info(
                    "auto-rotating input image %s by %d deg "
                    "(pre-rotation size detected)",
                    in_wh, self.camera_rotation_deg,
                )
            return _apply_rotation_deg(image_bgr, self.camera_rotation_deg)
        else:
            log.warning(
                "camera image size mismatch: got %s, "
                "expected %s (rotated) or %s (raw). "
                "Applying rotation %d deg as best effort.",
                in_wh, exp_post, exp_pre, self.camera_rotation_deg,
            )
            return _apply_rotation_deg(image_bgr, self.camera_rotation_deg)

    # ---- public API ----
    def warp(self, image_bgr: np.ndarray) -> np.ndarray:
        """Camera image → panel UV image (panel_image_size, BGR uint8)。

        入力画像は「生 (回転前)」「回転済み」どちらでも OK。
        shape から自動判定して内部で回転を揃えてから warp する。
        """
        if image_bgr.ndim != 3 or image_bgr.shape[2] != 3:
            raise ValueError(f"expected HxWx3 BGR image, got {image_bgr.shape}")

        # 回転を揃える (Q-R1 (a) の本体)
        image_for_warp = self._normalize_input_image(image_bgr)

        pw_px, ph_px = self.panel_image_size
        warped = cv2.warpPerspective(
            image_for_warp, self.H_cam2uv, (pw_px, ph_px),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(255, 255, 255),  # 白埋め
        )
        return warped

    def panel_uv_to_panel_mm(self, uv: np.ndarray) -> np.ndarray:
        """Panel UV (px, TL 原点) → panel mm (BL 原点、+v=上)。

        Args:
            uv: (N, 2) array of panel UV pixels.
        Returns:
            (N, 2) array of panel mm.
        """
        uv = np.asarray(uv, dtype=np.float32).reshape(-1, 2)
        pw_px, ph_px = self.panel_image_size
        pw_mm, ph_mm = self.panel_size_mm
        mm = np.empty_like(uv)
        mm[:, 0] = uv[:, 0] / pw_px * pw_mm
        mm[:, 1] = ph_mm - uv[:, 1] / ph_px * ph_mm
        return mm

    def panel_mm_to_panel_uv(self, mm: np.ndarray) -> np.ndarray:
        """Panel mm → panel UV (px)。"""
        mm = np.asarray(mm, dtype=np.float32).reshape(-1, 2)
        pw_px, ph_px = self.panel_image_size
        pw_mm, ph_mm = self.panel_size_mm
        uv = np.empty_like(mm)
        uv[:, 0] = mm[:, 0] / pw_mm * pw_px
        uv[:, 1] = (ph_mm - mm[:, 1]) / ph_mm * ph_px
        return uv

    def camera_px_to_panel_mm(self, pts: np.ndarray) -> np.ndarray:
        """Camera px → panel mm (homography 経由、直接)。

        Note: pts は「回転後画像」座標系である必要がある。
              生カメラ座標を渡したい場合は、先に _apply_rotation_deg() で
              座標変換するか、warp() 経由で panel_uv にしてから
              panel_uv_to_panel_mm() を使うこと。
        """
        pts = np.asarray(pts, dtype=np.float32).reshape(-1, 2)
        return self._apply_h(self.H_cam2mm, pts)

    # ---- diagnostics ----
    def summary(self) -> str:
        n = len(self.correspondences)
        n_in = int(self.inlier_mask.sum())
        return (
            f"PanelCropper: {n} pts ({n_in} inliers), "
            f"rotation={self.camera_rotation_deg} deg, "
            f"method={self.reproj_method}, "
            f"reproj_err mean={self.reproj_error_uv_px.mean():.2f} "
            f"max={self.reproj_error_uv_px.max():.2f} px"
        )


if __name__ == "__main__":
    # quick smoke test (requires existing yaml + a camera image)
    import argparse
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument("--yaml", default="calibration/panel_frame.yaml")
    ap.add_argument("--image", required=True, help="camera image to warp")
    ap.add_argument("--out", default="cropped_test.png")
    args = ap.parse_args()

    cropper = PanelCropper(args.yaml, verbose=True)
    print(cropper.summary())
    img = cv2.imread(args.image)
    if img is None:
        raise SystemExit(f"failed to load {args.image}")
    warped = cropper.warp(img)
    cv2.imwrite(args.out, warped)
    print(f"saved: {args.out}")
