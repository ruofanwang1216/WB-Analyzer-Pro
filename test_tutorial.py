from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import pandas as pd
from PySide6.QtCore import QEventLoop, QRect, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QSpinBox,
    QTableWidgetSelectionRange,
)

from gui.figure_generation import ColumnSetupDialog
from gui.figure_mode_window import FigureModeWindow
from gui.main_window import MainWindow


class TutorialWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _make_window(self) -> MainWindow:
        with (
            patch("gui.main_window.AppPersistence.update_config", return_value=None),
            patch(
                "gui.main_window.AppPersistence.read_config",
                return_value={"ui": {}},
            ),
        ):
            window = MainWindow()
        window._persistence.remember_ui_state = Mock()
        return window

    def _wait_for_image_loads(self, window: MainWindow) -> None:
        if not any(panel.canvas.is_loading() for panel in window._image_panels):
            return
        loop = QEventLoop()
        for panel in window._image_panels:
            panel.canvas.image_load_finished.connect(
                lambda _path: (
                    loop.quit()
                    if not any(item.canvas.is_loading() for item in window._image_panels)
                    else None
                )
            )
        QTimer.singleShot(5000, loop.quit)
        loop.exec()
        self.assertFalse(any(panel.canvas.is_loading() for panel in window._image_panels))

    def test_tutorial_import_uses_bundled_images_without_file_picker(self) -> None:
        window = self._make_window()
        original_lanes = window.param_panel.get_lane_count()
        window._tutorial_controller.start("densitometry")

        self.assertEqual(window.param_panel.get_lane_count(), 3)
        self.assertEqual(
            window._tutorial_controller.current_step_key,
            "create_column_table",
        )
        window._tutorial_controller.notify_column_setup_opened()
        window._tutorial_controller.notify_column_table_ready()
        with patch("gui.main_window.QFileDialog.getOpenFileNames") as picker:
            window._upload_files()
        self._wait_for_image_loads(window)

        picker.assert_not_called()
        loaded_names = {
            state["path"].split("/")[-1]
            for state in window._slot_states
            if state["path"]
        }
        self.assertEqual(
            loaded_names,
            {"tutorial_loading_control.tif", "tutorial_target_protein.tif"},
        )
        self.assertEqual(
            window._tutorial_controller.current_step_key,
            "target_left_lane_roi",
        )
        self.assertTrue(window._image_path.endswith("tutorial_target_protein.tif"))
        self.assertIsNotNone(window.canvas._tutorial_hint_item)
        self.assertTrue(window.canvas._tutorial_cursor_timer.isActive())
        lane_hint = window.canvas._tutorial_hint_rect
        window.canvas._tutorial_cursor_progress = 0.45
        window.canvas._advance_tutorial_roi_cursor()
        self.assertLess(
            window.canvas._tutorial_hint_item.rect().width(),
            lane_hint.width(),
        )

        # One box is the whole drawing step: the next step is Analyze, and
        # there is no second (band) box to draw.
        window._tutorial_controller.notify_roi_changed()
        self.assertEqual(
            window._tutorial_controller.current_step_key,
            "analyze_target_left",
        )
        self.assertIsNone(window.canvas._tutorial_hint_item)

        window._tutorial_controller.stop()
        self.assertEqual(window.param_panel.get_lane_count(), original_lanes)
        self.assertTrue(window._home_mode_active)
        self.assertFalse(window._loaded_slot_indices())
        self.assertFalse(window._converted_documents)
        self.assertIsNone(window._embedded_column_table)

    def test_densitometry_tutorial_creates_column_table_before_roi(self) -> None:
        window = self._make_window()
        window._tutorial_controller.start("densitometry")
        inspected: list[tuple[int, int]] = []
        arrow_is_lower_right: list[bool] = []

        def accept_setup() -> None:
            dialog = self._app.activeModalWidget()
            window._tutorial_controller._position_overlays()
            ok_button = dialog.findChild(
                type(window._tutorial_controller._panel._next_btn),
                "columnSetupOkButton",
            )
            corner = ok_button.mapToGlobal(ok_button.rect().bottomRight())
            arrow = window._tutorial_controller._highlight._arrow.geometry()
            arrow_is_lower_right.append(
                arrow.left() > corner.x() and arrow.top() > corner.y()
            )
            inspected.append((
                dialog._samples_spin.value(),
                dialog._replicates_spin.value(),
            ))
            dialog.accept()

        with patch("gui.main_window.FigureTypeDialog.exec") as figure_type_exec:
            QTimer.singleShot(0, accept_setup)
            window._on_figure_generation_clicked()

        figure_type_exec.assert_not_called()
        self.assertEqual(inspected, [(3, 2)])
        self.assertEqual(arrow_is_lower_right, [True])
        self.assertIsNotNone(window._embedded_column_table)
        self.assertEqual(window._tutorial_controller.current_step_key, "import_images")

    def test_click_steps_use_arrow_and_modal_confirmation_step(self) -> None:
        window = self._make_window()
        window._tutorial_controller.start("densitometry")
        self.assertTrue(window._tutorial_controller._steps[0].show_arrow)

        window._tutorial_controller.notify_column_setup_opened()
        self.assertEqual(
            window._tutorial_controller.current_step_key,
            "confirm_column_setup",
        )
        self.assertTrue(
            window._tutorial_controller._steps[
                window._tutorial_controller._step_index
            ].show_arrow
        )
        self.assertEqual(
            window._tutorial_controller._highlight._pulse.loopCount(),
            -1,
        )
        highlight = window._tutorial_controller._highlight
        highlight.setParent(window)
        highlight.set_target_geometry(
            QRect(window.width() - 90, 20, 80, 30),
            show_arrow=True,
        )
        self.assertEqual(highlight._arrow.text(), "→")

    def test_wb_tutorial_highlights_and_waits_for_each_blot_frame(self) -> None:
        window = self._make_window()
        window._tutorial_controller.start("wb_figure")
        workspace = window._figure_mode_window
        workspace._panels_spin.setValue(1)
        workspace._blots_spin.setValue(2)
        workspace._lanes_spin.setValue(3)
        workspace._on_apply_structure()

        keys = [step.key for step in window._tutorial_controller._steps]
        window._tutorial_controller._step_index = keys.index("select_target_frame")
        window._tutorial_controller._show_current_step()
        target = window._tutorial_controller._steps[
            window._tutorial_controller._step_index
        ].target()
        self.assertIsInstance(target, tuple)

        first_frame = next(
            frame
            for frame in workspace._canvas._blot_frames.values()
            if frame.source_ref.slot_idx == 0
        )
        workspace._on_canvas_blot_selected(first_frame.source_ref)
        self.assertEqual(
            window._tutorial_controller.current_step_key,
            "target_roi",
        )

    def test_wb_tutorial_uses_aspect_matched_manual_roi_and_enter_prompt(self) -> None:
        window = self._make_window()
        window._tutorial_controller.start("wb_figure")
        workspace = window._figure_mode_window
        self.assertEqual(workspace._roi_fill_mode, "manual")
        workspace._panels_spin.setValue(1)
        workspace._blots_spin.setValue(2)
        workspace._lanes_spin.setValue(3)
        workspace._on_apply_structure()
        window._upload_files()
        self._wait_for_image_loads(window)

        first_frame = next(
            frame
            for frame in workspace._canvas._blot_frames.values()
            if frame.source_ref.slot_idx == 0
        )
        workspace._on_canvas_blot_selected(first_frame.source_ref)
        controller = window._tutorial_controller
        keys = [step.key for step in controller._steps]
        controller._step_index = keys.index("target_roi")
        controller._show_current_step()
        roi = window.canvas._tutorial_hint_rect
        layout_item = workspace._canvas._blot_layout_items[
            first_frame.source_ref.key()
        ]
        self.assertAlmostEqual(
            roi.width() / roi.height(),
            layout_item.w_pt / layout_item.h_pt,
            delta=0.08,
        )

        window.canvas._roi_item = window.canvas._scene.addRect(roi)
        controller.notify_roi_changed()
        self.assertEqual(controller.current_step_key, "apply_target_roi")
        self.assertFalse(
            controller._table_drag_hint._enter_prompt.isHidden()
        )
        self.assertTrue(workspace.apply_roi_to_selected_slot())
        slot = workspace._get_slot(0, 0)
        self.assertAlmostEqual(
            slot.bounding_box.w / slot.bounding_box.h,
            layout_item.w_pt / layout_item.h_pt,
            delta=0.08,
        )
        self.assertFalse(slot.lane_crops)

    def test_tutorial_manual_fill_mode_restores_previous_wb_mode(self) -> None:
        workspace = FigureModeWindow()
        self.assertEqual(workspace._roi_fill_mode, "auto")
        workspace.set_tutorial_mode(True)
        self.assertEqual(workspace._roi_fill_mode, "manual")
        workspace.set_tutorial_mode(False)
        self.assertEqual(workspace._roi_fill_mode, "auto")

    def test_densitometry_tutorial_follows_the_one_box_flow(self) -> None:
        window = self._make_window()
        window._tutorial_controller.start("densitometry")
        keys = [step.key for step in window._tutorial_controller._steps]

        self.assertFalse([key for key in keys if key.endswith("_band_roi")])
        self.assertFalse([key for key in keys if key.startswith("autofill_")])
        self.assertFalse([key for key in keys if key.startswith("select_") and key.endswith("_row")])
        start = keys.index("target_left_lane_roi")
        # Same order as the table fills itself: target then loading, per replicate.
        self.assertEqual(
            keys[start:start + 8],
            [
                "target_left_lane_roi", "analyze_target_left",
                "loading_left_lane_roi", "analyze_loading_left",
                "target_right_lane_roi", "analyze_target_right",
                "loading_right_lane_roi", "analyze_loading_right",
            ],
        )
        self.assertEqual(keys[start + 8], "select_negative_control")

    def test_densitometry_tutorial_completes_table_control_and_figure(self) -> None:
        window = self._make_window()
        window._tutorial_controller.start("densitometry")
        QTimer.singleShot(0, lambda: self._app.activeModalWidget().accept())
        window._on_figure_generation_clicked()
        controller = window._tutorial_controller
        keys = [step.key for step in controller._steps]
        controller._step_index = keys.index("target_left_lane_roi")
        controller._show_current_step()
        table_window = window._embedded_column_table
        self.assertTrue(table_window.auto_fill_enabled())

        for values in (
            [20.0, 40.0, 60.0],
            [10.0, 20.0, 30.0],
            [24.0, 44.0, 64.0],
            [12.0, 22.0, 32.0],
        ):
            controller.notify_roi_changed()
            self.assertTrue(controller.current_step_key.startswith("analyze_"))
            # Analyze sends the run straight into the next table row.
            window._auto_fill_table_from_results(
                pd.DataFrame(
                    [
                        {"Band": 1, "Lane": lane, "Mean": value}
                        for lane, value in enumerate(values, start=1)
                    ]
                )
            )
            controller.notify_analysis_started()

        self.assertEqual(controller.current_step_key, "select_negative_control")
        self.assertEqual(float(table_window._table.item(0, 2).text()), 20.0)
        self.assertEqual(float(table_window._table.item(1, 2).text()), 10.0)
        self.assertEqual(float(table_window._table.item(3, 2).text()), 24.0)
        self.assertEqual(float(table_window._table.item(4, 2).text()), 12.0)
        table_window._on_select_negative_control_clicked()
        table_window._on_group_header_clicked(0)
        # Choosing the baseline redraws the chart; there is no Plot step.
        self.assertEqual(controller.current_step_key, "complete")
        self.assertTrue(table_window.has_generated_figure())
        self.assertTrue(table_window._table.item(2, 2).text())
        self.assertTrue(table_window._table.item(5, 2).text())

    def test_normal_import_still_uses_file_picker(self) -> None:
        window = self._make_window()
        with (
            patch.object(window, "_ensure_direct_exporter", return_value=None),
            patch(
                "gui.main_window.QFileDialog.getOpenFileNames",
                return_value=([], ""),
            ) as picker,
        ):
            window._upload_files()
        picker.assert_called_once()

    def test_exiting_wb_tutorial_rebuilds_fresh_home(self) -> None:
        window = self._make_window()
        window._tutorial_controller.start("wb_figure")
        self.assertIsNotNone(window._figure_mode_window)
        self.assertFalse(window._home_mode_active)

        window._tutorial_controller.stop()

        self.assertTrue(window._home_mode_active)
        self.assertIsNone(window._figure_mode_window)
        self.assertTrue(window._figure_workspace.isHidden())
        self.assertTrue(window._results_dock.isHidden())
        self.assertFalse(window._empty_state.isHidden())

    def test_column_setup_defaults_are_opt_in(self) -> None:
        normal = ColumnSetupDialog()
        tutorial = ColumnSetupDialog(default_samples=3, default_replicates=2)
        self.assertEqual((normal.get_input().samples, normal.get_input().replicates), (3, 3))
        self.assertEqual(
            (tutorial.get_input().samples, tutorial.get_input().replicates),
            (3, 2),
        )

    def test_frame_template_defaults_change_only_in_tutorial_mode(self) -> None:
        workspace = FigureModeWindow()
        inspected: list[tuple[int, int, int]] = []

        def inspect_dialog() -> None:
            dialog = self._app.activeModalWidget()
            inspected.append((
                dialog.findChild(QSpinBox, "frameTemplatePanelsSpin").value(),
                dialog.findChild(QSpinBox, "frameTemplateBlotsSpin").value(),
                dialog.findChild(QSpinBox, "frameTemplateLanesSpin").value(),
            ))
            dialog.reject()

        QTimer.singleShot(0, inspect_dialog)
        workspace._on_create_template()
        workspace.set_tutorial_mode(True)
        QTimer.singleShot(0, inspect_dialog)
        workspace._on_create_template()

        self.assertEqual(inspected, [(1, 2, 4), (1, 2, 3)])

    def test_condition_template_has_screenshot_preset_only_in_tutorial(self) -> None:
        workspace = FigureModeWindow()
        workspace._panels_spin.setValue(1)
        workspace._blots_spin.setValue(2)
        workspace._lanes_spin.setValue(3)
        workspace._on_apply_structure()
        workspace.set_tutorial_mode(True)
        inspected: list[tuple[int, int]] = []

        def inspect_dialog() -> None:
            dialog = self._app.activeModalWidget()
            rows = dialog.findChild(QSpinBox, "conditionRowsSpin_common")
            groups = dialog.findChild(QSpinBox, "laneGroupSpin_common")
            inspected.append((rows.value(), groups.value()))
            dialog.reject()

        QTimer.singleShot(0, inspect_dialog)
        workspace._on_create_condition_template()
        self.assertEqual(inspected, [(1, 1)])


if __name__ == "__main__":
    unittest.main()
