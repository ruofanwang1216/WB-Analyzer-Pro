"""core/export_engine.py — PDF and PPTX export from a LayoutResult.

PDF export: uses QPdfWriter + QPainter.  All rendering is done here
independently of FigureCanvas (no scene reference needed).

PPTX export: uses python-pptx (OPTIONAL).  If the library is unavailable the
module still loads cleanly; only PPTX_AVAILABLE is set to False and callers
must check it before calling PPTXExporter.export().

Image cropping for PPTX is performed in Python (via QImage) BEFORE the image
is inserted into the presentation.  PowerPoint's crop tool is NOT used.
"""
from __future__ import annotations

import os
import tempfile
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image
from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import (
    QColor, QFont, QImage, QPainter, QPen, QPixmap,
)

from core.image_transform import (
    default_inverted_for_pil_image,
    image_array_to_uint16_luminance,
    image_transform_from_dict,
    transform_pixels_16_to_8,
)
from core.layout_engine import (
    EXPORT_DPI, LayoutItem, LayoutResult, SCREEN_DPI, SCREEN_SCALE,
    pt_to_emu, pt_to_px,
)

# ── Optional python-pptx dependency ──────────────────────────────────────────
try:
    from pptx import Presentation as _Presentation
    from pptx.util import Emu as _Emu, Pt as _Pt
    from pptx.enum.text import (
        MSO_AUTO_SIZE as _MSO_AUTO_SIZE,
        MSO_VERTICAL_ANCHOR as _MSO_VERTICAL_ANCHOR,
        PP_ALIGN as _PP_ALIGN,
    )
    from pptx.dml.color import RGBColor as _RGBColor
    PPTX_AVAILABLE = True
except ImportError:
    PPTX_AVAILABLE = False


# ── Shared image utilities ────────────────────────────────────────────────────

# PowerPoint and Qt use different text rasterizers. These constants bias the
# editable PPTX output toward the live FigureCanvas preview.
_PPTX_FONT_SCALE = ((SCREEN_DPI / 72.0) / SCREEN_SCALE) * 0.78
def _crop_qimage(
    image_path: str,
    crop_px: dict | None,
    image_transform: dict | None = None,
) -> QImage:
    """Load *image_path* and crop to *crop_px* (IMAGE_PX coordinates).

    Returns a null QImage if the file is missing or unreadable.
    The crop rect is applied in the source image's own pixel space.
    No coordinate-space conversion is performed here.
    """
    try:
        with Image.open(image_path) as img:
            default_inverted = default_inverted_for_pil_image(img, fallback=True)
            pixels = image_array_to_uint16_luminance(np.array(img))
    except Exception:
        return QImage()

    if crop_px:
        img_h, img_w = pixels.shape
        if img_w <= 0 or img_h <= 0:
            return QImage()
        x = max(0, min(img_w - 1, int(round(float(crop_px.get("x", 0.0))))))
        y = max(0, min(img_h - 1, int(round(float(crop_px.get("y", 0.0))))))
        crop_w = max(1, int(round(float(crop_px.get("w", img_w - x)))))
        crop_h = max(1, int(round(float(crop_px.get("h", img_h - y)))))
        right = max(x + 1, min(img_w, x + crop_w))
        bottom = max(y + 1, min(img_h, y + crop_h))
        pixels = pixels[y:bottom, x:right]
    if pixels.size == 0:
        return QImage()

    display = transform_pixels_16_to_8(
        pixels,
        image_transform_from_dict(
            image_transform,
            default_inverted=default_inverted,
        ),
    )
    display = np.ascontiguousarray(display)
    height, width = display.shape
    return QImage(
        display.data,
        width,
        height,
        display.strides[0],
        QImage.Format.Format_Grayscale8,
    ).copy()


def _crop_to_png_bytes(
    image_path: str,
    crop_px: dict | None,
    image_transform: dict | None = None,
) -> bytes | None:
    """Crop image and return PNG-encoded bytes for embedding in PPTX.

    Cropping is performed in Python (QImage.copy) before insertion.
    PowerPoint's native crop is NOT used.
    """
    img = _crop_qimage(image_path, crop_px, image_transform)
    if img.isNull():
        return None
    # Write to a temp file and read back as bytes
    fd, tmp = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        img.save(tmp, "PNG")
        with open(tmp, "rb") as f:
            return f.read()
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _qimage_to_png_bytes(image: QImage) -> bytes | None:
    if image.isNull():
        return None
    fd, tmp = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        image.save(tmp, "PNG")
        with open(tmp, "rb") as f:
            return f.read()
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _save_presentation_atomically(prs, output_path: str) -> None:  # type: ignore[no-untyped-def]
    """Save a presentation through a verified temp file before replacing target."""
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=f".{target.stem}-",
        suffix=".pptx",
        dir=str(target.parent),
    )
    os.close(fd)
    try:
        prs.save(tmp)
        if not zipfile.is_zipfile(tmp):
            raise RuntimeError("PPTX export produced an invalid PowerPoint package.")
        _Presentation(tmp)
        os.replace(tmp, target)
    finally:
        try:
            if Path(tmp).exists():
                os.unlink(tmp)
        except OSError:
            pass


# ── PDF exporter ──────────────────────────────────────────────────────────────

class PDFExporter:
    """Renders a LayoutResult to a PDF file using QPdfWriter + QPainter.

    All coordinates are converted from PT to EXPORT_DPI pixels via pt_to_px().
    """

    def export(self, layout: LayoutResult, output_path: str) -> None:
        from PySide6.QtGui import QPdfWriter, QPageSize
        from PySide6.QtCore import QSizeF, QMarginsF

        writer = QPdfWriter(output_path)
        writer.setResolution(int(EXPORT_DPI))

        # Set page size to match the canvas (PT → mm: 1 pt = 25.4/72 mm)
        w_mm = layout.canvas_width_pt * 25.4 / 72.0
        h_mm = layout.canvas_height_pt * 25.4 / 72.0
        writer.setPageSize(QPageSize(QSizeF(w_mm, h_mm), QPageSize.Unit.Millimeter))
        writer.setPageMargins(QMarginsF(0.0, 0.0, 0.0, 0.0))

        painter = QPainter(writer)
        try:
            self._render(painter, layout, EXPORT_DPI)
        finally:
            painter.end()

    def export_image(
        self,
        image: QImage,
        canvas_width_pt: float,
        canvas_height_pt: float,
        output_path: str,
    ) -> None:
        """Write an exact raster snapshot of the figure to a one-page PDF."""
        if image.isNull():
            raise RuntimeError("PDF export could not render the figure snapshot.")

        from PySide6.QtGui import QPdfWriter, QPageSize
        from PySide6.QtCore import QSizeF, QMarginsF

        writer = QPdfWriter(output_path)
        writer.setResolution(int(EXPORT_DPI))

        w_mm = canvas_width_pt * 25.4 / 72.0
        h_mm = canvas_height_pt * 25.4 / 72.0
        writer.setPageSize(QPageSize(QSizeF(w_mm, h_mm), QPageSize.Unit.Millimeter))
        writer.setPageMargins(QMarginsF(0.0, 0.0, 0.0, 0.0))

        painter = QPainter(writer)
        try:
            target = writer.pageLayout().paintRectPixels(writer.resolution())
            if target.width() <= 0 or target.height() <= 0:
                target = QRect(0, 0, max(1, int(pt_to_px(canvas_width_pt, EXPORT_DPI))),
                               max(1, int(pt_to_px(canvas_height_pt, EXPORT_DPI))))
            painter.drawImage(target, image)
        finally:
            painter.end()

    # ── Rendering ─────────────────────────────────────────────────────────

    def _render(self, painter: QPainter, layout: LayoutResult, dpi: float) -> None:
        for item in sorted(layout.items, key=lambda i: i.z_order):
            self._draw_item(painter, item, dpi)

    def _draw_item(self, painter: QPainter, item: LayoutItem, dpi: float) -> None:
        x = pt_to_px(item.x_pt, dpi)
        y = pt_to_px(item.y_pt, dpi)
        w = pt_to_px(item.w_pt, dpi)
        h = pt_to_px(item.h_pt, dpi)
        rect = QRectF(x, y, max(w, 1.0), max(h, 1.0))
        irect = QRect(int(x), int(y), max(int(w), 1), max(int(h), 1))

        if item.kind == "blot":
            self._draw_blot(painter, item, irect, dpi)

        elif item.kind in ("label", "mw", "title", "panel_letter", "table_cell"):
            self._draw_text(painter, item, rect)

        elif item.kind == "line":
            pen = QPen(QColor(item.line_color or "#AAAAAA"))
            pen.setWidthF(pt_to_px(item.line_width_pt, dpi))
            painter.setPen(pen)
            painter.drawLine(int(x), int(y), int(x + w), int(y + h))

        elif item.kind == "divider":
            pen = QPen(QColor("#BBBBBB"))
            pen.setWidthF(pt_to_px(0.3, dpi))
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawLine(int(x), int(y), int(x), int(y + h))

    def _draw_blot(
        self, painter: QPainter, item: LayoutItem, rect: QRect, dpi: float
    ) -> None:
        if item.image_path and Path(item.image_path).exists():
            img = _crop_qimage(item.image_path, item.image_crop_px, item.image_transform)
            if not img.isNull():
                img = img.scaled(
                    rect.width(), rect.height(),
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                painter.drawImage(rect, img)
                return
        # Placeholder — grey rectangle with dashed border
        painter.fillRect(rect, QColor("#D8D8D8"))
        pen = QPen(QColor("#AAAAAA"))
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setWidthF(pt_to_px(0.5, dpi))
        painter.setPen(pen)
        painter.drawRect(rect)

    def _draw_text(
        self, painter: QPainter, item: LayoutItem, rect: QRectF
    ) -> None:
        font = QFont(item.font_family)
        font.setPointSizeF(item.font_size_pt)
        font.setBold(item.bold)
        font.setItalic(item.italic)
        font.setUnderline(item.underline)
        painter.setFont(font)
        painter.setPen(QColor("#000000"))

        h_flag = {
            "center": Qt.AlignmentFlag.AlignHCenter,
            "right":  Qt.AlignmentFlag.AlignRight,
        }.get(item.align, Qt.AlignmentFlag.AlignLeft)
        flags = h_flag | Qt.AlignmentFlag.AlignVCenter
        if item.rotation:
            painter.save()
            try:
                center = rect.center()
                painter.translate(center)
                painter.rotate(item.rotation)
                painter.translate(-center)
                painter.drawText(rect, flags, item.text)
                painter.setOpacity(0.28)
                painter.translate(0.18, 0.0)
                painter.drawText(rect, flags, item.text)
            finally:
                painter.restore()
            return
        painter.drawText(rect, flags, item.text)


# ── PPTX exporter ─────────────────────────────────────────────────────────────

class PPTXExporter:
    """Renders a LayoutResult to a PPTX file.

    Each blot → add_picture() with Python-cropped image (no PowerPoint crop).
    Each label → add_textbox() (independently editable in PowerPoint).
    Each line  → add_connector().
    """

    def export(self, layout: LayoutResult, output_path: str) -> None:
        if not PPTX_AVAILABLE:
            raise RuntimeError(
                "python-pptx is not installed.  Run: pip install python-pptx"
            )

        prs = _Presentation()
        slide_w = pt_to_emu(layout.canvas_width_pt)
        slide_h = pt_to_emu(layout.canvas_height_pt)
        prs.slide_width = slide_w
        prs.slide_height = slide_h

        layout_obj = prs.slide_layouts[6]   # blank layout
        slide = prs.slides.add_slide(layout_obj)

        self._add_layout_items(slide, layout)

        _save_presentation_atomically(prs, output_path)

    def export_append_slide(self, layout: LayoutResult, existing_path: str) -> None:
        """Append a new slide to an existing PPTX file and save in place."""
        if not PPTX_AVAILABLE:
            raise RuntimeError(
                "python-pptx is not installed.  Run: pip install python-pptx"
            )

        prs = _Presentation(existing_path)
        layout_obj = prs.slide_layouts[6]   # blank layout
        slide = prs.slides.add_slide(layout_obj)

        self._add_layout_items(slide, layout)

        _save_presentation_atomically(prs, existing_path)

    def export_image(
        self,
        image: QImage,
        canvas_width_pt: float,
        canvas_height_pt: float,
        output_path: str,
    ) -> None:
        """Export an exact raster snapshot of the figure as a PPTX slide."""
        if not PPTX_AVAILABLE:
            raise RuntimeError(
                "python-pptx is not installed.  Run: pip install python-pptx"
            )
        png_bytes = _qimage_to_png_bytes(image)
        if not png_bytes:
            raise RuntimeError("PPTX export could not render the figure snapshot.")

        prs = _Presentation()
        prs.slide_width = pt_to_emu(canvas_width_pt)
        prs.slide_height = pt_to_emu(canvas_height_pt)
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        self._add_snapshot_picture(slide, png_bytes, canvas_width_pt, canvas_height_pt)
        _save_presentation_atomically(prs, output_path)

    def export_append_image(
        self,
        image: QImage,
        canvas_width_pt: float,
        canvas_height_pt: float,
        existing_path: str,
    ) -> None:
        """Append an exact raster snapshot of the figure to an existing PPTX."""
        if not PPTX_AVAILABLE:
            raise RuntimeError(
                "python-pptx is not installed.  Run: pip install python-pptx"
            )
        png_bytes = _qimage_to_png_bytes(image)
        if not png_bytes:
            raise RuntimeError("PPTX export could not render the figure snapshot.")

        prs = _Presentation(existing_path)
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        self._add_snapshot_picture(slide, png_bytes, canvas_width_pt, canvas_height_pt)
        _save_presentation_atomically(prs, existing_path)

    @staticmethod
    def _add_snapshot_picture(
        slide,
        png_bytes: bytes,
        canvas_width_pt: float,
        canvas_height_pt: float,
    ):
        import io
        return slide.shapes.add_picture(
            io.BytesIO(png_bytes),
            _Emu(0),
            _Emu(0),
            _Emu(pt_to_emu(canvas_width_pt)),
            _Emu(pt_to_emu(canvas_height_pt)),
        )

    # ── Item dispatch ──────────────────────────────────────────────────────

    def _add_layout_items(self, slide, layout: LayoutResult) -> None:  # type: ignore[type-arg]
        for item in sorted(layout.items, key=lambda i: i.z_order):
            self._add_item(slide, item)

    def _add_item(self, slide, item: LayoutItem):  # type: ignore[type-arg,no-untyped-def]
        left   = _Emu(pt_to_emu(item.x_pt))
        top    = _Emu(pt_to_emu(item.y_pt))
        width  = _Emu(pt_to_emu(item.w_pt))
        height = _Emu(pt_to_emu(item.h_pt))

        if item.kind == "blot":
            return self._add_blot(slide, item, left, top, width, height)

        elif item.kind in ("label", "mw", "title", "panel_letter", "table_cell"):
            return self._add_textbox(slide, item, left, top, width, height)

        elif item.kind == "line":
            return self._add_line(slide, item)

        # dividers are omitted from PPTX (visual noise at publication scale)
        return None

    def _add_blot(self, slide, item: LayoutItem, left, top, width, height) -> None:
        if item.image_path and Path(item.image_path).exists():
            png_bytes = _crop_to_png_bytes(
                item.image_path,
                item.image_crop_px,
                item.image_transform,
            )
            if png_bytes:
                import io
                buf = io.BytesIO(png_bytes)
                picture = slide.shapes.add_picture(buf, left, top, width, height)
                self._apply_blot_border(picture)
                return picture
        # Placeholder rectangle when no image is available
        from pptx.dml.color import RGBColor as _RGB2
        shape = slide.shapes.add_shape(
            1,  # MSO_SHAPE_TYPE.RECTANGLE
            left, top, width, height,
        )
        shape.fill.solid()
        shape.fill.fore_color.rgb = _RGB2(0xD0, 0xD0, 0xD0)
        self._apply_blot_border(shape)
        return shape

    @staticmethod
    def _apply_blot_border(shape) -> None:  # type: ignore[no-untyped-def]
        shape.line.color.rgb = _RGBColor(0x00, 0x00, 0x00)
        shape.line.width = _Pt(1.0)

    def _add_textbox(self, slide, item: LayoutItem, left, top, width, height) -> None:
        txb = slide.shapes.add_textbox(left, top, width, height)
        txb.rotation = item.rotation
        tf = txb.text_frame
        tf.word_wrap = False
        tf.auto_size = _MSO_AUTO_SIZE.NONE
        tf.vertical_anchor = _MSO_VERTICAL_ANCHOR.MIDDLE
        tf.margin_left = 0
        tf.margin_right = 0
        tf.margin_top = 0
        tf.margin_bottom = 0
        p = tf.paragraphs[0]
        p.space_before = _Pt(0)
        p.space_after = _Pt(0)
        p.line_spacing = 1.0

        # Alignment
        align_map = {
            "center": _PP_ALIGN.CENTER,
            "right":  _PP_ALIGN.RIGHT,
            "left":   _PP_ALIGN.LEFT,
        }
        p.alignment = align_map.get(item.align, _PP_ALIGN.LEFT)

        run = p.add_run()
        run.text = item.text
        run.font.name = item.font_family
        run.font.size = _Pt(item.font_size_pt * _PPTX_FONT_SCALE)
        run.font.bold = item.bold
        run.font.italic = item.italic
        run.font.underline = item.underline
        run.font.color.rgb = _RGBColor(0x00, 0x00, 0x00)
        return txb

    def _add_line(self, slide, item: LayoutItem) -> None:
        from pptx.util import Emu as _E2
        x1 = _E2(pt_to_emu(item.x_pt))
        y1 = _E2(pt_to_emu(item.y_pt))
        x2 = _E2(pt_to_emu(item.x_pt + item.w_pt))
        y2 = _E2(pt_to_emu(item.y_pt + item.h_pt))
        connector = slide.shapes.add_connector(1, x1, y1, x2, y2)
        color = QColor(item.line_color or "#AAAAAA")
        connector.line.color.rgb = _RGBColor(color.red(), color.green(), color.blue())
        connector.line.width = _Emu(pt_to_emu(item.line_width_pt))
        return connector
