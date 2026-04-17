# -*- coding: utf-8 -*-

import bisect
import hashlib
import html
import json
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt
from PyQt6.QtGui import (
    QColor,
    QFont,
    QKeySequence,
    QShortcut,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextFormat,
)
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


BLOCK_RE = re.compile(
    r"<(?P<tag>p|h[1-6]|li|blockquote|pre)\b[^>]*>(?P<body>.*?)</(?P=tag)>",
    re.IGNORECASE | re.DOTALL,
)
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")


@dataclass
class SearchResult:
    target: str
    start: int
    end: int
    line: int
    preview: str


@dataclass
class ProblemSpot:
    title: str
    details: str
    target: str
    start: int = -1


@dataclass
class BlockPreview:
    index: int
    tag: str
    start: int
    end: int
    preview: str


def _hash_text(value: str) -> str:
    return hashlib.md5((value or "").encode("utf-8")).hexdigest()


def _hash_path(path: str) -> str:
    return hashlib.md5(os.path.abspath(path).encode("utf-8")).hexdigest()[:12]


def _safe_key(path: str) -> str:
    base_name = os.path.basename(path) or "chapter"
    base_name = re.sub(r"[^A-Za-z0-9._-]+", "_", base_name).strip("._") or "chapter"
    return f"{base_name}_{_hash_path(path)}"


def _read_text_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as file:
        return file.read()


def _atomic_write_text(path: str, content: str) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".chapter_editor_", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as temp_file:
            temp_file.write(content)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, path)
    except Exception:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        raise


def _visible_preview(raw_text: str, limit: int = 180) -> str:
    if not raw_text:
        return ""
    text = TAG_RE.sub(" ", raw_text)
    text = html.unescape(text)
    text = SPACE_RE.sub(" ", text).strip()
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


def _normalized_block_text(raw_text: str) -> str:
    return SPACE_RE.sub(" ", _visible_preview(raw_text, limit=1000)).strip().casefold()


def _build_line_starts(text: str) -> list[int]:
    starts = [0]
    for index, char in enumerate(text):
        if char == "\n":
            starts.append(index + 1)
    return starts


def _line_from_position(line_starts: list[int], position: int) -> int:
    if not line_starts:
        return 1
    return bisect.bisect_right(line_starts, max(0, position)) or 1


def _extract_blocks(text: str) -> list[BlockPreview]:
    blocks = []
    for index, match in enumerate(BLOCK_RE.finditer(text)):
        blocks.append(
            BlockPreview(
                index=index,
                tag=(match.group("tag") or "").lower(),
                start=match.start(),
                end=match.end(),
                preview=_visible_preview(match.group("body") or ""),
            )
        )
    if blocks:
        return blocks

    blocks = []
    for index, line in enumerate(text.splitlines()):
        preview = _visible_preview(line)
        if preview:
            blocks.append(
                BlockPreview(
                    index=index,
                    tag="line",
                    start=0,
                    end=0,
                    preview=preview,
                )
            )
    return blocks


def _read_from_epub(epub_path: str | None, internal_path: str | None) -> str:
    if not epub_path or not internal_path or not os.path.exists(epub_path):
        return ""

    normalized = internal_path.replace("\\", "/")
    try:
        with zipfile.ZipFile(epub_path, "r") as archive:
            with archive.open(normalized, "r") as chapter_file:
                data = chapter_file.read()
    except Exception:
        return ""

    for encoding in ("utf-8", "utf-8-sig", "cp1251", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


class HtmlSyntaxHighlighter(QSyntaxHighlighter):
    def __init__(self, document: QTextDocument):
        super().__init__(document)

        self.tag_format = QTextCharFormat()
        self.tag_format.setForeground(QColor("#0f5c7a"))
        self.tag_format.setFontWeight(QFont.Weight.Bold)

        self.attr_format = QTextCharFormat()
        self.attr_format.setForeground(QColor("#7b3fb7"))

        self.string_format = QTextCharFormat()
        self.string_format.setForeground(QColor("#b54708"))

        self.comment_format = QTextCharFormat()
        self.comment_format.setForeground(QColor("#687076"))
        self.comment_format.setFontItalic(True)

    def highlightBlock(self, text: str) -> None:
        for match in re.finditer(r"<!--.*?-->", text):
            self.setFormat(match.start(), match.end() - match.start(), self.comment_format)

        for match in re.finditer(r"</?[A-Za-z0-9:_-]+(?:\s+[^>]*?)?>", text):
            self.setFormat(match.start(), match.end() - match.start(), self.tag_format)

            inner_text = match.group(0)
            inner_offset = match.start()
            for attr_match in re.finditer(r"\b[A-Za-z_:][-A-Za-z0-9_:.]*(?=\=)", inner_text):
                self.setFormat(
                    inner_offset + attr_match.start(),
                    attr_match.end() - attr_match.start(),
                    self.attr_format,
                )
            for string_match in re.finditer(r"\"[^\"]*\"|'[^']*'", inner_text):
                self.setFormat(
                    inner_offset + string_match.start(),
                    string_match.end() - string_match.start(),
                    self.string_format,
                )


class ChapterEditorDialog(QDialog):
    AUTOSAVE_DELAY_MS = 1500
    ANALYSIS_DELAY_MS = 700
    SEARCH_DELAY_MS = 250
    MAX_SEARCH_RESULTS = 2000
    MAX_SEARCH_HIGHLIGHTS = 250
    DIFF_TEXT_LIMIT = 600_000
    DIFF_LINE_LIMIT = 8000

    def __init__(
        self,
        translated_path: str,
        parent=None,
        original_epub_path: str | None = None,
        original_internal_path: str | None = None,
        project_manager=None,
    ):
        super().__init__(parent)
        self.translated_path = translated_path
        self.original_epub_path = original_epub_path
        self.original_internal_path = original_internal_path
        self.project_manager = project_manager
        self.project_folder = (
            getattr(project_manager, "project_folder", None)
            or os.path.dirname(translated_path)
            or "."
        )

        key = _safe_key(translated_path)
        self.editor_state_dir = os.path.join(self.project_folder, ".chapter_editor")
        self.draft_path = os.path.join(self.editor_state_dir, "drafts", f"{key}.json")
        self.snapshot_dir = os.path.join(self.editor_state_dir, "snapshots", key)

        self._saved_text = ""
        self._saved_blocks = []
        self._original_text = ""
        self._changed_lines = set()
        self._problem_spots = []
        self._search_results = []
        self._current_search_index = -1
        self._blocks_stale = True
        self._loading = False
        self._syncing_scroll = False
        self._diff_is_limited = False

        self.search_timer = QtCore.QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self._refresh_search_results)

        self.analysis_timer = QtCore.QTimer(self)
        self.analysis_timer.setSingleShot(True)
        self.analysis_timer.timeout.connect(self._refresh_analysis)

        self.autosave_timer = QtCore.QTimer(self)
        self.autosave_timer.setSingleShot(True)
        self.autosave_timer.timeout.connect(self._save_draft)

        self.setWindowTitle("Редактор главы")
        self.resize(1450, 920)
        self.setMinimumSize(980, 720)

        self._build_ui()
        self._connect_signals()
        self._bind_shortcuts()
        self._load_state()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        header_layout = QVBoxLayout()
        self.title_label = QLabel()
        self.title_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        self.path_label = QLabel()
        self.path_label.setWordWrap(True)
        self.original_label = QLabel()
        self.original_label.setWordWrap(True)
        header_layout.addWidget(self.title_label)
        header_layout.addWidget(self.path_label)
        header_layout.addWidget(self.original_label)
        layout.addLayout(header_layout)

        actions_layout = QHBoxLayout()
        self.btn_save = QPushButton("Сохранить")
        self.btn_undo = QPushButton("Отмена")
        self.btn_redo = QPushButton("Повтор")
        self.btn_snapshots = QPushButton("Снимки…")
        self.btn_validation = QPushButton("Проверка…")
        self.btn_consistency = QPushButton("Согласованность…")
        self.status_label = QLabel("Загрузка…")
        self.status_label.setStyleSheet("font-weight: bold;")
        self.meta_label = QLabel()
        self.meta_label.setStyleSheet("color: #5a5a5a;")
        self.meta_label.setWordWrap(True)

        actions_layout.addWidget(self.btn_save)
        actions_layout.addWidget(self.btn_undo)
        actions_layout.addWidget(self.btn_redo)
        actions_layout.addWidget(self.btn_snapshots)
        actions_layout.addWidget(self.btn_validation)
        actions_layout.addWidget(self.btn_consistency)
        actions_layout.addStretch()
        actions_layout.addWidget(self.status_label)
        layout.addLayout(actions_layout)
        layout.addWidget(self.meta_label)

        search_layout = QHBoxLayout()
        search_label = QLabel("Поиск:")
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Текст или regex по текущей главе")
        self.search_target_combo = QComboBox()
        self.search_target_combo.addItem("Перевод", "translated")
        self.search_target_combo.addItem("Оригинал", "original")
        self.search_target_combo.addItem("Оба режима", "both")
        self.search_case_checkbox = QCheckBox("Учитывать регистр")
        self.search_regex_checkbox = QCheckBox("Regex")
        self.btn_search_prev = QPushButton("←")
        self.btn_search_next = QPushButton("→")
        self.search_count_label = QLabel("0")

        search_layout.addWidget(search_label)
        search_layout.addWidget(self.search_input, 1)
        search_layout.addWidget(self.search_target_combo)
        search_layout.addWidget(self.search_case_checkbox)
        search_layout.addWidget(self.search_regex_checkbox)
        search_layout.addWidget(self.btn_search_prev)
        search_layout.addWidget(self.btn_search_next)
        search_layout.addWidget(self.search_count_label)
        layout.addLayout(search_layout)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)

        self.mode_tabs = QTabWidget()
        splitter.addWidget(self.mode_tabs)

        self.translated_editor = self._create_editor(read_only=False)
        self.translated_document = self.translated_editor.document()
        self.translated_compare_editor = self._create_editor(read_only=False)
        self.translated_compare_editor.setDocument(self.translated_document)
        self.original_editor = self._create_editor(read_only=True)
        self.original_document = self.original_editor.document()

        self._translated_highlighter = HtmlSyntaxHighlighter(self.translated_document)
        self._original_highlighter = HtmlSyntaxHighlighter(self.original_document)

        self.mode_tabs.addTab(self.translated_editor, "Только перевод")

        side_by_side_widget = QWidget()
        side_by_side_layout = QVBoxLayout(side_by_side_widget)
        side_by_side_layout.setContentsMargins(0, 0, 0, 0)
        side_by_side_splitter = QSplitter(Qt.Orientation.Horizontal)
        side_by_side_splitter.addWidget(self.original_editor)
        side_by_side_splitter.addWidget(self.translated_compare_editor)
        side_by_side_splitter.setStretchFactor(0, 1)
        side_by_side_splitter.setStretchFactor(1, 1)
        side_by_side_layout.addWidget(side_by_side_splitter)
        self.mode_tabs.addTab(side_by_side_widget, "Оригинал + перевод")

        self.block_table = QTableWidget(0, 5)
        self.block_table.setHorizontalHeaderLabels(["#", "Тег", "Исходник", "Перевод", "Изм."])
        self.block_table.verticalHeader().setVisible(False)
        self.block_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.block_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.block_table.setAlternatingRowColors(True)
        self.block_table.horizontalHeader().setStretchLastSection(False)
        self.block_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.block_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.block_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.block_table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.block_table.horizontalHeader().setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.mode_tabs.addTab(self.block_table, "Поабзацно")

        sidebar = QWidget()
        sidebar_layout = QVBoxLayout(sidebar)
        splitter.addWidget(sidebar)
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 2)

        summary_group = QGroupBox("Сводка")
        summary_layout = QVBoxLayout(summary_group)
        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        self.warning_label = QLabel()
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet("color: #8a4b08;")
        summary_layout.addWidget(self.summary_label)
        summary_layout.addWidget(self.warning_label)
        sidebar_layout.addWidget(summary_group)

        self.sidebar_tabs = QTabWidget()
        self.issues_list = QListWidget()
        self.search_results_list = QListWidget()
        self.sidebar_tabs.addTab(self.issues_list, "Проблемы")
        self.sidebar_tabs.addTab(self.search_results_list, "Поиск")
        sidebar_layout.addWidget(self.sidebar_tabs, 1)

        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        layout.addWidget(self.button_box)

    def _create_editor(self, read_only: bool) -> QPlainTextEdit:
        editor = QPlainTextEdit()
        editor.setReadOnly(read_only)
        editor.setFont(QFont("Consolas", 10))
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        editor.setTabStopDistance(32)
        return editor

    def _connect_signals(self) -> None:
        self.btn_save.clicked.connect(self.save_changes)
        self.btn_undo.clicked.connect(self.translated_document.undo)
        self.btn_redo.clicked.connect(self.translated_document.redo)
        self.btn_snapshots.clicked.connect(self._open_snapshots_dialog)
        self.btn_validation.clicked.connect(self._open_validation_dialog)
        self.btn_consistency.clicked.connect(self._open_consistency_dialog)
        self.button_box.rejected.connect(self.close)

        self.search_input.textChanged.connect(self._schedule_search_refresh)
        self.search_target_combo.currentIndexChanged.connect(self._schedule_search_refresh)
        self.search_case_checkbox.toggled.connect(self._schedule_search_refresh)
        self.search_regex_checkbox.toggled.connect(self._schedule_search_refresh)
        self.btn_search_prev.clicked.connect(lambda: self._jump_search_result(-1))
        self.btn_search_next.clicked.connect(lambda: self._jump_search_result(1))
        self.search_results_list.itemActivated.connect(self._activate_search_result)
        self.search_results_list.itemClicked.connect(self._activate_search_result)
        self.issues_list.itemActivated.connect(self._activate_problem_spot)
        self.issues_list.itemClicked.connect(self._activate_problem_spot)
        self.block_table.itemDoubleClicked.connect(self._jump_to_block_row)
        self.mode_tabs.currentChanged.connect(self._on_mode_changed)

        self.translated_document.contentsChanged.connect(self._on_document_changed)
        self.translated_document.modificationChanged.connect(self._on_modification_changed)
        self.translated_editor.undoAvailable.connect(self.btn_undo.setEnabled)
        self.translated_editor.redoAvailable.connect(self.btn_redo.setEnabled)

        self.original_editor.verticalScrollBar().valueChanged.connect(self._sync_original_scroll)
        self.translated_compare_editor.verticalScrollBar().valueChanged.connect(self._sync_translated_scroll)

    def _bind_shortcuts(self) -> None:
        QShortcut(QKeySequence.StandardKey.Save, self, activated=self.save_changes)
        QShortcut(QKeySequence.StandardKey.Find, self, activated=self.search_input.setFocus)
        QShortcut(QKeySequence("F3"), self, activated=lambda: self._jump_search_result(1))
        QShortcut(QKeySequence("Shift+F3"), self, activated=lambda: self._jump_search_result(-1))

    def _load_state(self) -> None:
        self.title_label.setText(os.path.basename(self.translated_path))
        self.path_label.setText(f"Файл перевода: {self.translated_path}")
        if self.original_internal_path:
            self.original_label.setText(
                f"Оригинал EPUB: {self.original_internal_path}"
            )
        else:
            self.original_label.setText("Оригинал EPUB: недоступен")

        self._loading = True
        translated_text = _read_text_file(self.translated_path)
        self._original_text = _read_from_epub(self.original_epub_path, self.original_internal_path)
        self.translated_editor.setPlainText(translated_text)
        self.original_editor.setPlainText(
            self._original_text or "Оригинал главы не найден. Доступен только режим перевода."
        )
        self._loading = False

        self._set_saved_state(translated_text)
        self._restore_draft_if_needed()
        self._update_tool_availability()
        self._refresh_search_results()
        self._refresh_analysis()

    def _set_saved_state(self, text: str) -> None:
        self._saved_text = text
        self._saved_blocks = _extract_blocks(text)
        self.translated_document.setModified(False)
        self._changed_lines = set()
        try:
            timestamp = datetime.fromtimestamp(os.path.getmtime(self.translated_path))
            saved_info = timestamp.strftime("%d.%m.%Y %H:%M:%S")
        except OSError:
            saved_info = "недоступно"
        self.status_label.setText("Сохранено")
        self.meta_label.setText(f"Последняя версия на диске: {saved_info}")
        self._update_window_title()
        self._apply_editor_decorations()

    def _update_window_title(self) -> None:
        title = f"Редактор главы: {os.path.basename(self.translated_path)}"
        if self.translated_document.isModified():
            title = "* " + title
        self.setWindowTitle(title)

    def _update_tool_availability(self) -> None:
        has_original = bool(self._original_text)
        self.search_target_combo.model().item(1).setEnabled(has_original)
        self.search_target_combo.model().item(2).setEnabled(True)
        if not has_original and self.search_target_combo.currentData() == "original":
            self.search_target_combo.setCurrentIndex(0)

        self.btn_validation.setEnabled(bool(self.original_epub_path and self.project_manager))
        self.btn_validation.setToolTip(
            "Открыть существующее окно проверки проекта."
            if self.btn_validation.isEnabled()
            else "Нужен исходный EPUB и активный проект."
        )

        self.btn_consistency.setEnabled(bool(self._find_settings_manager()))
        self.btn_consistency.setToolTip(
            "Проверить текущую главу через окно согласованности."
            if self.btn_consistency.isEnabled()
            else "Не найден settings_manager в родительском окне."
        )

    def _load_draft_payload(self) -> dict | None:
        if not os.path.exists(self.draft_path):
            return None
        try:
            with open(self.draft_path, "r", encoding="utf-8") as file:
                return json.load(file)
        except (OSError, json.JSONDecodeError):
            return None

    def _restore_draft_if_needed(self) -> None:
        payload = self._load_draft_payload()
        if not payload:
            return

        draft_text = payload.get("text", "")
        if not draft_text or draft_text == self._saved_text:
            self._clear_draft()
            return

        saved_at = payload.get("updated_at") or "неизвестно"
        message = QMessageBox(self)
        message.setWindowTitle("Найден черновик")
        message.setText("Для этой главы найден несохранённый черновик.")
        message.setInformativeText(f"Последнее автосохранение: {saved_at}\nВосстановить черновик?")
        restore_button = message.addButton("Восстановить", QMessageBox.ButtonRole.AcceptRole)
        message.addButton("Игнорировать", QMessageBox.ButtonRole.RejectRole)
        message.exec()

        if message.clickedButton() != restore_button:
            return

        self._loading = True
        self.translated_editor.setPlainText(draft_text)
        self._loading = False
        self.translated_document.setModified(True)
        self.status_label.setText("Восстановлен черновик")
        self._update_window_title()
        self._schedule_search_refresh()
        self._schedule_analysis_refresh()

    def _schedule_search_refresh(self) -> None:
        self.search_timer.start(self.SEARCH_DELAY_MS)

    def _schedule_analysis_refresh(self) -> None:
        self.analysis_timer.start(self.ANALYSIS_DELAY_MS)

    def _on_document_changed(self) -> None:
        if self._loading:
            return
        self.status_label.setText("Изменено")
        self._update_window_title()
        self._schedule_search_refresh()
        self._schedule_analysis_refresh()
        self.autosave_timer.start(self.AUTOSAVE_DELAY_MS)

    def _on_modification_changed(self, _modified: bool) -> None:
        self._update_window_title()

    def _iter_search_matches(self, text: str, query: str) -> list[tuple[int, int]]:
        if not query:
            return []

        matches = []
        if self.search_regex_checkbox.isChecked():
            flags = re.MULTILINE
            if not self.search_case_checkbox.isChecked():
                flags |= re.IGNORECASE
            pattern = re.compile(query, flags)
            for match in pattern.finditer(text):
                start, end = match.span()
                if end == start:
                    end += 1
                matches.append((start, end))
        else:
            haystack = text if self.search_case_checkbox.isChecked() else text.casefold()
            needle = query if self.search_case_checkbox.isChecked() else query.casefold()
            start = 0
            while True:
                index = haystack.find(needle, start)
                if index < 0:
                    break
                matches.append((index, index + len(query)))
                start = index + max(1, len(query))
        return matches

    def _refresh_search_results(self) -> None:
        query = self.search_input.text().strip()
        self._search_results = []
        self._current_search_index = -1
        self.search_results_list.clear()

        if not query:
            self.search_count_label.setText("0")
            self._apply_editor_decorations()
            return

        target = self.search_target_combo.currentData()
        sources = [("translated", self.translated_document.toPlainText())]
        if target in ("original", "both"):
            sources = []
            if target == "original":
                sources.append(("original", self.original_document.toPlainText()))
            else:
                sources.append(("translated", self.translated_document.toPlainText()))
                if self._original_text:
                    sources.append(("original", self.original_document.toPlainText()))

        try:
            for source_name, source_text in sources:
                line_starts = _build_line_starts(source_text)
                for start, end in self._iter_search_matches(source_text, query):
                    preview = _visible_preview(source_text[max(0, start - 80): min(len(source_text), end + 80)], 200)
                    self._search_results.append(
                        SearchResult(
                            target=source_name,
                            start=start,
                            end=end,
                            line=_line_from_position(line_starts, start),
                            preview=preview,
                        )
                    )
                    if len(self._search_results) >= self.MAX_SEARCH_RESULTS:
                        break
                if len(self._search_results) >= self.MAX_SEARCH_RESULTS:
                    break
        except re.error as error:
            self.search_count_label.setText("Regex error")
            self.warning_label.setText(f"Ошибка регулярного выражения: {error}")
            self._apply_editor_decorations()
            return

        for index, result in enumerate(self._search_results):
            prefix = "Перевод" if result.target == "translated" else "Оригинал"
            item = QListWidgetItem(f"{prefix}, строка {result.line}: {result.preview}")
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.search_results_list.addItem(item)

        self.search_count_label.setText(str(len(self._search_results)))
        if self._search_results:
            self._current_search_index = 0
            self.search_results_list.setCurrentRow(0)
        self._apply_editor_decorations()

    def _refresh_analysis(self) -> None:
        current_text = self.translated_document.toPlainText()
        self._problem_spots = self._scan_problem_spots(current_text)
        self.issues_list.clear()
        for index, problem in enumerate(self._problem_spots):
            item = QListWidgetItem(f"{problem.title}: {problem.details}")
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.issues_list.addItem(item)

        line_count = current_text.count("\n") + 1 if current_text else 0
        if len(current_text) > self.DIFF_TEXT_LIMIT or line_count > self.DIFF_LINE_LIMIT:
            self._changed_lines = set()
            self._diff_is_limited = True
        else:
            matcher = SequenceMatcher(
                None,
                self._saved_text.splitlines(),
                current_text.splitlines(),
                autojunk=False,
            )
            changed_lines = set()
            current_lines = current_text.splitlines()
            for opcode, _i1, _i2, j1, j2 in matcher.get_opcodes():
                if opcode == "equal":
                    continue
                if j1 == j2 and current_lines:
                    changed_lines.add(min(j1, len(current_lines) - 1))
                else:
                    changed_lines.update(range(j1, j2))
            self._changed_lines = changed_lines
            self._diff_is_limited = False

        self._blocks_stale = True
        if self.mode_tabs.currentWidget() is self.block_table:
            self._refresh_block_table()

        self._update_summary()
        self._apply_editor_decorations()

    def _scan_problem_spots(self, text: str) -> list[ProblemSpot]:
        issues = []

        def add_matches(pattern: str, title: str, details: str) -> None:
            for match in re.finditer(pattern, text, re.IGNORECASE | re.MULTILINE):
                issues.append(
                    ProblemSpot(
                        title=title,
                        details=f"{details}: { _visible_preview(match.group(0), 120) }",
                        target="translated",
                        start=match.start(),
                    )
                )

        add_matches(r"RESTORED_IMAGE_WARNING", "Восстановленная картинка", "Проверьте комментарий")
        add_matches(r"(?<!\.)\.\.(?!\.)", "Подозрительные точки", "Похоже на двойную точку")
        add_matches(r"(^|[^\s])\s{2,}([^\s]|$)", "Двойной пробел", "Есть серия пробелов")
        add_matches(r"<p\b[^>]*>\s*[-—]", "Начало абзаца", "Проверьте оформление диалога")
        add_matches(r"\"", "Прямые кавычки", "Возможно, нужны типографские кавычки")

        if self._original_text:
            translation_p = len(re.findall(r"<p\b", text, re.IGNORECASE))
            original_p = len(re.findall(r"<p\b", self._original_text, re.IGNORECASE))
            if translation_p != original_p:
                issues.append(
                    ProblemSpot(
                        title="Количество абзацев",
                        details=f"В переводе {translation_p}, в оригинале {original_p}",
                        target="translated",
                        start=0,
                    )
                )

            translation_h = len(re.findall(r"<h[1-6]\b", text, re.IGNORECASE))
            original_h = len(re.findall(r"<h[1-6]\b", self._original_text, re.IGNORECASE))
            if translation_h != original_h:
                issues.append(
                    ProblemSpot(
                        title="Количество заголовков",
                        details=f"В переводе {translation_h}, в оригинале {original_h}",
                        target="translated",
                        start=0,
                    )
                )

        return issues

    def _refresh_block_table(self) -> None:
        translated_blocks = _extract_blocks(self.translated_document.toPlainText())
        original_blocks = _extract_blocks(self._original_text)
        row_count = max(len(translated_blocks), len(original_blocks))
        self.block_table.setRowCount(row_count)

        for row in range(row_count):
            translated_block = translated_blocks[row] if row < len(translated_blocks) else None
            original_block = original_blocks[row] if row < len(original_blocks) else None
            saved_block = self._saved_blocks[row] if row < len(self._saved_blocks) else None
            changed = bool(
                translated_block
                and _normalized_block_text(translated_block.preview)
                != _normalized_block_text(saved_block.preview if saved_block else "")
            )

            number_item = QTableWidgetItem(str(row + 1))
            number_item.setData(
                Qt.ItemDataRole.UserRole,
                translated_block.start if translated_block else -1,
            )
            tag_item = QTableWidgetItem(
                translated_block.tag if translated_block else (original_block.tag if original_block else "—")
            )
            source_item = QTableWidgetItem(original_block.preview if original_block else "")
            translated_item = QTableWidgetItem(translated_block.preview if translated_block else "")
            changed_item = QTableWidgetItem("●" if changed else "")

            if changed:
                changed_item.setForeground(QtGui.QBrush(QColor("#b35c00")))
                translated_item.setBackground(QColor("#fff4d6"))

            self.block_table.setItem(row, 0, number_item)
            self.block_table.setItem(row, 1, tag_item)
            self.block_table.setItem(row, 2, source_item)
            self.block_table.setItem(row, 3, translated_item)
            self.block_table.setItem(row, 4, changed_item)

        self._blocks_stale = False

    def _update_summary(self) -> None:
        block_count = len(_extract_blocks(self.translated_document.toPlainText()))
        changed_lines = len(self._changed_lines)
        self.summary_label.setText(
            "\n".join(
                [
                    f"Абзацев/блоков: {block_count}",
                    f"Изменённых строк: {changed_lines}",
                    f"Проблемных мест: {len(self._problem_spots)}",
                    f"Совпадений поиска: {len(self._search_results)}",
                ]
            )
        )

        warnings = []
        if self.translated_document.isModified():
            warnings.append("Есть несохранённые изменения, черновик сохранится автоматически.")
        if self._diff_is_limited:
            warnings.append("Подсветка изменений упрощена из-за большого размера главы.")
        if not self._original_text:
            warnings.append("Оригинал не найден, режим сравнения ограничен.")
        self.warning_label.setText("\n".join(warnings))

    def _line_selection(self, document: QTextDocument, line_number: int, color: QColor):
        block = document.findBlockByLineNumber(max(0, line_number))
        if not block.isValid():
            return None
        selection = QTextEdit.ExtraSelection()
        selection.cursor = QTextCursor(block)
        selection.format.setBackground(color)
        selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        return selection

    def _match_selection(self, document: QTextDocument, start: int, end: int, color: QColor):
        cursor = QTextCursor(document)
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        if cursor.selection().isEmpty():
            return None
        selection = QTextEdit.ExtraSelection()
        selection.cursor = cursor
        selection.format.setBackground(color)
        return selection

    def _apply_editor_decorations(self) -> None:
        translated_selections = []
        original_selections = []

        for line_number in sorted(self._changed_lines):
            selection = self._line_selection(
                self.translated_document,
                line_number,
                QColor("#fff4d6"),
            )
            if selection:
                translated_selections.append(selection)

        for index, result in enumerate(self._search_results[: self.MAX_SEARCH_HIGHLIGHTS]):
            selection = self._match_selection(
                self.translated_document if result.target == "translated" else self.original_document,
                result.start,
                result.end,
                QColor("#fff1a8"),
            )
            if not selection:
                continue
            if result.target == "translated":
                translated_selections.append(selection)
            else:
                original_selections.append(selection)

        if 0 <= self._current_search_index < len(self._search_results):
            current = self._search_results[self._current_search_index]
            current_selection = self._match_selection(
                self.translated_document if current.target == "translated" else self.original_document,
                current.start,
                current.end,
                QColor("#f9c74f"),
            )
            if current_selection:
                if current.target == "translated":
                    translated_selections.append(current_selection)
                else:
                    original_selections.append(current_selection)

        self.translated_editor.setExtraSelections(translated_selections)
        self.translated_compare_editor.setExtraSelections(translated_selections)
        self.original_editor.setExtraSelections(original_selections)

    def _activate_search_result(self, item: QListWidgetItem) -> None:
        index = item.data(Qt.ItemDataRole.UserRole)
        if index is None:
            return
        self._current_search_index = int(index)
        self._jump_to_search_result(self._search_results[self._current_search_index])

    def _activate_problem_spot(self, item: QListWidgetItem) -> None:
        index = item.data(Qt.ItemDataRole.UserRole)
        if index is None:
            return
        problem = self._problem_spots[int(index)]
        self._jump_to_position(problem.target, problem.start)

    def _jump_search_result(self, step: int) -> None:
        if not self._search_results:
            return
        self._current_search_index = (self._current_search_index + step) % len(self._search_results)
        self.search_results_list.setCurrentRow(self._current_search_index)
        self._jump_to_search_result(self._search_results[self._current_search_index])

    def _jump_to_search_result(self, result: SearchResult) -> None:
        self._jump_to_position(result.target, result.start, result.end)
        self._apply_editor_decorations()

    def _jump_to_position(self, target: str, start: int, end: int | None = None) -> None:
        if start < 0:
            return

        if target == "original":
            self.mode_tabs.setCurrentIndex(1)
            editor = self.original_editor
        else:
            if self.mode_tabs.currentIndex() == 2:
                self.mode_tabs.setCurrentIndex(0)
            editor = self.translated_compare_editor if self.mode_tabs.currentIndex() == 1 else self.translated_editor

        cursor = editor.textCursor()
        cursor.setPosition(start)
        if end and end > start:
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        editor.setTextCursor(cursor)
        editor.centerCursor()
        editor.setFocus()

    def _jump_to_block_row(self, item: QTableWidgetItem) -> None:
        position = self.block_table.item(item.row(), 0).data(Qt.ItemDataRole.UserRole)
        if position is None or int(position) < 0:
            return
        self.mode_tabs.setCurrentIndex(0)
        self._jump_to_position("translated", int(position))

    def _on_mode_changed(self, index: int) -> None:
        if index == 2 and self._blocks_stale:
            self._refresh_block_table()

    def _sync_original_scroll(self, value: int) -> None:
        if self._syncing_scroll or self.mode_tabs.currentIndex() != 1:
            return
        self._syncing_scroll = True
        self.translated_compare_editor.verticalScrollBar().setValue(value)
        self._syncing_scroll = False

    def _sync_translated_scroll(self, value: int) -> None:
        if self._syncing_scroll or self.mode_tabs.currentIndex() != 1:
            return
        self._syncing_scroll = True
        self.original_editor.verticalScrollBar().setValue(value)
        self._syncing_scroll = False

    def _save_draft(self) -> None:
        if not self.translated_document.isModified():
            return
        os.makedirs(os.path.dirname(self.draft_path), exist_ok=True)
        payload = {
            "path": self.translated_path,
            "updated_at": datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
            "saved_hash": _hash_text(self._saved_text),
            "text": self.translated_document.toPlainText(),
        }
        try:
            with open(self.draft_path, "w", encoding="utf-8") as file:
                json.dump(payload, file, ensure_ascii=False, indent=2)
            self.meta_label.setText(
                f"{self.meta_label.text().split(' • ')[0]} • Черновик обновлён {payload['updated_at']}"
            )
        except OSError:
            self.warning_label.setText("Не удалось записать черновик, проверьте доступ к диску.")

    def _clear_draft(self) -> None:
        if os.path.exists(self.draft_path):
            try:
                os.remove(self.draft_path)
            except OSError:
                pass

    def _create_snapshot(self, content: str) -> str | None:
        if not content:
            return None
        os.makedirs(self.snapshot_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        snapshot_path = os.path.join(self.snapshot_dir, f"{timestamp}.html")
        try:
            with open(snapshot_path, "w", encoding="utf-8") as file:
                file.write(content)
            return snapshot_path
        except OSError:
            return None

    def _open_snapshots_dialog(self) -> None:
        if not os.path.isdir(self.snapshot_dir):
            QMessageBox.information(self, "Снимки", "Снимков для этой главы пока нет.")
            return

        snapshots = sorted(
            [
                os.path.join(self.snapshot_dir, name)
                for name in os.listdir(self.snapshot_dir)
                if name.lower().endswith(".html")
            ],
            reverse=True,
        )
        if not snapshots:
            QMessageBox.information(self, "Снимки", "Снимков для этой главы пока нет.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Снимки главы")
        dialog.resize(760, 520)
        layout = QVBoxLayout(dialog)

        snapshots_list = QListWidget()
        preview = QPlainTextEdit()
        preview.setReadOnly(True)
        preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        preview.setFont(QFont("Consolas", 10))

        for path in snapshots:
            item = QListWidgetItem(os.path.basename(path))
            item.setData(Qt.ItemDataRole.UserRole, path)
            snapshots_list.addItem(item)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(snapshots_list)
        splitter.addWidget(preview)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter)

        buttons = QDialogButtonBox()
        restore_button = buttons.addButton("Восстановить в редактор", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Close)
        layout.addWidget(buttons)

        def show_preview():
            item = snapshots_list.currentItem()
            if not item:
                preview.clear()
                return
            path = item.data(Qt.ItemDataRole.UserRole)
            try:
                preview.setPlainText(_read_text_file(path))
            except OSError as error:
                preview.setPlainText(f"Не удалось прочитать снимок: {error}")

        snapshots_list.currentItemChanged.connect(lambda *_: show_preview())
        buttons.rejected.connect(dialog.reject)

        def restore_snapshot():
            item = snapshots_list.currentItem()
            if not item:
                return
            path = item.data(Qt.ItemDataRole.UserRole)
            try:
                content = _read_text_file(path)
            except OSError as error:
                QMessageBox.warning(dialog, "Снимок", f"Не удалось прочитать снимок:\n{error}")
                return

            self._loading = True
            self.translated_editor.setPlainText(content)
            self._loading = False
            self.translated_document.setModified(True)
            self.status_label.setText(f"Восстановлен снимок {os.path.basename(path)}")
            self._schedule_search_refresh()
            self._schedule_analysis_refresh()
            dialog.accept()

        restore_button.clicked.connect(restore_snapshot)
        snapshots_list.setCurrentRow(0)
        dialog.exec()

    def save_changes(self) -> bool:
        current_text = self.translated_document.toPlainText()
        try:
            disk_text = _read_text_file(self.translated_path)
        except OSError as error:
            QMessageBox.critical(self, "Сохранение", f"Не удалось прочитать файл перед сохранением:\n{error}")
            return False

        if disk_text != self._saved_text and self.translated_document.isModified():
            message = QMessageBox(self)
            message.setWindowTitle("Файл изменился на диске")
            message.setText("С момента открытия глава была изменена вне редактора.")
            message.setInformativeText("Продолжить сохранение поверх новой версии?")
            overwrite_button = message.addButton("Сохранить поверх", QMessageBox.ButtonRole.AcceptRole)
            message.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
            message.exec()
            if message.clickedButton() != overwrite_button:
                return False

        snapshot_path = None
        if disk_text != current_text:
            snapshot_path = self._create_snapshot(disk_text)

        try:
            _atomic_write_text(self.translated_path, current_text)
        except OSError as error:
            QMessageBox.critical(self, "Сохранение", f"Не удалось сохранить файл:\n{error}")
            return False

        self._set_saved_state(current_text)
        self._clear_draft()
        self._schedule_analysis_refresh()

        extra = f" Снимок: {os.path.basename(snapshot_path)}." if snapshot_path else ""
        self.status_label.setText("Сохранено")
        self.meta_label.setText(f"Глава сохранена атомарно.{extra}")
        return True

    def _find_settings_manager(self):
        widget = self.parentWidget()
        while widget is not None:
            if hasattr(widget, "settings_manager"):
                return getattr(widget, "settings_manager")
            widget = widget.parentWidget()
        return None

    def _open_consistency_dialog(self) -> None:
        settings_manager = self._find_settings_manager()
        if not settings_manager:
            QMessageBox.information(self, "Согласованность", "Не найден settings_manager для запуска проверки.")
            return
        if self.translated_document.isModified() and not self.save_changes():
            return

        from .consistency_checker import ConsistencyValidatorDialog

        dialog = ConsistencyValidatorDialog(
            [
                {
                    "name": os.path.basename(self.original_internal_path or self.translated_path),
                    "content": _read_text_file(self.translated_path),
                    "path": self.translated_path,
                }
            ],
            settings_manager,
            self,
            project_manager=self.project_manager,
        )
        if hasattr(dialog, "_update_chunk_stats"):
            dialog._update_chunk_stats()
        dialog.exec()

        self._loading = True
        self.translated_editor.setPlainText(_read_text_file(self.translated_path))
        self._loading = False
        self._set_saved_state(self.translated_document.toPlainText())
        self._schedule_search_refresh()
        self._schedule_analysis_refresh()

    def _open_validation_dialog(self) -> None:
        if not self.project_manager or not self.original_epub_path:
            QMessageBox.information(self, "Проверка", "Для открытия проверки нужен проект и исходный EPUB.")
            return
        if self.translated_document.isModified() and not self.save_changes():
            return

        from .validation import TranslationValidatorDialog

        dialog = TranslationValidatorDialog(
            self.project_folder,
            self.original_epub_path,
            self,
            project_manager=self.project_manager,
        )
        dialog.exec()

        try:
            reloaded = _read_text_file(self.translated_path)
        except OSError:
            return

        self._loading = True
        self.translated_editor.setPlainText(reloaded)
        self._loading = False
        self._set_saved_state(reloaded)
        self._schedule_search_refresh()
        self._schedule_analysis_refresh()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if not self.translated_document.isModified():
            event.accept()
            return

        message = QMessageBox(self)
        message.setWindowTitle("Несохранённые изменения")
        message.setText("В главе есть несохранённые изменения.")
        message.setInformativeText("Сохранить перед закрытием редактора?")
        save_button = message.addButton("Сохранить", QMessageBox.ButtonRole.AcceptRole)
        discard_button = message.addButton("Закрыть без сохранения", QMessageBox.ButtonRole.DestructiveRole)
        message.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
        message.exec()

        if message.clickedButton() == save_button:
            if self.save_changes():
                event.accept()
            else:
                event.ignore()
            return

        if message.clickedButton() == discard_button:
            self._clear_draft()
            event.accept()
            return

        event.ignore()
