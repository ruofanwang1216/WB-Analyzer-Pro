import unittest

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from gui.image_transform_dialog import ImageTransformDialog
from gui.param_panel import ParamPanel
from gui.results_panel import ResultsPanel
from gui.figure_mode_window import FigureModeWindow
from gui.figure_generation import ColumnSetupDialog, ColumnTableWindow, FigureTypeDialog
from core.image_transform import MAX_16BIT_VALUE, MAX_TONE_VALUE, MIN_TONE_VALUE
from utils.i18n import LANG_EN, LANG_ZH_CN, tr


class ChineseLocalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_western_blot_terms_use_academic_chinese(self) -> None:
        self.assertEqual(tr("Densitometry Figure Generation", LANG_ZH_CN), "灰度定量图生成")
        self.assertEqual(tr("Densitometry", LANG_ZH_CN), "定量分析")
        self.assertEqual(tr("Data Graphs", LANG_ZH_CN), "数据图表")
        self.assertEqual(tr("WB Figure Layout", LANG_ZH_CN), "WB Figure 排版")
        self.assertEqual(tr("Import Images", LANG_ZH_CN), "导入图像")
        self.assertEqual(tr("Lane Settings", LANG_ZH_CN), "泳道设置")
        self.assertEqual(tr("Band", LANG_ZH_CN), "条带")
        self.assertEqual(tr("ROI Settings", LANG_EN), "ROI Settings")

    def test_core_panels_refresh_in_place(self) -> None:
        params = ParamPanel()
        results = ResultsPanel()
        dialog = ImageTransformDialog()

        params.set_language(LANG_ZH_CN)
        results.set_language(LANG_ZH_CN)
        dialog.set_language(LANG_ZH_CN)

        self.assertEqual(params._roi_group.title(), "ROI 设置")
        self.assertEqual(params._lane_section_label.text(), "泳道设置")
        self.assertEqual(results._title.text(), "定量结果")
        self.assertEqual(dialog.windowTitle(), "图像显示调整")
        self.assertFalse(hasattr(dialog, "_note"))
        self.assertEqual(dialog._low_slider.minimum(), MIN_TONE_VALUE)
        self.assertEqual(dialog._low_slider.maximum(), MAX_16BIT_VALUE)
        self.assertEqual(dialog._low_slider.value(), 0)
        self.assertEqual(dialog._high_slider.minimum(), 0)
        self.assertEqual(dialog._high_slider.maximum(), MAX_TONE_VALUE)
        self.assertEqual(dialog._high_slider.value(), MAX_16BIT_VALUE)

    def test_wb_layout_workspace_translates_both_visible_sections(self) -> None:
        workspace = FigureModeWindow()
        workspace.set_language(LANG_ZH_CN)

        self.assertEqual(workspace._grp1._title_label.text(), "步骤 1：选择布局")
        self.assertEqual(workspace._grp4._title_label.text(), "步骤 2：填充印迹图框")
        self.assertEqual(workspace._annot_undo_btn.text(), "撤销")
        self.assertFalse(hasattr(workspace, "_rotation_label"))
        self.assertFalse(hasattr(workspace, "_text_rotation_spin"))
        self.assertEqual(workspace._export_pdf_btn.toolTip(), "将图形导出为 PDF")
        self.assertEqual(workspace._export_tiff_btn.toolTip(), "将图形导出为 TIFF")
        step1_labels = [label.text() for label in workspace._grp1.findChildren(QLabel)]
        self.assertIn("印迹图框", step1_labels)
        self.assertIn("印迹条件", step1_labels)
        self.assertIn("复用之前的布局", step1_labels)

        workspace.set_language(LANG_EN)
        self.assertEqual(workspace._grp1._title_label.text(), "Step 1: Choose Layout")
        self.assertEqual(workspace._grp4._title_label.text(), "Step 2: Fill Blot Frames")
        self.assertEqual(workspace._annot_undo_btn.text(), "Undo")
        self.assertEqual(workspace._export_pdf_btn.toolTip(), "Export Figure as PDF")
        self.assertEqual(workspace._export_tiff_btn.toolTip(), "Export Figure as TIFF")

    def test_wb_figure_can_be_constructed_directly_in_chinese(self) -> None:
        workspace = FigureModeWindow(language=LANG_ZH_CN)

        self.assertEqual(workspace._language, LANG_ZH_CN)
        self.assertEqual(workspace._grp1.title_text(), "步骤 1：选择布局")
        self.assertEqual(workspace._grp4.title_text(), "步骤 2：填充印迹图框")
        self.assertEqual(workspace._annot_undo_btn.text(), "撤销")
        self.assertEqual(workspace._fit_center_btn.text(), "适配并居中")

    def test_wb_figure_dialog_created_after_language_change_is_chinese(self) -> None:
        workspace = FigureModeWindow()
        workspace.set_language(LANG_ZH_CN)
        inspected: list[object] = []

        def inspect_dialog() -> None:
            dialog = self._app.activeModalWidget()
            inspected.extend([
                dialog.windowTitle(),
                [label.text() for label in dialog.findChildren(QLabel)],
                [button.text() for button in dialog.findChildren(QPushButton)],
            ])
            dialog.reject()

        QTimer.singleShot(0, inspect_dialog)
        workspace._on_create_template()

        self.assertEqual(inspected[0], "创建印迹图框模板")
        self.assertIn("创建新布局", inspected[1])
        self.assertIn("印迹图框数：", inspected[1])
        self.assertIn("应用图框", inspected[2])
        self.assertIn("取消", inspected[2])

    def test_densitometry_table_workflow_refreshes_in_place(self) -> None:
        chooser = FigureTypeDialog()
        setup = ColumnSetupDialog()
        table = ColumnTableWindow(samples=1, replicates=1)

        chooser.set_language(LANG_ZH_CN)
        setup.set_language(LANG_ZH_CN)
        table.set_language(LANG_ZH_CN)

        self.assertEqual(chooser.windowTitle(), "灰度定量图生成")
        self.assertEqual(setup.windowTitle(), "列式表设置")
        self.assertEqual(table.windowTitle(), "列式数据表")
        self.assertEqual(table._table.horizontalHeaderItem(0).text(), "重复")
        self.assertEqual(table._table.item(0, 1).text(), "目的蛋白条带")


if __name__ == "__main__":
    unittest.main()
