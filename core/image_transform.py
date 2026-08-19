"""Non-destructive presentation transforms for WB image previews.

Tone and geometry transforms in this module are deliberately presentation
metadata.  They never replace the source image and are never part of the
quantification pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import numpy as np
from PIL import Image


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


@dataclass(frozen=True)
class GeometryTransform:
    """Non-destructive raw-image -> presentation geometry.

    Flips are applied around the raw image centre first, followed by a
    clockwise rotation in screen coordinates.  The transformed image is
    expanded so no source pixels are clipped.
    """

    rotation: float = 0.0
    flip_x: bool = False
    flip_y: bool = False

    def sanitized(self) -> "GeometryTransform":
        rotation = float(self.rotation)
        if not math.isfinite(rotation):
            rotation = 0.0
        rotation = ((rotation + 180.0) % 360.0) - 180.0
        if abs(rotation) < 1e-12:
            rotation = 0.0
        return GeometryTransform(rotation, bool(self.flip_x), bool(self.flip_y))

    def is_identity(self) -> bool:
        value = self.sanitized()
        return not value.flip_x and not value.flip_y and value.rotation == 0.0

    def rotated(self, angle_deg: float) -> "GeometryTransform":
        value = self.sanitized()
        return GeometryTransform(
            rotation=value.rotation + float(angle_deg),
            flip_x=value.flip_x,
            flip_y=value.flip_y,
        ).sanitized()

    def flipped_in_presentation(self, *, vertical: bool) -> "GeometryTransform":
        """Post-compose a flip in the currently displayed coordinate space."""
        value = self.sanitized()
        return GeometryTransform(
            rotation=-value.rotation,
            flip_x=value.flip_x if vertical else not value.flip_x,
            flip_y=not value.flip_y if vertical else value.flip_y,
        ).sanitized()

    def affine(self, raw_width: int, raw_height: int) -> tuple[np.ndarray, tuple[int, int]]:
        """Return a 3x3 raw-edge -> canvas-edge matrix and output size."""
        width = max(1, int(raw_width))
        height = max(1, int(raw_height))
        value = self.sanitized()
        radians = math.radians(value.rotation)
        cosine = math.cos(radians)
        sine = math.sin(radians)
        if abs(cosine) < 1e-12:
            cosine = 0.0
        elif abs(abs(cosine) - 1.0) < 1e-12:
            cosine = math.copysign(1.0, cosine)
        if abs(sine) < 1e-12:
            sine = 0.0
        elif abs(abs(sine) - 1.0) < 1e-12:
            sine = math.copysign(1.0, sine)
        rotation = np.array(((cosine, -sine), (sine, cosine)), dtype=np.float64)
        flips = np.diag(((-1.0 if value.flip_x else 1.0), (-1.0 if value.flip_y else 1.0)))
        linear = rotation @ flips
        center = np.array((width / 2.0, height / 2.0), dtype=np.float64)
        corners = np.array(
            ((0.0, 0.0), (width, 0.0), (width, height), (0.0, height)),
            dtype=np.float64,
        )
        centered = (corners - center) @ linear.T
        minimum = np.min(centered, axis=0)
        maximum = np.max(centered, axis=0)
        output_width = max(1, int(math.ceil((maximum[0] - minimum[0]) - 1e-9)))
        output_height = max(1, int(math.ceil((maximum[1] - minimum[1]) - 1e-9)))
        translation = -minimum - (linear @ center)
        matrix = np.array(
            (
                (linear[0, 0], linear[0, 1], translation[0]),
                (linear[1, 0], linear[1, 1], translation[1]),
                (0.0, 0.0, 1.0),
            ),
            dtype=np.float64,
        )
        return matrix, (output_width, output_height)

    def map_points_to_canvas(
        self, points: np.ndarray, raw_width: int, raw_height: int
    ) -> np.ndarray:
        matrix, _ = self.affine(raw_width, raw_height)
        return _map_affine_points(points, matrix)

    def map_points_to_raw(
        self, points: np.ndarray, raw_width: int, raw_height: int
    ) -> np.ndarray:
        matrix, _ = self.affine(raw_width, raw_height)
        return _map_affine_points(points, np.linalg.inv(matrix))


def _map_affine_points(points: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    source = np.asarray(points, dtype=np.float64)
    original_shape = source.shape
    if source.size == 0 or original_shape[-1:] != (2,):
        return source.copy()
    flat = source.reshape(-1, 2)
    homogeneous = np.column_stack((flat, np.ones(len(flat), dtype=np.float64)))
    mapped = homogeneous @ matrix.T
    return mapped[:, :2].reshape(original_shape)


def geometry_transform_to_dict(transform: GeometryTransform) -> dict[str, Any]:
    value = transform.sanitized()
    return {
        "rotation": value.rotation,
        "flip_x": value.flip_x,
        "flip_y": value.flip_y,
    }


def geometry_transform_from_dict(data: dict[str, Any] | None) -> GeometryTransform:
    if not isinstance(data, dict) or not data:
        return GeometryTransform()
    return GeometryTransform(
        rotation=float(data.get("rotation", 0.0)),
        flip_x=bool(data.get("flip_x", False)),
        flip_y=bool(data.get("flip_y", False)),
    ).sanitized()


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

    # Import opens images with the full display range and gamma 1.  Avoid a
    # full-size float32 buffer (and several temporary arrays) for that common
    # path.  The integer expression is exactly equivalent to rounding
    # ``value * 255 / 65535`` for every uint16 input value.
    source = np.asarray(pixels)
    if (
        source.dtype == np.uint16
        and p.low == 0
        and p.high == MAX_16BIT_VALUE
        and abs(p.gamma - 1.0) <= 1e-6
    ):
        mapped = (
            (source.astype(np.uint32, copy=False) * 255 + (MAX_16BIT_VALUE // 2))
            // MAX_16BIT_VALUE
        ).astype(np.uint8)
        if p.inverted:
            np.subtract(255, mapped, out=mapped)
        return mapped

    arr = source.astype(np.float32, copy=False)
    stretched = (arr - float(p.low)) / max(1.0, float(p.high - p.low))
    stretched = np.clip(stretched, 0.0, 1.0)
    if abs(p.gamma - 1.0) > 1e-6:
        stretched = np.power(stretched, p.gamma)
    if p.inverted:
        stretched = 1.0 - stretched
    return np.rint(stretched * 255.0).clip(0, 255).astype(np.uint8)


def apply_geometry_to_display(
    display_pixels: np.ndarray,
    transform: GeometryTransform,
) -> np.ndarray:
    """Render an 8-bit preview through non-destructive geometry metadata."""
    pixels = np.ascontiguousarray(np.asarray(display_pixels, dtype=np.uint8))
    if pixels.ndim != 2 or pixels.size == 0:
        raise ValueError("Display geometry requires a non-empty 2D image.")
    value = transform.sanitized()
    if value.is_identity():
        # ``pixels`` is already the newly allocated presentation buffer.  It
        # is safe to hand it through without another full-image copy.
        return pixels

    height, width = pixels.shape
    matrix, output_size = value.affine(width, height)
    inverse = np.linalg.inv(matrix)
    coefficients = (
        float(inverse[0, 0]),
        float(inverse[0, 1]),
        float(inverse[0, 2]),
        float(inverse[1, 0]),
        float(inverse[1, 1]),
        float(inverse[1, 2]),
    )
    fill_value = int(round(float(border_median_fill_value(pixels))))
    image = Image.fromarray(pixels, mode="L").transform(
        output_size,
        Image.Transform.AFFINE,
        coefficients,
        resample=Image.Resampling.BICUBIC,
        fillcolor=fill_value,
    )
    return np.ascontiguousarray(np.asarray(image, dtype=np.uint8))


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
        if (
            np.issubdtype(source.dtype, np.unsignedinteger)
            and source.dtype.itemsize == 2
        ):
            # Accept native, little-endian, and big-endian uint16 sources.
            # A byte swap is needed only for a non-native source; preview
            # sampling keeps that conversion bounded to preview resolution.
            return source.astype(np.uint16, copy=False)
        return (source.astype(np.uint32, copy=False) * 257).clip(0, MAX_16BIT_VALUE).astype(np.uint16)

    if source.ndim == 3 and source.shape[2] >= 3:
        rgb = source[:, :, :3].astype(np.float32, copy=False)
        luma = (0.299 * rgb[:, :, 0]) + (0.587 * rgb[:, :, 1]) + (0.114 * rgb[:, :, 2])
        if source.dtype == np.uint16:
            return np.rint(luma).clip(0, MAX_16BIT_VALUE).astype(np.uint16)
        return np.rint(luma * 257.0).clip(0, MAX_16BIT_VALUE).astype(np.uint16)

    raise ValueError(f"Unexpected image shape: {source.shape}")


def image_array_to_raw_luminance(arr: np.ndarray) -> np.ndarray:
    """Return quantification pixels without reducing integer bit depth.

    Grayscale 8/16-bit arrays are returned in their native dtype. RGB/RGBA is
    converted to luminance because the densitometry result is scalar, while
    preserving the component dtype and its full numeric range.
    """
    source = np.asarray(arr)
    if source.ndim == 2:
        if np.issubdtype(source.dtype, np.integer) or np.issubdtype(source.dtype, np.floating):
            return np.ascontiguousarray(source)
        raise ValueError(f"Unsupported grayscale dtype: {source.dtype}")

    if source.ndim == 3 and source.shape[2] >= 3:
        rgb = source[:, :, :3].astype(np.float64, copy=False)
        luminance = (0.299 * rgb[:, :, 0]) + (0.587 * rgb[:, :, 1]) + (0.114 * rgb[:, :, 2])
        if np.issubdtype(source.dtype, np.integer):
            info = np.iinfo(source.dtype)
            return np.rint(luminance).clip(info.min, info.max).astype(source.dtype)
        if np.issubdtype(source.dtype, np.floating):
            return luminance.astype(source.dtype)
    raise ValueError(f"Unexpected image shape: {source.shape}")
