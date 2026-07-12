"""Western Blot plot generation dialog for PySide6."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import Qt, QRect, QSize, QPoint
from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QImage, QFont, QFontMetrics, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFontComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QGroupBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
)


# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class WBLaneGroup:
    """Represents a group of lanes in a Western blot."""
    name: str = ""
    lane_count: int = 2


@dataclass
class WBStrip:
    """Represents a single antibody strip (blot image + label)."""
    image_path: str = ""
    right_label: str = ""


@dataclass
class WBSection:
    """Represents a section (e.g., IP, Inputs) containing multiple strips."""
    left_label: str = ""
    strips: list = field(default_factory=list)


@dataclass
class WBPlotConfig:
    """Configuration for Western blot plot rendering."""
    title: str = "HeLa RNF113A"
    groups: list = field(default_factory=lambda: [WBLaneGroup("Group A", 2)])
    lane_labels: list = field(default_factory=list)
    lane_unit: str = "μg DNA/mL"
    sections: list = field(default_factory=lambda: [
        WBSection("IP", [WBStrip("", "IB: Protein A"), WBStrip("", "IB: Protein B")]),
        WBSection("Inputs", [WBStrip("", "IB: Control")]),
    ])
    font_family: str = "Arial"
    title_font_size: int = 11
    label_font_size: int = 10
    small_font_size: int = 9
    strip_height: int = 55
    section_gap: int = 10
    strip_gap: int = 3
    left_label_col_width: int = 48
    right_label_col_width: int = 180
    lane_width: int = 65
    header_row_height: int = 18
    dpi: int = 300


# ============================================================================
# WBPlotCanvas
# ============================================================================

class WBPlotCanvas(QWidget):
    """Custom widget that renders the Western blot figure."""

    def __init__(self, config: WBPlotConfig, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self._strip_pixmaps: dict[str, QPixmap | None] = {}
        self.setMinimumSize(700, 400)
        self.setStyleSheet("background-color: white;")

    def setConfig(self, config: WBPlotConfig) -> None:
        """Update the configuration and schedule a repaint."""
        self._config = config
        self.update()

    def load_strip_image(self, path: str) -> QPixmap | None:
        """Load an image file and cache it. Returns None on error."""
        if not path:
            return None
        if path in self._strip_pixmaps:
            return self._strip_pixmaps[path]
        try:
            pixmap = QPixmap(path)
            if pixmap.isNull():
                self._strip_pixmaps[path] = None
                return None
            self._strip_pixmaps[path] = pixmap
            return pixmap
        except Exception:
            self._strip_pixmaps[path] = None
            return None

    def sizeHint(self) -> QSize:
        """Return ideal size based on config."""
        cfg = self._config
        total_lanes = sum(g.lane_count for g in cfg.groups)
        all_strips = [s for sec in cfg.sections for s in sec.strips]
        num_sections = len(cfg.sections)

        plot_w = total_lanes * cfg.lane_width
        header_h = 4 * cfg.header_row_height + 8
        strip_h = len(all_strips) * cfg.strip_height + (num_sections - 1) * cfg.section_gap
        fig_w = cfg.left_label_col_width + plot_w + cfg.right_label_col_width + 16
        fig_h = header_h + strip_h + 16

        return QSize(fig_w, fig_h)

    def paintEvent(self, event) -> None:
        """Paint the entire Western blot figure."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Fill background
        painter.fillRect(self.rect(), QColor("white"))

        cfg = self._config

        # Calculate layout dimensions
        total_lanes = sum(g.lane_count for g in cfg.groups)
        all_strips = [s for sec in cfg.sections for s in sec.strips]
        all_sections = cfg.sections

        plot_w = total_lanes * cfg.lane_width
        origin_x = cfg.left_label_col_width
        header_h = 4 * cfg.header_row_height + 8
        origin_y = header_h

        # Ensure lane_labels is padded to match total_lanes
        lane_labels = list(cfg.lane_labels) + [""] * (total_lanes - len(cfg.lane_labels))

        # ====================================================================
        # DRAW TOP HEADERS
        # ====================================================================

        # Title row
        font = QFont(cfg.font_family)
        font.setPointSize(cfg.title_font_size)
        font.setBold(True)
        painter.setFont(font)
        title_rect = QRect(origin_x, 4, plot_w, cfg.header_row_height)
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignCenter, cfg.title)
        fm = QFontMetrics(font)
        title_bottom = 4 + fm.height() + 2

        # Underline title
        underline_y = title_bottom + 2
        painter.setPen(QPen(QColor("black"), 1))
        painter.drawLine(origin_x, underline_y, origin_x + plot_w, underline_y)

        # Group headers with brackets
        font.setBold(False)
        font.setPointSize(cfg.label_font_size)
        painter.setFont(font)
        group_row_bottom = underline_y + 10

        current_x = origin_x
        for group in cfg.groups:
            group_w = group.lane_count * cfg.lane_width
            if len(cfg.groups) > 1 or group.name:
                group_center_x = current_x + group_w // 2
                group_rect = QRect(
                    current_x, group_row_bottom,
                    group_w, cfg.header_row_height
                )
                painter.drawText(group_rect, Qt.AlignmentFlag.AlignCenter, group.name)

                # Draw bracket below group name
                bracket_y = group_row_bottom + cfg.header_row_height - 4
                painter.drawLine(current_x + 2, bracket_y, current_x + group_w - 2, bracket_y)
            current_x += group_w

        # Lane labels row
        lane_row_bottom = group_row_bottom + cfg.header_row_height + 4
        for i in range(total_lanes):
            lane_x = origin_x + i * cfg.lane_width + cfg.lane_width // 2
            lane_rect = QRect(
                origin_x + i * cfg.lane_width, lane_row_bottom - cfg.header_row_height,
                cfg.lane_width, cfg.header_row_height
            )
            painter.drawText(lane_rect, Qt.AlignmentFlag.AlignCenter, lane_labels[i])

        # Unit label (right-aligned)
        unit_rect = QRect(
            origin_x + plot_w + 4, lane_row_bottom - cfg.header_row_height,
            cfg.right_label_col_width - 8, cfg.header_row_height
        )
        painter.drawText(unit_rect, Qt.AlignmentFlag.AlignLeft, cfg.lane_unit)

        # Separator line below headers
        separator_y = origin_y - 2
        painter.drawLine(origin_x, separator_y, origin_x + plot_w, separator_y)

        # ====================================================================
        # DRAW SECTIONS AND STRIPS
        # ====================================================================

        current_y = origin_y
        for section_idx, section in enumerate(all_sections):
            section_top = current_y
            strip_positions = []

            # Draw all strips in this section
            for strip_idx, strip in enumerate(section.strips):
                strip_top = current_y
                strip_bottom = strip_top + cfg.strip_height
                strip_positions.append((strip_top, strip_bottom, strip))

                # Draw blot area
                blot_rect = QRect(origin_x, strip_top, plot_w, cfg.strip_height)

                # Try to load and draw image
                pixmap = self.load_strip_image(strip.image_path) if strip.image_path else None
                if pixmap:
                    # Draw scaled pixmap
                    scaled = pixmap.scaledToWidth(
                        plot_w, Qt.TransformationMode.SmoothTransformation
                    )
                    painter.drawPixmap(blot_rect.x(), blot_rect.y(), scaled)
                else:
                    # Draw gray placeholder
                    painter.fillRect(blot_rect, QColor("#E8E8E8"))
                    painter.setPen(QPen(QColor("#CCCCCC"), 1, Qt.PenStyle.DashLine))
                    painter.drawRect(blot_rect)

                # Draw right label
                font.setPointSize(cfg.label_font_size)
                painter.setFont(font)
                painter.setPen(QPen(QColor("black"), 1))
                label_x = origin_x + plot_w + 8
                label_y_center = strip_top + cfg.strip_height // 2
                label_rect = QRect(
                    label_x, label_y_center - cfg.header_row_height // 2,
                    cfg.right_label_col_width - 16, cfg.header_row_height
                )
                painter.drawText(label_rect, Qt.AlignmentFlag.AlignLeft, strip.right_label)

                # Move to next strip
                if strip_idx < len(section.strips) - 1:
                    current_y += cfg.strip_height + cfg.strip_gap
                else:
                    current_y += cfg.strip_height

            # Draw left section label (rotated 90° counter-clockwise)
            section_h = current_y - section_top
            label_center_y = section_top + section_h // 2
            label_center_x = cfg.left_label_col_width // 2

            font.setPointSize(cfg.label_font_size)
            painter.setFont(font)
            painter.setPen(QPen(QColor("black"), 1))

            painter.save()
            painter.translate(label_center_x, label_center_y)
            painter.rotate(-90)
            label_rect = QRect(
                -section_h // 2, -cfg.label_font_size - 2,
                section_h, cfg.label_font_size * 2
            )
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, section.left_label)
            painter.restore()

            # Draw bracket on left edge
            bracket_x = cfg.left_label_col_width - 4
            painter.setPen(QPen(QColor("black"), 1))
            painter.drawLine(bracket_x, section_top, bracket_x, current_y)
            painter.drawLine(bracket_x, section_top, bracket_x + 4, section_top)
            painter.drawLine(bracket_x, current_y, bracket_x + 4, current_y)

            # Section separator line
            if section_idx < len(all_sections) - 1:
                current_y += cfg.section_gap
                painter.drawLine(
                    origin_x, current_y - cfg.section_gap // 2,
                    origin_x + plot_w, current_y - cfg.section_gap // 2
                )

        painter.end()


# ============================================================================
# WBPlotGenerationDialog
# ============================================================================

class WBPlotGenerationDialog(QDialog):
    """Dialog for generating and configuring Western blot plots."""

    _BTN_STYLE = (
        "QPushButton {"
        "background-color: #E6EEF3; border: 1px solid #BACAD5; border-radius: 6px;"
        "color: #385161; padding: 4px 10px; font-size: 11px; font-weight: 600;}"
        "QPushButton:hover {background-color: #DCE7EE;}"
    )
    _BTN_GREEN_STYLE = (
        "QPushButton {"
        "background-color: #C2D3C8; border: 1px solid #9EB3A8; border-radius: 6px;"
        "color: #2C4A3D; padding: 4px 10px; font-size: 11px; font-weight: 600;}"
        "QPushButton:hover {background-color: #B4C8BB;}"
    )

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Western Blot Plot Generator")
        self.setModal(True)
        self.resize(1200, 600)

        self._config = WBPlotConfig()
        self._canvas = WBPlotCanvas(self._config)
        self._build_ui()

    def _build_ui(self) -> None:
        """Build the main UI."""
        root = QHBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        # Left config panel
        left_panel = self._build_left_panel()
        root.addWidget(left_panel, 0)

        # Right preview panel
        preview_scroll = QScrollArea()
        preview_scroll.setWidget(self._canvas)
        preview_scroll.setWidgetResizable(False)
        preview_scroll.setMinimumWidth(500)
        root.addWidget(preview_scroll, 1)

        # Bottom buttons
        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 10, 0, 0)
        button_row.setSpacing(8)
        button_row.addStretch(1)

        export_btn = QPushButton("Export PNG")
        export_btn.setStyleSheet(self._BTN_GREEN_STYLE)
        export_btn.clicked.connect(self._export_png)
        button_row.addWidget(export_btn)

        close_btn = QPushButton("Close")
        close_btn.setStyleSheet(self._BTN_STYLE)
        close_btn.clicked.connect(self.accept)
        button_row.addWidget(close_btn)

        root.addLayout(button_row)

    def _build_left_panel(self) -> QWidget:
        """Build the left configuration panel."""
        scroll = QScrollArea()
        scroll.setMinimumWidth(340)

        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(10)

        # Top Header section
        top_group = QGroupBox("Top Header")
        top_form = QFormLayout(top_group)

        self._title_edit = QLineEdit(self._config.title)
        self._title_edit.textChanged.connect(self._on_title_changed)
        top_form.addRow("Figure Title:", self._title_edit)

        self._unit_edit = QLineEdit(self._config.lane_unit)
        self._unit_edit.textChanged.connect(self._on_unit_changed)
        top_form.addRow("Lane Unit:", self._unit_edit)

        layout.addWidget(top_group)

        # Groups & Lanes section
        groups_group = QGroupBox("Groups & Lanes")
        groups_layout = QVBoxLayout(groups_group)

        # Groups table
        self._groups_table = QTableWidget()
        self._groups_table.setColumnCount(3)
        self._groups_table.setHorizontalHeaderLabels(["Group Name", "Lane Count", "Remove"])
        self._groups_table.horizontalHeader().setStretchLastSection(False)
        self._update_groups_table()
        groups_layout.addWidget(QLabel("Groups:"))
        groups_layout.addWidget(self._groups_table)

        add_group_btn = QPushButton("+ Add Group")
        add_group_btn.setStyleSheet(self._BTN_STYLE)
        add_group_btn.clicked.connect(self._add_group)
        groups_layout.addWidget(add_group_btn)

        # Lane labels
        groups_layout.addWidget(QLabel("Lane Labels (one per lane):"))
        self._lane_labels_layout = QHBoxLayout()
        self._update_lane_labels_inputs()
        lane_scroll = QScrollArea()
        lane_widget = QWidget()
        lane_widget.setLayout(self._lane_labels_layout)
        lane_scroll.setWidget(lane_widget)
        lane_scroll.setMaximumHeight(60)
        groups_layout.addWidget(lane_scroll)

        layout.addWidget(groups_group)

        # Sections & Strips section
        sections_group = QGroupBox("Sections & Strips")
        sections_layout = QVBoxLayout(sections_group)

        self._sections_scroll = QScrollArea()
        self._sections_widget = QWidget()
        self._sections_content_layout = QVBoxLayout(self._sections_widget)
        self._sections_content_layout.setContentsMargins(0, 0, 0, 0)
        self._sections_content_layout.setSpacing(6)
        self._update_sections_ui()
        self._sections_scroll.setWidget(self._sections_widget)
        sections_layout.addWidget(self._sections_scroll)

        add_section_btn = QPushButton("+ Add Section")
        add_section_btn.setStyleSheet(self._BTN_STYLE)
        add_section_btn.clicked.connect(self._add_section)
        sections_layout.addWidget(add_section_btn)

        layout.addWidget(sections_group)

        # Appearance section
        appear_group = QGroupBox("Appearance")
        appear_form = QFormLayout(appear_group)

        self._font_combo = QFontComboBox()
        self._font_combo.setCurrentFont(QFont(self._config.font_family))
        self._font_combo.currentFontChanged.connect(self._on_font_changed)
        appear_form.addRow("Font Family:", self._font_combo)

        self._strip_height_spin = QSpinBox()
        self._strip_height_spin.setRange(20, 200)
        self._strip_height_spin.setValue(self._config.strip_height)
        self._strip_height_spin.valueChanged.connect(self._on_strip_height_changed)
        appear_form.addRow("Strip Height:", self._strip_height_spin)

        self._lane_width_spin = QSpinBox()
        self._lane_width_spin.setRange(30, 200)
        self._lane_width_spin.setValue(self._config.lane_width)
        self._lane_width_spin.valueChanged.connect(self._on_lane_width_changed)
        appear_form.addRow("Lane Width:", self._lane_width_spin)

        layout.addWidget(appear_group)
        layout.addStretch(1)

        scroll.setWidget(panel)
        scroll.setWidgetResizable(True)
        return scroll

    def _update_groups_table(self) -> None:
        """Populate the groups table."""
        self._groups_table.setRowCount(len(self._config.groups))
        for row, group in enumerate(self._config.groups):
            # Group name
            name_edit = QLineEdit(group.name)
            name_edit.textChanged.connect(
                lambda text, r=row: self._on_group_name_changed(r, text)
            )
            self._groups_table.setCellWidget(row, 0, name_edit)

            # Lane count
            count_spin = QSpinBox()
            count_spin.setRange(1, 10)
            count_spin.setValue(group.lane_count)
            count_spin.valueChanged.connect(
                lambda val, r=row: self._on_group_lane_count_changed(r, val)
            )
            self._groups_table.setCellWidget(row, 1, count_spin)

            # Remove button
            remove_btn = QPushButton("✕")
            remove_btn.setMaximumWidth(40)
            remove_btn.setStyleSheet(self._BTN_STYLE)
            remove_btn.clicked.connect(lambda checked, r=row: self._remove_group(r))
            self._groups_table.setCellWidget(row, 2, remove_btn)

    def _update_lane_labels_inputs(self) -> None:
        """Create input fields for each lane."""
        # Clear previous
        while self._lane_labels_layout.count():
            self._lane_labels_layout.takeAt(0).widget().deleteLater()

        total_lanes = sum(g.lane_count for g in self._config.groups)
        for i in range(total_lanes):
            edit = QLineEdit()
            if i < len(self._config.lane_labels):
                edit.setText(self._config.lane_labels[i])
            edit.setMaximumWidth(60)
            edit.textChanged.connect(
                lambda text, idx=i: self._on_lane_label_changed(idx, text)
            )
            self._lane_labels_layout.addWidget(edit)
        self._lane_labels_layout.addStretch(1)

    def _update_sections_ui(self) -> None:
        """Build the sections and strips UI."""
        # Clear previous
        while self._sections_content_layout.count():
            self._sections_content_layout.takeAt(0).widget().deleteLater()

        for sec_idx, section in enumerate(self._config.sections):
            section_box = QGroupBox(f"Section: {section.left_label or '(unnamed)'}")
            section_layout = QVBoxLayout(section_box)

            # Section header row
            header_layout = QHBoxLayout()
            label_edit = QLineEdit(section.left_label)
            label_edit.setPlaceholderText("Section label (e.g., IP, Inputs)")
            label_edit.textChanged.connect(
                lambda text, s=sec_idx: self._on_section_label_changed(s, text)
            )
            header_layout.addWidget(QLabel("Label:"))
            header_layout.addWidget(label_edit)

            add_strip_btn = QPushButton("+ Strip")
            add_strip_btn.setStyleSheet(self._BTN_STYLE)
            add_strip_btn.clicked.connect(lambda checked, s=sec_idx: self._add_strip(s))
            header_layout.addWidget(add_strip_btn)

            remove_sec_btn = QPushButton("✕ Remove Section")
            remove_sec_btn.setStyleSheet(self._BTN_STYLE)
            remove_sec_btn.clicked.connect(lambda checked, s=sec_idx: self._remove_section(s))
            header_layout.addWidget(remove_sec_btn)

            header_layout.addStretch(1)
            section_layout.addLayout(header_layout)

            # Strips in this section
            for strip_idx, strip in enumerate(section.strips):
                strip_layout = QHBoxLayout()

                # Load image button
                image_filename = Path(strip.image_path).name if strip.image_path else "No image"
                load_btn = QPushButton(f"📁 {image_filename}")
                load_btn.setStyleSheet(self._BTN_STYLE)
                load_btn.setMaximumWidth(180)
                load_btn.clicked.connect(
                    lambda checked, s=sec_idx, st=strip_idx: self._load_strip_image(s, st)
                )
                strip_layout.addWidget(load_btn)

                # Right label
                label_edit = QLineEdit(strip.right_label)
                label_edit.setPlaceholderText("e.g., IB: RNF113A K20me3")
                label_edit.textChanged.connect(
                    lambda text, s=sec_idx, st=strip_idx: self._on_strip_label_changed(s, st, text)
                )
                strip_layout.addWidget(label_edit)

                # Remove strip button
                remove_btn = QPushButton("✕")
                remove_btn.setMaximumWidth(40)
                remove_btn.setStyleSheet(self._BTN_STYLE)
                remove_btn.clicked.connect(
                    lambda checked, s=sec_idx, st=strip_idx: self._remove_strip(s, st)
                )
                strip_layout.addWidget(remove_btn)

                section_layout.addLayout(strip_layout)

            self._sections_content_layout.addWidget(section_box)

        self._sections_content_layout.addStretch(1)

    def _on_title_changed(self, text: str) -> None:
        self._config.title = text
        self._canvas.update()

    def _on_unit_changed(self, text: str) -> None:
        self._config.lane_unit = text
        self._canvas.update()

    def _on_group_name_changed(self, row: int, text: str) -> None:
        if row < len(self._config.groups):
            self._config.groups[row].name = text
            self._canvas.update()

    def _on_group_lane_count_changed(self, row: int, value: int) -> None:
        if row < len(self._config.groups):
            self._config.groups[row].lane_count = value
            self._update_lane_labels_inputs()
            self._canvas.update()

    def _on_lane_label_changed(self, idx: int, text: str) -> None:
        while len(self._config.lane_labels) <= idx:
            self._config.lane_labels.append("")
        self._config.lane_labels[idx] = text
        self._canvas.update()

    def _on_section_label_changed(self, sec_idx: int, text: str) -> None:
        if sec_idx < len(self._config.sections):
            self._config.sections[sec_idx].left_label = text
            self._canvas.update()

    def _on_strip_label_changed(self, sec_idx: int, strip_idx: int, text: str) -> None:
        if sec_idx < len(self._config.sections):
            if strip_idx < len(self._config.sections[sec_idx].strips):
                self._config.sections[sec_idx].strips[strip_idx].right_label = text
                self._canvas.update()

    def _on_font_changed(self, font) -> None:
        self._config.font_family = font.family()
        self._canvas.update()

    def _on_strip_height_changed(self, value: int) -> None:
        self._config.strip_height = value
        self._canvas.update()

    def _on_lane_width_changed(self, value: int) -> None:
        self._config.lane_width = value
        self._canvas.update()

    def _add_group(self) -> None:
        self._config.groups.append(WBLaneGroup(f"Group {len(self._config.groups) + 1}", 2))
        self._update_groups_table()
        self._update_lane_labels_inputs()
        self._canvas.update()

    def _remove_group(self, row: int) -> None:
        if row < len(self._config.groups):
            del self._config.groups[row]
            self._update_groups_table()
            self._update_lane_labels_inputs()
            self._canvas.update()

    def _add_section(self) -> None:
        self._config.sections.append(WBSection(f"Section {len(self._config.sections) + 1}", [
            WBStrip("", "IB: Protein")
        ]))
        self._update_sections_ui()
        self._canvas.update()

    def _remove_section(self, sec_idx: int) -> None:
        if sec_idx < len(self._config.sections):
            del self._config.sections[sec_idx]
            self._update_sections_ui()
            self._canvas.update()

    def _add_strip(self, sec_idx: int) -> None:
        if sec_idx < len(self._config.sections):
            self._config.sections[sec_idx].strips.append(WBStrip("", "IB: Protein"))
            self._update_sections_ui()
            self._canvas.update()

    def _remove_strip(self, sec_idx: int, strip_idx: int) -> None:
        if sec_idx < len(self._config.sections):
            if strip_idx < len(self._config.sections[sec_idx].strips):
                del self._config.sections[sec_idx].strips[strip_idx]
                self._update_sections_ui()
                self._canvas.update()

    def _load_strip_image(self, sec_idx: int, strip_idx: int) -> None:
        """Open a file dialog to load a strip image."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Strip Image", "",
            "Image Files (*.png *.jpg *.jpeg *.bmp *.tiff);;All Files (*)"
        )
        if path and sec_idx < len(self._config.sections):
            if strip_idx < len(self._config.sections[sec_idx].strips):
                self._config.sections[sec_idx].strips[strip_idx].image_path = path
                self._update_sections_ui()
                self._canvas.update()

    def _export_png(self) -> None:
        """Export the current figure to a high-resolution PNG."""
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Western Blot Plot", "", "PNG Image (*.png)"
        )
        if not path:
            return

        try:
            scale = 3  # 3x for ~300 DPI
            size = self._canvas.sizeHint() * scale
            img = QImage(size, QImage.Format.Format_ARGB32)
            img.fill(QColor("white"))

            painter = QPainter(img)
            painter.scale(scale, scale)
            self._canvas.render(painter)
            painter.end()

            if img.save(path, "PNG"):
                QMessageBox.information(self, "Export Successful", f"Plot saved to:\n{path}")
            else:
                QMessageBox.warning(self, "Export Failed", "Could not save the image.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Export error:\n{str(e)}")
