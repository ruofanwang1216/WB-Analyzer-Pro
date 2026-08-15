"""Display-only image transform helpers for WB image previews."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, TiffImagePlugin


MAX_16BIT_VALUE = 65535
MIN_TONE_VALUE = -MAX_16BIT_VALUE
MAX_TONE_VALUE = MAX_16BIT_VALUE * 2


@dataclass(frozen=True)
class ImageTransformParams:
    low: int = 0
    high: int = MAX_16BIT_VALUE
    gamma: float = 1.0
    inverted: bool = False

    def sanitized(self) -> "ImageTransformParams":
        # Keep the source range (0..65535) at the center of each tone control.
        # Values outside the source range are useful: they let users expand the
        # displayed dynamic range instead of only being able to clip it inward.
        low = max(MIN_TONE_VALUE, min(MAX_16BIT_VALUE, int(round(self.low))))
        high = max(0, min(MAX_TONE_VALUE, int(round(self.high))))
        gamma = max(0.1, min(4.0, float(self.gamma)))
        if high <= low:
            high = min(MAX_16BIT_VALUE, low + 1)
            if high <= low:
                low = max(0, high - 1)
        return ImageTransformParams(low=low, high=high, gamma=gamma, inverted=bool(self.inverted))


def image_transform_to_dict(params: ImageTransformParams) -> dict[str, Any]:
    """Return JSON-ready display transform data."""
    p = params.sanitized()
    return {
        "low": p.low,
        "high": p.high,
        "gamma": p.gamma,
        "inverted": p.inverted,
    }


def image_transform_from_dict(
    data: dict[str, Any] | None,
    *,
    default_inverted: bool = True,
) -> ImageTransformParams:
    """Build display transform params from optional JSON-like data."""
    if not data:
        return ImageTransformParams(inverted=default_inverted)
    return ImageTransformParams(
        low=int(float(data.get("low", 0))),
        high=int(float(data.get("high", MAX_16BIT_VALUE))),
        gamma=float(data.get("gamma", 1.0)),
        inverted=bool(data.get("inverted", default_inverted)),
    ).sanitized()


def default_inverted_for_pil_image(img: Any, *, fallback: bool = True) -> bool:
    """Choose the default display polarity from TIFF photometric metadata."""
    mode = str(getattr(img, "mode", "") or "")
    try:
        tag_v2 = getattr(img, "tag_v2", None)
        raw_photometric = tag_v2.get(262) if tag_v2 is not None else None
        if isinstance(raw_photometric, (tuple, list)) and raw_photometric:
            raw_photometric = raw_photometric[0]
        if raw_photometric is None:
            if mode in {"RGB", "RGBA"}:
                return False
            return fallback
        photometric = int(raw_photometric)
    except Exception:
        if mode in {"RGB", "RGBA"}:
            return False
        return fallback

    if photometric == 0:  # WhiteIsZero: low source values should display white.
        return True
    if photometric == 1:  # BlackIsZero: source values already match display polarity.
        return False
    if photometric == 2:  # RGB: source values are already display-ready colors.
        return False
    return fallback


def border_median_fill_value(pixels: np.ndarray) -> float | tuple[float, ...]:
    """Return a robust background fill value estimated from image borders."""
    arr = np.asarray(pixels)
    if arr.size == 0:
        return 0.0

    if arr.ndim == 2:
        border = np.concatenate((
            arr[0, :].reshape(-1),
            arr[-1, :].reshape(-1),
            arr[:, 0].reshape(-1),
            arr[:, -1].reshape(-1),
        ))
        return float(np.median(border))

    if arr.ndim == 3:
        border = np.concatenate((
            arr[0, :, :].reshape(-1, arr.shape[-1]),
            arr[-1, :, :].reshape(-1, arr.shape[-1]),
            arr[:, 0, :].reshape(-1, arr.shape[-1]),
            arr[:, -1, :].reshape(-1, arr.shape[-1]),
        ), axis=0)
        medians = np.median(border, axis=0)
        return tuple(float(value) for value in medians.tolist())

    return 0.0


def rotate_display_pixels_to_file(
    display_pixels: np.ndarray,
    target_path: str | Path,
    *,
    angle_deg: float,
) -> ImageTransformParams:
    """
    Rotate the exact 8-bit grayscale display buffer and save it as an image.

    Custom rotate intentionally preserves what the user sees in the viewer,
    instead of reinterpreting source TIFF photometric metadata. TIFF output is
    normalized to BlackIsZero 8-bit grayscale, so it reloads without inversion.
    """
    pixels = np.ascontiguousarray(
        np.asarray(display_pixels).clip(0, 255).astype(np.uint8)
    )
    if pixels.ndim != 2 or pixels.size == 0:
        raise ValueError("Custom rotate requires a non-empty 2D display image.")

    fill_value = int(round(float(border_median_fill_value(pixels))))
    rotated_img = Image.fromarray(pixels, mode="L").rotate(
        float(angle_deg),
        resample=Image.Resampling.BICUBIC,
        expand=True,
        fillcolor=fill_value,
    )
    return _save_display_image(rotated_img, target_path)


def flip_display_pixels_to_file(
    display_pixels: np.ndarray,
    target_path: str | Path,
    *,
    vertical: bool,
) -> ImageTransformParams:
    """Flip the visible 8-bit grayscale buffer and save it as an image."""
    pixels = np.ascontiguousarray(
        np.asarray(display_pixels).clip(0, 255).astype(np.uint8)
    )
    if pixels.ndim != 2 or pixels.size == 0:
        raise ValueError("Image flip requires a non-empty 2D display image.")

    operation = (
        Image.Transpose.FLIP_TOP_BOTTOM
        if vertical
        else Image.Transpose.FLIP_LEFT_RIGHT
    )
    return _save_display_image(Image.fromarray(pixels, mode="L").transpose(operation), target_path)


def _save_display_image(image: Image.Image, target_path: str | Path) -> ImageTransformParams:
    """Save a display-ready grayscale image with normalized TIFF metadata."""
    target = Path(target_path)
    save_format = {
        ".tif": "TIFF",
        ".tiff": "TIFF",
        ".png": "PNG",
        ".jpg": "JPEG",
        ".jpeg": "JPEG",
    }.get(target.suffix.lower(), "TIFF")

    save_kwargs: dict[str, object] = {}
    if save_format == "TIFF":
        tiffinfo = TiffImagePlugin.ImageFileDirectory_v2()
        tiffinfo[262] = 1
        tiffinfo[258] = 8
        tiffinfo[277] = 1
        save_kwargs["compression"] = "raw"
        save_kwargs["tiffinfo"] = tiffinfo
    elif save_format == "JPEG":
        save_kwargs["quality"] = 100
        save_kwargs["subsampling"] = 0

    image.save(target, format=save_format, **save_kwargs)
    return ImageTransformParams(inverted=False)


def transform_pixel_16_to_8(pixel: int, params: ImageTransformParams) -> int:
    """Map one 16-bit pixel to an 8-bit display value using stretch + gamma."""
    p = params.sanitized()
    stretched = (float(pixel) - float(p.low)) / max(1.0, float(p.high - p.low))
    clamped = max(0.0, min(1.0, stretched))
    gamma_corrected = clamped ** p.gamma
    if p.inverted:
        gamma_corrected = 1.0 - gamma_corrected
    return int(round(max(0.0, min(255.0, gamma_corrected * 255.0))))


def transform_pixels_16_to_8(
    pixels: np.ndarray,
    params: ImageTransformParams,
) -> np.ndarray:
    """Vectorized 16-bit-to-8-bit display transform."""
    p = params.sanitized()
    arr = pixels.astype(np.float32, copy=False)
    stretched = (arr - float(p.low)) / max(1.0, float(p.high - p.low))
    stretched = np.clip(stretched, 0.0, 1.0)
    if abs(p.gamma - 1.0) > 1e-6:
        stretched = np.power(stretched, p.gamma)
    if p.inverted:
        stretched = 1.0 - stretched
    return np.rint(stretched * 255.0).clip(0, 255).astype(np.uint8)


def auto_scale_range_16(
    pixels: np.ndarray,
    trim_fraction: float = 0.001,
) -> tuple[int, int]:
    """
    Compute a robust display range by trimming the lowest/highest 0.1% pixels.

    Uses a 16-bit histogram, so runtime is O(N + 65536) rather than sorting all
    pixels. The returned range is intended for preview display, not analysis.
    """
    if pixels.size == 0:
        return 0, MAX_16BIT_VALUE

    flat = np.asarray(pixels, dtype=np.uint16).reshape(-1)
    hist = np.bincount(flat, minlength=MAX_16BIT_VALUE + 1)
    trim_count = min(flat.size // 2, int(round(flat.size * max(0.0, trim_fraction))))

    cumulative_low = np.cumsum(hist)
    low = int(np.searchsorted(cumulative_low, trim_count + 1, side="left"))

    cumulative_high = np.cumsum(hist[::-1])
    high_from_top = int(np.searchsorted(cumulative_high, trim_count + 1, side="left"))
    high = MAX_16BIT_VALUE - high_from_top

    if high <= low:
        if low < MAX_16BIT_VALUE:
            high = low + 1
        else:
            low = MAX_16BIT_VALUE - 1
            high = MAX_16BIT_VALUE
    return low, high


def image_array_to_uint16_luminance(arr: np.ndarray) -> np.ndarray:
    """
    Normalize common Pillow image arrays into a 2D uint16 luminance buffer.

    8-bit images are expanded to the 16-bit display slider range. 16-bit images
    are preserved. RGB/RGBA arrays are converted with standard luma weights.
    """
    source = np.asarray(arr)
    if source.ndim == 2:
        if source.dtype == np.uint16:
            return source.astype(np.uint16, copy=False)
        return (source.astype(np.uint32, copy=False) * 257).clip(0, MAX_16BIT_VALUE).astype(np.uint16)

    if source.ndim == 3 and source.shape[2] >= 3:
        rgb = source[:, :, :3].astype(np.float32, copy=False)
        luma = (0.299 * rgb[:, :, 0]) + (0.587 * rgb[:, :, 1]) + (0.114 * rgb[:, :, 2])
        if source.dtype == np.uint16:
            return np.rint(luma).clip(0, MAX_16BIT_VALUE).astype(np.uint16)
        return np.rint(luma * 257.0).clip(0, MAX_16BIT_VALUE).astype(np.uint16)

    raise ValueError(f"Unexpected image shape: {source.shape}")
