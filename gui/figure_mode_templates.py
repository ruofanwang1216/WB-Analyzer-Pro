"""Step 1 sidebar and the saved figure-template library.

Mixed into FigureModeWindow (gui.figure_mode_window); methods rely on its attributes.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.figure_project import FigureProject
from core.layout_engine import LayoutEngine
from core.template_engine import TemplateEngine
from gui.figure_canvas import FigureCanvas
from gui.figure_mode_styles import _APPLY_BTN_STYLE, _SMALL_BTN_STYLE
from gui.figure_mode_widgets import (
    _CollapseGroup,
    _TemplateNameLabel,
    _TemplatePreviewCanvas,
)
from utils.i18n import tr


class TemplateLibraryMixin:
    """Step 1 sidebar and the saved figure-template library."""

    def _build_step1(self) -> _CollapseGroup:
        grp = _CollapseGroup("Step 1: Choose Layout", step_number=1)
        bl = grp.body_layout()
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)

        # Keep the existing list as the source of template selection state,
        # but host it only inside the Saved Templates browser dialog.
        self._template_list = QListWidget()
        self._template_list.setMinimumWidth(180)
        self._template_list.setStyleSheet(
            "QListWidget { background:#FFFFFF; border:1px solid #D1DDD7; "
            "border-radius:7px; font-size:13px; outline:none; }"
            "QListWidget::item { padding:4px 5px; border-bottom:1px solid #EEF3F0; }"
            "QListWidget::item:selected { background:#E5F1EB; color:#183B2B; }"
        )
        self._template_list.itemClicked.connect(self._on_template_list_clicked)
        self._template_list.currentItemChanged.connect(
            lambda current, _previous: self._refresh_template_browser_preview(current)
        )
        self._populate_template_list()
        self._template_list.setParent(self)
        self._template_list.hide()

        # Three plain actions, top to bottom: start a layout, add its
        # condition labels, or reuse a saved one.  No numbering or nested
        # cards, so the order reads at a glance.
        actions = QWidget()
        actions.setObjectName("step1Flow")
        actions.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        actions.setStyleSheet("QWidget#step1Flow { background:transparent; }")
        actions_layout = QVBoxLayout(actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(8)

        create_btn = QPushButton("New Blot Frames")
        create_btn.setObjectName("step1PrimaryButton")
        create_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        create_btn.setToolTip("Choose how many blot frames and lanes the figure has")
        create_btn.setStyleSheet(
            "QPushButton#step1PrimaryButton { background:#1F6B4A; color:#FFFFFF; "
            "border:1px solid #1F6B4A; border-radius:8px; font-size:13px; "
            "font-weight:600; padding:3px 8px; }"
            "QPushButton#step1PrimaryButton:hover { background:#134A32; "
            "border-color:#134A32; }"
            "QPushButton#step1PrimaryButton:pressed { background:#0E3A27; }"
        )
        create_btn.setFixedHeight(38)
        create_btn.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Fixed,
        )
        create_btn.clicked.connect(self._on_create_template)
        actions_layout.addWidget(create_btn)

        outline_style = (
            "QPushButton#{name} {{ background:#FFFFFF; color:#1F6B4A; "
            "border:1px solid #1F6B4A; border-radius:8px; font-size:13px; "
            "font-weight:600; padding:3px 8px; }}"
            "QPushButton#{name}:hover {{ background:#E3EFE8; }}"
            "QPushButton#{name}:pressed {{ background:#CFE3D8; }}"
        )

        create_condition_btn = QPushButton("Add Conditions")
        create_condition_btn.setObjectName("step1SecondaryButton")
        create_condition_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        create_condition_btn.setToolTip("Add the +/− condition labels above the lanes")
        create_condition_btn.setStyleSheet(outline_style.format(name="step1SecondaryButton"))
        create_condition_btn.setFixedHeight(38)
        create_condition_btn.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Fixed,
        )
        create_condition_btn.clicked.connect(self._on_create_condition_template)
        actions_layout.addWidget(create_condition_btn)

        plain_style = (
            "QPushButton#{name} {{ background:#FFFFFF; color:#1C2622; "
            "border:1px solid #C9D3CE; border-radius:8px; font-size:13px; "
            "padding:3px 10px; text-align:left; }}"
            "QPushButton#{name}:hover {{ background:#E3EFE8; border-color:#1F6B4A; }}"
        )

        add_blot_btn = QPushButton("+ Extra Frame")
        add_blot_btn.setObjectName("step1TextButton")
        add_blot_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_blot_btn.setStyleSheet(plain_style.format(name="step1TextButton"))
        add_blot_btn.setFixedHeight(34)
        add_blot_btn.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Fixed,
        )
        add_blot_btn.setToolTip(
            "Add a free-position Blot Frame. Select it, draw a WB ROI, then press Enter/Return to fill it."
        )
        add_blot_btn.clicked.connect(self._on_add_blot_frame)

        self._saved_templates_btn = QPushButton("Saved Templates   ›")
        self._saved_templates_btn.setObjectName("step1SavedTemplatesButton")
        self._saved_templates_btn.setFixedHeight(34)
        self._saved_templates_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._saved_templates_btn.setToolTip("Reuse a previous layout")
        self._saved_templates_btn.setStyleSheet(
            plain_style.format(name="step1SavedTemplatesButton")
        )
        self._saved_templates_btn.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Fixed,
        )
        self._saved_templates_btn.clicked.connect(self._show_saved_templates_dialog)

        divider = QFrame()
        divider.setObjectName("step1Divider")
        divider.setFixedHeight(1)
        divider.setStyleSheet("QFrame#step1Divider { background:#E4E9E6; border:none; }")
        actions_layout.addSpacing(2)
        actions_layout.addWidget(divider)
        actions_layout.addSpacing(2)
        actions_layout.addWidget(self._saved_templates_btn)
        actions_layout.addWidget(add_blot_btn)
        bl.addWidget(actions)

        # Hidden state controls keep structure values in sync with loaded or
        # applied projects.  The dialog uses temporary controls so closing it
        # never leaves this window holding deleted Qt children.
        self._panels_spin = self._make_structure_spin(1, 15, 1)
        self._blots_spin = self._make_structure_spin(1, 15, 2)
        self._lanes_spin = self._make_structure_spin(2, 12, 4)
        for spin in (self._panels_spin, self._blots_spin, self._lanes_spin):
            spin.setParent(self)
            spin.hide()

        return grp

    def _populate_template_list(self) -> None:
        self._template_list.clear()
        for tmpl in TemplateEngine.all_templates():
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, tmpl.id)
            item.setSizeHint(QSize(0, 23))
            self._template_list.addItem(item)
            self._install_template_list_item(item, tmpl)
        if self._template_list.count():
            self._template_list.setCurrentRow(0)

    def _install_template_list_item(self, item: QListWidgetItem, tmpl) -> None:
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(6, 0, 4, 0)
        row_layout.setSpacing(4)
        lbl = _TemplateNameLabel(
            self._localized_template_name(tmpl.display_name, tmpl.is_user_template), tmpl.id
        )
        lbl.setStyleSheet("font-size:12px; color:#1E2D3F;")
        if tmpl.is_user_template:
            lbl.setCursor(Qt.CursorShape.IBeamCursor)
            lbl.setToolTip("Double-click to rename this Figure Template")
            lbl.doubleClicked.connect(self._on_rename_template)
        row_layout.addWidget(lbl, 1)
        del_btn = QToolButton()
        del_btn.setText("×")
        del_btn.setFixedSize(20, 20)
        del_btn.setStyleSheet(
            "QToolButton { border:1px solid #9EB3A8; border-radius:8px; "
            "background:#F7FAF8; color:#6A7A72; font-size:12px; }"
            "QToolButton:hover { background:#F0D7D7; color:#8A3B3B; border-color:#C99595; }"
        )
        del_btn.setToolTip(f'Delete template "{tmpl.display_name}"')
        del_btn.clicked.connect(
            lambda _=False, tid=tmpl.id: self._on_delete_template(tid)
        )
        row_layout.addWidget(del_btn)
        self._template_list.setItemWidget(item, row_widget)

    def _on_template_list_clicked(self, item: QListWidgetItem) -> None:
        self._template_list.setCurrentItem(item)

    def _show_saved_templates_dialog(self) -> None:
        """Browse saved templates with a true FigureCanvas preview."""
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("Saved Templates", self._language))
        dialog.setModal(True)
        dialog.setFixedSize(680, 420)
        dialog.setStyleSheet(
            "QDialog { background:#F5F8F6; }"
            "QLabel#templateBrowserTitle { color:#26382F; font-size:12px; font-weight:700; }"
        )
        self._template_browser_dialog = dialog

        root = QVBoxLayout(dialog)
        root.setContentsMargins(10, 10, 10, 9)
        root.setSpacing(8)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(7)

        list_panel = QWidget()
        list_layout = QVBoxLayout(list_panel)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(5)
        list_title = QLabel(tr("Saved Templates", self._language))
        list_title.setObjectName("templateBrowserTitle")
        list_layout.addWidget(list_title)
        self._template_list.setParent(list_panel)
        self._template_list.show()
        list_layout.addWidget(self._template_list, 1)
        splitter.addWidget(list_panel)

        preview_panel = QWidget()
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(5)
        preview_title = QLabel(tr("Template Preview", self._language))
        preview_title.setObjectName("templateBrowserTitle")
        preview_layout.addWidget(preview_title)
        preview_canvas = _TemplatePreviewCanvas(preview_panel)
        preview_canvas.setInteractive(False)
        preview_canvas.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        preview_canvas.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        preview_canvas.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        preview_canvas.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_canvas.setStyleSheet(
            "QGraphicsView { background:#FFFFFF; border:1px solid #D1DDD7; border-radius:7px; }"
        )
        preview_layout.addWidget(preview_canvas, 1)
        splitter.addWidget(preview_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([200, 460])
        root.addWidget(splitter, 1)

        action_row = QHBoxLayout()
        action_row.addStretch(1)
        cancel_btn = QPushButton(tr("Cancel", self._language))
        cancel_btn.setStyleSheet(_SMALL_BTN_STYLE)
        cancel_btn.setFixedHeight(25)
        cancel_btn.clicked.connect(dialog.reject)
        action_row.addWidget(cancel_btn)
        apply_btn = QPushButton(tr("Apply", self._language))
        apply_btn.setFixedSize(86, 25)
        apply_btn.setStyleSheet(_APPLY_BTN_STYLE)
        apply_btn.clicked.connect(lambda: self._apply_template_from_browser(dialog))
        action_row.addWidget(apply_btn)
        root.addLayout(action_row)

        self._template_preview_canvas = preview_canvas
        self._refresh_template_browser_preview(self._template_list.currentItem())
        self._retranslate_widget_tree(dialog)
        QTimer.singleShot(0, lambda: self._fit_template_preview(preview_canvas))
        try:
            dialog.exec()
        finally:
            self._template_preview_canvas = None
            self._template_browser_dialog = None
            self._template_list.setParent(self)
            self._template_list.hide()

    def _apply_template_from_browser(self, dialog: QDialog) -> None:
        if self._template_list.currentItem() is None:
            return
        if self._on_apply_template():
            dialog.accept()

    def _refresh_template_browser_preview(
        self,
        item: QListWidgetItem | None,
    ) -> None:
        canvas = self._template_preview_canvas
        if canvas is None or item is None:
            return
        template_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if not template_id:
            return
        try:
            project, canvas_state, overlay_data, styles = (
                self._template_preview_payload(template_id)
            )
            canvas.clear_all()
            if canvas_state:
                canvas.restore_state_snapshot(canvas_state, repopulate_scene=False)
            elif overlay_data:
                canvas._restore_overlay_from_data(overlay_data)

            layout = LayoutEngine().compute(project)
            self._apply_text_styles_to_layout(layout, styles)
            hidden = canvas.hidden_text_keys()
            if hidden:
                layout.items = [
                    layout_item
                    for layout_item in layout.items
                    if (
                        layout_item.source_ref is None
                        or layout_item.source_ref.key() not in hidden
                    )
                ]
            canvas.render(layout, project)
            self._fit_template_preview(canvas)
            QTimer.singleShot(0, lambda: self._fit_template_preview(canvas))
        except Exception as exc:
            canvas.clear_all()
            canvas.setToolTip(f"Unable to preview this Figure Template: {exc}")

    @staticmethod
    def _fit_template_preview(canvas: FigureCanvas) -> None:
        if canvas._background_item is None:
            return
        # Fit the actual template rather than the surrounding white page.
        # The fixed fill ratio makes the preview large and stable while still
        # leaving breathing room around every condition and blot label.
        canvas.fit_frame_content_to_view(fill_ratio=0.76)

    def _template_preview_payload(
        self,
        template_id: str,
    ) -> tuple[FigureProject, dict, list[dict], dict[tuple, dict]]:
        if not TemplateEngine.is_builtin(template_id):
            project, overlay_data = TemplateEngine.restore_user_project(template_id)
            format_state = TemplateEngine.restore_user_template_format_state(template_id)
            return (
                project,
                dict(format_state.get("canvas_state") or {}),
                list(overlay_data or []),
                dict(format_state.get("text_style_overrides") or {}),
            )

        template = TemplateEngine.get_template(template_id)
        table_style = self._default_table_style_for_template(template_id)
        project = TemplateEngine.build_project(template_id)
        for panel in project.panels:
            panel.condition_table = TemplateEngine.make_condition_table(
                table_style,
                template.default_lane_count,
            )
        project.global_layout.show_condition_table = table_style != "none"
        return project, {}, [], {}

    def _on_delete_template(self, template_id: str) -> None:
        tmpl = TemplateEngine.get_template(template_id)
        action = (
            "Hide built-in Figure Template"
            if TemplateEngine.is_builtin(template_id)
            else "Delete Figure Template"
        )
        reply = QMessageBox.question(
            self, "Delete Figure Template",
            f'{action} "{tmpl.display_name}"?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if TemplateEngine.is_builtin(template_id):
            TemplateEngine.hide_builtin_template(template_id)
        else:
            TemplateEngine.delete_user_template(template_id)
        self._populate_template_list()

    def _on_rename_template(self, template_id: str) -> None:
        if TemplateEngine.is_builtin(template_id):
            return
        try:
            tmpl = TemplateEngine.get_template(template_id)
        except KeyError:
            return
        name, ok = QInputDialog.getText(
            self,
            "Rename Figure Template",
            "Figure Template name:",
            text=tmpl.display_name,
        )
        if not ok:
            return
        name = name.strip()
        if not name or name == tmpl.display_name:
            return
        try:
            TemplateEngine.rename_user_template(template_id, name)
        except Exception as exc:
            QMessageBox.critical(self, "Rename Figure Template", f"Failed:\n{exc}")
            return
        self._populate_template_list()
        self._select_template_id(template_id)

    def _select_template_id(self, template_id: str) -> None:
        for row in range(self._template_list.count()):
            item = self._template_list.item(row)
            if str(item.data(Qt.ItemDataRole.UserRole)) == template_id:
                self._template_list.setCurrentItem(item)
                return

    def _current_template_selection(self) -> tuple[str, str]:
        current = self._template_list.currentItem()
        if current is None:
            return "normal_wb", "none"
        tid = str(current.data(Qt.ItemDataRole.UserRole) or "normal_wb")
        if not TemplateEngine.is_builtin(tid):
            return tid, "none"
        return tid, self._default_table_style_for_template(tid)

    def _default_table_style_for_template(self, template_id: str) -> str:
        if not TemplateEngine.is_builtin(template_id):
            return "none"
        tmpl = TemplateEngine.get_template(template_id)
        if not tmpl.has_condition_table:
            return "none"
        if template_id == "dose_response":
            return "group_dose"
        if template_id == "ip_coip":
            return "ip_input"
        return "vector_matrix"
