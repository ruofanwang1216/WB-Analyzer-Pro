"""Automatic lane + band finding inside one user-drawn ROI (densitometry mode).

The user draws one large rectangle around the blot area and says how many
lanes it contains.  This module then

1. works on native-depth pixels (16-bit stays 16-bit) and stretches them by the
   ROI's own intensity range, so faint bands are not lost to an 8-bit cut;
2. removes a fitted background plane and the noise floor;
3. finds the horizontal band rows from the pooled row profile;
4. fits exactly ``lane_count`` lane centres to the column profile with a
   near-equal-pitch model that tolerates small offsets and group gaps, so
   empty lanes still get a position;
5. places one box per (lane, band row).  All boxes of a row share the same
   width and height (equal Area, as in the manual workflow) but each lane may
   shift vertically a little to follow smiling / tilted bands.

Coordinates are returned in the same pixel space as ``pixels`` (callers pass
presentation-space pixels, which equal canvas scene coordinates).
"""
from __future__ import annotations

from typing import Any

import numpy as np

from utils.logger import get_logger

log = get_logger(__name__)

Rect = tuple[float, float, float, float]  # x, y, width, height

MAX_BAND_ROWS = 8


# ── Pixel preparation ─────────────────────────────────────────────────────────


def _to_float_gray(pixels: np.ndarray) -> np.ndarray:
    arr = np.asarray(pixels)
    if arr.ndim == 3:
        rgb = arr[:, :, :3].astype(np.float64)
        return 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]
    if arr.ndim != 2:
        raise ValueError(f"Unexpected image shape: {arr.shape}")
    return arr.astype(np.float64)


def infer_light_on_dark(region: np.ndarray) -> bool:
    """True when signal is brighter than background (raw chemiluminescence)."""
    # Remove the background gradient first; otherwise a tilted background
    # can outweigh faint bands and flip the answer.
    h, w = region.shape
    yy, xx = np.mgrid[0:h, 0:w]
    design = np.column_stack([np.ones(h * w), xx.ravel() / max(1, w), yy.ravel() / max(1, h)])
    coeffs, *_ = np.linalg.lstsq(design, region.ravel(), rcond=None)
    resid = region.ravel() - design @ coeffs
    lo, med, hi = np.percentile(resid, [0.5, 50.0, 99.5])
    return (hi - med) >= (med - lo)


def _fit_background_plane(img: np.ndarray) -> np.ndarray:
    """Robust plane fit using only the darker (background) pixels."""
    h, w = img.shape
    yy, xx = np.mgrid[0:h, 0:w]
    design = np.column_stack([np.ones(h * w), xx.ravel() / max(1, w), yy.ravel() / max(1, h)])
    values = img.ravel()
    mask = values <= np.percentile(values, 50)
    coeffs = np.zeros(3)
    for _ in range(3):
        if mask.sum() < 8:
            break
        coeffs, *_ = np.linalg.lstsq(design[mask], values[mask], rcond=None)
        resid = values - design @ coeffs
        mask = resid <= np.percentile(resid, 40)
    return (design @ coeffs).reshape(h, w)


def _prepare_signal(region: np.ndarray, light_on_dark: bool) -> np.ndarray:
    """Background-free signal stretched to the ROI's own range (≈0..1)."""
    from scipy.ndimage import gaussian_filter

    img = region if light_on_dark else (region.max() - region)
    sigma = max(0.8, min(img.shape) / 160.0)
    smooth = gaussian_filter(img, sigma)
    resid = smooth - _fit_background_plane(smooth)
    below = resid[resid <= np.median(resid)]
    noise = 1.4826 * np.median(np.abs(below - np.median(below))) if below.size else 0.0
    signal = np.clip(resid - np.median(below) - 2.0 * noise, 0.0, None) if below.size else np.clip(resid, 0, None)
    scale = np.percentile(signal, 99.7)
    if scale <= 0:
        scale = signal.max()
    if scale <= 0:
        return np.zeros_like(signal)
    return np.clip(signal / scale, 0.0, 1.5)


# ── Band rows ────────────────────────────────────────────────────────────────


def _row_extent(profile: np.ndarray, peak: int, floor_frac: float = 0.25) -> tuple[int, int]:
    """Walk outwards from a peak until the profile falls to floor_frac*peak or rises again."""
    target = profile[peak] * floor_frac
    top = peak
    while top > 0 and profile[top - 1] > target and profile[top - 1] <= profile[top] * 1.02:
        top -= 1
    bottom = peak
    n = len(profile)
    while bottom < n - 1 and profile[bottom + 1] > target and profile[bottom + 1] <= profile[bottom] * 1.02:
        bottom += 1
    return top, bottom


def find_band_rows(signal: np.ndarray, min_rel: float = 0.12) -> list[dict]:
    """Return band rows (top..bottom, centre) from the pooled row profile."""
    from scipy.ndimage import gaussian_filter1d
    from scipy.signal import find_peaks

    h, w = signal.shape
    # Use the brighter half of the columns so sparse lanes still register.
    k = max(1, w // 2)
    top_cols = np.sort(signal, axis=1)[:, -k:]
    profile = gaussian_filter1d(top_cols.mean(axis=1), 1.0)
    peak_max = float(profile.max())
    if peak_max <= 1e-6:
        return []
    peaks, props = find_peaks(profile, prominence=peak_max * min_rel, distance=max(2, h // 60))
    rows: list[dict] = []
    for peak, prom in zip(peaks, props["prominences"]):
        top, bottom = _row_extent(profile, int(peak))
        rows.append({"center": int(peak), "top": top, "bottom": bottom, "strength": float(prom)})
    rows.sort(key=lambda r: -r["strength"])
    rows = sorted(rows[:MAX_BAND_ROWS], key=lambda r: r["center"])
    # Neighbouring rows must not overlap: split at the profile minimum between them.
    for upper, lower in zip(rows, rows[1:]):
        if upper["bottom"] >= lower["top"]:
            lo, hi = upper["center"], lower["center"]
            valley = lo + int(np.argmin(profile[lo:hi + 1]))
            upper["bottom"] = max(upper["center"], valley - 1)
            lower["top"] = min(lower["center"], valley + 1)
    # A faint band hugging a main band is too weak to be a row of its own; it
    # shows up as a one-sided shoulder on the main band's flank.
    for index, row in enumerate(rows):
        lo_limit = rows[index - 1]["bottom"] + 1 if index > 0 else 0
        hi_limit = rows[index + 1]["top"] - 1 if index < len(rows) - 1 else h - 1
        above = _flank_shoulder(profile, row["center"], -1, lo_limit)
        below = _flank_shoulder(profile, row["center"], +1, hi_limit)
        row["adjacent"] = None
        if (above is None) == (below is None):
            # None, or both sides (a diffuse halo around a strong band): one band.
            continue
        if below is not None:
            main_edge, far_edge = below
            row["adjacent"] = {
                "side": "below",
                "main": (row["top"], min(row["bottom"], main_edge)),
                "full": (row["top"], max(row["bottom"], far_edge)),
            }
        else:
            main_edge, far_edge = above
            row["adjacent"] = {
                "side": "above",
                "main": (max(row["top"], main_edge), row["bottom"]),
                "full": (min(row["top"], far_edge), row["bottom"]),
            }
    # Two neighbouring rows may claim the same stretch between them: share it.
    for upper, lower in zip(rows, rows[1:]):
        up_adj, low_adj = upper["adjacent"], lower["adjacent"]
        up_end = up_adj["full"][1] if up_adj else upper["bottom"]
        low_start = low_adj["full"][0] if low_adj else lower["top"]
        if up_end >= low_start:
            cut = (upper["bottom"] + lower["top"]) // 2
            if up_adj:
                up_adj["full"] = (up_adj["full"][0], max(upper["bottom"], min(up_end, cut)))
            if low_adj:
                low_adj["full"] = (min(lower["top"], max(low_start, cut + 1)), low_adj["full"][1])
    return rows


def _flank_shoulder(profile: np.ndarray, peak: int, step: int, limit: int) -> tuple[int, int] | None:
    """Find a faint band riding on one flank of a main band.

    Walking outwards from the peak, the flank first falls steeply.  If it then
    flattens out while still well above the baseline, and stays there long
    enough to hold real signal, that plateau (or small second hump) is an
    adjacent band.  Returns (last index of the main band, last index of the
    adjacent band), or None for a clean flank.
    """
    peak_v = float(profile[peak])
    if peak_v <= 0:
        return None
    idx = [peak]
    i = peak
    quiet = 0
    while 0 <= (i + step) < len(profile) and (i + step - limit) * step <= 0:
        i += step
        idx.append(i)
        quiet = quiet + 1 if profile[i] < 0.04 * peak_v else 0
        if quiet >= 3:
            break
    if len(idx) < 5:
        return None
    values = profile[idx] / peak_v
    drops = values[:-1] - values[1:]
    steepest = int(np.argmax(drops))
    if drops[steepest] <= 0:
        return None
    # Where the steep fall of the main band gives way to a plateau or a rise.
    start = None
    for j in range(steepest + 1, len(drops)):
        if drops[j] < 0.35 * drops[steepest]:
            start = j
            break
    if start is None:
        return None
    if values[start] <= 0.08:
        return None
    # The adjacent band must touch the main band and stay close to it: stop
    # at the first real gap, and never reach further than the main band is wide.
    reach = min(len(values) - 1, start + max(4, int(round(1.5 * start))))
    end = start
    while end + 1 <= reach and values[end + 1] > 0.08:
        end += 1
    extent = int(np.count_nonzero(values[start:end + 1] > 0.08))
    main_area = float(values[:start].sum())
    shoulder_area = float(values[start:end + 1].sum())
    if extent < 3 or main_area <= 0 or shoulder_area < 0.15 * main_area:
        return None
    return idx[start - 1], idx[end]


# ── Lanes ────────────────────────────────────────────────────────────────────


def _lane_kernel(pitch: float) -> np.ndarray:
    """Matched filter: reward signal in the lane core, punish signal on the lane edge."""
    half = int(np.ceil(0.6 * pitch))
    x = np.arange(-half, half + 1, dtype=np.float64)
    ax = np.abs(x) / pitch
    kernel = np.where(ax <= 0.32, 1.0, np.where(ax <= 0.42, 0.0, -1.6))
    return kernel


def _fit_lane_centres(profile: np.ndarray, lane_count: int) -> tuple[np.ndarray, float]:
    """Choose exactly lane_count centres maximising matched-filter score."""
    w = len(profile)
    best: tuple[float, np.ndarray, float] | None = None
    p_max = w / lane_count
    floor = 0.08 * float(np.percentile(profile, 95))
    # The ROI may be much wider than the blot, so allow pitches well below W/N.
    p_min = max(3.0, p_max * 0.2)
    for pitch in np.geomspace(p_min, p_max, 44):
        kernel = _lane_kernel(pitch)
        # Core pixels below the floor count against the lane, so an over-wide
        # pitch (cores spilling into valleys) is not rewarded.
        score = np.convolve(profile, kernel, mode="same") - floor * np.count_nonzero(kernel > 0)
        # An empty slot between two bright lanes must not look worse than an
        # empty slot outside the blot, so cap how negative a position can be.
        top = float(score.max())
        if top <= 0:
            continue
        # Concave reward: N lanes each sitting on a band beat N/2 double-wide
        # lanes that swallow two bands apiece (matters when the ROI is much
        # wider than the blot).
        # (No per-pitch normalisation here: totals must stay comparable
        # across pitches.)
        score = np.sign(score) * np.sqrt(np.abs(score))
        score = np.maximum(score, -0.05 * np.sqrt(top))
        centres, total = _dp_place(score, lane_count, pitch)
        if centres is None:
            continue
        if best is None or total > best[0]:
            best = (total, centres, pitch)
    if best is None:
        pitch = p_max
        return np.array([pitch * (i + 0.5) for i in range(lane_count)]), pitch
    centres, pitch = best[1], best[2]
    # The model pitch only constrains spacing; report the measured one.
    if len(centres) > 1:
        gaps = np.diff(centres)
        regular = gaps[gaps <= 1.5 * np.min(gaps)]
        pitch = float(np.median(regular))
    return centres, pitch


def _dp_place(score: np.ndarray, n: int, pitch: float) -> tuple[np.ndarray | None, float]:
    """Dynamic programme over positions with near-equal spacing and gap penalty."""
    w = len(score)
    d_min = max(2, int(round(0.82 * pitch)))
    d_reg_hi = int(round(1.22 * pitch))
    d_max = int(round(3.2 * pitch))
    margin = int(round(0.25 * pitch))
    lo_x, hi_x = margin, w - 1 - margin
    if hi_x - lo_x < d_min * (n - 1):
        return None, -np.inf
    scale = max(1e-9, float(np.max(np.abs(score))))
    gap_pen = 0.12 * scale
    irreg_pen = 0.5 * scale

    neg = -np.inf
    dp = np.full((n, w), neg)
    back = np.zeros((n, w), dtype=np.int32)
    dp[0, lo_x:hi_x + 1] = score[lo_x:hi_x + 1]
    dists = np.arange(d_min, d_max + 1)
    dev = np.abs(dists - pitch) / pitch
    pen = np.where(dists <= d_reg_hi, irreg_pen * dev ** 2, gap_pen + irreg_pen * 0.05)
    for i in range(1, n):
        prev = dp[i - 1]
        for x in range(lo_x + d_min * i, hi_x + 1):
            src = x - dists
            valid = src >= lo_x
            if not valid.any():
                continue
            cand = prev[src[valid]] - pen[valid]
            j = int(np.argmax(cand))
            if cand[j] == neg:
                continue
            dp[i, x] = cand[j] + score[x]
            back[i, x] = src[valid][j]
    last = int(np.argmax(dp[n - 1]))
    total = float(dp[n - 1, last])
    if not np.isfinite(total):
        return None, -np.inf
    centres = [last]
    for i in range(n - 1, 0, -1):
        centres.append(int(back[i, centres[-1]]))
    return np.array(centres[::-1], dtype=np.float64), total


# ── Public API ───────────────────────────────────────────────────────────────


def detect_lanes_in_roi(
    pixels: np.ndarray,
    roi: Rect,
    lane_count: int,
    *,
    light_on_dark: bool | None = None,
    include_adjacent: bool = False,
) -> dict[str, Any]:
    """Detect ``lane_count`` lanes and their band boxes inside ``roi``.

    When a faint band sits right next to a main band, boxes wrap the main band
    only unless ``include_adjacent`` is set; ``result["adjacent_rows"]`` lists
    the rows where that choice exists.

    Returns ``{"lanes": [...], "rows": [...], "light_on_dark": bool, "message": str}``.
    Each lane is ``{"lane_index", "lane_rect", "bands": [{"band_rect", "band_index",
    "row_index", "empty"}]}`` with rects as (x, y, w, h) in pixel space.
    """
    from scipy.ndimage import gaussian_filter1d

    gray = _to_float_gray(pixels)
    img_h, img_w = gray.shape
    rx, ry, rw, rh = roi
    x1 = max(0, int(np.floor(rx)))
    y1 = max(0, int(np.floor(ry)))
    x2 = min(img_w, int(np.ceil(rx + rw)))
    y2 = min(img_h, int(np.ceil(ry + rh)))
    lane_count = max(1, int(lane_count))
    result: dict[str, Any] = {
        "lanes": [], "rows": [], "light_on_dark": True, "message": "",
        "adjacent_rows": [], "include_adjacent": bool(include_adjacent),
    }
    if x2 - x1 < max(6, 2 * lane_count) or y2 - y1 < 6:
        result["message"] = "ROI is too small for automatic lane detection."
        return result

    region = gray[y1:y2, x1:x2]
    if light_on_dark is None:
        light_on_dark = infer_light_on_dark(region)
    result["light_on_dark"] = bool(light_on_dark)
    signal = _prepare_signal(region, light_on_dark)
    h, w = signal.shape

    rows = find_band_rows(signal)
    for row in rows:
        adjacent = row.get("adjacent")
        row["pad_top"] = row["pad_bottom"] = True
        if adjacent is None:
            continue
        row["top"], row["bottom"] = adjacent["full"] if include_adjacent else adjacent["main"]
        if not include_adjacent:
            # No extra margin on the side that faces the adjacent band.
            row["pad_bottom" if adjacent["side"] == "below" else "pad_top"] = False
    if not rows:
        result["message"] = "No band rows found in the ROI."
        rows_for_lanes = [{"center": h // 2, "top": 0, "bottom": h - 1}]
    else:
        rows_for_lanes = rows

    # Column profile pooled over band rows only (less smear / noise).
    row_mask = np.zeros(h, dtype=bool)
    for row in rows_for_lanes:
        pad = max(1, (row["bottom"] - row["top"]) // 3)
        row_mask[max(0, row["top"] - pad):min(h, row["bottom"] + pad + 1)] = True
    col_profile = signal[row_mask].mean(axis=0)
    col_profile = np.cbrt(gaussian_filter1d(col_profile, 0.8))  # lift faint lanes

    centres, pitch = _fit_lane_centres(col_profile, lane_count)
    box_w = max(2.0, round(0.8 * pitch))
    if len(centres) > 1:
        box_w = max(2.0, min(box_w, float(np.floor(np.min(np.diff(centres))))))

    # Lane frames: Voronoi split between neighbouring centres.
    bounds = [max(0.0, centres[0] - pitch / 2)]
    for a, b in zip(centres, centres[1:]):
        bounds.append((a + b) / 2)
    bounds.append(min(float(w), centres[-1] + pitch / 2))

    lanes: list[dict] = []
    for i, c in enumerate(centres):
        left, right = bounds[i], bounds[i + 1]
        if i > 0 and centres[i] - centres[i - 1] > 1.5 * pitch:
            left = max(left, c - pitch / 2)
        if i < len(centres) - 1 and centres[i + 1] - centres[i] > 1.5 * pitch:
            right = min(right, c + pitch / 2)
        lanes.append({
            "lane_index": i + 1,
            "centre": float(c),
            "lane_rect": (x1 + left, float(y1), right - left, float(h)),
            "bands": [],
        })

    for row_index, row in enumerate(rows, start=1):
        row_h = max(3, row["bottom"] - row["top"] + 1)
        pad = max(2, int(round(row_h * 0.25)))
        pad_top = pad if row.get("pad_top", True) else 0
        pad_bottom = pad if row.get("pad_bottom", True) else 0
        box_h = row_h + pad_top + pad_bottom
        base_top = row["top"] - pad_top
        max_shift = max(1, int(round(0.6 * row_h)))
        offsets: list[int | None] = []
        strengths: list[float] = []
        # Strips are often cut and re-laid slightly shifted: let the whole row
        # slide sideways (same shift for every lane) to sit on its own bands.
        r_top, r_bot = max(0, base_top), min(h, base_top + box_h)
        row_cols = np.concatenate([[0.0], np.cumsum(signal[r_top:r_bot].sum(axis=0))])
        max_dx = int(round(0.45 * pitch))
        best_dx, best_sum = 0, -1.0
        for dx in sorted(range(-max_dx, max_dx + 1), key=abs):
            total = 0.0
            for lane in lanes:
                a = int(np.clip(round(lane["centre"] + dx - box_w / 2), 0, w))
                b = int(np.clip(round(lane["centre"] + dx + box_w / 2), 0, w))
                total += row_cols[b] - row_cols[a]
            if total > best_sum * 1.02 + 1e-9:
                best_dx, best_sum = dx, total
        row_dx = best_dx

        # Horizontal extent of every band in this row, so the boxes wrap the
        # whole band instead of a fixed fraction of the lane pitch.
        row_line = gaussian_filter1d(signal[r_top:r_bot].sum(axis=0), 0.8)
        spans: list[tuple[float, float] | None] = []
        for lane_i, lane in enumerate(lanes):
            c = lane["centre"] + row_dx
            lo_lim = int(np.clip(np.floor(c - 0.75 * pitch), 0, w - 1))
            hi_lim = int(np.clip(np.ceil(c + 0.75 * pitch), 0, w - 1))
            if lane_i > 0:
                lo_lim = max(lo_lim, int(np.ceil((lanes[lane_i - 1]["centre"] + lane["centre"]) / 2 + row_dx)))
            if lane_i < len(lanes) - 1:
                hi_lim = min(hi_lim, int(np.floor((lanes[lane_i + 1]["centre"] + lane["centre"]) / 2 + row_dx)))
            core_a = int(np.clip(round(c - 0.3 * pitch), lo_lim, hi_lim))
            core_b = int(np.clip(round(c + 0.3 * pitch), lo_lim, hi_lim))
            if core_b <= core_a:
                spans.append(None)
                continue
            peak_x = core_a + int(np.argmax(row_line[core_a:core_b + 1]))
            peak_v = float(row_line[peak_x])
            if peak_v <= 0.02 * (r_bot - r_top):
                spans.append(None)
                continue
            # Half-maximum edges: a low threshold lets a one-sided tail or the
            # neighbour's shoulder drag the box off-centre and clip the far end.
            floor_v = 0.5 * peak_v
            left = peak_x
            while left > lo_lim and row_line[left - 1] > floor_v:
                left -= 1
            right = peak_x
            while right < hi_lim and row_line[right + 1] > floor_v:
                right += 1
            spans.append((float(left), float(right + 1)))
        found = [b - a for span in spans if span is not None for a, b in [span]]
        side_pad = max(2.0, round(0.08 * pitch))
        row_box_w = box_w
        if found:
            row_box_w = max(box_w, float(np.ceil(max(found) + 2 * side_pad)))
        if len(lanes) > 1:
            row_box_w = min(row_box_w, float(np.floor(np.mean(np.sort(np.diff([ln["centre"] for ln in lanes]))[: max(1, len(lanes) // 2)]))))
        row_box_w = float(min(row_box_w, w))
        # Centre each box on its own band; empty lanes keep the fitted centre.
        box_centres = []
        for lane, span in zip(lanes, spans):
            fitted = lane["centre"] + row_dx
            if span is None or (span[1] - span[0]) < 0.4 * pitch:
                # No band, or only a noisy sliver of one: trust the lane fit.
                box_centres.append(fitted)
                continue
            measured = (span[0] + span[1]) / 2.0
            box_centres.append(float(np.clip(measured, fitted - 0.25 * pitch, fitted + 0.25 * pitch)))
        box_lefts = [float(np.clip(cx - row_box_w / 2, 0, max(0.0, w - row_box_w))) for cx in box_centres]
        # Neighbouring boxes must not overlap: push them apart (an empty
        # lane's box gives way entirely to a real band).
        for _ in range(12):
            moved = False
            for i in range(1, len(box_lefts)):
                overlap = box_lefts[i - 1] + row_box_w - box_lefts[i]
                if overlap <= 1e-6:
                    continue
                moved = True
                if spans[i - 1] is None and spans[i] is not None:
                    box_lefts[i - 1] -= overlap
                elif spans[i] is None and spans[i - 1] is not None:
                    box_lefts[i] += overlap
                else:
                    box_lefts[i - 1] -= overlap / 2.0
                    box_lefts[i] += overlap / 2.0
            if not moved:
                break
        box_lefts = [float(np.clip(left, 0, max(0.0, w - row_box_w))) for left in box_lefts]
        for lane, box_left in zip(lanes, box_lefts):
            cx1 = int(max(0, round(box_left)))
            cx2 = int(min(w, round(box_left + row_box_w)))
            column = signal[:, cx1:cx2].mean(axis=1) if cx2 > cx1 else np.zeros(h)
            best_off, best_val = 0, -1.0
            for off in range(-max_shift, max_shift + 1):
                t = base_top + off
                tt, bb = max(0, t), min(h, t + box_h)
                if bb <= tt:
                    continue
                val = float(column[tt:bb].sum())
                if val > best_val + 1e-9 or (abs(val - best_val) <= 1e-9 and abs(off) < abs(best_off)):
                    best_off, best_val = off, val
            if row.get("pad_top", True) != row.get("pad_bottom", True) and best_val > 0.02 * box_h:
                # Main-band-only box beside a faint adjacent band: the largest
                # sum would slide the box onto that band, so follow the main
                # band's own peak in this lane instead.
                lo_y = max(0, row["center"] - max_shift)
                hi_y = min(h, row["center"] + max_shift + 1)
                smooth_col = gaussian_filter1d(column, 1.0)
                best_off = lo_y + int(np.argmax(smooth_col[lo_y:hi_y])) - row["center"]
            base_val = float(column[max(0, base_top):min(h, base_top + box_h)].sum())
            strengths.append(base_val)
            offsets.append(best_off if best_val > 0.02 * box_h else None)
        known = [o for o in offsets if o is not None]
        fallback = int(round(float(np.median(known)))) if known else 0
        row_peak = max(strengths) if strengths else 0.0
        for lane, off, strength, box_left in zip(lanes, offsets, strengths, box_lefts):
            top = base_top + (fallback if off is None else off)
            top = int(min(max(0, top), max(0, h - box_h)))
            bx = box_left
            lane["bands"].append({
                "band_rect": (x1 + bx, float(y1 + top), float(row_box_w), float(min(box_h, h))),
                "row_index": row_index,
                "empty": off is None or strength < 0.05 * row_peak,
            })

    for lane in lanes:
        lane["bands"].sort(key=lambda b: b["band_rect"][1])
        # Per-lane shifts must not make neighbouring rows overlap: split the overlap.
        for upper, lower in zip(lane["bands"], lane["bands"][1:]):
            ux, uy, uw, uh = upper["band_rect"]
            lx, ly, lw, lh = lower["band_rect"]
            overlap = (uy + uh) - ly
            if overlap > 0:
                cut = overlap / 2.0
                upper["band_rect"] = (ux, uy, uw, max(1.0, uh - cut))
                lower["band_rect"] = (lx, ly + cut, lw, max(1.0, lh - cut))
        for band_index, band in enumerate(lane["bands"], start=1):
            band["band_index"] = band_index
        lane.pop("centre", None)

    result["lanes"] = lanes
    result["rows"] = [
        {"top": y1 + r["top"], "bottom": y1 + r["bottom"], "center": y1 + r["center"]} for r in rows
    ]
    result["adjacent_rows"] = [
        {"row_index": index, "side": r["adjacent"]["side"]}
        for index, r in enumerate(rows, start=1)
        if r.get("adjacent") is not None
    ]
    result["include_adjacent"] = bool(include_adjacent)
    result["pitch"] = float(pitch)
    if rows:
        result["message"] = f"{len(lanes)} lane(s), {len(rows)} band row(s)."
    log.info("Lane auto-detect: %s (pitch %.1f px)", result["message"], pitch)
    return result
