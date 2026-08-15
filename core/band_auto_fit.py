"""Pure geometry helpers for fitting detected WB bands into figure frames."""
from __future__ import annotations

from dataclasses import dataclass
from math import exp
from statistics import median
from typing import Any

from core.figure_project import ImageBBox


@dataclass(frozen=True)
class AspectFitPlacement:
    """Uniform-scale placement of source content inside a target frame."""

    scale: float
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class BandAutoFitResult:
    """Final source-image crop derived from editable auto detections."""

    crop_box: ImageBBox
    target_row: int
    lane_count: int
    band_count: int
    confidence: float
    low_confidence: bool
    margin_clipped: bool
    padding_required: bool
    lane_centers_x: tuple[float, ...]
    row_anchor_y: float
    alignment_used: str
    lane_crop_boxes: tuple[ImageBBox, ...]
    composite_width: float
    composite_height: float


def aspect_fit_placement(
    source_width: float,
    source_height: float,
    frame_width: float,
    frame_height: float,
) -> AspectFitPlacement:
    """Return a centered, aspect-preserving ``contain`` placement."""
    source_width = max(1.0, float(source_width))
    source_height = max(1.0, float(source_height))
    frame_width = max(0.0, float(frame_width))
    frame_height = max(0.0, float(frame_height))
    scale = min(frame_width / source_width, frame_height / source_height)
    width = source_width * scale
    height = source_height * scale
    return AspectFitPlacement(
        scale=scale,
        x=(frame_width - width) / 2.0,
        y=(frame_height - height) / 2.0,
        width=width,
        height=height,
    )


def _rect_values(rect: Any) -> tuple[float, float, float, float]:
    """Read a QRectF, ImageBBox, or project-style rectangle dict."""
    if isinstance(rect, ImageBBox):
        return rect.x, rect.y, rect.w, rect.h
    if hasattr(rect, "x") and callable(rect.x):
        return float(rect.x()), float(rect.y()), float(rect.width()), float(rect.height())
    if isinstance(rect, dict):
        return (
            float(rect.get("x", 0.0)),
            float(rect.get("y", 0.0)),
            float(rect.get("w", rect.get("width", 0.0))),
            float(rect.get("h", rect.get("height", 0.0))),
        )
    raise TypeError(f"Unsupported rectangle type: {type(rect)!r}")


def _choose_target_row(detections: list[dict], requested_row: int | None) -> int | None:
    rows: dict[int, dict[str, float | set[int]]] = {}
    for lane_position, lane in enumerate(detections, start=1):
        lane_index = int(lane.get("lane_index", lane_position))
        for band in lane.get("bands", []):
            row = int(band.get("row_index", band.get("band_index", 1)))
            if requested_row is not None and row != requested_row:
                continue
            _x, _y, width, height = _rect_values(band["band_rect"])
            stats = rows.setdefault(row, {"lanes": set(), "area": 0.0})
            lanes = stats["lanes"]
            assert isinstance(lanes, set)
            lanes.add(lane_index)
            stats["area"] = float(stats["area"]) + max(0.0, width * height)
    if requested_row is not None:
        return requested_row if requested_row in rows else None
    if not rows:
        return None
    return max(
        rows,
        key=lambda row: (len(rows[row]["lanes"]), float(rows[row]["area"]), -row),
    )


def calculate_band_auto_fit(
    detections: list[dict],
    *,
    search_roi: Any,
    image_width: int,
    image_height: int,
    horizontal_margin_px: int = 8,
    vertical_margin_px: int = 8,
    alignment: str = "center",
    target_row: int | None = None,
    expected_lane_count: int | None = None,
) -> BandAutoFitResult:
    """Calculate one source crop for a detected cross-lane band row.

    The rough ROI is used only to obtain detections. Final crop geometry is
    derived from source-image signal bounds and explicit margins.
    """
    if image_width <= 0 or image_height <= 0:
        raise ValueError("Source image dimensions must be positive.")
    if not detections:
        raise ValueError("No lane detections are available.")

    chosen_row = _choose_target_row(detections, target_row)
    if chosen_row is None:
        raise ValueError("No detected band row matches the requested target.")

    band_records: list[
        tuple[int, tuple[float, float, float, float], float | None]
    ] = []
    band_signal_by_lane: dict[int, tuple[float, float, float, float]] = {}
    band_peak_by_lane: dict[int, float] = {}
    for lane_position, lane in enumerate(detections, start=1):
        lane_index = int(lane.get("lane_index", lane_position))
        matching = [
            band
            for band in lane.get("bands", [])
            if int(band.get("row_index", band.get("band_index", 1))) == chosen_row
        ]
        if not matching:
            continue
        for band in matching:
            band_rect = _rect_values(band["band_rect"])
            signal_rect = _rect_values(
                band.get("signal_rect", band["band_rect"])
            )
            band_signal_by_lane.setdefault(lane_index, signal_rect)
            row_center = band.get("row_center")
            peak_y = band.get("peak_y")
            if peak_y is not None:
                peak_value = float(peak_y)
                band_top_value = band_rect[1]
                band_bottom_value = band_rect[1] + band_rect[3]
                # A peak outside its own source band is almost certainly still
                # in search-ROI-local coordinates. Never let it create an
                # enormous lane crop or frame.
                if band_top_value - 1.0 <= peak_value <= band_bottom_value + 1.0:
                    band_peak_by_lane[lane_index] = peak_value
            band_records.append((
                lane_index,
                band_rect,
                float(row_center) if row_center is not None else None,
            ))

    if not band_records:
        raise ValueError("The selected row contains no editable band boxes.")

    _search_x, _search_y, search_w, search_h = _rect_values(search_roi)
    if search_w <= 0 or search_h <= 0:
        raise ValueError("The rough search ROI is empty.")

    horizontal_margin = max(0.0, float(horizontal_margin_px))
    vertical_margin = max(0.0, float(vertical_margin_px))

    band_by_lane: dict[int, tuple[float, float, float, float]] = {}
    for lane, rect, _row_center in band_records:
        band_by_lane.setdefault(lane, rect)

    lane_centers: list[float] = []
    for lane in sorted(band_by_lane):
        rect = band_signal_by_lane[lane]
        lane_centers.append(rect[0] + rect[2] / 2.0)

    band_left = min(rect[0] for rect in band_signal_by_lane.values())
    band_right = max(
        rect[0] + rect[2] for rect in band_signal_by_lane.values()
    )
    if len(lane_centers) >= 2:
        spacings = [
            right - left
            for left, right in zip(lane_centers, lane_centers[1:])
            if right - left > 0.5
        ]
        lane_pitch = median(spacings) if spacings else 0.0
    else:
        lane_pitch = 0.0

    # H/V margins are exact additions around detected signal bounds. The
    # condition table follows stored lane centres, so no implicit half-lane
    # background needs to be added for column alignment.
    desired_left = band_left - horizontal_margin
    desired_right = band_right + horizontal_margin

    band_top = min(rect[1] for rect in band_signal_by_lane.values())
    band_bottom = max(
        rect[1] + rect[3] for rect in band_signal_by_lane.values()
    )
    measured_centers = [
        rect[1] + rect[3] / 2.0 for rect in band_signal_by_lane.values()
    ]
    row_centers = [
        band_peak_by_lane.get(lane, rect[1] + rect[3] / 2.0)
        for lane, rect in band_signal_by_lane.items()
    ]
    heights = [max(1.0, rect[3]) for rect in band_signal_by_lane.values()]
    tops = [rect[1] for rect in band_signal_by_lane.values()]
    bottoms = [
        rect[1] + rect[3] for rect in band_signal_by_lane.values()
    ]
    row_anchor = median(row_centers)
    median_height = max(1.0, median(heights))

    requested_alignment = str(alignment or "auto").lower()
    alignment_used = requested_alignment
    if alignment_used == "auto" and len(band_peak_by_lane) == len(band_by_lane):
        alignment_used = "peak"
    elif alignment_used == "auto":
        top_spread = max(tops) - min(tops)
        bottom_spread = max(bottoms) - min(bottoms)
        tolerance = max(1.0, median_height * 0.12)
        if bottom_spread > top_spread + tolerance:
            alignment_used = "top"
        elif top_spread > bottom_spread + tolerance:
            alignment_used = "bottom"
        else:
            alignment_used = "center"
    if alignment_used not in {"center", "top", "bottom", "peak"}:
        alignment_used = "center"

    desired_top = band_top - vertical_margin
    desired_bottom = band_bottom + vertical_margin
    # The rough ROI restricts detection, not the final background margin.
    # Final cropping may extend beyond it, but never beyond the source image.
    left = max(0.0, desired_left)
    top = max(0.0, desired_top)
    right = min(float(image_width), desired_right)
    bottom = min(float(image_height), desired_bottom)
    if right - left < 1.0 or bottom - top < 1.0:
        raise ValueError("The calculated crop is empty after image-boundary clipping.")

    margin_clipped = any(
        (
            abs(left - desired_left) > 0.01,
            abs(top - desired_top) > 0.01,
            abs(right - desired_right) > 0.01,
            abs(bottom - desired_bottom) > 0.01,
        )
    )

    ordered_lanes = sorted(band_by_lane)
    standard_lane_width = max(
        1.0,
        max(rect[2] for rect in band_signal_by_lane.values())
        + 2.0 * horizontal_margin,
    )

    lane_anchors: dict[int, float] = {}
    for lane in ordered_lanes:
        rect = band_signal_by_lane[lane]
        if alignment_used == "top":
            anchor = rect[1]
        elif alignment_used == "bottom":
            anchor = rect[1] + rect[3]
        elif alignment_used == "peak":
            anchor = band_peak_by_lane[lane]
        else:
            anchor = rect[1] + rect[3] / 2.0
        lane_anchors[lane] = anchor

    above_extent = max(
        lane_anchors[lane] - band_signal_by_lane[lane][1]
        for lane in ordered_lanes
    )
    below_extent = max(
        band_signal_by_lane[lane][1]
        + band_signal_by_lane[lane][3]
        - lane_anchors[lane]
        for lane in ordered_lanes
    )
    crop_above = max(0.0, above_extent) + vertical_margin
    standard_lane_height = max(
        1.0,
        crop_above + max(0.0, below_extent) + vertical_margin,
    )
    anchor_spread = max(lane_anchors.values()) - min(lane_anchors.values())
    needs_lane_composition = (
        len(ordered_lanes) > 1
        and anchor_spread > max(2.5, median_height * 0.20)
    )
    if needs_lane_composition:
        lane_crop_boxes = tuple(
            ImageBBox(
                lane_centers[index] - standard_lane_width / 2.0,
                lane_anchors[lane] - crop_above,
                standard_lane_width,
                standard_lane_height,
            )
            for index, lane in enumerate(ordered_lanes)
        )
        composite_width = standard_lane_width * len(lane_crop_boxes)
        composite_height = standard_lane_height
    else:
        # Preserve the continuous source strip when its band anchors are
        # already aligned. This avoids unnecessary lane seams entirely.
        lane_crop_boxes = ()
        composite_width = right - left
        composite_height = bottom - top
    padding_required = any(
        crop.x < 0.0
        or crop.y < 0.0
        or crop.x + crop.w > image_width
        or crop.y + crop.h > image_height
        for crop in lane_crop_boxes
    )

    unique_lanes = len({lane for lane, _rect, _anchor in band_records})
    expected = max(1, int(expected_lane_count or unique_lanes))
    coverage = min(1.0, unique_lanes / expected)
    center_mad = median(abs(center - median(measured_centers)) for center in measured_centers)
    alignment_score = exp(-center_mad / median_height)
    count_score = min(1.0, len(band_records) / max(1, unique_lanes))
    confidence = max(0.0, min(1.0, 0.65 * coverage + 0.25 * alignment_score + 0.10 * count_score))
    low_confidence = confidence < 0.55 or unique_lanes < max(1, (expected + 1) // 2)

    return BandAutoFitResult(
        crop_box=ImageBBox(left, top, right - left, bottom - top),
        target_row=chosen_row,
        lane_count=unique_lanes,
        band_count=len(band_records),
        confidence=confidence,
        low_confidence=low_confidence,
        margin_clipped=margin_clipped,
        padding_required=padding_required,
        lane_centers_x=tuple(lane_centers),
        row_anchor_y=row_anchor,
        alignment_used=alignment_used,
        lane_crop_boxes=lane_crop_boxes,
        composite_width=composite_width,
        composite_height=composite_height,
    )
