# -*- coding: utf-8 -*-
"""SystemWindowsPage — рамки для системных окон в переведённых главах проекта.

Страница тонкая: поиск, применение и снятие оформления живут в
``gemini_translator.utils.system_windows`` и гоняются в потоке. Здесь
композиция: шапка с проектом и главным действием, свёрнутые настройки,
панель фильтров, таблица кандидатов с предпросмотром и футер с действиями.
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
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from gemini_translator.ui import theme_manager
from gemini_translator.ui.shell import ShellPage
from gemini_translator.ui.widgets.common_widgets import NoScrollComboBox
from gemini_translator.utils.qt_utils import deferred_column_autosize
from gemini_translator.utils.system_windows import (
    DEFAULT_EXCLUDE,
    DEFAULT_TEMPLATES,
    DEFAULT_TRIGGERS,
    KIND_ORDER,
    SAMPLE_WINDOWS,
    DetectorSettings,
    apply_project,
    find_source_epub,
    render_preview_document,
    render_window,
    scan_project,
    strip_project,
)

UI_STATE_KEY = "system_windows_ui"
_ORIGIN_LABELS = {"brackets": "скобки", "quotes": "кавычки", "pairs": "пары", "source": "исходник"}
_COLOR_COLUMNS = (("border", "Рамка"), ("background", "Фон"), ("text", "Текст"), ("accent", "Заголовок"))
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_PREVIEW_FILE = "translatorfork_system_windows_preview.html"
_SWATCH_SIZE = 16
_PAGE_MARGIN = 16
_BLOCK_GAP = 12
_ROW_GAP = 8


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


class _Disclosure(QWidget):
    """Раскрывающийся раздел: заголовок со стрелкой и тело, свёрнутое по умолчанию."""

    toggled = pyqtSignal(bool)
    _CLOSED = "\u25b8"
    _OPEN = "\u25be"

    def __init__(self, title: str, body: QWidget, parent=None):
        super().__init__(parent)
        self.body = body
        self._title = title
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(_ROW_GAP)
        self.button = QPushButton()
        self.button.setCheckable(True)
        self.button.setFlat(True)
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.setStyleSheet(
            "QPushButton { text-align: left; border: none; background: transparent; padding: 4px 6px;"
            f" color: {theme_manager.color('text_primary')}; font-weight: 600; }}"
            f"QPushButton:hover {{ color: {theme_manager.color('accent')}; }}"
        )
        self.button.toggled.connect(self._on_toggled)
        self._on_toggled(False)
        layout.addWidget(self.button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.body)
        self.body.setVisible(False)

    def is_open(self) -> bool:
        return self.button.isChecked()

    def set_open(self, flag: bool) -> None:
        self.button.setChecked(bool(flag))

    def _on_toggled(self, checked: bool) -> None:
        self.body.setVisible(checked)
        self.button.setText(f"{self._OPEN if checked else self._CLOSED}  {self._title}")
        self.toggled.emit(checked)


class SystemWindowsPage(ShellPage):
    page_title = "Системные окна"
    preferred_window_size = (1200, 840)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker = None
        self._scans = []
        self._build_ui()
        self._restore_ui_state()
        self._update_counter()
        self._refresh_preview()

    # --- жизненный цикл -----------------------------------------------------

    def can_leave(self) -> bool:
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "Подождите", "Сначала дождитесь завершения обработки.")
            return False
        return True

    # --- интерфейс ----------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(_PAGE_MARGIN, _PAGE_MARGIN, _PAGE_MARGIN, _PAGE_MARGIN)
        root.setSpacing(_BLOCK_GAP)

        root.addWidget(self._build_header())
        root.addWidget(self._build_settings_disclosure())
        root.addWidget(self._build_colors_disclosure())
        root.addLayout(self._build_toolbar())
        root.addWidget(self._build_workspace(), 1)
        root.addWidget(self._build_action_bar())
        root.addWidget(self._build_log_disclosure())

    def _build_header(self) -> QWidget:
        self.header_card = QFrame()
        self.header_card.setObjectName("projectHeaderCard")
        layout = QVBoxLayout(self.header_card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(_ROW_GAP)

        top_row = QHBoxLayout()
        top_row.setSpacing(_ROW_GAP)
        intro = QVBoxLayout()
        intro.setSpacing(2)
        eyebrow = QLabel("Оформление глав")
        eyebrow.setObjectName("sectionEyebrow")
        intro.addWidget(eyebrow)
        title = QLabel("Системные окна")
        title.setObjectName("heroTitle")
        intro.addWidget(title)
        subtitle = QLabel(
            "Найдите в переведённых главах системные сообщения, статусы и навыки, "
            "проверьте список и оформите их рамками. Дальше сборка EPUB и «EPUB → Rulate MD»."
        )
        subtitle.setObjectName("heroSubtitle")
        subtitle.setWordWrap(True)
        intro.addWidget(subtitle)
        top_row.addLayout(intro, 1)

        self.status_chip = QLabel()
        self.status_chip.setObjectName("statusChip")
        self.status_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top_row.addWidget(self.status_chip, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top_row)

        project_row = QHBoxLayout()
        project_row.setSpacing(_ROW_GAP)
        self.project_edit = QLineEdit()
        self.project_edit.setPlaceholderText("Папка проекта с файлом translation_map.json")
        self.project_edit.setClearButtonEnabled(True)
        self.project_edit.returnPressed.connect(lambda: self.scan())
        self.project_edit.textChanged.connect(self._on_project_changed)
        project_row.addWidget(self.project_edit, 1)
        browse_button = QPushButton("Обзор")
        browse_button.setObjectName("compactActionButton")
        browse_button.clicked.connect(self._choose_project)
        project_row.addWidget(browse_button)
        self.scan_button = QPushButton("Найти окна")
        self.scan_button.setObjectName("primaryActionButton")
        self.scan_button.setMinimumHeight(36)
        self.scan_button.setDefault(True)
        self.scan_button.clicked.connect(self.scan)
        project_row.addWidget(self.scan_button)
        layout.addLayout(project_row)

        source_row = QHBoxLayout()
        source_row.setSpacing(_ROW_GAP)
        self.source_check = QCheckBox("Сверять с исходником")
        self.source_check.setChecked(True)
        self.source_check.setToolTip(
            "Строки перевода напротив скобок оригинала считаются системными,\n"
            "даже если модель убрала скобки при переводе."
        )
        source_row.addWidget(self.source_check)
        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("Исходный EPUB (ищется в папке проекта)")
        self.source_edit.setClearButtonEnabled(True)
        source_row.addWidget(self.source_edit, 1)
        source_browse = QPushButton("Обзор")
        source_browse.setObjectName("compactActionButton")
        source_browse.clicked.connect(self._choose_source)
        source_row.addWidget(source_browse)
        layout.addLayout(source_row)
        self._set_status("Проект не выбран")
        return self.header_card

    def _build_settings_disclosure(self) -> QWidget:
        body = QFrame()
        body.setObjectName("statusSurface")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(_ROW_GAP)

        triggers_row = QHBoxLayout()
        triggers_row.setSpacing(_ROW_GAP)
        triggers_label = QLabel("Слова заголовков")
        triggers_label.setObjectName("helperLabel")
        triggers_row.addWidget(triggers_label)
        self.triggers_edit = QLineEdit(", ".join(DEFAULT_TRIGGERS))
        self.triggers_edit.setToolTip(
            "Короткая строка без скобок считается заголовком окна, если содержит одно из этих слов.\n"
            "Строки в скобках [ ] и 【 】 считаются системными всегда."
        )
        triggers_row.addWidget(self.triggers_edit, 1)
        layout.addLayout(triggers_row)

        options_row = QHBoxLayout()
        options_row.setSpacing(_ROW_GAP)
        self.single_check = QCheckBox("Оформлять одиночные строки в скобках")
        self.single_check.setChecked(True)
        options_row.addWidget(self.single_check)
        exclude_label = QLabel("Не трогать строки")
        exclude_label.setObjectName("helperLabel")
        options_row.addWidget(exclude_label)
        self.exclude_edit = QLineEdit(DEFAULT_EXCLUDE)
        self.exclude_edit.setToolTip(
            "Регулярное выражение. Подходящие строки никогда не оформляются, пустое поле снимает ограничение."
        )
        options_row.addWidget(self.exclude_edit, 1)
        layout.addLayout(options_row)

        self.settings_disclosure = _Disclosure("Настройки поиска", body)
        return self.settings_disclosure

    def _build_colors_disclosure(self) -> QWidget:
        body = QFrame()
        body.setObjectName("statusSurface")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(_ROW_GAP)
        hint = QLabel("Двойной щелчок по ячейке открывает палитру, значение можно вписать и как #hex.")
        hint.setObjectName("helperLabel")
        layout.addWidget(hint)
        self.colors_table = QTableWidget(len(KIND_ORDER), 1 + len(_COLOR_COLUMNS))
        self.colors_table.setHorizontalHeaderLabels(["Тип"] + [title for _, title in _COLOR_COLUMNS])
        self.colors_table.verticalHeader().setVisible(False)
        self.colors_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.colors_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        for row, kind in enumerate(KIND_ORDER):
            template = DEFAULT_TEMPLATES[kind]
            label_item = QTableWidgetItem(template["label"])
            label_item.setFlags(label_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            label_item.setData(Qt.ItemDataRole.UserRole, kind)
            self.colors_table.setItem(row, 0, label_item)
            for column, (key, _title) in enumerate(_COLOR_COLUMNS, start=1):
                item = QTableWidgetItem(template[key])
                item.setToolTip("Двойной щелчок открывает палитру.")
                self._update_swatch(item)
                self.colors_table.setItem(row, column, item)
        self.colors_table.setMaximumHeight(self.colors_table.rowHeight(0) * (len(KIND_ORDER) + 1) + 12)
        self.colors_table.itemChanged.connect(self._on_color_item_changed)
        self.colors_table.cellDoubleClicked.connect(self._pick_color)
        layout.addWidget(self.colors_table)
        self.colors_disclosure = _Disclosure("Цвета рамок", body)
        return self.colors_disclosure

    def _build_toolbar(self) -> QHBoxLayout:
        toolbar = QHBoxLayout()
        toolbar.setSpacing(_ROW_GAP)
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("keySearchField")
        self.search_edit.setPlaceholderText("Поиск по тексту окна или названию главы")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._apply_filter)
        toolbar.addWidget(self.search_edit, 2)
        self.kind_filter = QComboBox()
        self.kind_filter.addItem("Все типы", None)
        for kind in KIND_ORDER:
            self.kind_filter.addItem(DEFAULT_TEMPLATES[kind]["label"], kind)
        self.kind_filter.currentIndexChanged.connect(self._apply_filter)
        toolbar.addWidget(self.kind_filter)
        self.counter_label = QLabel()
        self.counter_label.setObjectName("helperLabel")
        toolbar.addWidget(self.counter_label)
        toolbar.addStretch(1)
        check_all = QPushButton("Отметить все")
        check_all.setObjectName("compactActionButton")
        check_all.setToolTip("Отметить строки, видимые при текущем фильтре.")
        check_all.clicked.connect(lambda: self._set_all_checked(True))
        toolbar.addWidget(check_all)
        check_none = QPushButton("Снять отметки")
        check_none.setObjectName("compactActionButton")
        check_none.setToolTip("Снять отметки со строк, видимых при текущем фильтре.")
        check_none.clicked.connect(lambda: self._set_all_checked(False))
        toolbar.addWidget(check_none)
        return toolbar

    def _build_workspace(self) -> QWidget:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        self.table_stack = QStackedWidget()
        self.empty_state = self._build_empty_state()
        self.table_stack.addWidget(self.empty_state)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["", "Глава", "Тип", "Строк", "Текст", "Как найдено"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setColumnWidth(1, 180)
        self.table.setColumnWidth(2, 150)
        self.table.setColumnWidth(3, 56)
        self.table.itemSelectionChanged.connect(self._refresh_preview)
        self.table.itemChanged.connect(self._on_table_item_changed)
        self.table_stack.addWidget(self.table)
        self.table_stack.setCurrentWidget(self.empty_state)
        splitter.addWidget(self.table_stack)

        preview_panel = QFrame()
        preview_panel.setObjectName("statusSurface")
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(14, 12, 14, 12)
        preview_layout.setSpacing(_ROW_GAP)
        self.preview_label = QLabel("Образцы всех типов")
        self.preview_label.setObjectName("sectionEyebrow")
        preview_layout.addWidget(self.preview_label)
        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(False)
        self.preview.setFrameShape(QFrame.Shape.NoFrame)
        preview_layout.addWidget(self.preview, 1)
        self.browser_button = QPushButton("Открыть в браузере")
        self.browser_button.setObjectName("ghostActionButton")
        self.browser_button.setToolTip("Точный вид рамок в настоящем браузере: выбранное окно и образцы.")
        self.browser_button.clicked.connect(self.open_preview_in_browser)
        preview_layout.addWidget(self.browser_button, 0, Qt.AlignmentFlag.AlignRight)
        splitter.addWidget(preview_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([3400, 1600])
        return splitter

    def _build_empty_state(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("statusSurface")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.addStretch(1)
        card = QWidget()
        card.setFixedWidth(460)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(6)
        title = QLabel("Окон пока нет")
        title.setObjectName("projectCardValue")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(title)
        text = QLabel(
            "Укажите папку проекта и нажмите «Найти окна». Здесь появится список серий "
            "системных строк: глава, тип рамки и первые строки, а справа их вид."
        )
        text.setObjectName("helperLabel")
        text.setWordWrap(True)
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(text)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(card)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(1)
        return frame

    def _build_action_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("actionBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(_ROW_GAP)
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar, 1)
        layout.addStretch(1)
        self.strip_button = QPushButton("Убрать рамки")
        self.strip_button.setObjectName("ghostActionButton")
        self.strip_button.setToolTip("Вернуть исходные абзацы во всех главах проекта.")
        self.strip_button.clicked.connect(self.strip_all)
        layout.addWidget(self.strip_button)
        self.apply_button = QPushButton("Применить к отмеченным")
        self.apply_button.setObjectName("primaryActionButton")
        self.apply_button.setMinimumHeight(36)
        self.apply_button.clicked.connect(self.apply_selected)
        layout.addWidget(self.apply_button)
        return bar

    def _build_log_disclosure(self) -> QWidget:
        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setFont(QtGui.QFont("Courier New", 10))
        self.log_output.setMaximumHeight(120)
        self.log_disclosure = _Disclosure("Журнал", self.log_output)
        return self.log_disclosure

    # --- состояние шапки ------------------------------------------------------

    def _set_status(self, text: str, tone: str = "") -> None:
        self.status_chip.setText(text)
        self.status_chip.setProperty("tone", tone)
        style = self.status_chip.style()
        style.unpolish(self.status_chip)
        style.polish(self.status_chip)

    def _on_project_changed(self, text: str) -> None:
        if not self._scans:
            self._set_status("Проект выбран" if text.strip() else "Проект не выбран")
        folder = text.strip()
        if folder and os.path.isdir(folder):
            found = find_source_epub(folder)
            self.source_edit.setText(found or "")

    def _choose_source(self):
        start = self.source_edit.text().strip() or self.project_edit.text().strip()
        path, _filter = QFileDialog.getOpenFileName(self, "Исходный EPUB", start, "EPUB (*.epub)")
        if path:
            self.source_edit.setText(path)

    def _source_epub(self):
        if not self.source_check.isChecked():
            return None
        path = self.source_edit.text().strip()
        return path if path and os.path.isfile(path) else None

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
        if "exclude_pattern" in state:
            self.exclude_edit.setText(str(state["exclude_pattern"]))
        colors = state.get("colors") or {}
        for row in range(self.colors_table.rowCount()):
            kind = self.colors_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            for column, (key, _title) in enumerate(_COLOR_COLUMNS, start=1):
                value = (colors.get(kind) or {}).get(key)
                if isinstance(value, str) and _HEX_RE.match(value):
                    self.colors_table.item(row, column).setText(value)
        self.settings_disclosure.set_open(bool(state.get("settings_open", False)))
        self.colors_disclosure.set_open(bool(state.get("colors_open", False)))
        if "use_source" in state:
            self.source_check.setChecked(bool(state["use_source"]))

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
            "settings_open": self.settings_disclosure.is_open(),
            "colors_open": self.colors_disclosure.is_open(),
            "use_source": self.source_check.isChecked(),
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
        total = self.table.rowCount()
        self._log(
            f"Глав просмотрено: {len(self._scans)}, с окнами: {chapters_with_windows}, "
            f"окон найдено: {total}."
        )
        if total:
            self._set_status(f"Найдено окон: {total} в главах: {chapters_with_windows}", "success")
            self.table_stack.setCurrentWidget(self.table)
        else:
            self._set_status("Окон не найдено" if self._scans else "Проект не выбран")
            self.table_stack.setCurrentWidget(self.empty_state)
        self._apply_filter()
        self._update_counter()
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
                title_item = QTableWidgetItem(scan.title)
                title_item.setToolTip(scan.title)
                self.table.setItem(row, 1, title_item)
                combo = NoScrollComboBox()
                combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
                for kind in KIND_ORDER:
                    combo.addItem(DEFAULT_TEMPLATES[kind]["label"], kind)
                combo.setCurrentIndex(max(combo.findData(candidate.kind), 0))
                combo.currentIndexChanged.connect(lambda _index, r=row: self._on_kind_changed(r))
                self.table.setCellWidget(row, 2, combo)
                count_item = QTableWidgetItem(str(len(candidate.lines)))
                count_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, 3, count_item)
                preview_text = " / ".join(candidate.lines[:2])
                if len(preview_text) > 160:
                    preview_text = preview_text[:157] + "…"
                text_item = QTableWidgetItem(preview_text)
                text_item.setToolTip("\n".join(candidate.lines))
                self.table.setItem(row, 4, text_item)
                origin_item = QTableWidgetItem(_ORIGIN_LABELS.get(candidate.origin, candidate.origin))
                origin_item.setToolTip(
                    "скобки: строки в скобках или с тире перед скобкой; кавычки: сообщение в «кавычках»;\n"
                    "пары: карточка «ключ: значение»; исходник: в оригинале строка в скобках, перевод их потерял."
                )
                self.table.setItem(row, 5, origin_item)

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
        self._apply_filter()
        self._refresh_preview()

    def _on_table_item_changed(self, item):
        if item.column() == 0:
            self._update_counter()

    def _apply_filter(self, *_args):
        wanted_kind = self.kind_filter.currentData()
        needle = self.search_edit.text().strip().lower()
        for row in range(self.table.rowCount()):
            combo = self.table.cellWidget(row, 2)
            kind = combo.currentData() if combo is not None else None
            haystack = " ".join(
                self.table.item(row, column).text() for column in (1, 4) if self.table.item(row, column)
            ).lower()
            visible = (wanted_kind is None or kind == wanted_kind) and (not needle or needle in haystack)
            self.table.setRowHidden(row, not visible)
        self._update_counter()

    def _update_counter(self):
        total = self.table.rowCount()
        checked = sum(
            1 for row in range(total)
            if self.table.item(row, 0) is not None and self.table.item(row, 0).checkState() == Qt.CheckState.Checked
        )
        visible = sum(1 for row in range(total) if not self.table.isRowHidden(row))
        text = f"Отмечено {checked} из {total}"
        if visible != total:
            text += f", показано {visible}"
        self.counter_label.setText(text)

    def _set_all_checked(self, checked: bool):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and not self.table.isRowHidden(row):
                item.setCheckState(state)
        self.table.blockSignals(False)
        self._update_counter()

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

    def _start(self, job, *args, on_done, busy_label, busy_button, **kwargs):
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "Подождите", "Предыдущая операция ещё идёт.")
            return False
        self.progress_bar.setValue(0)
        self._set_busy(True, busy_label, busy_button)
        self.worker = _Worker(job, *args, **kwargs)
        self.worker.progress.connect(self.progress_bar.setValue)
        self.worker.finished_work.connect(lambda result: self._finish(on_done, result))
        self.worker.error.connect(self._on_error)
        self.worker.start()
        return True

    def _finish(self, on_done, result):
        self._set_busy(False)
        on_done(result)

    def _on_error(self, error_text):
        self._set_busy(False)
        self._set_status("Ошибка", "danger")
        self._log(error_text)
        self.log_disclosure.set_open(True)
        QMessageBox.critical(self, "Ошибка", error_text)

    def _set_busy(self, busy: bool, label: str = "", button=None):
        self.progress_bar.setVisible(busy)
        for action in (self.scan_button, self.apply_button, self.strip_button):
            action.setEnabled(not busy)
        if busy:
            self._busy_button = (button, button.text()) if button is not None else None
            if button is not None:
                button.setText(label)
            self._set_status(label)
        elif getattr(self, "_busy_button", None):
            button, text = self._busy_button
            button.setText(text)
            self._busy_button = None

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
        source_epub = self._source_epub()
        self._log("Ищу системные окна…" + (" Сверяю с исходником." if source_epub else ""))
        self._start(
            scan_project, folder, settings, source_epub=source_epub,
            on_done=self.set_scan_results, busy_label="Ищу окна…", busy_button=self.scan_button,
        )

    def apply_selected(self):
        selections = self.selected_candidates()
        if not selections:
            QMessageBox.warning(self, "Ничего не отмечено", "Отметьте окна, которые нужно оформить.")
            return
        self._save_ui_state()
        self._start(
            apply_project, selections, templates=self.templates(),
            on_done=self._on_applied, busy_label="Применяю…", busy_button=self.apply_button,
        )

    def _on_applied(self, result):
        chapters, windows = result
        self._log(f"Оформлено окон: {windows} в главах: {chapters}.")
        self.set_scan_results([])
        self._set_status(f"Оформлено окон: {windows} в главах: {chapters}", "success")
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
            self, "Убрать рамки",
            "Убрать рамки во всех главах проекта и вернуть исходные абзацы?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start(
            strip_project, folder,
            on_done=self._on_stripped, busy_label="Убираю рамки…", busy_button=self.strip_button,
        )

    def _on_stripped(self, result):
        chapters, windows = result
        self._log(f"Убраны рамки: окон {windows} в главах: {chapters}.")
        self.set_scan_results([])
        self._set_status(f"Убраны рамки: окон {windows} в главах: {chapters}", "success")
        QMessageBox.information(self, "Готово", f"Убраны рамки: окон {windows} в главах: {chapters}.")

    def _log(self, message):
        self.log_output.appendPlainText(message)
