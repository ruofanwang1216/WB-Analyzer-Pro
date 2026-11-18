"""Non-destructive presentation transforms for WB image previews.

Tone and geometry transforms in this module are deliberately presentation
metadata.  They never replace the source image and are never part of the
quantification pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any

import numpy as np
from PIL import Image


MAX_16BIT_VALUE = 65535
MIN_TONE_VALUE = -MAX_16BIT_VALUE
MAX_TONE_VALUE = MAX_16BIT_VALUE * 2
MIN_GAMMA = 0.1
MAX_GAMMA = 10.0

# Image Lab clips 0.1 % of the pixels at each end when "Auto Scale" is
# pressed.  Verified against the display settings stored in .scn documents.
IMAGE_LAB_AUTO_TRIM = 0.001
# ImageJ "Brightness/Contrast" constants (ij.plugin.frame.ContrastAdjuster).
IMAGEJ_AUTO_THRESHOLD = 5000
IMAGEJ_SLIDER_RANGE = 256

# TIFF ImageDescription key used to carry the display settings an Image Lab
# document was saved with.  The pixels themselves stay untouched.
TIFF_IMAGE_DESCRIPTION_TAG = 270
DISPLAY_SETTINGS_KEY = "wb_analyzer_display"


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
        gamma = float(self.gamma)
        if not math.isfinite(gamma):
            gamma = 1.0
        gamma = max(MIN_GAMMA, min(MAX_GAMMA, gamma))
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

    def is_axis_aligned(self) -> bool:
        """True when the rotation is a multiple of 90 degrees (no resampling)."""
        quarter_turns = self.sanitized().rotation / 90.0
        return abs(quarter_turns - round(quarter_turns)) < 1e-9

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
    *,
    scale: int = 1,
) -> np.ndarray:
    """Render an 8-bit preview through non-destructive geometry metadata.

    ``scale`` renders the result on a grid that many times finer than the
    source, using bilinear interpolation.  The view enlarges an unrotated
    preview bilinearly as well, so a rotated image drawn this way looks the
    same as before the rotation: neither softer nor sharper.  Use an odd
    factor so the finer grid samples source pixel centres.  It only applies
    when the geometry actually rotates.
    """
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
    factor = max(1, int(scale))
    resample = Image.Resampling.BICUBIC
    if factor > 1 and not value.is_axis_aligned():
        resample = Image.Resampling.BILINEAR
        # Output pixel centre (i + 0.5) / factor is where the 1x canvas is
        # sampled, so the finer grid covers exactly the same canvas area.
        inverse = inverse @ np.diag((1.0 / factor, 1.0 / factor, 1.0))
        output_size = (output_size[0] * factor, output_size[1] * factor)
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
        resample=resample,
        fillcolor=fill_value,
    )
    return np.ascontiguousarray(np.asarray(image, dtype=np.uint8))


def auto_scale_range_16(
    pixels: np.ndarray,
    trim_fraction: float = 0.001,
) -> tuple[int, int]:
    """
    Image Lab's "Auto Scale": with ``n = round(0.1 % of the pixels)``, Low is
    the n-th darkest pixel value and High the n-th brightest.

    This rule reproduces, to the exact value, the Low / High that Image Lab
    stores in .scn documents after Auto Scale.  Uses a 16-bit histogram, so
    runtime is O(N + 65536) rather than sorting all pixels.  The returned
    range is intended for preview display, not analysis.
    """
    if pixels.size == 0:
        return 0, MAX_16BIT_VALUE

    flat = np.asarray(pixels, dtype=np.uint16).reshape(-1)
    hist = np.bincount(flat, minlength=MAX_16BIT_VALUE + 1)
    trim_count = min(flat.size // 2, int(round(flat.size * max(0.0, trim_fraction))))

    cumulative_low = np.cumsum(hist)
    rank = max(1, trim_count)
    low = int(np.searchsorted(cumulative_low, rank, side="left"))

    cumulative_high = np.cumsum(hist[::-1])
    high_from_top = int(np.searchsorted(cumulative_high, rank, side="left"))
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


# ── Display defaults carried by the image file ──────────────────────────────


def encode_display_settings(params: ImageTransformParams, *, source: str = "") -> str:
    """Return the TIFF ImageDescription text that stores display settings."""
    payload: dict[str, Any] = image_transform_to_dict(params)
    if source:
        payload["source"] = source
    return json.dumps({DISPLAY_SETTINGS_KEY: payload}, separators=(",", ":"))


def embedded_display_settings(img: Any) -> ImageTransformParams | None:
    """Read display settings stored in a TIFF ImageDescription, if any."""
    try:
        tag_v2 = getattr(img, "tag_v2", None)
        raw = tag_v2.get(TIFF_IMAGE_DESCRIPTION_TAG) if tag_v2 is not None else None
        if isinstance(raw, (tuple, list)):
            raw = raw[0] if raw else None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")
        if not isinstance(raw, str) or DISPLAY_SETTINGS_KEY not in raw:
            return None
        data = json.loads(raw.strip("\x00 \t\r\n")).get(DISPLAY_SETTINGS_KEY)
        if not isinstance(data, dict):
            return None
        return ImageTransformParams(
            low=int(float(data["low"])),
            high=int(float(data["high"])),
            gamma=float(data.get("gamma", 1.0)),
            inverted=bool(data.get("inverted", True)),
        ).sanitized()
    except Exception:
        return None


def default_display_params(
    img: Any,
    pixels: np.ndarray | None = None,
) -> ImageTransformParams:
    """Choose how an image is first shown.

    1. Settings stored with the file (an Image Lab document's own Low / High /
       Gamma / Invert) win, so the image opens exactly as it looked there.
    2. A 16-bit image without stored settings is auto-scaled the Image Lab
       way; shown over 0..65535 it would usually be almost blank.
    3. Everything else (8-bit, RGB) is shown over its full range.
    """
    embedded = embedded_display_settings(img)
    if embedded is not None:
        return embedded
    inverted = default_inverted_for_pil_image(img, fallback=True)
    if pixels is not None:
        source = np.asarray(pixels)
        if (
            source.ndim == 2
            and source.size
            and np.issubdtype(source.dtype, np.unsignedinteger)
            and source.dtype.itemsize == 2
        ):
            low, high = auto_scale_range_16(source)
            return ImageTransformParams(low=low, high=high, inverted=inverted).sanitized()
    return ImageTransformParams(inverted=inverted)


# ── Histogram and saturation ────────────────────────────────────────────────


def display_histogram(pixels: np.ndarray, bins: int = 256) -> tuple[np.ndarray, int, int]:
    """Histogram of a 16-bit display source between its darkest and brightest value.

    Returns ``(counts, data_min, data_max)``.  Like Image Lab, the x axis covers
    the range present in the image rather than the full 16 bits.
    """
    source = np.asarray(pixels)
    if source.size == 0:
        return np.zeros(bins, dtype=np.int64), 0, MAX_16BIT_VALUE
    flat = source.reshape(-1)
    data_min = int(flat.min())
    data_max = int(flat.max())
    if data_max <= data_min:
        counts = np.zeros(bins, dtype=np.int64)
        counts[0] = flat.size
        return counts, data_min, data_min + 1
    counts, _ = np.histogram(flat, bins=bins, range=(data_min, data_max))
    return counts.astype(np.int64), data_min, data_max


def saturation_ceiling(pixels: np.ndarray) -> int:
    """Largest value the pixel type can hold; pixels at it are saturated."""
    dtype = np.asarray(pixels).dtype
    if np.issubdtype(dtype, np.integer):
        return int(np.iinfo(dtype).max)
    return MAX_16BIT_VALUE


def saturated_mask(pixels: np.ndarray) -> np.ndarray:
    source = np.asarray(pixels)
    if source.ndim == 3:
        source = source[:, :, :3].max(axis=2)
    return source >= saturation_ceiling(source)


# ── ImageJ-compatible auto and brightness / contrast ────────────────────────


def imagej_auto_range(
    pixels: np.ndarray,
    auto_threshold: int = 0,
) -> tuple[int, int, int]:
    """ImageJ's Brightness/Contrast "Auto" button.

    Port of ``ContrastAdjuster.autoAdjust``.  Each press is more aggressive
    than the last; pass the returned threshold back in to get the next step
    (``0`` starts over).  Returns ``(low, high, auto_threshold)``.
    """
    source = np.asarray(pixels)
    if source.size == 0:
        return 0, MAX_16BIT_VALUE, 0
    flat = source.reshape(-1)
    data_min = float(flat.min())
    data_max = float(flat.max())
    if data_max <= data_min:
        return int(data_min), int(data_min) + 1, 0

    bins = 256
    bin_size = (data_max - data_min) / bins
    histogram, _ = np.histogram(flat, bins=bins, range=(data_min, data_max))
    pixel_count = int(flat.size)

    if auto_threshold < 10:
        auto_threshold = IMAGEJ_AUTO_THRESHOLD
    else:
        auto_threshold //= 2
    threshold = pixel_count // auto_threshold
    limit = pixel_count // 10

    counts = np.where(histogram > limit, 0, histogram)
    found = np.flatnonzero(counts > threshold)
    if found.size == 0:
        return int(round(data_min)), int(round(data_max)), auto_threshold
    hmin = int(found[0])
    hmax = int(found[-1])
    low = data_min + hmin * bin_size
    high = data_min + hmax * bin_size
    if high <= low:
        low, high = data_min, data_max
    return int(round(low)), int(round(high)), auto_threshold


def imagej_adjust_brightness(
    low: float,
    high: float,
    slider: float,
    default_low: float,
    default_high: float,
) -> tuple[int, int]:
    """Move the display window like ImageJ's Brightness slider (0..256)."""
    span = float(default_high - default_low)
    center = default_low + span * ((IMAGEJ_SLIDER_RANGE - float(slider)) / IMAGEJ_SLIDER_RANGE)
    width = float(high - low)
    return int(round(center - width / 2.0)), int(round(center + width / 2.0))


def imagej_adjust_contrast(
    low: float,
    high: float,
    slider: float,
    default_low: float,
    default_high: float,
) -> tuple[int, int]:
    """Change the display window width like ImageJ's Contrast slider (0..256)."""
    center = low + (high - low) / 2.0
    span = float(default_high - default_low)
    mid = IMAGEJ_SLIDER_RANGE / 2.0
    value = float(slider)
    if value <= mid:
        slope = value / mid
    else:
        slope = mid / max(1e-9, IMAGEJ_SLIDER_RANGE - value)
    if slope <= 0:
        return int(round(low)), int(round(high))
    half = (0.5 * span) / slope
    return int(round(center - half)), int(round(center + half))


def imagej_slider_positions(
    low: float,
    high: float,
    default_low: float,
    default_high: float,
) -> tuple[int, int]:
    """Brightness and Contrast slider positions ImageJ shows for a window."""
    span = max(1.0, float(default_high - default_low))
    width = max(1.0, float(high - low))
    mid = IMAGEJ_SLIDER_RANGE / 2.0
    contrast = (span / width) * mid
    if contrast > mid:
        contrast = IMAGEJ_SLIDER_RANGE - (width / span) * mid
    center = low + width / 2.0
    brightness = IMAGEJ_SLIDER_RANGE - ((center - default_low) / span) * IMAGEJ_SLIDER_RANGE
    clamp = lambda v: int(round(max(0.0, min(float(IMAGEJ_SLIDER_RANGE), v))))  # noqa: E731
    return clamp(brightness), clamp(contrast)


def bake_display_into_uint16(
    pixels: np.ndarray,
    params: ImageTransformParams,
    *,
    flip: bool = False,
) -> np.ndarray:
    """Return 16-bit pixels with the Low / High / Gamma stretch applied.

    Used only when a display-adjusted TIFF is exported for other programs.
    """
    p = params.sanitized()
    arr = np.asarray(pixels).astype(np.float64)
    stretched = np.clip((arr - p.low) / max(1.0, float(p.high - p.low)), 0.0, 1.0)
    if abs(p.gamma - 1.0) > 1e-6:
        stretched = np.power(stretched, p.gamma)
    if flip:
        stretched = 1.0 - stretched
    return np.rint(stretched * MAX_16BIT_VALUE).astype(np.uint16)
