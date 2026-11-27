"""Automatic tilt detection for crooked Western blot images.

A blot that was photographed slightly rotated has bands that run along a
tilted line instead of horizontally.  ``estimate_band_tilt`` measures that
angle so the GUI can straighten the image in one click.

The estimate is a projection-profile search: for a candidate angle the image
is summed along lines of that angle.  When the lines run parallel to the
bands, every band collapses into a tall narrow peak, so the profile is at its
"sharpest".  The angle with the sharpest profile is the band tilt.

Nothing in this module modifies pixels.  The result is only an angle, which
the caller stores as non-destructive ``GeometryTransform`` metadata.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
from scipy import ndimage as ndi

from core.image_transform import GeometryTransform


# Search window.  A membrane placed by hand is rarely more than a few degrees
# off; beyond ~30 degrees horizontal bands and vertical lane smears become
# ambiguous.
DEFAULT_MAX_TILT_DEG = 30.0
# Tilts below this are not worth resampling the preview for.
MIN_USEFUL_TILT_DEG = 0.15
# The winning angle's profile sharpness must exceed the median over all
# candidate angles by this factor.  Clean blots score 8-50, pure noise and
# artifact-only images score below 2.5 (measured on the 24-image sample set).
MIN_CONFIDENCE_RATIO = 3.0

_ANALYSIS_MAX_SIDE = 420
_COARSE_STEP_DEG = 0.5
_FINE_STEP_DEG = 0.05
_FINE_HALF_WINDOW_DEG = 1.5
_PEAK_TOP_FRACTION = 0.8


@dataclass(frozen=True)
class TiltEstimate:
    """Result of :func:`estimate_band_tilt`.

    ``angle_deg`` is the clockwise tilt of the bands in screen coordinates
    (y pointing down): a positive value means bands descend towards the
    right.  ``confidence`` is the peak-to-median sharpness ratio.
    """

    angle_deg: float
    confidence: float
    reliable: bool

    @property
    def needs_correction(self) -> bool:
        return self.reliable and abs(self.angle_deg) >= MIN_USEFUL_TILT_DEG


def _to_luminance(pixels: np.ndarray) -> np.ndarray:
    source = np.asarray(pixels)
    if source.ndim == 3 and source.shape[2] >= 3:
        rgb = source[:, :, :3].astype(np.float64, copy=False)
        return (0.299 * rgb[:, :, 0]) + (0.587 * rgb[:, :, 1]) + (0.114 * rgb[:, :, 2])
    if source.ndim != 2:
        raise ValueError(f"Unexpected image shape: {source.shape}")
    return source.astype(np.float64, copy=False)


def _prepare(pixels: np.ndarray) -> np.ndarray:
    """Downsample, clip outliers and remove the slowly varying background."""
    image = _to_luminance(pixels)
    step = max(1, int(math.ceil(max(image.shape) / _ANALYSIS_MAX_SIDE)))
    if step > 1:
        height = (image.shape[0] // step) * step
        width = (image.shape[1] // step) * step
        image = image[:height, :width].reshape(
            height // step, step, width // step, step
        ).mean(axis=(1, 3))
    low, high = np.percentile(image, (1.0, 99.5))
    if not high > low:
        return np.zeros_like(image)
    image = (np.clip(image, low, high) - low) / (high - low)
    image = image - ndi.gaussian_filter(image, max(image.shape) / 12.0, mode="nearest")
    return ndi.gaussian_filter(image, 0.7)


class _ProfileScorer:
    """Scores how sharply the image projects along a candidate band angle."""

    def __init__(self, image: np.ndarray) -> None:
        height, width = image.shape
        yy, xx = np.mgrid[0:height, 0:width].astype(np.float64)
        yy -= (height - 1) / 2.0
        xx -= (width - 1) / 2.0
        # A centred disc keeps the number of contributing pixels identical
        # for every angle, so no angle is favoured by the image's shape.
        radius = max(2.0, min(height, width) / 2.0 - 1.0)
        mask = (xx * xx + yy * yy) <= radius * radius
        self._x = xx[mask]
        self._y = yy[mask]
        values = image[mask]
        self._values = values - values.mean() if values.size else values
        self._radius = radius
        self._bins = int(2 * radius) + 3

    def score(self, angle_deg: float) -> float:
        if self._values.size == 0:
            return 0.0
        theta = math.radians(angle_deg)
        offset = self._y * math.cos(theta) - self._x * math.sin(theta) + self._radius + 1.0
        index = np.floor(offset).astype(np.intp)
        fraction = offset - index
        bins = self._bins
        sums = (
            np.bincount(index, self._values * (1.0 - fraction), minlength=bins + 1)
            + np.bincount(index + 1, self._values * fraction, minlength=bins + 1)
        )
        counts = (
            np.bincount(index, 1.0 - fraction, minlength=bins + 1)
            + np.bincount(index + 1, fraction, minlength=bins + 1)
        )
        valid = counts > self._radius * 0.5
        if int(np.count_nonzero(valid)) < 3:
            return 0.0
        profile = sums[valid] / counts[valid]
        return float(np.sum(np.diff(profile) ** 2))


def estimate_band_tilt(
    pixels: np.ndarray,
    *,
    max_tilt_deg: float = DEFAULT_MAX_TILT_DEG,
) -> TiltEstimate:
    """Estimate how far the bands in ``pixels`` are rotated from horizontal.

    Works on raw 8/16-bit grayscale or RGB arrays of either polarity.  The
    input is never modified.
    """
    source = np.asarray(pixels)
    if source.size == 0 or min(source.shape[:2]) < 16:
        return TiltEstimate(0.0, 0.0, False)
    scorer = _ProfileScorer(_prepare(source))

    limit = max(1.0, float(max_tilt_deg))
    coarse = np.arange(-limit, limit + 1e-9, _COARSE_STEP_DEG)
    coarse_scores = np.array([scorer.score(angle) for angle in coarse])
    baseline = float(np.median(coarse_scores))
    best = int(np.argmax(coarse_scores))
    if baseline <= 0.0 or not np.isfinite(coarse_scores[best]):
        return TiltEstimate(0.0, 0.0, False)
    confidence = float(coarse_scores[best] / baseline)

    # Thick bands leave the profile almost equally sharp over a few tenths of
    # a degree, so picking the single best sample would wander across that
    # plateau.  The centre of mass of the top of the peak is stable.
    fine = np.arange(
        coarse[best] - _FINE_HALF_WINDOW_DEG,
        coarse[best] + _FINE_HALF_WINDOW_DEG + 1e-9,
        _FINE_STEP_DEG,
    )
    fine_scores = np.array([scorer.score(angle) for angle in fine])
    top = float(np.max(fine_scores))
    floor = baseline + _PEAK_TOP_FRACTION * (top - baseline)
    peak = int(np.argmax(fine_scores))
    left = peak
    while left > 0 and fine_scores[left - 1] > floor:
        left -= 1
    right = peak
    while right < len(fine) - 1 and fine_scores[right + 1] > floor:
        right += 1
    weights = fine_scores[left : right + 1] - floor
    if float(np.sum(weights)) > 0.0:
        angle = float(np.sum(fine[left : right + 1] * weights) / np.sum(weights))
    else:
        angle = float(fine[peak])
    confidence = max(confidence, top / baseline)

    # A maximum pinned to the edge of the search window is not a real peak.
    at_edge = abs(angle) >= limit - _COARSE_STEP_DEG
    reliable = confidence >= MIN_CONFIDENCE_RATIO and not at_edge
    return TiltEstimate(round(angle, 3), confidence, reliable)


def straightened_geometry(
    current: GeometryTransform,
    raw_tilt_deg: float,
) -> GeometryTransform:
    """Return ``current`` with its rotation replaced so bands are horizontal.

    ``raw_tilt_deg`` is the tilt measured on the *raw* (untransformed) image.
    Existing flips are kept.  Because horizontal is reached at two rotations
    180 degrees apart, the one nearest the current rotation is chosen so the
    blot never turns upside down.
    """
    value = current.sanitized()
    # A single mirror reverses the sense of a tilt; two mirrors restore it.
    presented_tilt = -raw_tilt_deg if value.flip_x != value.flip_y else raw_tilt_deg
    target = -presented_tilt
    delta = ((target - value.rotation + 90.0) % 180.0) - 90.0
    return GeometryTransform(
        rotation=value.rotation + delta,
        flip_x=value.flip_x,
        flip_y=value.flip_y,
    ).sanitized()
