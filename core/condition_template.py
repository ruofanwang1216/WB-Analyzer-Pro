"""Pure condition-template grouping and model-conversion services.

The functions in this module are deliberately independent of Qt.  GUI code
collects user input; this service normalizes it and constructs the existing
``ConditionTable`` wire format without changing that format.
"""
from __future__ import annotations

from core.figure_project import ConditionTable

LaneRange = tuple[int, int]
GroupLevels = list[list[LaneRange]]


def even_lane_group_ranges(
    lane_count: int,
    group_count: int,
) -> list[LaneRange]:
    """Partition all lanes into the requested number of contiguous groups."""
    lane_count = max(1, lane_count)
    group_count = min(group_count, lane_count)
    if group_count <= 0:
        return []

    base, remainder = divmod(lane_count, group_count)
    ranges: list[LaneRange] = []
    start = 1
    for group_index in range(group_count):
        size = base + (1 if group_index < remainder else 0)
        end = start + size - 1
        ranges.append((start, end))
        start = end + 1
    return ranges


def normalize_group_levels(group_ranges: object) -> GroupLevels:
    """Normalize the legacy one-level and current multi-level inputs."""
    raw_groups = list(group_ranges) if isinstance(group_ranges, list) else []
    if raw_groups and isinstance(raw_groups[0], tuple):
        return [raw_groups]
    if raw_groups and isinstance(raw_groups[0], list):
        return [list(level) for level in raw_groups]
    return []


def make_condition_table(
    lane_count: int,
    condition_rows: int,
    group_ranges: object,
) -> ConditionTable:
    """Convert dialog values to the established ConditionTable row format."""
    lane_count = max(1, int(lane_count))
    headers = [f"Lane {index + 1}" for index in range(lane_count)]
    group_levels = normalize_group_levels(group_ranges)
    rows: list[list[str]] = []

    # Highest level is drawn first; Level 1 stays closest to conditions.
    for level_index in reversed(range(len(group_levels))):
        if not group_levels[level_index]:
            continue
        group_row = (
            ["__groups__"]
            if level_index == 0
            else ["__groups_level__", str(level_index + 1)]
        )
        for group_index, (start, end) in enumerate(group_levels[level_index]):
            group_row.extend([
                f"{start}-{end}",
                f"Group {group_index + 1}",
            ])
        rows.append(group_row)

    for row_index in range(max(1, int(condition_rows))):
        values = [
            "+" if (lane_index + row_index) % 3 == 1 else "-"
            for lane_index in range(lane_count)
        ]
        rows.append([f"Condition {row_index + 1}"] + values)
    return ConditionTable(headers=headers, rows=rows)
