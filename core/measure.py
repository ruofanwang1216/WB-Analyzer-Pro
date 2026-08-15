"""
Pure-Python band densitometry matching Fiji/ImageJ 8-bit signal behavior.
Supports 8-bit and 16-bit grayscale TIFF (Bio-Rad ChemiDoc), PNG, JPG.

The values reported here are signal intensities: a stronger band is a larger
number regardless of whether the file stores the band as dark-on-light or
light-on-dark.  Display-only inversion never changes the measurement polarity.
"""
from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image
from core.image_transform import (
    ImageTransformParams,
    default_inverted_for_pil_image,
    image_array_to_uint16_luminance,
    image_transform_from_dict,
    transform_pixels_16_to_8,
)
from utils.logger import get_logger

log = get_logger(__name__)


def _array_to_8bit_grayscale(arr: np.ndarray) -> np.ndarray:
    """Convert a Pillow image array to ImageJ-style 8-bit grayscale.

    - 16-bit grayscale: divide by 256 (same as ImageJ default)
    - RGB/RGBA: luminance = 0.299R + 0.587G + 0.114B, then scale if needed
    - 8-bit grayscale: use as-is
    """
    if arr.ndim == 2:
        if arr.dtype == np.uint16:
            # 16-bit grayscale (Bio-Rad ChemiDoc) -> 8-bit, matching ImageJ
            arr = (arr / 256).astype(np.uint8)
        else:
            arr = arr.astype(np.uint8)
    elif arr.ndim == 3:
        # RGB or RGBA
        rgb = arr[:, :, :3].astype(np.float64)
        gray = 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]
        if arr.dtype == np.uint16:
            gray = gray / 256
        arr = gray.round().clip(0, 255).astype(np.uint8)
    else:
        raise ValueError(f"Unexpected image shape: {arr.shape}")

    return arr


def _to_8bit_signal(image_path: str) -> np.ndarray:
    """Load an image as 8-bit WB signal, where a stronger band is brighter."""
    with Image.open(image_path) as img:
        # The viewer's default transform normalizes WB presentation to dark
        # bands. Quantitation must therefore use the opposite polarity.
        signal_inverted = not default_inverted_for_pil_image(img, fallback=True)
        arr = np.array(img)

    arr = _array_to_8bit_grayscale(arr)
    if signal_inverted:
        arr = np.subtract(255, arr, dtype=np.uint8)
    return arr


def _to_transformed_8bit_grayscale(
    image_path: str,
    image_transform: dict[str, Any] | ImageTransformParams,
) -> np.ndarray:
    """Load image and convert it to transformed 8-bit WB signal intensity."""
    with Image.open(image_path) as img:
        signal_inverted = not default_inverted_for_pil_image(img, fallback=True)
        arr = np.array(img)
    params = (
        image_transform.sanitized()
        if isinstance(image_transform, ImageTransformParams)
        else image_transform_from_dict(image_transform)
    )
    # ``image_transform.inverted`` is a display preference. Measurement
    # polarity comes from the file's default WB presentation instead.
    params = ImageTransformParams(
        low=params.low,
        high=params.high,
        gamma=params.gamma,
        inverted=signal_inverted,
    )
    pixels_16 = image_array_to_uint16_luminance(arr)
    return transform_pixels_16_to_8(pixels_16, params)


def measure_all_lanes(
    image_path: str,
    band_rois: list,  # list of QRectF or dict
    image_transform: dict[str, Any] | ImageTransformParams | None = None,
) -> list[dict]:
    """
    Load image once, convert to 8-bit WB signal, measure all lane ROIs.
    Returns list of result dicts with keys:
    lane, Area, Mean, Min, Max, IntDen, RawIntDen
    """
    if image_transform is None:
        arr = _to_8bit_signal(image_path)
    else:
        arr = _to_transformed_8bit_grayscale(image_path, image_transform)
    img_h, img_w = arr.shape
    log.info("Image loaded as 8-bit: %dx%d", img_w, img_h)
    return measure_all_lanes_in_array(arr, band_rois)


def measure_all_lanes_in_array(
    arr: np.ndarray,
    band_rois: list,  # list of QRectF or dict
) -> list[dict]:
    """Measure ROIs from an 8-bit array already prepared as signal intensity."""
    img_h, img_w = arr.shape

    results = []
    for i, r in enumerate(band_rois, start=1):
        if hasattr(r, 'x') and callable(r.x):
            x, y = int(r.x()), int(r.y())
            w, h = max(1, int(r.width())), max(1, int(r.height()))
            lane_index = i
            band_index = None
        else:
            x, y = int(r.get('x', 0)), int(r.get('y', 0))
            w, h = max(1, int(r.get('width', 1))), max(1, int(r.get('height', 1)))
            lane_index = int(r.get('lane', i))
            band_index = r.get('band')
            band_label = r.get('band_label')
        if hasattr(r, 'x') and callable(r.x):
            band_label = None

        x1, y1 = max(0, x), max(0, y)
        x2, y2 = min(img_w, x + w), min(img_h, y + h)
        roi = arr[y1:y2, x1:x2]

        if roi.size == 0:
            log.warning("Lane %d: empty ROI after clamping", lane_index)
            results.append({"lane": lane_index, "Area": 0, "Mean": 0.0,
                            "Min": 0, "Max": 0, "IntDen": 0.0, "RawIntDen": 0})
            continue

        roi_f = roi.astype(np.float64)
        area = roi.size
        mean = float(np.mean(roi_f))
        raw_int_den = int(np.sum(roi_f))

        row = {
            "lane": lane_index,
            "Area": area,
            "Mean": round(mean, 3),
            "Min": int(np.min(roi)),
            "Max": int(np.max(roi)),
            "IntDen": round(mean * area, 3),
            "RawIntDen": raw_int_den,
        }
        if band_index is not None:
            row["band"] = band_label or f"Band {int(band_index)}"
            log.info("Lane %d Band %s: %s", lane_index, row["band"], row)
        else:
            log.info("Lane %d: %s", lane_index, row)
        results.append(row)

    return results
