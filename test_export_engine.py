import unittest
import zipfile
from tempfile import TemporaryDirectory

from PySide6.QtGui import QColor, QImage

from core.export_engine import PDFExporter, PPTX_AVAILABLE, PPTXExporter
from core.figure_project import SourceRef
from core.layout_engine import EMU_PER_PT, LayoutItem, LayoutResult, SCREEN_DPI, SCREEN_SCALE


if PPTX_AVAILABLE:
    from pptx import Presentation


@unittest.skipUnless(PPTX_AVAILABLE, "python-pptx is not installed")
class PPTXExporterTests(unittest.TestCase):
    _expected_font_scale = ((SCREEN_DPI / 72.0) / SCREEN_SCALE) * 0.78

    def _layout(self, text: str = "HEK293T") -> LayoutResult:
        return LayoutResult(
            canvas_width_pt=456.0,
            canvas_height_pt=220.0,
            items=[
                LayoutItem(
                    kind="title",
                    x_pt=160.0,
                    y_pt=20.0,
                    w_pt=42.0,
                    h_pt=18.0,
                    text=text,
                    font_size_pt=16.0,
                    bold=True,
                    align="center",
                    z_order=2,
                ),
                LayoutItem(
                    kind="table_cell",
                    x_pt=40.0,
                    y_pt=50.0,
                    w_pt=48.0,
                    h_pt=17.0,
                    text="pcDNA-SMYD3",
                    font_size_pt=13.0,
                    bold=True,
                    align="right",
                    z_order=2,
                    source_ref=SourceRef(panel_idx=0, table_row=1, table_col=0, field="condition_cell"),
                ),
                LayoutItem(
                    kind="table_cell",
                    x_pt=210.0,
                    y_pt=56.0,
                    w_pt=18.0,
                    h_pt=30.0,
                    text="1000",
                    font_size_pt=13.0,
                    align="center",
                    z_order=2,
                    source_ref=SourceRef(panel_idx=0, table_row=1, table_col=1, field="condition_cell"),
                ),
                LayoutItem(
                    kind="blot",
                    x_pt=96.0,
                    y_pt=80.0,
                    w_pt=240.0,
                    h_pt=18.0,
                    z_order=1,
                ),
                LayoutItem(
                    kind="line",
                    x_pt=110.0,
                    y_pt=42.0,
                    w_pt=180.0,
                    h_pt=0.0,
                    line_color="#222222",
                    line_width_pt=1.0,
                    z_order=3,
                ),
            ],
        )

    def test_export_text_boxes_do_not_wrap(self) -> None:
        with TemporaryDirectory() as tmp:
            path = f"{tmp}/figure.pptx"

            PPTXExporter().export(self._layout(), path)

            prs = Presentation(path)
            text_shapes = [
                shape for shape in prs.slides[0].shapes
                if getattr(shape, "has_text_frame", False) and shape.text
            ]
            table_shapes = [
                shape for shape in prs.slides[0].shapes
                if getattr(shape, "has_table", False)
            ]
            self.assertEqual(len(text_shapes), 3)
            self.assertEqual(len(table_shapes), 0)
            self.assertTrue(all(shape.text_frame.word_wrap is False for shape in text_shapes))
            self.assertEqual([shape.text for shape in text_shapes], ["HEK293T", "pcDNA-SMYD3", "1000"])
            self.assertAlmostEqual(
                text_shapes[0].text_frame.paragraphs[0].runs[0].font.size.pt,
                16.0 * self._expected_font_scale,
                places=1,
            )
            self.assertEqual(
                text_shapes[0].top,
                int(round(20.0 * EMU_PER_PT)),
            )
            self.assertEqual(
                text_shapes[1].top,
                int(round(50.0 * EMU_PER_PT)),
            )
            self.assertEqual(
                text_shapes[2].top,
                int(round(56.0 * EMU_PER_PT)),
            )
            line_shape = next(
                shape for shape in prs.slides[0].shapes
                if hasattr(shape, "line")
                and str(getattr(shape.line.color, "rgb", "")) == "222222"
            )
            self.assertEqual(str(line_shape.line.color.rgb), "222222")
            blot_shape = prs.slides[0].shapes[0]
            self.assertEqual(str(blot_shape.line.color.rgb), "000000")

    def test_condition_cells_export_at_layout_item_positions(self) -> None:
        with TemporaryDirectory() as tmp:
            path = f"{tmp}/figure.pptx"

            PPTXExporter().export(self._layout(), path)

            prs = Presentation(path)
            self.assertEqual(
                sum(1 for shape in prs.slides[0].shapes if getattr(shape, "has_table", False)),
                0,
            )
            table_text_shapes = [
                shape for shape in prs.slides[0].shapes
                if getattr(shape, "has_text_frame", False)
                and shape.text in {"pcDNA-SMYD3", "1000"}
            ]
            self.assertEqual(len(table_text_shapes), 2)
            self.assertEqual(table_text_shapes[0].left, int(round(40.0 * EMU_PER_PT)))
            self.assertEqual(table_text_shapes[1].left, int(round(210.0 * EMU_PER_PT)))
            self.assertEqual(table_text_shapes[0].top, int(round(50.0 * EMU_PER_PT)))
            self.assertEqual(table_text_shapes[1].top, int(round(56.0 * EMU_PER_PT)))

    def test_append_slide_saves_valid_existing_pptx_atomically(self) -> None:
        with TemporaryDirectory() as tmp:
            path = f"{tmp}/existing.pptx"
            exporter = PPTXExporter()
            exporter.export(self._layout("Original"), path)

            exporter.export_append_slide(self._layout("Appended"), path)

            self.assertTrue(zipfile.is_zipfile(path))
            prs = Presentation(path)
            self.assertEqual(len(prs.slides), 2)
            slide_text = "\n".join(shape.text for shape in prs.slides[-1].shapes if getattr(shape, "has_text_frame", False))
            self.assertIn("Appended", slide_text)

    def test_export_image_writes_single_exact_snapshot_picture(self) -> None:
        with TemporaryDirectory() as tmp:
            path = f"{tmp}/snapshot.pptx"
            image = QImage(200, 100, QImage.Format.Format_ARGB32)
            image.fill(QColor("#FFFFFF"))

            PPTXExporter().export_image(image, 456.0, 220.0, path)

            prs = Presentation(path)
            self.assertEqual(prs.slide_width, int(round(456.0 * EMU_PER_PT)))
            self.assertEqual(prs.slide_height, int(round(220.0 * EMU_PER_PT)))
            shapes = list(prs.slides[0].shapes)
            self.assertEqual(len(shapes), 1)
            self.assertEqual(shapes[0].left, 0)
            self.assertEqual(shapes[0].top, 0)
            self.assertEqual(shapes[0].width, int(round(456.0 * EMU_PER_PT)))
            self.assertEqual(shapes[0].height, int(round(220.0 * EMU_PER_PT)))


class PDFExporterTests(unittest.TestCase):
    def test_export_image_writes_pdf_snapshot(self) -> None:
        with TemporaryDirectory() as tmp:
            path = f"{tmp}/snapshot.pdf"
            image = QImage(200, 100, QImage.Format.Format_ARGB32)
            image.fill(QColor("#FFFFFF"))

            PDFExporter().export_image(image, 456.0, 220.0, path)

            with open(path, "rb") as handle:
                self.assertEqual(handle.read(5), b"%PDF-")


if __name__ == "__main__":
    unittest.main()
