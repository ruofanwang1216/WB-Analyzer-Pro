import unittest

from core.condition_template import (
    even_lane_group_ranges,
    make_condition_table,
    normalize_group_levels,
)


class ConditionTemplateServiceTests(unittest.TestCase):
    def test_even_ranges_cover_all_lanes_without_overlap(self) -> None:
        self.assertEqual(
            even_lane_group_ranges(7, 3),
            [(1, 3), (4, 5), (6, 7)],
        )

    def test_legacy_single_level_input_is_preserved(self) -> None:
        ranges = [(1, 2), (3, 4)]

        self.assertEqual(normalize_group_levels(ranges), [ranges])

    def test_model_conversion_preserves_condition_table_wire_format(self) -> None:
        table = make_condition_table(
            4,
            2,
            [[(1, 2), (3, 4)], [(1, 4)]],
        )

        self.assertEqual(table.headers, ["Lane 1", "Lane 2", "Lane 3", "Lane 4"])
        self.assertEqual(table.rows[0], ["__groups_level__", "2", "1-4", "Group 1"])
        self.assertEqual(
            table.rows[1],
            ["__groups__", "1-2", "Group 1", "3-4", "Group 2"],
        )
        self.assertEqual(table.rows[2], ["Condition 1", "-", "+", "-", "-"])
        self.assertEqual(table.rows[3], ["Condition 2", "+", "-", "-", "+"])


if __name__ == "__main__":
    unittest.main()
