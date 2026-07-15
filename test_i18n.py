import unittest

from PySide6.QtWidgets import QApplication

from gui.image_transform_dialog import ImageTransformDialog
from gui.param_panel import ParamPanel
from gui.results_panel import ResultsPanel
from gui.figure_mode_window import FigureModeWindow
from gui.figure_generation import ColumnSetupDialog, ColumnTableWindow, FigureTypeDialog
from utils.i18n import LANG_EN, LANG_ZH_CN, tr


class ChineseLocalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_western_blot_terms_use_academic_chinese(self) -> None:
        self.assertEqual(tr("Densitometry Figure Generation", LANG_ZH_CN), "灰度定量图生成")
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

    def test_wb_layout_workspace_translates_both_visible_sections(self) -> None:
        workspace = FigureModeWindow()
        workspace.set_language(LANG_ZH_CN)

        self.assertEqual(workspace._grp1._title_label.text(), "图像布局")
        self.assertEqual(workspace._annot_undo_btn.text(), "撤销")
        self.assertEqual(workspace._rotation_label.text(), "旋转：")

        workspace.set_language(LANG_EN)
        self.assertEqual(workspace._grp1._title_label.text(), "Layout")
        self.assertEqual(workspace._annot_undo_btn.text(), "Undo")

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
