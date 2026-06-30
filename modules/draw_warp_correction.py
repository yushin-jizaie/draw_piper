"""Empirical 2D draw-warp correction for wall drawing.

The robot-side kinematic model is useful for reaching the board, but the
final pen trace is measured on the board.  This module fits a local 2D affine
map from commanded board coordinates to measured board coordinates, then
stores the inverse map used at draw time:

    command_local = desired_to_command @ desired_local + offset

Coordinates are in canvas-local millimeters: +u = board right, +v = board up.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORRECTION_PATH = PROJECT_ROOT / "calibration" / "draw_warp_correction.yaml"
DEFAULT_MEASUREMENTS_PATH = PROJECT_ROOT / "calibration" / "draw_warp_measurements.yaml"


@dataclass(frozen=True)
class DrawWarpCorrection:
    """Canvas-local affine desired->command correction."""

    enabled: bool
    matrix: np.ndarray
    offset: np.ndarray
    source_path: str | None = None
    residual_rms_mm: float | None = None
    residual_max_mm: float | None = None
    n_points: int = 0

    @classmethod
    def identity(cls, source_path: str | None = None) -> "DrawWarpCorrection":
        return cls(
            enabled=False,
            matrix=np.eye(2, dtype=float),
            offset=np.zeros(2, dtype=float),
            source_path=source_path,
            residual_rms_mm=None,
            residual_max_mm=None,
            n_points=0,
        )

    def apply(self, local_uv_mm: Sequence[float]) -> np.ndarray:
        p = np.asarray(local_uv_mm, dtype=float)
        if p.shape != (2,):
            raise ValueError(f"local point must have 2 values, got {p!r}")
        if not self.enabled:
            return p
        return self.matrix @ p + self.offset

    def summary(self) -> str:
        if not self.enabled:
            return "OFF"
        rms = "?" if self.residual_rms_mm is None else f"{self.residual_rms_mm:.2f}"
        mx = "?" if self.residual_max_mm is None else f"{self.residual_max_mm:.2f}"
        return f"ON ({self.n_points} pts, rms={rms}mm, max={mx}mm)"


def _as_pair(value: object, key: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{key} must be [u, v]")
    return float(value[0]), float(value[1])


def fit_affine(
    command_points: Iterable[Sequence[float]],
    measured_points: Iterable[Sequence[float]],
) -> dict:
    """Fit command->actual and derive desired->command affine maps."""

    command = np.asarray(list(command_points), dtype=float)
    measured = np.asarray(list(measured_points), dtype=float)
    if command.shape != measured.shape or command.ndim != 2 or command.shape[1] != 2:
        raise ValueError("command/measured points must both be Nx2 arrays")
    if command.shape[0] < 3:
        raise ValueError("at least 3 measured points are required")

    design = np.column_stack([command, np.ones(command.shape[0])])
    coeff, _, rank, _ = np.linalg.lstsq(design, measured, rcond=None)
    if rank < 3:
        raise ValueError("measurement points are degenerate; use non-collinear points")

    # design @ coeff = measured, coeff shape is (u, v, 1) x (actual_u, actual_v).
    cmd_to_actual_m = np.array(
        [[coeff[0, 0], coeff[1, 0]], [coeff[0, 1], coeff[1, 1]]],
        dtype=float,
    )
    cmd_to_actual_b = np.array([coeff[2, 0], coeff[2, 1]], dtype=float)

    det = float(np.linalg.det(cmd_to_actual_m))
    if abs(det) < 1e-6:
        raise ValueError("fitted affine matrix is singular")
    desired_to_command_m = np.linalg.inv(cmd_to_actual_m)
    desired_to_command_b = -desired_to_command_m @ cmd_to_actual_b

    pred = (cmd_to_actual_m @ command.T).T + cmd_to_actual_b
    residuals = np.linalg.norm(pred - measured, axis=1)
    return {
        "command_to_actual_matrix": cmd_to_actual_m,
        "command_to_actual_offset": cmd_to_actual_b,
        "desired_to_command_matrix": desired_to_command_m,
        "desired_to_command_offset": desired_to_command_b,
        "residuals_mm": residuals,
        "residual_rms_mm": float(np.sqrt(np.mean(residuals * residuals))),
        "residual_max_mm": float(np.max(residuals)),
        "determinant": det,
        "n_points": int(command.shape[0]),
    }


def load_correction(path: str | Path = DEFAULT_CORRECTION_PATH) -> DrawWarpCorrection:
    path = Path(path)
    if not path.exists():
        return DrawWarpCorrection.identity(str(path))
    with path.open() as f:
        data = yaml.safe_load(f) or {}
    enabled = bool(data.get("enabled", False))
    fit = data.get("fit") or {}
    d2c = fit.get("desired_to_command") or data.get("desired_to_command") or {}
    matrix = np.asarray(d2c.get("matrix", [[1.0, 0.0], [0.0, 1.0]]), dtype=float)
    offset = np.asarray(d2c.get("offset", [0.0, 0.0]), dtype=float)
    if matrix.shape != (2, 2) or offset.shape != (2,):
        raise ValueError(f"{path}: desired_to_command matrix/offset malformed")
    residuals = fit.get("residuals") or {}
    return DrawWarpCorrection(
        enabled=enabled,
        matrix=matrix,
        offset=offset,
        source_path=str(path),
        residual_rms_mm=_optional_float(residuals.get("rms_mm")),
        residual_max_mm=_optional_float(residuals.get("max_mm")),
        n_points=int(residuals.get("n_points") or len(data.get("points") or [])),
    )


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def read_measurement_points(path: str | Path) -> list[dict]:
    path = Path(path)
    with path.open() as f:
        data = yaml.safe_load(f) or {}
    points = data.get("points") or []
    out = []
    for idx, p in enumerate(points):
        if not isinstance(p, Mapping):
            raise ValueError(f"points[{idx}] must be a mapping")
        measured = p.get("measured_local_mm", p.get("actual_local_mm"))
        if measured is None:
            continue
        command = p.get("command_local_mm")
        if command is None:
            raise ValueError(f"points[{idx}] has measured value but no command_local_mm")
        out.append(
            {
                "label": str(p.get("label", idx)),
                "command_local_mm": _as_pair(command, f"points[{idx}].command_local_mm"),
                "measured_local_mm": _as_pair(measured, f"points[{idx}].measured_local_mm"),
            }
        )
    return out


def write_measurement_template(
    path: str | Path = DEFAULT_MEASUREMENTS_PATH,
    points: Iterable[Mapping[str, object]] = (),
    metadata: Mapping[str, object] | None = None,
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema_version": 1,
        "space": "canvas_local_mm",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "instructions": (
            "Fill measured_local_mm for each mark from a photo or ruler. "
            "+u is board right, +v is board up, both relative to the intended "
            "target center. If the C mark is shifted, measured_local_mm for C "
            "must be that shift, not [0, 0]."
        ),
        "metadata": dict(metadata or {}),
        "points": [],
    }
    for p in points:
        command = _as_pair(p.get("command_local_mm"), "command_local_mm")
        data["points"].append(
            {
                "label": str(p.get("label", "")),
                "command_local_mm": [round(command[0], 3), round(command[1], 3)],
                "measured_local_mm": None,
            }
        )
    with path.open("w") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
    return path


def fit_from_measurements_file(
    measurements_path: str | Path = DEFAULT_MEASUREMENTS_PATH,
    correction_path: str | Path = DEFAULT_CORRECTION_PATH,
    metadata: Mapping[str, object] | None = None,
    enabled: bool = True,
) -> Path:
    points = read_measurement_points(measurements_path)
    if len(points) < 3:
        raise ValueError(
            f"{measurements_path}: measured_local_mm is needed for at least 3 points"
        )
    fit = fit_affine(
        [p["command_local_mm"] for p in points],
        [p["measured_local_mm"] for p in points],
    )
    path = Path(correction_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    residuals = fit["residuals_mm"]
    data = {
        "schema_version": 1,
        "enabled": bool(enabled),
        "space": "canvas_local_mm",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "metadata": dict(metadata or {}),
        "fit": {
            "command_to_actual": {
                "matrix": _round_nested(fit["command_to_actual_matrix"]),
                "offset": _round_list(fit["command_to_actual_offset"]),
            },
            "desired_to_command": {
                "matrix": _round_nested(fit["desired_to_command_matrix"]),
                "offset": _round_list(fit["desired_to_command_offset"]),
            },
            "residuals": {
                "rms_mm": round(float(fit["residual_rms_mm"]), 4),
                "max_mm": round(float(fit["residual_max_mm"]), 4),
                "n_points": int(fit["n_points"]),
                "by_point_mm": [
                    {
                        "label": points[i]["label"],
                        "error_mm": round(float(residuals[i]), 4),
                    }
                    for i in range(len(points))
                ],
            },
            "determinant": round(float(fit["determinant"]), 8),
        },
        "points": [
            {
                "label": p["label"],
                "command_local_mm": _round_list(p["command_local_mm"]),
                "measured_local_mm": _round_list(p["measured_local_mm"]),
            }
            for p in points
        ],
    }
    with path.open("w") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
    return path


def _round_list(values: Sequence[float] | np.ndarray) -> list[float]:
    return [round(float(v), 6) for v in values]


def _round_nested(values: Sequence[Sequence[float]] | np.ndarray) -> list[list[float]]:
    arr = np.asarray(values, dtype=float)
    return [[round(float(v), 6) for v in row] for row in arr.tolist()]
