import os

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...utils.document_importer import (
    DOCUMENT_INPUT_FILTER,
    convert_source_to_epub_with_dialog,
)


class ProjectPathsWidget(QWidget):
    """
    Header widget for choosing the source EPUB/TXT and the project folder.
    """

    file_selected = pyqtSignal(str)
    folder_selected = pyqtSignal(str)
    chapters_reselection_requested = pyqtSignal()
    swap_file_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._file_path = None
        self._folder_path = None
        self._chapters_count = 0
        self._collapsed = False
        self._init_ui()

    def _init_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.header_card = QFrame(self)
        self.header_card.setObjectName("projectHeaderCard")
        header_layout = QVBoxLayout(self.header_card)
        header_layout.setContentsMargins(12, 12, 12, 12)
        header_layout.setSpacing(10)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(8)

        intro_layout = QVBoxLayout()
        intro_layout.setContentsMargins(0, 0, 0, 0)
        intro_layout.setSpacing(2)

        self.eyebrow_label = QLabel("Рабочая область")
        self.eyebrow_label.setObjectName("sectionEyebrow")
        intro_layout.addWidget(self.eyebrow_label)

        self.title_label = QLabel("Подготовка проекта перевода")
        self.title_label.setObjectName("heroTitle")
        intro_layout.addWidget(self.title_label)

        self.subtitle_label = QLabel(
            "Выберите книгу и папку проекта, затем настройте сессию и очередь задач."
        )
        self.subtitle_label.setObjectName("heroSubtitle")
        self.subtitle_label.setWordWrap(True)
        intro_layout.addWidget(self.subtitle_label)

        self.compact_summary_label = QLabel()
        self.compact_summary_label.setObjectName("heroSubtitle")
        self.compact_summary_label.setWordWrap(True)
        self.compact_summary_label.setVisible(False)
        intro_layout.addWidget(self.compact_summary_label)

        self.context_status_label = QLabel()
        self.context_status_label.setObjectName("projectStateLabel")
        self.context_status_label.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )

        self.toggle_header_btn = QPushButton("Скрыть")
        self.toggle_header_btn.setObjectName("compactActionButton")
        self.toggle_header_btn.setToolTip("Свернуть рабочую область и освободить место для списка задач.")
        self.toggle_header_btn.clicked.connect(lambda: self.set_collapsed(not self._collapsed))

        top_actions_layout = QVBoxLayout()
        top_actions_layout.setContentsMargins(0, 0, 0, 0)
        top_actions_layout.setSpacing(6)
        top_actions_layout.addWidget(self.context_status_label, 0, Qt.AlignmentFlag.AlignRight)
        top_actions_layout.addWidget(self.toggle_header_btn, 0, Qt.AlignmentFlag.AlignRight)
        top_actions_layout.addStretch(1)

        top_row.addLayout(intro_layout, 1)
        top_row.addLayout(top_actions_layout, 0)
        header_layout.addLayout(top_row)

        self.cards_container = QWidget(self.header_card)
        cards_row = QHBoxLayout(self.cards_container)
        cards_row.setContentsMargins(0, 0, 0, 0)
        cards_row.setSpacing(10)

        (
            self.file_card,
            self.file_value_label,
            self.file_detail_label,
            self.file_path_btn,
        ) = self._create_path_card(
            title="Источник",
            empty_value="Файл не выбран",
            empty_detail="Поддерживаются EPUB, DOCX, TXT, Markdown, HTML и PDF.",
            button_text="Выбрать файл",
            slot=self._on_select_file,
        )

        (
            self.folder_card,
            self.folder_value_label,
            self.folder_detail_label,
            self.folder_path_btn,
        ) = self._create_path_card(
            title="Папка проекта",
            empty_value="Папка не выбрана",
            empty_detail="Сюда сохраняются переводы, карта проекта и промежуточные файлы.",
            button_text="Выбрать папку",
            slot=self._on_select_folder,
        )

        self.stats_card = QFrame(self.header_card)
        self.stats_card.setObjectName("projectStatsCard")
        stats_layout = QVBoxLayout(self.stats_card)
        stats_layout.setContentsMargins(12, 12, 12, 12)
        stats_layout.setSpacing(6)

        stats_title = QLabel("Состояние")
        stats_title.setObjectName("projectCardTitle")
        stats_layout.addWidget(stats_title)

        self.chapters_value_label = QLabel("0")
        self.chapters_value_label.setObjectName("metricValueLabel")
        stats_layout.addWidget(self.chapters_value_label)

        self.chapters_meta_label = QLabel("Главы появятся после анализа книги")
        self.chapters_meta_label.setObjectName("mutedLabel")
        self.chapters_meta_label.setWordWrap(True)
        stats_layout.addWidget(self.chapters_meta_label)

        self.chapters_info_btn = QPushButton("Выбрать главы")
        self.chapters_info_btn.setObjectName("pathActionButton")
        self.chapters_info_btn.clicked.connect(self.chapters_reselection_requested.emit)
        stats_layout.addWidget(self.chapters_info_btn)

        self.btn_swap_file = QPushButton("Заменить исходник")
        self.btn_swap_file.setObjectName("compactActionButton")
        self.btn_swap_file.setToolTip(
            "Заменить исходник в текущем проекте. Не-EPUB документы будут сначала импортированы в EPUB."
        )
        self.btn_swap_file.clicked.connect(self.swap_file_requested.emit)
        stats_layout.addWidget(self.btn_swap_file)
        stats_layout.addStretch(1)

        cards_row.addWidget(self.file_card, 4)
        cards_row.addWidget(self.folder_card, 4)
        cards_row.addWidget(self.stats_card, 2)
        header_layout.addWidget(self.cards_container)

        root_layout.addWidget(self.header_card)
        self._refresh_header_state()

    def set_collapsed(self, collapsed: bool):
        self._collapsed = bool(collapsed)
        self.cards_container.setVisible(not self._collapsed)
        self.subtitle_label.setVisible(not self._collapsed)
        self.compact_summary_label.setVisible(self._collapsed)
        self.toggle_header_btn.setText("Показать" if self._collapsed else "Скрыть")
        self.toggle_header_btn.setToolTip(
            "Развернуть рабочую область."
            if self._collapsed
            else "Свернуть рабочую область и освободить место для списка задач."
        )
        self._refresh_header_state()
        self.updateGeometry()
        parent = self.parentWidget()
        if parent:
            parent.updateGeometry()

    def is_collapsed(self) -> bool:
        return self._collapsed

    def _create_path_card(self, title, empty_value, empty_detail, button_text, slot):
        card = QFrame(self.header_card)
        card.setObjectName("projectPathCard")

        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        title_label = QLabel(title)
        title_label.setObjectName("projectCardTitle")
        layout.addWidget(title_label)

        value_label = QLabel(empty_value)
        value_label.setObjectName("projectCardValue")
        value_label.setWordWrap(True)
        layout.addWidget(value_label)

        detail_label = QLabel(empty_detail)
        detail_label.setObjectName("projectCardDetail")
        detail_label.setWordWrap(True)
        layout.addWidget(detail_label, 1)

        button = QPushButton(button_text)
        button.setObjectName("pathActionButton")
        button.clicked.connect(slot)
        layout.addWidget(button, 0)

        return card, value_label, detail_label, button

    def _on_select_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите книгу или документ",
            "",
            DOCUMENT_INPUT_FILTER,
        )
        if file_path:
            output_dir = self._folder_path or os.path.dirname(file_path)
            imported_path = convert_source_to_epub_with_dialog(file_path, output_dir, self)
            if not imported_path:
                return
            file_path = imported_path
            self.file_selected.emit(file_path)

    def _on_select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку для проекта перевода")
        if folder:
            self.folder_selected.emit(folder)

    def set_file_path(self, path):
        self._file_path = path if path and os.path.exists(path) else None

        if self._file_path:
            self.file_value_label.setText(os.path.basename(self._file_path))
            self.file_detail_label.setText(self._file_path)
            self.file_path_btn.setText("Сменить файл")
        else:
            self.file_value_label.setText("Файл не выбран")
            self.file_detail_label.setText(
                "Поддерживаются EPUB, DOCX, TXT, Markdown, HTML и PDF."
            )
            self.file_path_btn.setText("Выбрать файл")
            self._chapters_count = 0

        self._refresh_header_state()

    def set_folder_path(self, path):
        self._folder_path = path if path and os.path.isdir(path) else None

        if self._folder_path:
            self.folder_value_label.setText(os.path.basename(self._folder_path))
            self.folder_detail_label.setText(self._folder_path)
            self.folder_path_btn.setText("Сменить папку")
        else:
            self.folder_value_label.setText("Папка не выбрана")
            self.folder_detail_label.setText(
                "Сюда сохраняются переводы, карта проекта и промежуточные файлы."
            )
            self.folder_path_btn.setText("Выбрать папку")

        self._refresh_header_state()

    def update_chapters_info(self, count):
        self._chapters_count = max(0, int(count or 0))
        self._refresh_header_state()

    def _refresh_header_state(self):
        self.chapters_value_label.setText(str(self._chapters_count))

        has_file = bool(self._file_path)
        has_folder = bool(self._folder_path)

        self.chapters_info_btn.setEnabled(has_file)
        self.btn_swap_file.setVisible(has_file and has_folder)
        self.btn_swap_file.setEnabled(has_file and has_folder)

        if self._chapters_count > 0:
            self.chapters_meta_label.setText(
                f"К проекту подключено глав: {self._chapters_count}"
            )
            self.chapters_info_btn.setText("Перевыбрать главы")
        elif has_file:
            self.chapters_meta_label.setText(
                "Главы будут показаны после анализа книги и структуры проекта."
            )
            self.chapters_info_btn.setText("Выбрать главы")
        else:
            self.chapters_meta_label.setText("Главы появятся после анализа книги")
            self.chapters_info_btn.setText("Выбрать главы")

        if has_file and has_folder:
            self.context_status_label.setProperty("ready", True)
            self.context_status_label.setText("Проект готов к настройке")
        elif has_file:
            self.context_status_label.setProperty("ready", False)
            self.context_status_label.setText("Нужно выбрать папку проекта")
        else:
            self.context_status_label.setProperty("ready", False)
            self.context_status_label.setText("Выберите исходную книгу")

        file_name = os.path.basename(self._file_path) if has_file else "источник не выбран"
        folder_name = os.path.basename(self._folder_path) if has_folder else "папка не выбрана"
        chapters_text = f"{self._chapters_count} глав" if self._chapters_count else "главы не выбраны"
        self.compact_summary_label.setText(
            f"{file_name} · {folder_name} · {chapters_text}"
        )

        self.style().unpolish(self.context_status_label)
        self.style().polish(self.context_status_label)
