from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from gui.main_window import MainWindow
from gui.figure_generation import ColumnTableWindow
from utils.i18n import LANG_EN, LANG_ZH_CN


class MainWindowHomePageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _make_window(self, ui: dict | None = None) -> MainWindow:
        saved_ui = dict(ui or {})
        with (
            patch("gui.main_window.AppPersistence.update_config", return_value=None),
            patch(
                "gui.main_window.AppPersistence.read_config",
                return_value={"ui": saved_ui},
            ),
        ):
            window = MainWindow()
        window._persistence.remember_ui_state = Mock()
        return window

    def test_home_is_blank_canvas_with_tools_and_top_right_language(self) -> None:
        window = self._make_window()
        window.resize(1200, 800)
        window.show()
        self._app.processEvents()

        self.assertIs(window._mode_container.currentWidget(), window._main_splitter)
        self.assertTrue(window._home_mode_active)
        self.assertTrue(window._files_panel_collapsed)
        self.assertTrue(window._param_panel_host.isHidden())
        self.assertTrue(window._figure_workspace.isHidden())
        self.assertTrue(window._results_dock.isHidden())
        self.assertTrue(window._open_toolbar_action.isVisible())
        self.assertTrue(window._workspace_toolbar_action.isVisible())
        self.assertFalse(window._analyze_toolbar_action.isVisible())
        self.assertLess(
            window._open_toolbar_btn.geometry().left(),
            window._workspace_toolbar_group.geometry().left(),
        )
        self.assertGreater(
            window._language_combo.geometry().left(),
            window._workspace_toolbar_group.geometry().right(),
        )
        self.assertFalse(window._act_open.icon().isNull())
        self.assertEqual(
            window._open_toolbar_btn.toolButtonStyle(),
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon,
        )
        self.assertFalse(hasattr(window, "_act_export_all"))
        self.assertFalse(hasattr(window, "_act_reset"))

    def test_top_tools_open_their_existing_workspaces(self) -> None:
        densitometry = self._make_window()
        with patch("gui.main_window.FigureTypeDialog.exec", return_value=0):
            densitometry._on_figure_generation_clicked()
        self.assertFalse(densitometry._home_mode_active)
        self.assertTrue(densitometry._files_panel_collapsed)
        self.assertFalse(densitometry._figure_workspace.isHidden())
        self.assertFalse(densitometry._param_panel_host.isHidden())
        self.assertFalse(densitometry._results_dock.isHidden())

        wb_figure = self._make_window()
        wb_figure._on_wb_plot_mode()
        self.assertFalse(wb_figure._home_mode_active)
        self.assertTrue(wb_figure._files_panel_collapsed)
        self.assertFalse(wb_figure._figure_workspace.isHidden())
        self.assertIs(
            wb_figure._figure_workspace_stack.currentWidget(),
            wb_figure._wb_plot_workspace_host,
        )

    def test_roi_settings_share_left_side_with_uploaded_files(self) -> None:
        window = self._make_window()
        window._on_densitometry_mode()

        self.assertIs(window._main_splitter.widget(0), window._files_panel)
        self.assertIs(window._main_splitter.widget(1), window._param_panel_host)
        self.assertIs(window._main_splitter.widget(2), window._workspace_splitter)
        self.assertTrue(window._files_panel_collapsed)
        self.assertTrue(window._files_list.isHidden())
        self.assertFalse(window._param_panel_host.isHidden())
        self.assertFalse(hasattr(window.param_panel, "_rotate_group"))

        window._set_files_panel_collapsed(False)

        self.assertFalse(window._files_list.isHidden())
        self.assertTrue(window._param_panel_host.isHidden())

    def test_tool_switches_preserve_uploaded_files_panel_state(self) -> None:
        window = self._make_window()
        window._on_densitometry_mode()
        self.assertTrue(window._files_panel_collapsed)

        window._set_files_panel_collapsed(False)
        window._on_wb_plot_mode()
        self.assertFalse(window._files_panel_collapsed)

        window._show_home_page()
        self.assertFalse(window._files_panel_collapsed)

        window._set_files_panel_collapsed(True)
        window._on_densitometry_mode()
        self.assertTrue(window._files_panel_collapsed)
        self.assertFalse(window._param_panel_host.isHidden())

    def test_saved_uploaded_files_panel_state_is_restored_on_startup(self) -> None:
        expanded = self._make_window({"files_panel_collapsed": False})
        self.assertFalse(expanded._files_panel_collapsed)
        self.assertFalse(expanded._files_list.isHidden())

        collapsed = self._make_window({"files_panel_collapsed": True})
        self.assertTrue(collapsed._files_panel_collapsed)
        self.assertTrue(collapsed._files_list.isHidden())

    def test_import_on_home_loads_same_viewer_without_opening_a_tool(self) -> None:
        window = self._make_window()
        with tempfile.TemporaryDirectory() as tmpdir:
            image_path = Path(tmpdir) / "home-import.tif"
            Image.fromarray(np.zeros((12, 18), dtype=np.uint16)).save(image_path)
            with (
                patch.object(window._persistence, "remember_open_dir", return_value=None),
                patch(
                    "gui.main_window.QFileDialog.getOpenFileNames",
                    return_value=([str(image_path)], ""),
                ),
            ):
                window._upload_files()

        self.assertTrue(window._home_mode_active)
        self.assertIs(window._mode_container.currentWidget(), window._main_splitter)
        self.assertEqual(window._slot_states[0]["path"], str(image_path.resolve()))
        self.assertTrue(window._param_panel_host.isHidden())
        self.assertTrue(window._figure_workspace.isHidden())
        self.assertTrue(window._results_dock.isHidden())

    def test_home_controls_switch_between_english_and_chinese(self) -> None:
        window = self._make_window()

        window._set_language(LANG_ZH_CN, persist=False)
        self.assertEqual(window._act_open.text(), "导入图像")
        self.assertEqual(window._figure_generation_btn.text(), "灰度定量图生成")
        self.assertEqual(window._wb_plot_generation_btn.text(), "Western blot 图像排版")
        self.assertEqual(window._language_combo.currentData(), LANG_ZH_CN)

        window._set_language(LANG_EN, persist=False)
        self.assertEqual(window._act_open.text(), "Import Images")
        self.assertEqual(
            window._figure_generation_btn.text(),
            "Densitometry Figure Generation",
        )
        self.assertEqual(
            window._wb_plot_generation_btn.text(),
            "WB Plot Figure Generation",
        )

    def test_image_rotation_menu_uses_compact_icon_actions(self) -> None:
        window = self._make_window()
        panel = window._image_panels[0]

        action_texts = [action.text() for action in panel.rotate_menu.actions()]
        self.assertEqual(action_texts, ["Horizontal", "Vertical", "", "Custom", "", "Undo"])
        self.assertFalse(panel.flip_horizontal_action.icon().isNull())
        self.assertFalse(panel.flip_vertical_action.icon().isNull())
        self.assertFalse(panel.rotate_custom_action.icon().isNull())
        self.assertEqual(panel.rotate_custom_action.toolTip(), "Press Enter/Return to apply")

        window._set_language(LANG_ZH_CN, persist=False)
        self.assertEqual(panel.flip_horizontal_action.text(), "水平")
        self.assertEqual(panel.flip_vertical_action.text(), "垂直")
        self.assertEqual(panel.rotate_custom_action.text(), "自定义")
        self.assertEqual(panel.rotate_custom_action.toolTip(), "按 Enter/Return 应用")
        self.assertEqual(panel.undo_image_operation_action.text(), "撤销")

    def test_wb_figure_workspace_fully_tracks_language_before_and_after_entry(self) -> None:
        before_entry = self._make_window()
        before_entry._language_combo.setCurrentIndex(
            before_entry._language_combo.findData(LANG_ZH_CN)
        )
        before_entry._on_wb_plot_mode()
        figure = before_entry._figure_mode_window
        self.assertEqual(figure._grp1.title_text(), "步骤 1：选择布局")
        self.assertEqual(figure._grp4.title_text(), "步骤 2：填充印迹图框")
        self.assertEqual(figure._saved_templates_btn.text(), "已保存模板   ›")
        self.assertEqual(figure._annot_undo_btn.text(), "撤销")
        self.assertEqual(figure._fit_center_btn.text(), "适配并居中")

        after_entry = self._make_window()
        after_entry._on_wb_plot_mode()
        after_entry._language_combo.setCurrentIndex(
            after_entry._language_combo.findData(LANG_ZH_CN)
        )
        figure = after_entry._figure_mode_window
        self.assertEqual(figure._grp5.title_text(), "已保存印迹图文件")
        self.assertEqual(figure._grp6.title_text(), "导出图形")
        self.assertEqual(figure._export_pdf_btn.toolTip(), "将图形导出为 PDF")
        self.assertEqual(figure._align_text_boxes_combo.currentText(), "对齐文本框")
        self.assertEqual(after_entry._status_bar.currentMessage(), "已进入 WB Figure 排版。")

        after_entry._language_combo.setCurrentIndex(
            after_entry._language_combo.findData(LANG_EN)
        )
        self.assertEqual(figure._grp1.title_text(), "Step 1: Choose Layout")
        self.assertEqual(figure._annot_undo_btn.text(), "Undo")

    def test_restored_chinese_language_constructs_wb_figure_in_chinese(self) -> None:
        window = self._make_window({"language": LANG_ZH_CN})

        self.assertEqual(window._language_combo.currentData(), LANG_ZH_CN)
        window._on_wb_plot_mode()

        figure = window._figure_mode_window
        self.assertEqual(figure._language, LANG_ZH_CN)
        self.assertEqual(figure._grp1.title_text(), "步骤 1：选择布局")
        self.assertEqual(figure._saved_templates_btn.text(), "已保存模板   ›")
        self.assertEqual(figure._annot_undo_btn.text(), "撤销")

    def test_language_switch_refreshes_all_created_tools_in_both_directions(self) -> None:
        window = self._make_window()
        table = ColumnTableWindow(samples=2, replicates=2, parent=window)
        window._mount_column_table_in_workspace(table)
        window._on_wb_plot_mode()

        window._language_combo.setCurrentIndex(
            window._language_combo.findData(LANG_ZH_CN)
        )

        self.assertEqual(table.windowTitle(), "列式数据表")
        self.assertEqual(window.param_panel._roi_group.title(), "ROI 设置")
        self.assertEqual(window.results_panel._title.text(), "定量结果")
        self.assertEqual(window._figure_mode_window._grp1.title_text(), "步骤 1：选择布局")
        self.assertEqual(window._figure_mode_window._annot_undo_btn.text(), "撤销")
        self.assertEqual(window._status_bar.currentMessage(), "已进入 WB Figure 排版。")
        self.assertEqual(window._files_toggle_btn.toolTip(), "展开已上传文件面板")

        window._language_combo.setCurrentIndex(
            window._language_combo.findData(LANG_EN)
        )

        self.assertEqual(table.windowTitle(), "Column Table")
        self.assertEqual(window.param_panel._roi_group.title(), "ROI Settings")
        self.assertEqual(window.results_panel._title.text(), "Results")
        self.assertEqual(window._figure_mode_window._grp1.title_text(), "Step 1: Choose Layout")
        self.assertEqual(window._figure_mode_window._annot_undo_btn.text(), "Undo")
        self.assertEqual(window._status_bar.currentMessage(), "Opened WB Figure Layout workspace.")
        self.assertEqual(window._files_toggle_btn.toolTip(), "Expand Uploaded Files panel")

    def test_one_tool_translation_failure_does_not_block_other_tools(self) -> None:
        window = self._make_window()
        table = ColumnTableWindow(samples=1, replicates=1, parent=window)
        window._mount_column_table_in_workspace(table)
        window._on_wb_plot_mode()

        with patch.object(
            table,
            "set_language",
            side_effect=RuntimeError("simulated translation failure"),
        ):
            window._language_combo.setCurrentIndex(
                window._language_combo.findData(LANG_ZH_CN)
            )

        self.assertEqual(window.param_panel._roi_group.title(), "ROI 设置")
        self.assertEqual(window.results_panel._title.text(), "定量结果")
        self.assertEqual(window._figure_mode_window._grp1.title_text(), "步骤 1：选择布局")
        self.assertEqual(window._figure_mode_window._annot_undo_btn.text(), "撤销")


if __name__ == "__main__":
    unittest.main()
