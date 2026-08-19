"""Scientific densitometry over immutable source-image pixels.

The functions in this module know nothing about preview buffers, tone controls,
display inversion, zoom, or presentation geometry. Callers must inverse-map
Canvas ROIs into raw-image geometry before entering this module.
"""
from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image

from core.image_transform import image_array_to_raw_luminance
from utils.logger import get_logger

log = get_logger(__name__)


def load_raw_quantification_image(image_path: str) -> np.ndarray:
    """Load scalar source pixels without an 8-bit conversion."""
    with Image.open(image_path) as image:
        raw = np.array(image)
    pixels = image_array_to_raw_luminance(raw)
    if pixels.ndim != 2:
        raise ValueError(f"Quantification requires a 2D scalar image, got {pixels.shape}")
    return np.ascontiguousarray(pixels)


def _rect_components(geometry: Any) -> tuple[float, float, float, float] | None:
    if hasattr(geometry, "x") and callable(geometry.x):
        return (
            float(geometry.x()),
            float(geometry.y()),
            float(geometry.width()),
            float(geometry.height()),
        )
    if isinstance(geometry, dict) and "points" not in geometry:
        return (
            float(geometry.get("x", 0.0)),
            float(geometry.get("y", 0.0)),
            float(geometry.get("width", geometry.get("w", 1.0))),
            float(geometry.get("height", geometry.get("h", 1.0))),
        )
    return None


def _polygon_points(geometry: Any) -> np.ndarray | None:
    if not isinstance(geometry, dict) or "points" not in geometry:
        return None
    values = geometry.get("points") or []
    points: list[tuple[float, float]] = []
    for point in values:
        if isinstance(point, dict):
            points.append((float(point["x"]), float(point["y"])))
        else:
            points.append((float(point[0]), float(point[1])))
    if len(points) < 3:
        return np.empty((0, 2), dtype=np.float64)
    return np.asarray(points, dtype=np.float64)


def _points_in_polygon(x: np.ndarray, y: np.ndarray, polygon: np.ndarray) -> np.ndarray:
    """Vectorized even/odd test over pixel centres, including polygon edges."""
    inside = np.zeros(x.shape, dtype=bool)
    on_edge = np.zeros(x.shape, dtype=bool)
    x1, y1 = polygon[-1]
    tolerance = 1e-9
    for x2, y2 in polygon:
        dx = x2 - x1
        dy = y2 - y1
        cross = ((x - x1) * dy) - ((y - y1) * dx)
        within = (
            (x >= min(x1, x2) - tolerance)
            & (x <= max(x1, x2) + tolerance)
            & (y >= min(y1, y2) - tolerance)
            & (y <= max(y1, y2) + tolerance)
        )
        on_edge |= (np.abs(cross) <= tolerance) & within
        crossing = (y1 > y) != (y2 > y)
        denominator = dy if abs(dy) > np.finfo(float).eps else np.finfo(float).eps
        x_intersection = ((dx * (y - y1)) / denominator) + x1
        inside ^= crossing & (x < x_intersection)
        x1, y1 = x2, y2
    return inside | on_edge


def roi_pixels(raw_image: np.ndarray, raw_roi_geometry: Any) -> np.ndarray:
    """Return source pixels selected by a raw-space rectangle or polygon."""
    pixels = np.asarray(raw_image)
    if pixels.ndim != 2:
        raise ValueError(f"Quantification requires a 2D raw image, got {pixels.shape}")
    image_height, image_width = pixels.shape

    polygon = _polygon_points(raw_roi_geometry)
    if polygon is not None:
        if len(polygon) < 3:
            return pixels[0:0, 0:0].reshape(-1)
        left = max(0, int(np.floor(np.min(polygon[:, 0]))))
        top = max(0, int(np.floor(np.min(polygon[:, 1]))))
        right = min(image_width, int(np.ceil(np.max(polygon[:, 0]))))
        bottom = min(image_height, int(np.ceil(np.max(polygon[:, 1]))))
        if right <= left or bottom <= top:
            return pixels[0:0, 0:0].reshape(-1)
        yy, xx = np.mgrid[top:bottom, left:right]
        mask = _points_in_polygon(xx + 0.5, yy + 0.5, polygon)
        return pixels[top:bottom, left:right][mask]

    rect = _rect_components(raw_roi_geometry)
    if rect is None:
        raise ValueError("ROI geometry must be a rectangle or a polygon with points.")
    x, y, width, height = rect
    x1 = max(0, int(x))
    y1 = max(0, int(y))
    x2 = min(image_width, int(x) + max(1, int(width)))
    y2 = min(image_height, int(y) + max(1, int(height)))
    if x2 <= x1 or y2 <= y1:
        return pixels[0:0, 0:0].reshape(-1)
    return pixels[y1:y2, x1:x2].reshape(-1)


def _reported_scalar(value: np.generic | float | int) -> int | float:
    scalar = value.item() if isinstance(value, np.generic) else value
    if isinstance(scalar, (int, np.integer)):
        return int(scalar)
    return float(scalar)


def quantify_roi(raw_image: np.ndarray, raw_roi_geometry: Any) -> dict[str, int | float]:
    """Quantify one raw-space ROI using only immutable source values."""
    selected = roi_pixels(raw_image, raw_roi_geometry)
    if selected.size == 0:
        return {
            "Area": 0,
            "Mean": 0.0,
            "Min": 0,
            "Max": 0,
            "IntDen": 0.0,
            "RawIntDen": 0,
        }
    values = selected.astype(np.float64, copy=False)
    area = int(selected.size)
    mean = float(np.mean(values))
    if np.issubdtype(selected.dtype, np.unsignedinteger):
        raw_total: int | float = int(np.sum(selected, dtype=np.uint64))
    elif np.issubdtype(selected.dtype, np.signedinteger):
        raw_total = int(np.sum(selected, dtype=np.int64))
    else:
        raw_total = float(np.sum(values, dtype=np.float64))
    total = float(raw_total)
    return {
        "Area": area,
        "Mean": round(mean, 3),
        "Min": _reported_scalar(np.min(selected)),
        "Max": _reported_scalar(np.max(selected)),
        "IntDen": round(total, 3),
        "RawIntDen": raw_total,
    }


def measure_all_lanes(
    image_path: str,
    band_rois: list,
    image_transform: dict[str, Any] | None = None,
) -> list[dict]:
    """Load the source once and quantify raw-space ROIs at native bit depth.

    image_transform remains accepted for old callers and saved workflows, but
    is intentionally ignored because it is presentation-only metadata.
    """
    del image_transform
    raw_image = load_raw_quantification_image(image_path)
    log.info(
        "Raw image loaded for quantification: %dx%d dtype=%s",
        raw_image.shape[1],
        raw_image.shape[0],
        raw_image.dtype,
    )
    return measure_all_lanes_in_array(raw_image, band_rois)


def measure_all_lanes_in_array(arr: np.ndarray, band_rois: list) -> list[dict]:
    """Quantify raw-space ROIs from an already loaded native-depth array."""
    raw_image = np.asarray(arr)
    results: list[dict] = []
    for ordinal, geometry in enumerate(band_rois, start=1):
        if isinstance(geometry, dict):
            lane_index = int(geometry.get("lane", ordinal))
            band_index = geometry.get("band")
            band_label = geometry.get("band_label")
        else:
            lane_index = ordinal
            band_index = None
            band_label = None
        row = {"lane": lane_index, **quantify_roi(raw_image, geometry)}
        if band_index is not None:
            row["band"] = band_label or f"Band {int(band_index)}"
        results.append(row)
        log.info("Lane %d: %s", lane_index, row)
    return results
