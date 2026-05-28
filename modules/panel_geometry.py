"""modules/panel_geometry.py

「カメラ warp / 画像生成 / ロボット認識平面」 の 3 つを物理 panel サイズで
合わせ込むための single source of truth。

```
canvas_calibration.yaml             panel_frame.yaml
  whiteboard_computed                 panel.size_mm (ペン先計測 — 古い可能性)
    width_mm  ← 真値                 phase_a_calibration
    height_mm   (ペン先 drag-teach)     panel_image_size  ← 旧: [1024, 1024]
                                       panel_size_mm
            \\                       /
             \\                     /
              ▼   PanelGeometry   ▼
              ──────────────────────
              panel_size_mm  (W_mm, H_mm)
              panel_image_size (W_px, H_px) ← SDXL bucket
              aspect, mm_per_px, source
              ──────────────────────
                       │
        ┌──────────────┼─────────────────┐
        ▼              ▼                 ▼
   image_gen        panel_crop         vectorizer
   (生成解像度)      (warp 先 px)        (px→mm スケール)
```

# 設計メモ

- canvas_calibration の `whiteboard_computed.width_mm/height_mm` は B1
  (ペン先 4 corner drag-teach) で実測した「いま robot が認識している panel」。
  これを真値とする。
- panel_image_size は SDXL の bucket 制約 (64 倍数 + area ≈ 1024²) から
  panel aspect に最近接のものを選ぶ。 これで生成画像が panel と同じ aspect
  になり、 vectorize_to_panel の px→mm スケールが等方になる。
- panel_frame.yaml は (panel_size_mm / panel_image_size) のミラーとして
  保持。 GUI から「いまの canvas 計測値で同期する」 ボタンで上書きする想定。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

import yaml

log = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CANVAS_YAML = _PROJECT_ROOT / "calibration" / "canvas_calibration.yaml"
DEFAULT_PANEL_FRAME_YAML = _PROJECT_ROOT / "calibration" / "panel_frame.yaml"


# SDXL native bucket (W, H)。 全 64 倍数、 area = 1024 × 1024 = 1048576 ± 数%。
# SDXL 推論時のサポート解像度として diffusers / stability-ai docs から抜粋。
# aspect (W/H) は 0.5 〜 2.0 をカバー。
SDXL_BUCKETS: list[tuple[int, int]] = [
    (640, 1536),   # 0.417 (縦長極端)
    (704, 1472),   # 0.478
    (704, 1408),   # 0.500
    (768, 1344),   # 0.571
    (768, 1280),   # 0.600
    (832, 1216),   # 0.684
    (832, 1152),   # 0.722
    (896, 1152),   # 0.778
    (896, 1088),   # 0.824
    (960, 1088),   # 0.882
    (960, 1024),   # 0.938
    (1024, 1024),  # 1.000
    (1024, 960),   # 1.067
    (1088, 960),   # 1.133
    (1088, 896),   # 1.214
    (1152, 896),   # 1.286
    (1152, 832),   # 1.385
    (1216, 832),   # 1.462
    (1280, 768),   # 1.667
    (1344, 768),   # 1.750
    (1408, 704),   # 2.000
    (1472, 704),   # 2.091
    (1536, 640),   # 2.400
]


@dataclass(frozen=True)
class PanelGeometry:
    """物理 panel と生成画像 (panel_uv) のサイズ整合 record。"""

    panel_size_mm: tuple[float, float]      # (W_mm, H_mm)
    panel_image_size: tuple[int, int]       # (W_px, H_px) = SDXL bucket
    source: str                              # "canvas_calibration" / "panel_frame" / "explicit"
    bucket_aspect_err: float = 0.0           # |panel_aspect - bucket_aspect| / panel_aspect

    @property
    def aspect(self) -> float:
        return self.panel_size_mm[0] / self.panel_size_mm[1]

    @property
    def bucket_aspect(self) -> float:
        return self.panel_image_size[0] / self.panel_image_size[1]

    @property
    def mm_per_px(self) -> tuple[float, float]:
        return (
            self.panel_size_mm[0] / self.panel_image_size[0],
            self.panel_size_mm[1] / self.panel_image_size[1],
        )

    def summary(self) -> str:
        mu, mv = self.mm_per_px
        return (
            f"PanelGeometry(panel={self.panel_size_mm[0]:.2f}×"
            f"{self.panel_size_mm[1]:.2f} mm, "
            f"image={self.panel_image_size[0]}×{self.panel_image_size[1]} px, "
            f"aspect={self.aspect:.3f} (bucket {self.bucket_aspect:.3f}, "
            f"err {self.bucket_aspect_err * 100:.1f}%), "
            f"mm/px=({mu:.4f}, {mv:.4f}), "
            f"source={self.source})"
        )


def select_sdxl_bucket(
    aspect: float,
    buckets: Sequence[tuple[int, int]] = SDXL_BUCKETS,
) -> tuple[tuple[int, int], float]:
    """指定 aspect (W/H) に最も近い SDXL bucket と相対誤差を返す。

    Returns:
        ((W, H), relative_error)  relative_error = |bucket_aspect - aspect| / aspect
    """
    if aspect <= 0:
        raise ValueError(f"aspect must be > 0, got {aspect!r}")
    best = min(buckets, key=lambda b: abs((b[0] / b[1]) - aspect))
    rel_err = abs((best[0] / best[1]) - aspect) / aspect
    return best, rel_err


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open() as f:
        return yaml.safe_load(f) or {}


def _panel_mm_from_canvas(canvas_data: dict) -> Optional[tuple[float, float]]:
    """canvas_calibration.yaml から (W_mm, H_mm) を抜く。

    優先順:
        1. whiteboard_computed.width_mm / height_mm  (Step 4 plane-fit 由来)
        2. whiteboard_corners_mm (4 corner) から TL-TR / TL-BL の距離を計算
    """
    wb = canvas_data.get("whiteboard_computed") or {}
    w = wb.get("width_mm")
    h = wb.get("height_mm")
    if isinstance(w, (int, float)) and isinstance(h, (int, float)) and w > 0 and h > 0:
        return (float(w), float(h))

    corners = canvas_data.get("whiteboard_corners_mm") or {}
    needed = ("tl", "tr", "br", "bl")
    if all(k in corners for k in needed):
        try:
            def _pen_yz(key: str) -> tuple[float, float]:
                yz = corners[key]["pen_yz_mm"]
                return float(yz[0]), float(yz[1])

            tl = _pen_yz("tl")
            tr = _pen_yz("tr")
            bl = _pen_yz("bl")
            # 横 = TL-TR の距離、 縦 = TL-BL の距離 (panel 平面内の y/z mm)
            w_mm = ((tr[0] - tl[0]) ** 2 + (tr[1] - tl[1]) ** 2) ** 0.5
            h_mm = ((bl[0] - tl[0]) ** 2 + (bl[1] - tl[1]) ** 2) ** 0.5
            if w_mm > 0 and h_mm > 0:
                return (w_mm, h_mm)
        except (KeyError, TypeError, ValueError, IndexError):
            pass
    return None


def _panel_mm_from_panel_frame(panel_data: dict) -> Optional[tuple[float, float]]:
    """panel_frame.yaml の panel.size_mm を fallback として読む。"""
    panel = panel_data.get("panel") or {}
    sz = panel.get("size_mm")
    if isinstance(sz, (list, tuple)) and len(sz) == 2:
        try:
            w, h = float(sz[0]), float(sz[1])
            if w > 0 and h > 0:
                return (w, h)
        except (TypeError, ValueError):
            pass
    return None


def load_panel_geometry(
    canvas_path: Path = DEFAULT_CANVAS_YAML,
    panel_frame_path: Path = DEFAULT_PANEL_FRAME_YAML,
    *,
    explicit_panel_size_mm: Optional[tuple[float, float]] = None,
    explicit_image_size: Optional[tuple[int, int]] = None,
    buckets: Sequence[tuple[int, int]] = SDXL_BUCKETS,
) -> PanelGeometry:
    """画像生成・ロボット間で共有する PanelGeometry を組み立てる。

    優先順:
        1. explicit_panel_size_mm が指定されていればそれを使う
        2. canvas_calibration.yaml の whiteboard_computed (実測値)
        3. canvas_calibration.yaml の whiteboard_corners_mm から計算
        4. panel_frame.yaml の panel.size_mm

    image_size:
        explicit_image_size が指定されていればそれをそのまま採用 (bucket snap しない)。
        指定無ければ panel aspect から SDXL bucket を選ぶ。
    """
    canvas = _load_yaml(canvas_path)
    panel = _load_yaml(panel_frame_path)

    if explicit_panel_size_mm is not None:
        w_mm, h_mm = explicit_panel_size_mm
        source = "explicit"
    else:
        mm = _panel_mm_from_canvas(canvas)
        if mm is not None:
            w_mm, h_mm = mm
            source = "canvas_calibration"
        else:
            mm = _panel_mm_from_panel_frame(panel)
            if mm is not None:
                w_mm, h_mm = mm
                source = "panel_frame.panel.size_mm"
            else:
                raise FileNotFoundError(
                    f"No panel size found in {canvas_path} or {panel_frame_path}. "
                    "Pass explicit_panel_size_mm=(W, H) or run canvas calibration."
                )

    aspect = w_mm / h_mm
    if explicit_image_size is not None:
        w_px, h_px = explicit_image_size
        bucket_err = abs((w_px / h_px) - aspect) / aspect
    else:
        (w_px, h_px), bucket_err = select_sdxl_bucket(aspect, buckets=buckets)

    geom = PanelGeometry(
        panel_size_mm=(float(w_mm), float(h_mm)),
        panel_image_size=(int(w_px), int(h_px)),
        source=source,
        bucket_aspect_err=float(bucket_err),
    )
    if bucket_err > 0.10:
        log.warning(
            "panel aspect %.3f does not closely match any SDXL bucket "
            "(picked %dx%d, err %.1f%%). Generated image will be slightly "
            "stretched on the canvas.",
            aspect, w_px, h_px, bucket_err * 100,
        )
    return geom


def write_geometry_to_panel_frame(
    geom: PanelGeometry,
    panel_frame_path: Path = DEFAULT_PANEL_FRAME_YAML,
    *,
    update_phase_a: bool = True,
) -> Path:
    """canvas 由来の geometry を panel_frame.yaml にミラーする。

    - panel.size_mm を更新 (panel.calibrated は触らない)
    - update_phase_a=True なら phase_a_calibration.panel_size_mm / panel_image_size も上書き

    `phase_a_calibration.correspondences` の camera_px は変更しない。
    panel_mm はラベル文字列扱いなので panel_size_mm 変更で homography は影響を受けない。
    """
    data = _load_yaml(panel_frame_path)
    panel = data.setdefault("panel", {})
    panel["size_mm"] = [round(geom.panel_size_mm[0], 4), round(geom.panel_size_mm[1], 4)]

    if update_phase_a:
        pa = data.setdefault("phase_a_calibration", {})
        # phase_a_calibration の panel_size_mm は correspondences の panel_mm
        # と整合させないと homography が壊れる。 ここで上書きする場合は
        # correspondences の panel_mm も同じ scale で書き換える必要がある。
        # → 安全側: phase_a_calibration の panel_size_mm が canvas と一致して
        # いなければ警告ログを出して **panel_image_size のみ** 更新する。
        old_size = pa.get("panel_size_mm")
        if (isinstance(old_size, (list, tuple)) and len(old_size) == 2
                and (abs(old_size[0] - geom.panel_size_mm[0]) > 0.5
                     or abs(old_size[1] - geom.panel_size_mm[1]) > 0.5)):
            log.warning(
                "phase_a_calibration.panel_size_mm=%s mismatches canvas "
                "%s. Leaving phase_a.panel_size_mm unchanged (would invalidate "
                "correspondences). Update via scripts/calibrate_panel.py.",
                old_size, list(geom.panel_size_mm),
            )
        else:
            pa["panel_size_mm"] = [
                round(geom.panel_size_mm[0], 4),
                round(geom.panel_size_mm[1], 4),
            ]
        pa["panel_image_size"] = [int(geom.panel_image_size[0]),
                                   int(geom.panel_image_size[1])]

    with panel_frame_path.open("w") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True,
                       default_flow_style=False)
    return panel_frame_path


__all__ = [
    "PanelGeometry",
    "SDXL_BUCKETS",
    "select_sdxl_bucket",
    "load_panel_geometry",
    "write_geometry_to_panel_frame",
    "DEFAULT_CANVAS_YAML",
    "DEFAULT_PANEL_FRAME_YAML",
]
