"""Non-destructive composition of equal-size source-image lane crops."""
from __future__ import annotations

import numpy as np


def compose_lane_crops(pixels: np.ndarray, crops: list[dict]) -> np.ndarray:
    """Return horizontally joined crops, padding beyond source boundaries.

    Pixels are copied without interpolation. Padding uses the median source
    border value and never invents or content-fills band signal.
    """
    if pixels.ndim != 2 or not crops:
        return pixels
    img_h, img_w = pixels.shape
    border = np.concatenate((
        pixels[0, :],
        pixels[-1, :],
        pixels[:, 0],
        pixels[:, -1],
    ))
    padding_value = np.asarray(np.median(border), dtype=pixels.dtype).item()
    pieces: list[np.ndarray] = []
    for crop in crops:
        x = int(round(float(crop.get("x", 0.0))))
        y = int(round(float(crop.get("y", 0.0))))
        width = max(1, int(round(float(crop.get("w", 1.0)))))
        height = max(1, int(round(float(crop.get("h", 1.0)))))
        piece = np.full((height, width), padding_value, dtype=pixels.dtype)
        source_left = max(0, x)
        source_top = max(0, y)
        source_right = min(img_w, x + width)
        source_bottom = min(img_h, y + height)
        if source_right > source_left and source_bottom > source_top:
            target_left = source_left - x
            target_top = source_top - y
            piece[
                target_top:target_top + source_bottom - source_top,
                target_left:target_left + source_right - source_left,
            ] = pixels[source_top:source_bottom, source_left:source_right]
        pieces.append(piece)
    return np.concatenate(pieces, axis=1)
