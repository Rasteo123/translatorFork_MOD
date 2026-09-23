# -*- coding: utf-8 -*-
"""SystemWindowsPage — рамки для системных окон в переведённых главах проекта.

Страница тонкая: поиск, применение и снятие оформления живут в
``gemini_translator.utils.system_windows`` и гоняются в потоке. Здесь только
таблица кандидатов с галочками, предпросмотр и настройки поиска и цветов.
"""

import os
import re
import tempfile
import traceback

from PyQt6 import QtGui, QtWidgets
from PyQt6.QtCore import Qt, QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from gemini_translator.ui import theme_manager
from gemini_translator.ui.shell import ShellPage
from gemini_translator.utils.qt_utils import deferred_column_autosize
from gemini_translator.utils.system_windows import (
    DEFAULT_TEMPLATES,
    DEFAULT_TRIGGERS,
    KIND_ORDER,
    SAMPLE_WINDOWS,
    DetectorSettings,
    apply_project,
    render_preview_document,
    render_window,
    scan_project,
    strip_project,
)

UI_STATE_KEY = "system_windows_ui"
_COLOR_COLUMNS = (("border", "Рамка"), ("background", "Фон"), ("text", "Текст"), ("accent", "Заголовок"))
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_PREVIEW_FILE = "translatorfork_system_windows_preview.html"
_SWATCH_SIZE = 16


class _Worker(QThread):
    progress = pyqtSignal(int)
    finished_work = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, job, *args, **kwargs):
        super().__init__()
        self._job = job
        self._args = args
        self._kwargs = kwargs

    def run(self):
        try:
            result = self._job(*self._args, progress=self._report, **self._kwargs)
        except Exception:
            self.error.emit(traceback.format_exc())
            return
        self.finished_work.emit(result)

    def _report(self, done, total, _label):
        self.progress.emit(int(done / total * 100) if total else 100)


class SystemWindowsPage(ShellPage):
    page_title = "Системные окна"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker = None
        self._scans = []
        self._build_ui()
        self._restore_ui_state()

    # --- жизненный цикл -----------------------------------------------------

    def can_leave(self) -> bool:
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "Подождите", "Сначала дождитесь завершения обработки.")
            return False
        return True

    # --- интерфейс ----------------------------------------------------------

    def _build_ui(self):
        main_layout = QVBoxLayout(self)

        project_group = QGroupBox("Проект перевода")
        project_layout = QHBoxLayout(project_group)
        self.project_edit = QLineEdit()
        self.project_edit.setPlaceholderText("Папка проекта с translation_map.json")
        browse_button = QPushButton("Обзор...")
        browse_button.clicked.connect(self._choose_project)
        self.scan_button = QPushButton("🔍 Найти окна")
        self.scan_button.clicked.connect(self.scan)
        project_layout.addWidget(self.project_edit, 1)
        project_layout.addWidget(browse_button)
        project_layout.addWidget(self.scan_button)
        main_layout.addWidget(project_group)

        settings_group = QGroupBox("Поиск")
        settings_layout = QVBoxLayout(settings_group)
        triggers_row = QHBoxLayout()
        triggers_row.addWidget(QLabel("Слова заголовков:"))
        self.triggers_edit = QLineEdit(", ".join(DEFAULT_TRIGGERS))
        self.triggers_edit.setToolTip(
            "Короткая строка без скобок считается заголовком окна, если содержит одно из этих слов.\n"
            "Строки в скобках [ ] и 【 】 считаются системными всегда."
        )
        triggers_row.addWidget(self.triggers_edit, 1)
        settings_layout.addLayout(triggers_row)
        options_row = QHBoxLayout()
        self.single_check = QCheckBox("Одиночные строки в скобках тоже оформлять")
        self.single_check.setChecked(True)
        options_row.addWidget(self.single_check)
        options_row.addWidget(QLabel("Не трогать строки (регулярное выражение):"))
        self.exclude_edit = QLineEdit()
        self.exclude_edit.setPlaceholderText(r"например прим\.\s*пер")
        options_row.addWidget(self.exclude_edit, 1)
        settings_layout.addLayout(options_row)
        main_layout.addWidget(settings_group)

        colors_group = QGroupBox("Цвета рамок")
        colors_layout = QVBoxLayout(colors_group)
        self.colors_table = QTableWidget(len(KIND_ORDER), 1 + len(_COLOR_COLUMNS))
        self.colors_table.setHorizontalHeaderLabels(["Тип"] + [title for _, title in _COLOR_COLUMNS])
        self.colors_table.verticalHeader().setVisible(False)
        self.colors_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for row, kind in enumerate(KIND_ORDER):
            template = DEFAULT_TEMPLATES[kind]
            label_item = QTableWidgetItem(template["label"])
            label_item.setFlags(label_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            label_item.setData(Qt.ItemDataRole.UserRole, kind)
            self.colors_table.setItem(row, 0, label_item)
            for column, (key, _title) in enumerate(_COLOR_COLUMNS, start=1):
                item = QTableWidgetItem(template[key])
                item.setToolTip("Двойной щелчок открывает палитру, можно вписать и #hex вручную.")
                self._update_swatch(item)
                self.colors_table.setItem(row, column, item)
        self.colors_table.setMaximumHeight(self.colors_table.rowHeight(0) * (len(KIND_ORDER) + 1) + 12)
        self.colors_table.itemChanged.connect(self._on_color_item_changed)
        self.colors_table.cellDoubleClicked.connect(self._pick_color)
        colors_layout.addWidget(self.colors_table)
        main_layout.addWidget(colors_group)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        table_widget = QWidget()
        table_layout = QVBoxLayout(table_widget)
        table_layout.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["", "Глава", "Тип", "Строк", "Текст"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(1, 240)
        self.table.setColumnWidth(3, 64)
        self.table.itemSelectionChanged.connect(self._refresh_preview)
        table_layout.addWidget(self.table, 1)
        check_row = QHBoxLayout()
        check_all = QPushButton("Отметить все")
        check_all.clicked.connect(lambda: self._set_all_checked(True))
        check_none = QPushButton("Снять отметки")
        check_none.clicked.connect(lambda: self._set_all_checked(False))
        check_row.addWidget(check_all)
        check_row.addWidget(check_none)
        check_row.addStretch(1)
        table_layout.addLayout(check_row)
        splitter.addWidget(table_widget)

        preview_widget = QWidget()
        preview_layout = QVBoxLayout(preview_widget)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        self.preview_label = QLabel("Образцы всех типов")
        preview_layout.addWidget(self.preview_label)
        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(False)
        preview_layout.addWidget(self.preview, 1)
        self.browser_button = QPushButton("🌐 Открыть в браузере")
        self.browser_button.setToolTip("Точный вид рамок в настоящем браузере: выбранное окно и образцы.")
        self.browser_button.clicked.connect(self.open_preview_in_browser)
        preview_layout.addWidget(self.browser_button)
        splitter.addWidget(preview_widget)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        main_layout.addWidget(splitter, 1)

        actions = QHBoxLayout()
        self.apply_button = QPushButton("✔ Применить к отмеченным")
        self.apply_button.setMinimumHeight(40)
        self.apply_button.setStyleSheet(
            f"font-weight: bold; background-color: {theme_manager.color('accent')}; "
            f"color: {theme_manager.color('panel_bg')};"
        )
        self.apply_button.clicked.connect(self.apply_selected)
        self.strip_button = QPushButton("↩ Снять оформление во всех главах")
        self.strip_button.setMinimumHeight(40)
        self.strip_button.clicked.connect(self.strip_all)
        actions.addWidget(self.apply_button, 2)
        actions.addWidget(self.strip_button, 1)
        main_layout.addLayout(actions)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        main_layout.addWidget(self.progress_bar)

        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setFont(QtGui.QFont("Courier New", 10))
        self.log_output.setMaximumHeight(120)
        main_layout.addWidget(self.log_output)
        self._refresh_preview()

    # --- настройки ----------------------------------------------------------

    def detector_settings(self) -> DetectorSettings:
        triggers = tuple(part.strip() for part in self.triggers_edit.text().split(",") if part.strip())
        return DetectorSettings(
            triggers=triggers or DEFAULT_TRIGGERS,
            single_bracketed=self.single_check.isChecked(),
            exclude_pattern=self.exclude_edit.text().strip(),
        )

    def templates(self) -> dict:
        result = {}
        for row in range(self.colors_table.rowCount()):
            kind = self.colors_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            template = dict(DEFAULT_TEMPLATES[kind])
            for column, (key, _title) in enumerate(_COLOR_COLUMNS, start=1):
                item = self.colors_table.item(row, column)
                value = item.text().strip() if item else ""
                if _HEX_RE.match(value):
                    template[key] = value
            result[kind] = template
        return result

    @staticmethod
    def _settings_manager():
        app = QtWidgets.QApplication.instance()
        getter = getattr(app, "get_settings_manager", None)
        if callable(getter):
            return getter()
        return getattr(app, "settings_manager", None)

    def _restore_ui_state(self):
        manager = self._settings_manager()
        if manager is None:
            return
        try:
            state = (manager.load_settings() or {}).get(UI_STATE_KEY, {}) or {}
        except Exception:
            return
        if state.get("project"):
            self.project_edit.setText(str(state["project"]))
        if state.get("triggers"):
            self.triggers_edit.setText(str(state["triggers"]))
        if "single_bracketed" in state:
            self.single_check.setChecked(bool(state["single_bracketed"]))
        if state.get("exclude_pattern"):
            self.exclude_edit.setText(str(state["exclude_pattern"]))
        colors = state.get("colors") or {}
        for row in range(self.colors_table.rowCount()):
            kind = self.colors_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            for column, (key, _title) in enumerate(_COLOR_COLUMNS, start=1):
                value = (colors.get(kind) or {}).get(key)
                if isinstance(value, str) and _HEX_RE.match(value):
                    self.colors_table.item(row, column).setText(value)

    def _save_ui_state(self):
        manager = self._settings_manager()
        if manager is None:
            return
        colors = {
            kind: {key: template[key] for key, _title in _COLOR_COLUMNS}
            for kind, template in self.templates().items()
        }
        state = {
            "project": self.project_edit.text().strip(),
            "triggers": self.triggers_edit.text().strip(),
            "single_bracketed": self.single_check.isChecked(),
            "exclude_pattern": self.exclude_edit.text().strip(),
            "colors": colors,
        }
        try:
            manager.save_ui_state({UI_STATE_KEY: state})
        except Exception:
            pass

    # --- таблица ------------------------------------------------------------

    def set_scan_results(self, scans):
        self._scans = list(scans)
        self.table.setRowCount(0)
        self.table.blockSignals(True)
        with deferred_column_autosize(self.table):
            self._fill_rows()
        self.table.blockSignals(False)
        chapters_with_windows = sum(1 for scan in self._scans if scan.candidates)
        self._log(
            f"Глав просмотрено: {len(self._scans)}, с окнами: {chapters_with_windows}, "
            f"окон найдено: {self.table.rowCount()}."
        )
        self._refresh_preview()

    def _fill_rows(self):
        for scan_index, scan in enumerate(self._scans):
            for candidate_index, candidate in enumerate(scan.candidates):
                row = self.table.rowCount()
                self.table.insertRow(row)
                check_item = QTableWidgetItem()
                check_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                check_item.setCheckState(Qt.CheckState.Checked)
                check_item.setData(Qt.ItemDataRole.UserRole, (scan_index, candidate_index))
                self.table.setItem(row, 0, check_item)
                self.table.setItem(row, 1, QTableWidgetItem(scan.title))
                combo = QComboBox()
                for kind in KIND_ORDER:
                    combo.addItem(DEFAULT_TEMPLATES[kind]["label"], kind)
                combo.setCurrentIndex(max(combo.findData(candidate.kind), 0))
                combo.currentIndexChanged.connect(lambda _index, r=row: self._on_kind_changed(r))
                self.table.setCellWidget(row, 2, combo)
                self.table.setItem(row, 3, QTableWidgetItem(str(len(candidate.lines))))
                preview_text = " / ".join(candidate.lines[:2])
                if len(preview_text) > 160:
                    preview_text = preview_text[:157] + "…"
                self.table.setItem(row, 4, QTableWidgetItem(preview_text))

    def _row_candidate(self, row):
        item = self.table.item(row, 0)
        if item is None:
            return None, None
        scan_index, candidate_index = item.data(Qt.ItemDataRole.UserRole)
        scan = self._scans[scan_index]
        return scan, scan.candidates[candidate_index]

    def _on_kind_changed(self, row):
        _scan, candidate = self._row_candidate(row)
        combo = self.table.cellWidget(row, 2)
        if candidate is not None and combo is not None:
            candidate.kind = combo.currentData()
        self._refresh_preview()

    def _set_all_checked(self, checked: bool):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None:
                item.setCheckState(state)

    def selected_candidates(self):
        """[(путь файла главы, [кандидаты])] для отмеченных строк, в порядке книги."""
        grouped = {}
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is None or item.checkState() != Qt.CheckState.Checked:
                continue
            scan, candidate = self._row_candidate(row)
            combo = self.table.cellWidget(row, 2)
            if combo is not None:
                candidate.kind = combo.currentData()
            grouped.setdefault(scan.path, []).append(candidate)
        return list(grouped.items())

    # --- цвета и предпросмотр -------------------------------------------------

    def _update_swatch(self, item):
        value = item.text().strip()
        if not _HEX_RE.match(value):
            item.setIcon(QtGui.QIcon())
            return
        pixmap = QtGui.QPixmap(_SWATCH_SIZE, _SWATCH_SIZE)
        pixmap.fill(QtGui.QColor(value))
        item.setIcon(QtGui.QIcon(pixmap))

    def _on_color_item_changed(self, item):
        if item.column() == 0:
            return
        self.colors_table.blockSignals(True)
        try:
            self._update_swatch(item)
        finally:
            self.colors_table.blockSignals(False)
        self._refresh_preview()

    def _pick_color(self, row, column):
        """Палитра для ячейки цвета; столбец типа палитру не открывает."""
        if column == 0:
            return
        item = self.colors_table.item(row, column)
        if item is None:
            return
        initial = QtGui.QColor(item.text().strip())
        if not initial.isValid():
            initial = QtGui.QColor("#ffffff")
        color = QColorDialog.getColor(initial, self, "Цвет рамки")
        if color is not None and color.isValid():
            item.setText(color.name())

    def _selected_candidate(self):
        rows = {index.row() for index in self.table.selectedIndexes()}
        if not rows:
            return None, None
        row = min(rows)
        _scan, candidate = self._row_candidate(row)
        if candidate is None:
            return None, None
        combo = self.table.cellWidget(row, 2)
        kind = combo.currentData() if combo is not None else candidate.kind
        return candidate, kind

    @staticmethod
    def _approximate_block(block, template):
        """Рамка средствами rich text Qt: таблица с цветной полосой слева."""
        inner = block[block.index(">") + 1:block.rfind("</div>")]
        border = template["border"]
        return (
            '<table width="100%" cellspacing="0" cellpadding="0" style="margin-top:6px;">'
            f'<tr><td width="8" bgcolor="{border}"></td>'
            f'<td bgcolor="{template["background"]}" style="border:2px solid {border};">'
            f'<table width="100%" cellpadding="12"><tr><td align="center" style="color:{template["text"]};">'
            f"{inner}</td></tr></table></td></tr></table><p>&nbsp;</p>"
        )

    def _refresh_preview(self):
        templates = self.templates()
        candidate, kind = self._selected_candidate()
        if candidate is not None:
            shown = [(candidate.lines, kind)]
            self.preview_label.setText("Выбранное окно")
        else:
            shown = [(lines, sample_kind) for sample_kind, lines in SAMPLE_WINDOWS.items()]
            self.preview_label.setText("Образцы всех типов")
        parts = []
        for lines, block_kind in shown:
            template = templates.get(block_kind, DEFAULT_TEMPLATES[block_kind])
            parts.append(self._approximate_block(render_window(lines, block_kind, templates=templates), template))
        self.preview.setHtml(
            "".join(parts)
            + f'<p style="color:{theme_manager.color("text_muted")};font-size:11px;">'
            "Примерный вид. Точный вид даёт кнопка «Открыть в браузере».</p>"
        )

    def open_preview_in_browser(self):
        """Записать страницу с рамками во временный файл и открыть её в браузере."""
        candidate, kind = self._selected_candidate()
        extra = [(candidate.lines, kind)] if candidate is not None else None
        document = render_preview_document(self.templates(), extra=extra)
        path = os.path.join(tempfile.gettempdir(), _PREVIEW_FILE)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(document)
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        return path

    # --- действия -----------------------------------------------------------

    def _choose_project(self):
        folder = QFileDialog.getExistingDirectory(self, "Папка проекта перевода", self.project_edit.text().strip())
        if folder:
            self.project_edit.setText(folder)

    def _project_folder(self):
        folder = self.project_edit.text().strip()
        if not folder or not os.path.isfile(os.path.join(folder, "translation_map.json")):
            QMessageBox.warning(self, "Проект не найден", "Укажите папку проекта с файлом translation_map.json.")
            return None
        return folder

    def _start(self, job, *args, on_done, **kwargs):
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "Подождите", "Предыдущая операция ещё идёт.")
            return False
        self.progress_bar.setValue(0)
        self._set_busy(True)
        self.worker = _Worker(job, *args, **kwargs)
        self.worker.progress.connect(self.progress_bar.setValue)
        self.worker.finished_work.connect(lambda result: self._finish(on_done, result))
        self.worker.error.connect(self._on_error)
        self.worker.start()
        return True

    def _finish(self, on_done, result):
        self._set_busy(False)
        self.progress_bar.setValue(100)
        on_done(result)

    def _on_error(self, error_text):
        self._set_busy(False)
        self.progress_bar.setValue(0)
        self._log(error_text)
        QMessageBox.critical(self, "Ошибка", error_text)

    def _set_busy(self, busy: bool):
        for button in (self.scan_button, self.apply_button, self.strip_button):
            button.setEnabled(not busy)

    def scan(self):
        folder = self._project_folder()
        if folder is None:
            return
        try:
            settings = self.detector_settings()
            re.compile(settings.exclude_pattern)
        except re.error as exc:
            QMessageBox.warning(self, "Регулярное выражение", f"Не удалось разобрать исключение: {exc}")
            return
        self._save_ui_state()
        self._log("Ищу системные окна…")
        self._start(scan_project, folder, settings, on_done=self.set_scan_results)

    def apply_selected(self):
        selections = self.selected_candidates()
        if not selections:
            QMessageBox.warning(self, "Ничего не отмечено", "Отметьте окна, которые нужно оформить.")
            return
        self._save_ui_state()
        self._start(apply_project, selections, templates=self.templates(), on_done=self._on_applied)

    def _on_applied(self, result):
        chapters, windows = result
        self._log(f"Оформлено окон: {windows} в главах: {chapters}.")
        self.table.setRowCount(0)
        self._refresh_preview()
        QMessageBox.information(
            self, "Готово",
            f"Оформлено окон: {windows} в главах: {chapters}.\n"
            "Дальше: сборка EPUB и «EPUB → Rulate MD».",
        )

    def strip_all(self):
        folder = self._project_folder()
        if folder is None:
            return
        answer = QMessageBox.question(
            self, "Снять оформление",
            "Убрать рамки во всех главах проекта и вернуть исходные абзацы?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start(strip_project, folder, on_done=self._on_stripped)

    def _on_stripped(self, result):
        chapters, windows = result
        self._log(f"Снято оформление: окон {windows} в главах: {chapters}.")
        self.table.setRowCount(0)
        self._refresh_preview()
        QMessageBox.information(self, "Готово", f"Снято оформление: окон {windows} в главах: {chapters}.")

    def _log(self, message):
        self.log_output.appendPlainText(message)
