"""
WB band detection — manual ROI mode and fully automatic mode.
Band boundaries are computed per-peak using half-height width (not uniform height).
Auto mode preserves the true per-lane band ROIs returned by peak detection.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image
from PySide6.QtCore import QRectF

from utils.logger import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class _AutoDetectParams:
    sensitivity: float
    pre_smooth_sigma: float
    background_sigma: float
    y_smooth_sigma: float
    x_smooth_sigma: float
    band_smooth_sigma: float
    zone_peak_prom_frac: float
    zone_peak_min_distance: int
    zone_peak_min_width: int
    zone_peak_keep_frac: float
    zone_padding: int
    zone_min_height: int
    lane_peak_prom_frac: float
    lane_min_distance: int
    lane_min_width: int
    lane_edge_margin: int
    lane_outer_width: int
    lane_max_width: int
    lane_width_tol: float
    band_peak_prom_frac: float
    band_min_distance_frac: float
    band_min_width_frac: float
    band_padding: int
    edge_lane_fill_margin_frac: float
    lane_gap_fill_frac: float


def _load_8bit(image_path: str) -> np.ndarray:
    img = Image.open(image_path)
    arr = np.array(img)
    if arr.ndim == 2:
        if arr.dtype == np.uint16:
            return (arr / 256).astype(np.uint8)
        return arr.astype(np.uint8)
    elif arr.ndim == 3:
        rgb = arr[:, :, :3].astype(np.float64)
        gray = 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]
        return gray.round().clip(0, 255).astype(np.uint8)
    raise ValueError(f"Unexpected image shape: {arr.shape}")


def _smooth(signal: np.ndarray, sigma: float) -> np.ndarray:
    from scipy.ndimage import gaussian_filter1d

    return gaussian_filter1d(signal.astype(np.float64), sigma=max(1.0, sigma))


def _find_band_boundaries(smoothed: np.ndarray, peak: int) -> tuple[int, int]:
    """
    Find the left/right (or top/bottom) boundaries of a peak
    using the half-maximum method (FWHM).
    Returns (start, end) indices.
    """
    peak_val = smoothed[peak]
    half = peak_val / 2.0

    # Walk left from peak to find start
    start = peak
    for i in range(peak, -1, -1):
        if smoothed[i] < half:
            start = i
            break

    # Walk right from peak to find end
    end = peak
    for i in range(peak, len(smoothed)):
        if smoothed[i] < half:
            end = i
            break

    # Ensure minimum size
    if end - start < 4:
        start = max(0, peak - 2)
        end = min(len(smoothed) - 1, peak + 2)

    return start, end


def detect_band_roi(
    image_path: str,
    lane_roi: QRectF,
    bands_per_lane: int,
    target_band: int,
    dark_on_light: bool = False,
    sensitivity: float = 0.5,
) -> QRectF | None:
    """
    Manual mode: user provides lane_roi, bands_per_lane, target_band.
    Returns a single QRectF for the target band with FWHM-based height.
    """
    from scipy.signal import find_peaks

    arr = _load_8bit(image_path)
    img_h, img_w = arr.shape

    x1 = max(0, int(lane_roi.x()))
    y1 = max(0, int(lane_roi.y()))
    x2 = min(img_w, int(lane_roi.x() + lane_roi.width()))
    y2 = min(img_h, int(lane_roi.y() + lane_roi.height()))

    roi_arr = arr[y1:y2, x1:x2]
    if roi_arr.size == 0:
        log.warning("Empty ROI")
        return None

    projection = roi_arr.mean(axis=1).astype(np.float64)
    if dark_on_light:
        projection = 255.0 - projection

    roi_height = y2 - y1
    sigma = max(2.0, roi_height / 40.0)
    smoothed = _smooth(projection, sigma)

    prominence = smoothed.max() * (0.15 - sensitivity * 0.1)  # lower threshold at high sensitivity
    min_dist = max(4, roi_height // (bands_per_lane * 2))
    peaks, _ = find_peaks(smoothed, distance=min_dist, prominence=max(1.0, prominence))

    log.info("Manual detect: %d peak(s) found, want band %d of %d", len(peaks), target_band, bands_per_lane)

    if len(peaks) < target_band:
        log.warning("Only %d band(s) found", len(peaks))
        return None

    peaks_sorted = sorted(peaks)
    peak = peaks_sorted[target_band - 1]
    b_start, b_end = _find_band_boundaries(smoothed, peak)

    return QRectF(
        lane_roi.x(),
        y1 + b_start,
        lane_roi.width(),
        max(4, b_end - b_start),
    )


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _build_auto_params(img_h: int, img_w: int, sensitivity: float) -> _AutoDetectParams:
    sens = _clamp01(sensitivity)
    min_dim = min(img_h, img_w)
    return _AutoDetectParams(
        sensitivity=sens,
        pre_smooth_sigma=max(0.8, min_dim / 500.0),
        background_sigma=max(12.0, min_dim / 14.0),
        y_smooth_sigma=max(2.0, img_h / 90.0),
        x_smooth_sigma=max(2.0, img_w / 120.0),
        band_smooth_sigma=max(1.5, img_h / 120.0),
        zone_peak_prom_frac=0.18 - 0.08 * sens,
        zone_peak_min_distance=max(6, int(img_h * (0.08 - 0.03 * sens))),
        zone_peak_min_width=max(4, int(img_h * (0.018 - 0.006 * sens))),
        zone_peak_keep_frac=0.55 - 0.20 * sens,
        zone_padding=max(10, int(img_h * (0.05 + 0.03 * sens))),
        zone_min_height=max(20, int(img_h * (0.16 - 0.04 * sens))),
        lane_peak_prom_frac=0.22 - 0.14 * sens,
        lane_min_distance=max(12, int(img_w * (0.10 - 0.04 * sens))),
        lane_min_width=max(8, int(img_w * (0.020 - 0.008 * sens))),
        lane_edge_margin=max(2, int(img_w * 0.01)),
        lane_outer_width=max(12, int(img_w * (0.06 + 0.02 * sens))),
        lane_max_width=max(24, int(img_w * (0.25 + 0.10 * sens))),
        lane_width_tol=2.4 + 0.4 * sens,
        band_peak_prom_frac=0.18 - 0.10 * sens,
        band_min_distance_frac=0.09 - 0.03 * sens,
        band_min_width_frac=0.020 - 0.007 * sens,
        band_padding=max(2, int(img_h * (0.006 + 0.004 * sens))),
        edge_lane_fill_margin_frac=1.55 - 0.20 * sens,
        lane_gap_fill_frac=1.70 - 0.15 * sens,
    )


def _prepare_signal_for_auto_detection(
    arr: np.ndarray,
    dark_on_light: bool,
    params: _AutoDetectParams,
) -> np.ndarray:
    """
    Build a background-corrected signal image where stronger band/lane signal
    always means larger numeric values, regardless of polarity.
    """
    from scipy.ndimage import gaussian_filter

    arr_f = arr.astype(np.float64)
    background = gaussian_filter(arr_f, sigma=params.background_sigma)
    if dark_on_light:
        signal = background - arr_f
    else:
        signal = arr_f - background

    signal = np.clip(signal, 0.0, None)
    baseline = np.percentile(signal, 25.0)
    signal = np.clip(signal - baseline, 0.0, None)
    signal = gaussian_filter(signal, sigma=params.pre_smooth_sigma)

    high = np.percentile(signal, 99.5)
    if high > 0:
        signal = signal / high
    return signal


def _find_band_rich_horizontal_zone(
    signal: np.ndarray,
    params: _AutoDetectParams,
) -> tuple[tuple[int, int] | None, dict]:
    from scipy.signal import find_peaks, peak_widths

    y_proj = signal.sum(axis=1).astype(np.float64)
    y_smooth = _smooth(y_proj, params.y_smooth_sigma)
    max_val = float(y_smooth.max()) if y_smooth.size else 0.0
    if max_val <= 0:
        return None, {"failure_stage": "horizontal_zone", "message": "no signal after background correction"}

    prominence = max(max_val * params.zone_peak_prom_frac, max_val * 0.04)
    peaks, props = find_peaks(
        y_smooth,
        prominence=prominence,
        distance=params.zone_peak_min_distance,
        width=params.zone_peak_min_width,
    )

    if len(peaks) == 0:
        return None, {"failure_stage": "horizontal_zone", "message": "no horizontal signal peaks found"}

    prominences = props.get("prominences", np.zeros(len(peaks), dtype=np.float64))
    keep_threshold = float(prominences.max()) * params.zone_peak_keep_frac
    keep_mask = prominences >= keep_threshold
    selected_peaks = peaks[keep_mask]
    if len(selected_peaks) == 0:
        selected_peaks = np.array([int(peaks[int(np.argmax(prominences))])], dtype=int)

    widths, _, left_ips, right_ips = peak_widths(y_smooth, selected_peaks, rel_height=0.7)
    starts = np.floor(left_ips).astype(int)
    ends = np.ceil(right_ips).astype(int)
    y1 = max(0, int(starts.min()) - params.zone_padding)
    y2 = min(signal.shape[0], int(ends.max()) + params.zone_padding)

    if y2 - y1 < params.zone_min_height:
        center = int(round((y1 + y2) / 2))
        half = params.zone_min_height // 2
        y1 = max(0, center - half)
        y2 = min(signal.shape[0], center + half)

    if y2 - y1 < 8:
        return None, {"failure_stage": "horizontal_zone", "message": "horizontal signal zone collapsed after padding"}

    return (y1, y2), {
        "failure_stage": None,
        "message": "",
        "selected_peaks": selected_peaks.tolist(),
        "peak_widths": widths.tolist(),
        "y_projection_max": max_val,
    }


def _find_lane_peaks_in_band_slice(
    signal: np.ndarray,
    center_row: int,
    band_width: float,
    params: _AutoDetectParams,
) -> tuple[list[dict], np.ndarray]:
    from scipy.signal import find_peaks

    slice_half = max(4, int(round(max(4.0, band_width * 0.55))))
    y1 = max(0, center_row - slice_half)
    y2 = min(signal.shape[0], center_row + slice_half)
    band_slice = signal[y1:y2, :]
    if band_slice.size == 0:
        return [], np.zeros(signal.shape[1], dtype=np.float64)

    x_proj = band_slice.sum(axis=0).astype(np.float64)
    x_smooth = _smooth(x_proj, max(1.2, params.x_smooth_sigma * 0.55))
    max_val = float(x_smooth.max()) if x_smooth.size else 0.0
    if max_val <= 0:
        return [], x_smooth

    prominence = max(max_val * (0.08 - 0.03 * params.sensitivity), max_val * 0.02)
    peaks, props = find_peaks(
        x_smooth,
        prominence=prominence,
        distance=max(8, params.lane_min_distance // 3),
        width=max(2, params.lane_min_width // 3),
    )

    candidates = []
    prominences = props.get("prominences", np.zeros(len(peaks), dtype=np.float64))
    for i, peak in enumerate(peaks):
        candidates.append({
            "peak": int(peak),
            "score": float(prominences[i]),
        })
    return candidates, x_smooth


def _cluster_lane_peak_candidates(
    candidates: list[dict],
    merge_distance: int,
    params: _AutoDetectParams,
) -> list[int]:
    if not candidates:
        return []

    candidates = sorted(candidates, key=lambda item: item["peak"])
    clusters: list[dict] = []
    for candidate in candidates:
        if not clusters or candidate["peak"] - clusters[-1]["peaks"][-1] > merge_distance:
            clusters.append({
                "peaks": [candidate["peak"]],
                "scores": [candidate["score"]],
                "source_rows": [int(candidate.get("source_row", 0))],
                "normalized_scores": [float(candidate.get("normalized_score", candidate["score"]))],
            })
        else:
            clusters[-1]["peaks"].append(candidate["peak"])
            clusters[-1]["scores"].append(candidate["score"])
            clusters[-1]["source_rows"].append(int(candidate.get("source_row", 0)))
            clusters[-1]["normalized_scores"].append(float(candidate.get("normalized_score", candidate["score"])))

    cluster_scores = [sum(cluster["scores"]) for cluster in clusters]
    if not cluster_scores:
        return []

    keep_threshold = max(cluster_scores) * (0.22 - 0.08 * params.sensitivity)
    centers = []
    for cluster, score in zip(clusters, cluster_scores):
        if score < keep_threshold:
            continue
        weights = np.array(cluster["scores"], dtype=np.float64)
        peaks = np.array(cluster["peaks"], dtype=np.float64)
        if weights.sum() <= 0:
            center = int(round(float(np.mean(peaks))))
        else:
            center = int(round(float(np.average(peaks, weights=weights))))
        centers.append(center)

    return sorted(set(centers))


def _find_local_valley(x_profile: np.ndarray, approx: int, radius: int) -> int:
    left = max(0, approx - radius)
    right = min(x_profile.size, approx + radius + 1)
    if right <= left:
        return approx
    return left + int(np.argmin(x_profile[left:right]))


def _build_lane_rois_from_centers(
    x_profile: np.ndarray,
    centers: list[int],
    img_h: int,
    zone: tuple[int, int],
    params: _AutoDetectParams,
) -> list[dict]:
    if not centers:
        return []

    centers = sorted(int(c) for c in centers)
    if len(centers) == 1:
        half = max(params.lane_outer_width, params.lane_min_distance)
        left = max(0, centers[0] - half)
        right = min(x_profile.size, centers[0] + half)
        return [{
            "peak": centers[0],
            "score": float(x_profile[centers[0]]),
            "x_range": (left, right),
            "lane_rect": QRectF(left, zone[0], right - left, zone[1] - zone[0]),
        }]

    spacings = [centers[i + 1] - centers[i] for i in range(len(centers) - 1)]
    median_spacing = max(params.lane_min_width, int(round(float(np.median(spacings)))))
    edge_half = max(params.lane_outer_width // 2, median_spacing // 2)

    boundaries = []
    for left_center, right_center in zip(centers[:-1], centers[1:]):
        midpoint = (left_center + right_center) // 2
        radius = max(4, (right_center - left_center) // 4)
        boundaries.append(_find_local_valley(x_profile, midpoint, radius))

    lane_bounds = []
    for i, center in enumerate(centers):
        if i == 0:
            left = max(0, center - edge_half)
        else:
            left = boundaries[i - 1]
        if i == len(centers) - 1:
            right = min(x_profile.size, center + edge_half)
        else:
            right = boundaries[i]

        if right - left < params.lane_min_width:
            deficit = params.lane_min_width - (right - left)
            left = max(0, left - deficit // 2)
            right = min(x_profile.size, right + deficit - deficit // 2)
        lane_bounds.append((left, right, center))

    widths = [right - left for left, right, _ in lane_bounds]
    max_allowed = max(params.lane_max_width, int(round(median_spacing * (2.2 + 0.5 * params.sensitivity))))

    lane_rois = []
    for left, right, center in lane_bounds:
        width = right - left
        if width < params.lane_min_width or width > max_allowed:
            continue
        if center < params.lane_edge_margin or center > x_profile.size - params.lane_edge_margin:
            continue
        lane_rois.append({
            "peak": center,
            "score": float(x_profile[center]),
            "x_range": (left, right),
            "lane_rect": QRectF(left, zone[0], width, zone[1] - zone[0]),
        })

    lane_rois.sort(key=lambda item: item["x_range"][0])
    return lane_rois


def _soft_constrain_lane_centers(
    centers: list[int],
    expected_lane_count: int | None,
    x_profile: np.ndarray,
    params: _AutoDetectParams,
) -> list[int]:
    """
    Guided-mode helper: when the user supplies an expected lane count, softly
    merge only the weakest-separated neighboring centers until the count moves
    toward the requested value. This keeps the detector signal-driven and does
    not impose evenly spaced synthetic lanes.
    """
    if expected_lane_count is None or expected_lane_count <= 0:
        return sorted(set(int(center) for center in centers))

    adjusted = sorted(set(int(center) for center in centers))
    if len(adjusted) <= expected_lane_count:
        return adjusted

    while len(adjusted) > expected_lane_count and len(adjusted) >= 2:
        spacings = np.diff(np.array(adjusted, dtype=np.float64))
        if spacings.size == 0:
            break
        typical_spacing = max(1.0, float(np.median(spacings)))

        best_index = None
        best_score = None
        best_center = None
        for idx, gap in enumerate(spacings.tolist()):
            left_center = adjusted[idx]
            right_center = adjusted[idx + 1]
            midpoint = int(round((left_center + right_center) / 2.0))
            valley_index = _find_local_valley(x_profile, midpoint, max(4, int(gap // 3)))
            valley_value = float(x_profile[valley_index])
            left_value = float(x_profile[left_center])
            right_value = float(x_profile[right_center])
            denom = max(1.0, min(left_value, right_value))
            valley_ratio = valley_value / denom
            closeness = float(gap) / typical_spacing

            # Prefer merging close neighbors whose separating valley is weak.
            score = valley_ratio * 2.5 + max(0.0, 1.5 - closeness)
            if best_score is None or score > best_score:
                merged_center = int(round((left_center * max(1.0, left_value) + right_center * max(1.0, right_value)) / (max(1.0, left_value) + max(1.0, right_value))))
                best_score = score
                best_index = idx
                best_center = merged_center

        if best_index is None or best_center is None:
            break

        left_center = adjusted[best_index]
        right_center = adjusted[best_index + 1]
        log.info(
            "Auto detect: softly merging lane centers %d and %d -> %d using expected lane count %d",
            left_center,
            right_center,
            best_center,
            expected_lane_count,
        )
        adjusted = adjusted[:best_index] + [best_center] + adjusted[best_index + 2:]

    return adjusted


def _lane_boundary_ratio(x_profile: np.ndarray, left_lane: dict, right_lane: dict) -> float:
    left_peak = int(round((left_lane["x_range"][0] + left_lane["x_range"][1]) / 2))
    right_peak = int(round((right_lane["x_range"][0] + right_lane["x_range"][1]) / 2))
    boundary = right_lane["x_range"][0]
    valley = float(x_profile[boundary])
    left_val = float(x_profile[left_peak])
    right_val = float(x_profile[right_peak])
    denom = max(1.0, min(left_val, right_val))
    return valley / denom


def _merge_adjacent_lanes(left_lane: dict, right_lane: dict, zone: tuple[int, int]) -> dict:
    left = left_lane["x_range"][0]
    right = right_lane["x_range"][1]
    center = int(round((left_lane["peak"] + right_lane["peak"]) / 2))
    return {
        "peak": center,
        "score": max(float(left_lane.get("score", 0.0)), float(right_lane.get("score", 0.0))),
        "x_range": (left, right),
        "lane_rect": QRectF(left, zone[0], right - left, zone[1] - zone[0]),
    }


def _merge_oversplit_lanes(
    lane_rois: list[dict],
    x_profile: np.ndarray,
    zone: tuple[int, int],
    params: _AutoDetectParams,
    *,
    guided_mode: bool = False,
) -> list[dict]:
    if len(lane_rois) < 2:
        return lane_rois

    merged = list(lane_rois)
    changed = True
    while changed and len(merged) >= 2:
        changed = False
        widths = np.array([lane["x_range"][1] - lane["x_range"][0] for lane in merged], dtype=np.float64)
        if widths.size == 0:
            break
        ordered = np.sort(widths)
        target_width = float(np.median(ordered[len(ordered) // 2:]))
        if target_width <= 0:
            break

        new_lanes: list[dict] = []
        i = 0
        while i < len(merged):
            if i == len(merged) - 1:
                new_lanes.append(merged[i])
                break

            left_lane = merged[i]
            right_lane = merged[i + 1]
            left_w = left_lane["x_range"][1] - left_lane["x_range"][0]
            right_w = right_lane["x_range"][1] - right_lane["x_range"][0]
            combined_w = right_lane["x_range"][1] - left_lane["x_range"][0]
            center_gap = float(abs(right_lane["peak"] - left_lane["peak"]))
            valley_ratio = _lane_boundary_ratio(x_profile, left_lane, right_lane)

            if guided_mode:
                # Guided ROI mode should aggressively suppress split-peak
                # artifacts where one real lane becomes two narrow neighbors.
                both_narrow = left_w < target_width * 0.88 and right_w < target_width * 0.88
                one_tiny = left_w < target_width * 0.55 or right_w < target_width * 0.55
                close_centers = center_gap <= max(6.0, target_width * 0.72)
                same_band_body = combined_w <= target_width * 1.62
                weak_valley = valley_ratio >= 0.46
                merge_allowed = (
                    weak_valley
                    and same_band_body
                    and (
                        (both_narrow and combined_w <= target_width * 1.42)
                        or (one_tiny and combined_w <= target_width * 1.28)
                        or close_centers
                    )
                )
            else:
                both_narrow = left_w < target_width * 0.78 and right_w < target_width * 0.78
                one_tiny = left_w < target_width * 0.45 or right_w < target_width * 0.45
                merge_allowed = (
                    valley_ratio >= 0.62
                    and (
                        (both_narrow and combined_w <= target_width * 1.22)
                        or (one_tiny and combined_w <= target_width * 1.18)
                    )
                )

            if merge_allowed:
                combined_lane = _merge_adjacent_lanes(left_lane, right_lane, zone)
                log.info(
                    "Auto detect: merging oversplit lanes x=%d:%d and x=%d:%d -> x=%d:%d "
                    "(guided=%s target_width=%.1f valley_ratio=%.2f center_gap=%.1f)",
                    left_lane["x_range"][0], left_lane["x_range"][1],
                    right_lane["x_range"][0], right_lane["x_range"][1],
                    combined_lane["x_range"][0], combined_lane["x_range"][1],
                    guided_mode, target_width, valley_ratio, center_gap,
                )
                new_lanes.append(combined_lane)
                i += 2
                changed = True
            else:
                new_lanes.append(left_lane)
                i += 1

        merged = new_lanes

    return merged


def _lane_center(lane: dict) -> float:
    left, right = lane["x_range"]
    return (left + right) / 2.0


def _make_lane_from_center(
    center: float,
    width: float,
    zone: tuple[int, int],
    x_size: int,
) -> dict:
    half = max(4, int(round(width / 2.0)))
    center_i = int(round(center))
    left = max(0, center_i - half)
    right = min(x_size, center_i + half)
    if right - left < 4:
        right = min(x_size, left + 4)
    return {
        "peak": center_i,
        "score": 0.0,
        "x_range": (left, right),
        "lane_rect": QRectF(left, zone[0], right - left, zone[1] - zone[0]),
    }


def _fill_missing_lanes(
    lane_rois: list[dict],
    x_profile: np.ndarray,
    zone: tuple[int, int],
    params: _AutoDetectParams,
) -> list[dict]:
    if len(lane_rois) < 2:
        return lane_rois

    x_size = x_profile.size
    widths = np.array([lane["x_range"][1] - lane["x_range"][0] for lane in lane_rois], dtype=np.float64)
    centers = np.array([_lane_center(lane) for lane in lane_rois], dtype=np.float64)
    spacings = np.diff(centers)
    if spacings.size == 0:
        return lane_rois

    typical_width = float(np.median(widths))
    typical_spacing = float(np.median(spacings))
    if typical_width <= 0 or typical_spacing <= 0:
        return lane_rois

    filled = list(lane_rois)

    # Fill obvious internal gaps first.
    gap_threshold = typical_spacing * params.lane_gap_fill_frac
    internal_centers: list[float] = []
    for left_center, right_center in zip(centers[:-1], centers[1:]):
        gap = right_center - left_center
        if gap <= gap_threshold:
            continue
        missing = max(1, int(round(gap / typical_spacing)) - 1)
        for idx in range(1, missing + 1):
            internal_centers.append(left_center + typical_spacing * idx)

    for center in internal_centers:
        new_lane = _make_lane_from_center(center, typical_width, zone, x_size)
        log.info(
            "Auto detect: inserting missing internal lane x=%d:%d",
            new_lane["x_range"][0], new_lane["x_range"][1],
        )
        filled.append(new_lane)

    filled.sort(key=lambda item: item["x_range"][0])
    centers = np.array([_lane_center(lane) for lane in filled], dtype=np.float64)

    # If one edge margin is substantially larger, infer one blank edge lane there.
    left_margin = float(filled[0]["x_range"][0])
    right_margin = float(x_size - filled[-1]["x_range"][1])
    left_trigger = left_margin > typical_width * params.edge_lane_fill_margin_frac
    right_trigger = right_margin > typical_width * params.edge_lane_fill_margin_frac

    candidate_scores: list[tuple[float, str, dict]] = []
    if left_trigger:
        left_center = centers[0] - typical_spacing
        lane = _make_lane_from_center(left_center, typical_width, zone, x_size)
        new_left_margin = float(lane["x_range"][0])
        asym_before = abs(left_margin - right_margin)
        asym_after = abs(new_left_margin - right_margin)
        candidate_scores.append((asym_before - asym_after, "left", lane))
    if right_trigger:
        right_center = centers[-1] + typical_spacing
        lane = _make_lane_from_center(right_center, typical_width, zone, x_size)
        new_right_margin = float(x_size - lane["x_range"][1])
        asym_before = abs(left_margin - right_margin)
        asym_after = abs(left_margin - new_right_margin)
        candidate_scores.append((asym_before - asym_after, "right", lane))

    if candidate_scores:
        score, side, lane = max(candidate_scores, key=lambda item: item[0])
        if score > typical_width * 0.25:
            log.info(
                "Auto detect: inserting missing %s edge lane x=%d:%d (balance improvement %.1f)",
                side, lane["x_range"][0], lane["x_range"][1], score,
            )
            filled.append(lane)
            filled.sort(key=lambda item: item["x_range"][0])

    return filled


def _apply_lane_count_constraint(
    lane_rois: list[dict],
    expected_lane_count: int | None,
    x_profile: np.ndarray,
    zone: tuple[int, int],
    params: _AutoDetectParams,
) -> list[dict]:
    if expected_lane_count is None or expected_lane_count <= 0:
        return lane_rois
    if not lane_rois:
        return lane_rois

    constrained = list(lane_rois)
    previous_len = -1
    while len(constrained) < expected_lane_count and len(constrained) != previous_len:
        previous_len = len(constrained)
        constrained = _fill_missing_lanes(constrained, x_profile, zone, params)

    if len(constrained) <= expected_lane_count:
        return constrained

    constrained.sort(key=lambda item: item["x_range"][0])
    best_start = 0
    best_score = None
    for start in range(0, len(constrained) - expected_lane_count + 1):
        window = constrained[start:start + expected_lane_count]
        score = sum(float(lane.get("score", 0.0)) for lane in window)
        if best_score is None or score > best_score:
            best_score = score
            best_start = start

    selected = constrained[best_start:best_start + expected_lane_count]
    log.info(
        "Auto detect: constrained lanes from %d to %d using expected lane count",
        len(lane_rois), len(selected),
    )
    return selected


def _kmeans_1d(values: np.ndarray, cluster_count: int, max_iter: int = 24) -> tuple[np.ndarray, np.ndarray]:
    if cluster_count <= 1 or values.size <= 1:
        return np.zeros(values.size, dtype=int), np.array([float(np.mean(values)) if values.size else 0.0], dtype=np.float64)

    sorted_values = np.sort(values.astype(np.float64))
    quantiles = np.linspace(0.0, 1.0, cluster_count)
    centroids = np.quantile(sorted_values, quantiles).astype(np.float64)

    labels = np.zeros(values.size, dtype=int)
    for _ in range(max_iter):
        distances = np.abs(values[:, None] - centroids[None, :])
        new_labels = np.argmin(distances, axis=1)
        new_centroids = centroids.copy()
        for idx in range(cluster_count):
            members = values[new_labels == idx]
            if members.size:
                new_centroids[idx] = float(np.mean(members))
            else:
                new_centroids[idx] = float(sorted_values[min(idx, sorted_values.size - 1)])
        if np.array_equal(new_labels, labels) and np.allclose(new_centroids, centroids):
            labels = new_labels
            centroids = new_centroids
            break
        labels = new_labels
        centroids = new_centroids

    return labels, centroids


def _cluster_band_rows(
    centers: np.ndarray,
    heights: np.ndarray,
    expected_rows: int | None,
) -> tuple[np.ndarray, list[float]]:
    if centers.size == 0:
        return np.array([], dtype=int), []

    if expected_rows is not None and expected_rows > 0:
        cluster_count = min(int(expected_rows), int(centers.size))
        labels, centroids = _kmeans_1d(centers, cluster_count)
    else:
        threshold = max(6.0, float(np.median(heights)) * 1.35 if heights.size else 10.0)
        order = np.argsort(centers)
        labels = np.zeros(centers.size, dtype=int)
        current_cluster = 0
        previous_center = float(centers[order[0]])
        for idx in order[1:]:
            center = float(centers[idx])
            if center - previous_center > threshold:
                current_cluster += 1
            labels[idx] = current_cluster
            previous_center = center
        centroids = np.array(
            [float(np.mean(centers[labels == idx])) for idx in range(current_cluster + 1)],
            dtype=np.float64,
        )

    centroid_order = np.argsort(centroids)
    remap = {int(old_idx): new_idx + 1 for new_idx, old_idx in enumerate(centroid_order.tolist())}
    row_indices = np.array([remap[int(label)] for label in labels], dtype=int)
    row_centers = [float(centroids[idx]) for idx in centroid_order.tolist()]
    return row_indices, row_centers


def _merge_compact_row_clusters(grouped: list[dict], expected_rows_per_lane: int | None = None) -> None:
    """
    Merge adjacent row clusters when their median boxes nearly touch across the
    same lanes. This preserves true faint rows while collapsing obvious split
    detections from a single broad band into one global row.
    """
    row_stats: dict[int, dict] = {}
    for lane in grouped:
        lane_index = int(lane["lane_index"])
        for band in lane.get("bands", []):
            row_index = int(band["row_index"])
            rect = band["band_rect"]
            stats = row_stats.setdefault(row_index, {"tops": [], "heights": [], "lanes": set()})
            stats["tops"].append(float(rect.y()))
            stats["heights"].append(max(1.0, float(rect.height())))
            stats["lanes"].add(lane_index)

    sorted_rows = sorted(row_stats)
    if len(sorted_rows) < 2:
        return
    merged_groups: list[list[int]] = []
    current_group = [sorted_rows[0]]
    current_stats = row_stats[sorted_rows[0]]
    current_bottom = float(np.median(current_stats["tops"]) + np.median(current_stats["heights"]))

    for row_index in sorted_rows[1:]:
        next_stats = row_stats[row_index]
        next_top = float(np.median(next_stats["tops"]))
        next_bottom = float(np.median(next_stats["tops"]) + np.median(next_stats["heights"]))
        gap = next_top - current_bottom
        median_height = float(np.median(current_stats["heights"] + next_stats["heights"]))
        gap_ratio = gap / max(1.0, median_height)
        lane_overlap = len(current_stats["lanes"] & next_stats["lanes"]) / max(
            1, min(len(current_stats["lanes"]), len(next_stats["lanes"]))
        )

        if gap_ratio <= 0.28 and lane_overlap >= 0.5:
            current_group.append(row_index)
            current_stats = {
                "tops": current_stats["tops"] + next_stats["tops"],
                "heights": current_stats["heights"] + next_stats["heights"],
                "lanes": current_stats["lanes"] | next_stats["lanes"],
            }
            current_bottom = max(current_bottom, next_bottom)
            continue

        merged_groups.append(current_group)
        current_group = [row_index]
        current_stats = next_stats
        current_bottom = next_bottom

    merged_groups.append(current_group)

    if all(len(group) == 1 for group in merged_groups):
        return

    remap: dict[int, int] = {}
    for new_index, group in enumerate(merged_groups, start=1):
        for old_index in group:
            remap[old_index] = new_index

    for lane in grouped:
        for band in lane.get("bands", []):
            band["row_index"] = remap[int(band["row_index"])]


def _detect_global_band_rows(
    signal: np.ndarray,
    params: _AutoDetectParams,
    expected_rows: int | None,
) -> list[dict]:
    from scipy.signal import find_peaks, peak_widths

    y_proj = signal.sum(axis=1).astype(np.float64)
    y_smooth = _smooth(y_proj, params.y_smooth_sigma)
    max_val = float(y_smooth.max()) if y_smooth.size else 0.0
    if max_val <= 0:
        return []

    prominence = max(max_val * max(0.06, 0.11 - 0.04 * params.sensitivity), max_val * 0.025)
    min_distance = max(8, int(signal.shape[0] * 0.08))
    min_width = max(3, int(signal.shape[0] * 0.01))
    peaks, props = find_peaks(
        y_smooth,
        prominence=prominence,
        distance=min_distance,
        width=min_width,
    )
    if len(peaks) == 0:
        return []

    prominences = props.get("prominences", np.zeros(len(peaks), dtype=np.float64))
    if expected_rows is not None and expected_rows > 0 and len(peaks) > expected_rows:
        keep_indices = np.argsort(prominences)[-expected_rows:]
        keep_indices = np.array(sorted(keep_indices.tolist()), dtype=int)
        peaks = peaks[keep_indices]
        prominences = prominences[keep_indices]

    widths, _, left_ips, right_ips = peak_widths(y_smooth, peaks, rel_height=0.7)
    rows = []
    for peak, width, left_ip, right_ip, prominence_value in zip(peaks, widths, left_ips, right_ips, prominences):
        rows.append({
            "center": float(peak),
            "width": max(8.0, float(width)),
            "top": max(0.0, float(left_ip)),
            "bottom": min(float(signal.shape[0] - 1), float(right_ip)),
            "prominence": float(prominence_value),
        })
    rows.sort(key=lambda item: item["center"])
    return rows


def _guided_peak_windows(guided_rows: list[dict], signal_size: int) -> list[tuple[int, int]]:
    if not guided_rows:
        return []

    windows: list[tuple[int, int]] = []
    for idx, row in enumerate(guided_rows):
        center = float(row["center"])
        half_width = max(8.0, float(row.get("width", 12.0)) * 0.8)
        if idx == 0:
            top = center - half_width
        else:
            top = min(center - half_width, (guided_rows[idx - 1]["center"] + center) / 2.0)
        if idx == len(guided_rows) - 1:
            bottom = center + half_width
        else:
            bottom = max(center + half_width, (center + guided_rows[idx + 1]["center"]) / 2.0)
        windows.append((max(0, int(np.floor(top))), min(signal_size, int(np.ceil(bottom)))))
    return windows


def _build_band_rois_from_peak_list(
    y_profile: np.ndarray,
    peaks: list[int],
    lane_x_range: tuple[int, int],
    zone_y1: int,
    params: _AutoDetectParams,
    clip_windows: list[tuple[int, int]] | None = None,
) -> list[dict]:
    left, right = lane_x_range
    band_rois = []
    for band_index, peak in enumerate(sorted(int(p) for p in peaks), start=1):
        signal_start, signal_end = _find_band_boundaries(y_profile, peak)
        if clip_windows is not None and band_index - 1 < len(clip_windows):
            clip_top, clip_bottom = clip_windows[band_index - 1]
            signal_start = max(signal_start, clip_top)
            signal_end = min(signal_end, max(clip_top + 1, clip_bottom - 1))
        start = max(0, signal_start - params.band_padding)
        end = min(y_profile.size - 1, signal_end + params.band_padding)
        height = max(4, end - start)
        band_rois.append({
            "band_index": band_index,
            "peak_y": float(zone_y1 + peak),
            "band_rect": QRectF(left, zone_y1 + start, right - left, height),
            "signal_rect": QRectF(
                left,
                zone_y1 + signal_start,
                right - left,
                max(1, signal_end - signal_start),
            ),
        })
    return band_rois


def group_auto_detected_rows(
    detections: list[dict],
    expected_rows_per_lane: int | None = None,
    target_band_row: int | None = None,
    *,
    merge_compact_rows: bool = True,
    collapse_lane_duplicates: bool = True,
) -> list[dict]:
    # Row grouping only assigns shared row indices and optional target-row
    # filtering. It intentionally preserves each lane's measured band_rect
    # geometry instead of harmonizing rows into uniform cross-lane templates.
    grouped: list[dict] = []
    band_records: list[tuple[dict, dict]] = []

    for lane in detections:
        lane_copy = {
            "lane_index": lane["lane_index"],
            "lane_rect": QRectF(lane["lane_rect"]),
            "bands": [],
        }
        grouped.append(lane_copy)
        for band in lane.get("bands", []):
            band_copy = dict(band)
            band_copy["band_rect"] = QRectF(band["band_rect"])
            lane_copy["bands"].append(band_copy)
            band_records.append((lane_copy, band_copy))

    if not band_records:
        return grouped

    centers = np.array(
        [band["band_rect"].y() + (band["band_rect"].height() / 2.0) for _, band in band_records],
        dtype=np.float64,
    )
    heights = np.array(
        [max(1.0, float(band["band_rect"].height())) for _, band in band_records],
        dtype=np.float64,
    )
    row_indices, row_centers = _cluster_band_rows(centers, heights, expected_rows_per_lane)

    for row_index, row_center, (_, band) in zip(row_indices.tolist(), (row_centers[idx - 1] for idx in row_indices.tolist()), band_records):
        band["row_index"] = int(row_index)
        band["row_center"] = float(row_center)

    if merge_compact_rows:
        _merge_compact_row_clusters(grouped, expected_rows_per_lane)

    lane_row_counts: dict[tuple[int, int], int] = {}
    for lane in grouped:
        filtered_bands = []
        lane["bands"].sort(key=lambda item: (item["row_index"], item["band_rect"].y(), item["band_rect"].x()))
        for band in lane["bands"]:
            row_index = int(band["row_index"])
            if target_band_row is not None and row_index != target_band_row:
                continue

            if (
                collapse_lane_duplicates
                and filtered_bands
                and int(filtered_bands[-1]["row_index"]) == row_index
            ):
                previous = filtered_bands[-1]
                prev_rect = previous["band_rect"]
                curr_rect = band["band_rect"]
                prev_bottom = prev_rect.y() + prev_rect.height()
                curr_bottom = curr_rect.y() + curr_rect.height()
                gap = curr_rect.y() - prev_bottom
                median_height = float(np.median([prev_rect.height(), curr_rect.height()]))
                if gap <= max(6.0, median_height * 0.35):
                    merged_top = min(prev_rect.y(), curr_rect.y())
                    merged_bottom = max(prev_bottom, curr_bottom)
                    previous["band_rect"] = QRectF(
                        prev_rect.x(),
                        merged_top,
                        prev_rect.width(),
                        max(4.0, merged_bottom - merged_top),
                    )
                    continue

            key = (lane["lane_index"], row_index)
            lane_row_counts[key] = lane_row_counts.get(key, 0) + 1
            band["row_member_index"] = lane_row_counts[key]
            band["display_name"] = f"Row {row_index}"
            filtered_bands.append(band)
        lane["bands"] = filtered_bands

    return grouped


def _detect_lanes_within_zone(
    signal: np.ndarray,
    zone: tuple[int, int],
    img_h: int,
    zone_info: dict,
    params: _AutoDetectParams,
    expected_lane_count: int | None = None,
    guided_rows: list[dict] | None = None,
    guided_mode: bool = False,
) -> tuple[list[dict], dict]:
    y1, y2 = zone
    zone_signal = signal[y1:y2, :]
    if zone_signal.size == 0:
        return [], {"failure_stage": "lanes", "message": "horizontal signal zone is empty"}

    combined_profile = np.zeros(zone_signal.shape[1], dtype=np.float64)
    peak_candidates: list[dict] = []
    if guided_rows:
        selected_rows = [int(round(row["center"])) for row in guided_rows]
        peak_widths = [float(row.get("width", 12.0)) for row in guided_rows]
    else:
        selected_rows = zone_info.get("selected_peaks", [])
        peak_widths = zone_info.get("peak_widths", [])
    for row_idx, (center_row, band_width) in enumerate(zip(selected_rows, peak_widths)):
        slice_candidates, slice_profile = _find_lane_peaks_in_band_slice(signal, int(center_row), float(band_width), params)
        slice_scale = float(np.percentile(slice_profile, 90.0)) if slice_profile.size else 0.0
        if slice_scale > 0:
            combined_profile += slice_profile / slice_scale
        else:
            combined_profile += slice_profile

        max_candidate_score = max((float(candidate["score"]) for candidate in slice_candidates), default=0.0)
        for candidate in slice_candidates:
            enriched = dict(candidate)
            enriched["source_row"] = row_idx
            if max_candidate_score > 0:
                enriched["normalized_score"] = float(candidate["score"]) / max_candidate_score
            else:
                enriched["normalized_score"] = 0.0
            peak_candidates.append(enriched)

    if not peak_candidates:
        combined_profile = zone_signal.sum(axis=0).astype(np.float64)
        slice_like_candidates, _ = _find_lane_peaks_in_band_slice(
            signal,
            center_row=(y1 + y2) // 2,
            band_width=max(8.0, float(y2 - y1) / 3.0),
            params=params,
        )
        for candidate in slice_like_candidates:
            enriched = dict(candidate)
            enriched["source_row"] = 0
            enriched["normalized_score"] = 1.0
            peak_candidates.append(enriched)

    if not peak_candidates:
        return [], {"failure_stage": "lanes", "message": "no lane peaks found inside horizontal signal zone"}

    x_profile = _smooth(combined_profile, max(1.2, params.x_smooth_sigma * 0.7))
    merge_distance = max(8, params.lane_min_distance // 3)
    if guided_mode and expected_lane_count is None:
        merge_distance = max(10, int(round(params.lane_min_distance * 0.55)))

    centers = _cluster_lane_peak_candidates(
        peak_candidates,
        merge_distance=merge_distance,
        params=params,
    )
    if expected_lane_count is not None and expected_lane_count > 0:
        centers = _soft_constrain_lane_centers(centers, expected_lane_count, x_profile, params)
    if not centers:
        return [], {"failure_stage": "lanes", "message": "lane peaks found, but no stable centers remained after clustering"}

    lane_rois = _build_lane_rois_from_centers(x_profile, centers, img_h, zone, params)
    if not lane_rois:
        return [], {"failure_stage": "lanes", "message": "lane peaks found, but all candidates were filtered out"}

    lane_rois = _merge_oversplit_lanes(lane_rois, x_profile, zone, params, guided_mode=guided_mode)
    if not lane_rois:
        return [], {"failure_stage": "lanes", "message": "lane candidates were removed during oversplit merge filtering"}

    lane_rois = _fill_missing_lanes(lane_rois, x_profile, zone, params)
    lane_rois = _apply_lane_count_constraint(lane_rois, expected_lane_count, x_profile, zone, params)

    return lane_rois, {
        "failure_stage": None,
        "message": "",
        "lane_candidates": len(centers),
        "kept_lanes": len(lane_rois),
    }


def _build_band_rois_from_peaks(
    y_profile: np.ndarray,
    peaks: np.ndarray,
    lane_x_range: tuple[int, int],
    zone_y1: int,
    params: _AutoDetectParams,
) -> list[dict]:
    left, right = lane_x_range
    band_rois = []
    for band_index, peak in enumerate(sorted(int(p) for p in peaks), start=1):
        signal_start, signal_end = _find_band_boundaries(y_profile, peak)
        start = max(0, signal_start - params.band_padding)
        end = min(y_profile.size - 1, signal_end + params.band_padding)
        height = max(4, end - start)
        band_rois.append({
            "band_index": band_index,
            "peak_y": float(zone_y1 + peak),
            "band_rect": QRectF(left, zone_y1 + start, right - left, height),
            "signal_rect": QRectF(
                left,
                zone_y1 + signal_start,
                right - left,
                max(1, signal_end - signal_start),
            ),
        })
    return band_rois


def _refine_band_horizontal_signal_bounds(
    signal: np.ndarray,
    bands: list[dict],
) -> None:
    """Measure the full band width, including a faint one-sided shoulder."""
    img_h, img_w = signal.shape
    for band in bands:
        rect = band.get("signal_rect")
        if rect is None:
            continue
        initial_left = max(0, int(np.floor(rect.x())))
        initial_right = min(img_w, int(np.ceil(rect.x() + rect.width())))
        search_padding = max(4, int(round(rect.width() * 0.45)))
        search_left = max(0, initial_left - search_padding)
        search_right = min(img_w, initial_right + search_padding)
        top = max(0, int(np.floor(rect.y())))
        bottom = min(img_h, int(np.ceil(rect.y() + rect.height())))
        patch = signal[top:bottom, search_left:search_right]
        if patch.size == 0 or patch.shape[1] < 2:
            continue
        profile = patch.mean(axis=0).astype(np.float64)
        profile = _smooth(profile, 1.0)
        baseline = float(np.percentile(profile, 15.0))
        peak_value = float(profile.max())
        dynamic_range = peak_value - baseline
        if dynamic_range <= 0:
            continue

        # The high threshold identifies a trustworthy band core. The lower
        # hysteresis threshold then follows a gradual shoulder/tail until two
        # consecutive background samples are reached.
        high_threshold = baseline + dynamic_range * 0.12
        low_values = profile[profile <= np.percentile(profile, 25.0)]
        noise_mad = (
            float(np.median(np.abs(low_values - np.median(low_values))))
            if low_values.size
            else 0.0
        )
        low_threshold = baseline + max(dynamic_range * 0.04, noise_mad * 2.5)
        core_left = max(0, initial_left - search_left)
        core_right = min(profile.size, initial_right - search_left)
        if core_right <= core_left:
            continue
        peak_index = core_left + int(np.argmax(profile[core_left:core_right]))
        if profile[peak_index] < high_threshold:
            continue

        def trace_boundary(direction: int) -> int:
            index = peak_index
            last_signal = peak_index
            below_count = 0
            while 0 <= index + direction < profile.size:
                index += direction
                if profile[index] >= low_threshold:
                    last_signal = index
                    below_count = 0
                else:
                    below_count += 1
                    if below_count >= 2:
                        break
            return last_signal

        left_index = trace_boundary(-1)
        right_index = trace_boundary(1)
        signal_left = search_left + left_index
        signal_width = max(1, right_index - left_index + 1)
        band["signal_rect"] = QRectF(
            signal_left,
            rect.y(),
            signal_width,
            rect.height(),
        )
        display_rect = band.get("band_rect")
        if display_rect is not None:
            band["band_rect"] = QRectF(
                signal_left,
                display_rect.y(),
                signal_width,
                display_rect.height(),
            )


def _detect_bands_in_lane(
    signal: np.ndarray,
    lane_x_range: tuple[int, int],
    zone: tuple[int, int],
    params: _AutoDetectParams,
    guided_rows: list[dict] | None = None,
) -> tuple[list[dict], dict]:
    from scipy.signal import find_peaks

    left, right = lane_x_range
    zone_y1, zone_y2 = zone
    use_guided_rows = bool(guided_rows)
    signal_y1 = 0 if use_guided_rows else zone_y1
    signal_y2 = signal.shape[0] if use_guided_rows else zone_y2
    lane_signal = signal[signal_y1:signal_y2, left:right]
    if lane_signal.size == 0:
        return [], {"failure_stage": "bands", "message": "lane signal is empty"}

    y_proj = lane_signal.sum(axis=1).astype(np.float64)
    lane_h = max(1, lane_signal.shape[0])
    sigma = max(params.band_smooth_sigma, lane_h / 90.0)
    y_smooth = _smooth(y_proj, sigma)
    max_val = float(y_smooth.max()) if y_smooth.size else 0.0
    if max_val <= 0:
        return [], {"failure_stage": "bands", "message": "lane band projection has no positive signal"}

    min_distance = max(4, int(lane_h * params.band_min_distance_frac))
    min_width = max(2, int(lane_h * params.band_min_width_frac))
    prominence = max(max_val * params.band_peak_prom_frac, max_val * 0.05)
    if use_guided_rows:
        prominence = max(max_val * max(0.02, params.band_peak_prom_frac * 0.35), max_val * 0.012)
        min_distance = max(4, int(min_distance * 0.6))
        min_width = max(2, int(min_width * 0.6))
    peaks, _ = find_peaks(
        y_smooth,
        prominence=prominence,
        distance=min_distance,
        width=min_width,
    )

    if use_guided_rows:
        windows = _guided_peak_windows(guided_rows or [], lane_signal.shape[0])
        selected_peaks: list[int] = []
        selected_windows: list[tuple[int, int]] = []
        used_peaks: set[int] = set()
        fallback_threshold = max(max_val * 0.025, max_val * params.band_peak_prom_frac * 0.25)
        for window_top, window_bottom in windows:
            if window_bottom - window_top < 3:
                continue
            candidates = [int(peak) for peak in peaks if window_top <= int(peak) < window_bottom and int(peak) not in used_peaks]
            if candidates:
                peak = max(candidates, key=lambda idx: y_smooth[idx])
                selected_peaks.append(peak)
                selected_windows.append((window_top, window_bottom))
                used_peaks.add(peak)
                continue

            local_segment = y_smooth[window_top:window_bottom]
            if local_segment.size == 0:
                continue
            local_offset = int(np.argmax(local_segment))
            peak = window_top + local_offset
            if y_smooth[peak] < fallback_threshold:
                continue
            selected_peaks.append(peak)
            selected_windows.append((window_top, window_bottom))

        unique_peaks = sorted(set(selected_peaks))
        if not unique_peaks:
            return [], {"failure_stage": "bands", "message": "no guided band peaks found in lane"}
        ordered = sorted(zip(selected_peaks, selected_windows), key=lambda item: item[0])
        bands = _build_band_rois_from_peak_list(
            y_smooth,
            [peak for peak, _ in ordered],
            lane_x_range,
            signal_y1,
            params,
            clip_windows=[window for _, window in ordered],
        )
    else:
        if len(peaks) == 0:
            return [], {"failure_stage": "bands", "message": "no band peaks found in lane"}
        bands = _build_band_rois_from_peaks(y_smooth, peaks, lane_x_range, zone_y1, params)
    _refine_band_horizontal_signal_bounds(signal, bands)
    return bands, {
        "failure_stage": None,
        "message": "",
        "band_peaks": len(bands),
    }


def _copy_and_offset_detections(
    lane_candidates_with_bands: list[tuple[dict, list[dict]]],
    search_x1: int,
    search_y1: int,
    search_h: int,
) -> list[dict]:
    detections = []
    for lane_index, (lane_candidate, bands) in enumerate(lane_candidates_with_bands, start=1):
        left, right = lane_candidate["x_range"]
        copied_bands = []
        for band in bands:
            rect = band["band_rect"]
            band_copy = dict(band)
            band_copy["band_rect"] = QRectF(
                search_x1 + rect.x(),
                search_y1 + rect.y(),
                rect.width(),
                rect.height(),
            )
            if band.get("peak_y") is not None:
                band_copy["peak_y"] = float(search_y1 + float(band["peak_y"]))
            signal_rect = band.get("signal_rect")
            if signal_rect is not None:
                band_copy["signal_rect"] = QRectF(
                    search_x1 + signal_rect.x(),
                    search_y1 + signal_rect.y(),
                    signal_rect.width(),
                    signal_rect.height(),
                )
            copied_bands.append(band_copy)
        detections.append({
            "lane_index": lane_index,
            "lane_rect": QRectF(search_x1 + left, search_y1, right - left, search_h),
            "bands": copied_bands,
        })
    return detections


def _finalize_auto_detections(
    detections: list[dict],
    metadata: dict,
    *,
    expected_rows_per_lane: int | None = None,
    target_band_row: int | None = None,
    merge_compact_rows: bool = True,
    collapse_lane_duplicates: bool = True,
) -> tuple[list[dict], dict]:
    detections = group_auto_detected_rows(
        detections,
        expected_rows_per_lane=expected_rows_per_lane,
        target_band_row=target_band_row,
        merge_compact_rows=merge_compact_rows,
        collapse_lane_duplicates=collapse_lane_duplicates,
    )
    total_bands = sum(len(lane["bands"]) for lane in detections)
    metadata["retained_lanes"] = len(detections)
    metadata["retained_bands"] = total_bands

    if not detections or total_bands == 0:
        metadata["failure_stage"] = "bands"
        metadata["message"] = "lanes were found, but no band peaks were retained"
        log.warning("Auto detect failed: bands not found after lane detection")
        return [], metadata

    log.info(
        "Auto detect: retained %d lane(s) and %d band(s) total",
        metadata["retained_lanes"], metadata["retained_bands"],
    )
    return detections, metadata


def _prepare_auto_detect_region(
    image_path: str,
    dark_on_light: bool,
    sensitivity: float,
    *,
    mode: str,
    search_rect: QRectF | None = None,
) -> tuple[np.ndarray | None, np.ndarray | None, _AutoDetectParams | None, dict]:
    arr = _load_8bit(image_path)
    img_h, img_w = arr.shape
    dark_on_light = bool(dark_on_light)
    polarity_label = "Dark on Light" if dark_on_light else "Light on Dark"
    metadata = {
        "failure_stage": None,
        "message": "",
        "image_size": (img_w, img_h),
        "polarity": polarity_label,
        "sensitivity": _clamp01(sensitivity),
        "horizontal_zone": None,
        "search_region": None,
        "lane_candidates": 0,
        "retained_lanes": 0,
        "retained_bands": 0,
        "mode": mode,
    }

    search_x1 = 0
    search_y1 = 0
    search_h = img_h
    if search_rect is not None:
        search_x1 = max(0, int(search_rect.x()))
        search_y1 = max(0, int(search_rect.y()))
        search_x2 = min(img_w, int(search_rect.x() + search_rect.width()))
        search_y2 = min(img_h, int(search_rect.y() + search_rect.height()))
        if search_x2 - search_x1 < 8 or search_y2 - search_y1 < 8:
            metadata["failure_stage"] = "search_region"
            metadata["message"] = "search ROI is too small for auto detection"
            return None, None, None, metadata
        arr = arr[search_y1:search_y2, search_x1:search_x2]
        search_h = search_y2 - search_y1
        metadata["search_region"] = (search_x1, search_y1, search_x2 - search_x1, search_h)

    params = _build_auto_params(arr.shape[0], arr.shape[1], sensitivity)
    metadata["sensitivity"] = params.sensitivity
    log.info(
        "Auto detect: image=%dx%d polarity=%s sensitivity=%.2f",
        img_w, img_h, polarity_label, params.sensitivity,
    )
    signal = _prepare_signal_for_auto_detection(arr, dark_on_light, params)
    metadata["search_offsets"] = (search_x1, search_y1, search_h)
    return arr, signal, params, metadata


def auto_detect_all(
    image_path: str,
    dark_on_light: bool = False,
    sensitivity: float = 0.5,
    return_metadata: bool = False,
) -> list[dict] | tuple[list[dict], dict]:
    """
    Baseline-safe auto mode. This path intentionally keeps the original lane
    and band search simple:
    - full image only
    - fixed Light on Dark polarity
    - no user-driven lane/row constraints
    - no polarity fallback
    - keeps the stable row-guided baseline, but isolates more aggressive
      constraint handling to guided mode
    """
    _, signal, params, metadata = _prepare_auto_detect_region(
        image_path,
        dark_on_light,
        sensitivity,
        mode="default",
        search_rect=None,
    )
    if signal is None or params is None:
        return ([], metadata) if return_metadata else []

    _, img_h = metadata["image_size"]
    guided_rows = _detect_global_band_rows(signal, params, None)
    zone, zone_info = _find_band_rich_horizontal_zone(signal, params)
    if zone is None:
        metadata["failure_stage"] = zone_info["failure_stage"]
        metadata["message"] = zone_info["message"]
        log.warning("Auto detect failed: no horizontal signal zone found (%s)", zone_info["message"])
        return ([], metadata) if return_metadata else []

    zone_y1, zone_y2 = zone
    metadata["horizontal_zone"] = zone
    log.info("Auto detect: horizontal band zone found at rows %d:%d", zone_y1, zone_y2)

    lane_candidates, lane_info = _detect_lanes_within_zone(
        signal,
        zone,
        img_h,
        zone_info,
        params,
        expected_lane_count=None,
        guided_rows=guided_rows,
        guided_mode=False,
    )
    metadata["lane_candidates"] = lane_info.get("lane_candidates", 0)
    if not lane_candidates:
        metadata["failure_stage"] = lane_info["failure_stage"]
        metadata["message"] = lane_info["message"]
        log.warning("Auto detect failed: no lanes found (%s)", lane_info["message"])
        return ([], metadata) if return_metadata else []

    log.info("Auto detect: %d lane candidates found", len(lane_candidates))

    lane_candidates_with_bands: list[tuple[dict, list[dict]]] = []
    for lane_index, lane_candidate in enumerate(lane_candidates, start=1):
        left, right = lane_candidate["x_range"]
        bands, _ = _detect_bands_in_lane(
            signal,
            lane_candidate["x_range"],
            zone,
            params,
            guided_rows=guided_rows,
        )
        log.info(
            "Auto detect: lane %d x=%d:%d -> %d band peak(s)",
            lane_index, left, right, len(bands),
        )
        lane_candidates_with_bands.append((lane_candidate, bands))

    detections = _copy_and_offset_detections(lane_candidates_with_bands, 0, 0, img_h)
    detections, metadata = _finalize_auto_detections(
        detections,
        metadata,
        expected_rows_per_lane=len(guided_rows) if len(guided_rows) > 1 else None,
        target_band_row=None,
        merge_compact_rows=True,
        collapse_lane_duplicates=True,
    )
    return (detections, metadata) if return_metadata else detections

def auto_detect_guided(
    image_path: str,
    dark_on_light: bool = False,
    sensitivity: float = 0.5,
    search_rect: QRectF | None = None,
    expected_lane_count: int | None = None,
    target_band_row: int | None = None,
    expected_rows_per_lane: int | None = None,
    return_metadata: bool = False,
) -> list[dict] | tuple[list[dict], dict]:
    """
    Guided auto mode. This path is reserved for explicit constraints such as a
    search ROI, expected lane count, target row, or expected row count, so the
    advanced heuristics stay isolated from the stable default path. Guided mode
    still uses the real per-lane ROIs returned by _detect_bands_in_lane() and
    does not apply any cross-lane template harmonization.
    """
    _, signal, params, metadata = _prepare_auto_detect_region(
        image_path,
        dark_on_light,
        sensitivity,
        mode="guided",
        search_rect=search_rect,
    )
    if signal is None or params is None:
        return ([], metadata) if return_metadata else []

    search_x1, search_y1, search_h = metadata["search_offsets"]
    guided_rows = _detect_global_band_rows(signal, params, expected_rows_per_lane)

    zone, zone_info = _find_band_rich_horizontal_zone(signal, params)
    if zone is None:
        metadata["failure_stage"] = zone_info["failure_stage"]
        metadata["message"] = zone_info["message"]
        log.warning("Auto detect failed: no horizontal signal zone found (%s)", zone_info["message"])
        return ([], metadata) if return_metadata else []

    zone_y1, zone_y2 = zone
    metadata["horizontal_zone"] = zone
    log.info("Auto detect: horizontal band zone found at rows %d:%d", zone_y1, zone_y2)

    lane_candidates, lane_info = _detect_lanes_within_zone(
        signal,
        zone,
        search_h,
        zone_info,
        params,
        expected_lane_count=expected_lane_count,
        guided_rows=guided_rows,
        guided_mode=True,
    )
    metadata["lane_candidates"] = lane_info.get("lane_candidates", 0)
    if not lane_candidates:
        metadata["failure_stage"] = lane_info["failure_stage"]
        metadata["message"] = lane_info["message"]
        log.warning("Auto detect failed: no lanes found (%s)", lane_info["message"])
        return ([], metadata) if return_metadata else []

    log.info("Auto detect: %d lane candidates found", len(lane_candidates))

    lane_candidates_with_bands: list[tuple[dict, list[dict]]] = []
    for lane_index, lane_candidate in enumerate(lane_candidates, start=1):
        left, right = lane_candidate["x_range"]
        bands, _ = _detect_bands_in_lane(
            signal,
            lane_candidate["x_range"],
            zone,
            params,
            guided_rows=guided_rows,
        )
        log.info(
            "Auto detect: lane %d x=%d:%d -> %d band peak(s)",
            lane_index, left, right, len(bands),
        )
        lane_candidates_with_bands.append((lane_candidate, bands))
    detections = _copy_and_offset_detections(lane_candidates_with_bands, search_x1, search_y1, search_h)
    detections, metadata = _finalize_auto_detections(
        detections,
        metadata,
        expected_rows_per_lane=expected_rows_per_lane or (len(guided_rows) if len(guided_rows) > 1 else None),
        target_band_row=target_band_row,
        merge_compact_rows=True,
        collapse_lane_duplicates=True,
    )
    return (detections, metadata) if return_metadata else detections
