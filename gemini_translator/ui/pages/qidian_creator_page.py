# -*- coding: utf-8 -*-
"""QidianCreatorPage — Qidian/Fanqie → Rulate creator as an embeddable ShellPage."""

from __future__ import annotations

from PyQt6 import QtWidgets
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from qidian_rulate.models import PreparedRulateMetadata, QidianBookMetadata, RulateBookDraft
from qidian_rulate.workers import (
    AiPrepareWorker,
    CoverPromptWorker,
    QidianFetchWorker,
    RulateFillWorker,
    RulateLoginWorker,
    _download_cover_image,
    normalize_rulate_tags,
    validate_source_url,
)

from ..widgets.key_management_widget import KeyManagementWidget
from ..widgets.model_settings_widget import ModelSettingsWidget
from gemini_translator.ui.shell import ShellPage
from gemini_translator.ui.dialogs.qidian_rulate_creator import _split_csv


class QidianCreatorPage(ShellPage):
    page_title = "Qidian/Fanqie → Rulate"

    def __init__(self, parent=None):
        super().__init__(parent)

        app = QtWidgets.QApplication.instance()
        if not app or not hasattr(app, "get_settings_manager"):
            raise RuntimeError("SettingsManager не найден в QApplication.")

        self.settings_manager = app.get_settings_manager()
        self.server_manager = getattr(app, "server_manager", None)
        self._qidian_metadata: QidianBookMetadata | None = None
        self._prepared_metadata: PreparedRulateMetadata | None = None
        self._workers = []

        self._build_ui()
        self._connect_ai_widgets()
        self._update_action_state()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        self.main_tabs = QTabWidget()
        self.main_tabs.setDocumentMode(False)
        root.addWidget(self.main_tabs, 1)

        main_tab = QWidget()
        main_layout = QVBoxLayout(main_tab)

        main_layout.addWidget(self._build_source_group())

        unified_scroll = QScrollArea()
        unified_scroll.setWidgetResizable(True)

        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)

        scroll_layout.addWidget(self._build_preview_group(), 1)

        self.key_widget = KeyManagementWidget(
            self.settings_manager,
            self,
            server_manager=self.server_manager,
        )
        self.model_settings_widget = ModelSettingsWidget(
            self,
            settings_manager=self.settings_manager,
            server_manager=self.server_manager,
        )
        self.model_settings_widget.set_cjk_options_visible(False)
        self.model_settings_widget.set_glossary_options_visible(False)
        self.model_settings_widget.set_misc_options_visible(False)

        scroll_layout.addWidget(self.key_widget)
        scroll_layout.addWidget(self.model_settings_widget)

        unified_scroll.setWidget(scroll_content)
        main_layout.addWidget(unified_scroll, 1)

        self.main_tabs.addTab(main_tab, "Основное")

        log_tab = QWidget()
        log_layout = QVBoxLayout(log_tab)
        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setMaximumBlockCount(1000)
        log_layout.addWidget(self.log_edit)
        self.main_tabs.addTab(log_tab, "Лог")

    def _build_source_group(self) -> QGroupBox:
        group = QGroupBox("Источник и действия")
        layout = QVBoxLayout(group)

        url_row = QHBoxLayout()
        url_row.addWidget(QLabel("URL источника:"))
        self.qidian_url_edit = QLineEdit("https://www.qidian.com/book/1041604040/")
        self.qidian_url_edit.setPlaceholderText(
            "https://www.qidian.com/book/1041604040/ или https://fanqienovel.com/page/7229603492648717324"
        )
        url_row.addWidget(self.qidian_url_edit, 1)
        self.visible_qidian_checkbox = QCheckBox("Открывать источник видимо")
        url_row.addWidget(self.visible_qidian_checkbox)
        layout.addLayout(url_row)

        action_row = QHBoxLayout()
        self.fetch_qidian_btn = QPushButton("Получить данные источника")
        self.fetch_qidian_btn.clicked.connect(self._fetch_qidian)
        action_row.addWidget(self.fetch_qidian_btn)

        self.prepare_ai_btn = QPushButton("Подготовить перевод, жанры, теги и промпт")
        self.prepare_ai_btn.clicked.connect(self._prepare_ai)
        action_row.addWidget(self.prepare_ai_btn)

        self.cover_prompt_btn = QPushButton("Сгенерировать промпт для обложки")
        self.cover_prompt_btn.clicked.connect(self._generate_cover_prompt)
        action_row.addWidget(self.cover_prompt_btn)

        self.login_rulate_btn = QPushButton("Войти в Rulate")
        self.login_rulate_btn.clicked.connect(self._login_rulate)
        action_row.addWidget(self.login_rulate_btn)

        self.fill_rulate_btn = QPushButton("Открыть и заполнить Rulate")
        self.fill_rulate_btn.clicked.connect(self._fill_rulate)
        action_row.addWidget(self.fill_rulate_btn)
        action_row.addStretch()
        layout.addLayout(action_row)

        hint = QLabel(
            "Форма Rulate заполняется в открытом браузере. Проверьте поля и сохраните вручную."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        return group

    def _build_preview_group(self) -> QSplitter:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_qidian_group())
        splitter.addWidget(self._build_rulate_group())
        splitter.setSizes([560, 560])
        return splitter

    def _build_qidian_group(self) -> QGroupBox:
        group = QGroupBox("Данные источника")
        layout = QFormLayout(group)

        self.original_title_edit = QLineEdit()
        self.author_edit = QLineEdit()
        self.source_url_edit = QLineEdit()
        self.cover_url_edit = QLineEdit()
        self.cover_url_edit.editingFinished.connect(self._load_cover_preview_from_current_url)
        self.reload_cover_btn = QPushButton("Загрузить")
        self.reload_cover_btn.clicked.connect(self._load_cover_preview_from_current_url)
        cover_url_widget = QWidget()
        cover_url_layout = QHBoxLayout(cover_url_widget)
        cover_url_layout.setContentsMargins(0, 0, 0, 0)
        cover_url_layout.addWidget(self.cover_url_edit, 1)
        cover_url_layout.addWidget(self.reload_cover_btn)

        self.cover_preview_label = QLabel("Обложка не загружена")
        self.cover_preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_preview_label.setFixedSize(150, 210)
        self.cover_preview_label.setStyleSheet(
            "QLabel { border: 1px solid #444; background: #15191d; color: #888; }"
        )
        self.description_edit = QTextEdit()
        self.description_edit.setAcceptRichText(False)
        self.description_edit.setMinimumHeight(170)

        layout.addRow("Название:", self.original_title_edit)
        layout.addRow("Автор:", self.author_edit)
        layout.addRow("Оригинал:", self.source_url_edit)
        layout.addRow("Обложка URL:", cover_url_widget)
        layout.addRow("Превью:", self.cover_preview_label)
        layout.addRow("Описание:", self.description_edit)

        return group

    def _build_rulate_group(self) -> QGroupBox:
        group = QGroupBox("Черновик Rulate")
        layout = QFormLayout(group)

        self.english_title_edit = QLineEdit()
        self.translated_title_edit = QLineEdit()
        self.translated_description_edit = QTextEdit()
        self.translated_description_edit.setAcceptRichText(False)
        self.translated_description_edit.setMinimumHeight(170)
        self.genres_edit = QLineEdit()
        self.genres_edit.setPlaceholderText("фэнтези, мистика, приключения")
        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("китайская новелла, тайны, сверхъестественное")
        self.cover_prompt_edit = QTextEdit()
        self.cover_prompt_edit.setAcceptRichText(False)
        self.cover_prompt_edit.setMinimumHeight(130)
        self.cover_prompt_edit.setPlaceholderText("Здесь появится английский промпт для генерации обложки")

        layout.addRow("Название EN:", self.english_title_edit)
        layout.addRow("Название RU:", self.translated_title_edit)
        layout.addRow("Описание RU:", self.translated_description_edit)
        layout.addRow("Жанры:", self.genres_edit)
        layout.addRow("Теги:", self.tags_edit)
        layout.addRow("Промпт обложки:", self.cover_prompt_edit)

        return group

    def _connect_ai_widgets(self) -> None:
        provider_id = self.key_widget.get_selected_provider()
        self.model_settings_widget.set_available_models(provider_id)
        self.key_widget.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        self.model_settings_widget.model_combo.currentIndexChanged.connect(self._on_model_changed)
        self._on_model_changed(self.model_settings_widget.model_combo.currentIndex())

    def _on_provider_changed(self, _index: int) -> None:
        provider_id = self.key_widget.get_selected_provider()
        self.model_settings_widget.set_available_models(provider_id)
        self._on_model_changed(self.model_settings_widget.model_combo.currentIndex())

    def _on_model_changed(self, index: int) -> None:
        if index < 0:
            return
        model_id = self.model_settings_widget.model_combo.itemData(index)
        if model_id:
            self.key_widget.set_current_model(model_id)

    def _fetch_qidian(self) -> None:
        url = self.qidian_url_edit.text().strip()
        if not validate_source_url(url):
            QMessageBox.warning(
                self,
                "Источник",
                "Введите ссылку вида https://www.qidian.com/book/1041604040/ "
                "или https://fanqienovel.com/page/7229603492648717324",
            )
            return
        self.fetch_qidian_btn.setEnabled(False)
        worker = QidianFetchWorker(url, visible_browser=self.visible_qidian_checkbox.isChecked())
        worker.log_signal.connect(self._log)
        worker.metadata_ready.connect(self._apply_qidian_metadata)
        worker.finished_signal.connect(lambda: self._worker_finished(worker, self.fetch_qidian_btn))
        self._workers.append(worker)
        worker.start()

    def _prepare_ai(self) -> None:
        metadata = self._collect_qidian_metadata()
        if not metadata.title_original or not metadata.description:
            QMessageBox.warning(self, "AI", "Сначала получите или заполните название и описание Qidian.")
            return

        provider_id = self.key_widget.get_selected_provider()
        active_keys = self.key_widget.get_active_keys()
        model_settings = self.model_settings_widget.get_settings()
        self.prepare_ai_btn.setEnabled(False)
        worker = AiPrepareWorker(
            metadata,
            provider_id,
            model_settings,
            active_keys,
            self.settings_manager,
            visible_browser=self.visible_qidian_checkbox.isChecked(),
        )
        worker.log_signal.connect(self._log)
        worker.prepared_ready.connect(self._apply_prepared_metadata)
        worker.finished_signal.connect(lambda: self._worker_finished(worker, self.prepare_ai_btn))
        self._workers.append(worker)
        worker.start()

    def _generate_cover_prompt(self) -> None:
        url = self.source_url_edit.text().strip() or self.qidian_url_edit.text().strip()
        if not validate_source_url(url):
            QMessageBox.warning(
                self,
                "Обложка",
                "Введите ссылку вида https://www.qidian.com/book/1041604040/ "
                "или https://fanqienovel.com/page/7229603492648717324",
            )
            return

        title_ru = self.translated_title_edit.text().strip()
        if not title_ru:
            QMessageBox.warning(self, "Обложка", "Сначала заполните русское название.")
            return

        provider_id = self.key_widget.get_selected_provider()
        active_keys = self.key_widget.get_active_keys()
        model_settings = self.model_settings_widget.get_settings()
        self.cover_prompt_btn.setEnabled(False)
        worker = CoverPromptWorker(
            url,
            title_ru,
            provider_id,
            model_settings,
            active_keys,
            self.settings_manager,
            original_description=self.description_edit.toPlainText().strip(),
            visible_browser=self.visible_qidian_checkbox.isChecked(),
        )
        worker.log_signal.connect(self._log)
        worker.prompt_ready.connect(self._apply_cover_prompt)
        worker.finished_signal.connect(lambda: self._worker_finished(worker, self.cover_prompt_btn))
        self._workers.append(worker)
        worker.start()

    def _login_rulate(self) -> None:
        self.login_rulate_btn.setEnabled(False)
        worker = RulateLoginWorker()
        worker.log_signal.connect(self._log)
        worker.finished_signal.connect(lambda: self._worker_finished(worker, self.login_rulate_btn))
        self._workers.append(worker)
        worker.start()

    def _fill_rulate(self) -> None:
        qidian = self._collect_qidian_metadata()
        prepared = self._collect_prepared_metadata()
        try:
            prepared.tags = normalize_rulate_tags(prepared.tags)
            self.tags_edit.setText(", ".join(prepared.tags))
        except ValueError as error:
            QMessageBox.warning(self, "Rulate", str(error))
            return

        missing = []
        if not qidian.title_original:
            missing.append("китайское название")
        if not qidian.author_name:
            missing.append("автор")
        if not qidian.source_url:
            missing.append("ссылка на оригинал")
        if not prepared.english_title:
            missing.append("английское название")
        if not prepared.translated_title:
            missing.append("название на языке перевода")
        if not prepared.translated_description:
            missing.append("описание")
        if len(prepared.genres) < 3:
            missing.append("минимум 3 жанра")
        if len(prepared.tags) < 3:
            missing.append("минимум 3 тега")
        if missing:
            QMessageBox.warning(self, "Rulate", "Не хватает данных: " + ", ".join(missing))
            return

        self.fill_rulate_btn.setEnabled(False)
        worker = RulateFillWorker(RulateBookDraft(qidian=qidian, prepared=prepared))
        worker.log_signal.connect(self._log)
        worker.finished_signal.connect(lambda: self._worker_finished(worker, self.fill_rulate_btn))
        self._workers.append(worker)
        worker.start()

    def _apply_qidian_metadata(self, metadata: QidianBookMetadata) -> None:
        self._qidian_metadata = metadata
        self.original_title_edit.setText(metadata.title_original)
        self.author_edit.setText(metadata.author_name)
        self.source_url_edit.setText(metadata.source_url)
        self.cover_url_edit.setText(metadata.cover_url)
        self._set_cover_preview(metadata.cover_image_data)
        if metadata.cover_url and not metadata.cover_image_data:
            self._load_cover_preview_from_current_url()
        self.description_edit.setPlainText(metadata.description)
        self._update_action_state()

    def _apply_prepared_metadata(self, prepared: PreparedRulateMetadata) -> None:
        self._prepared_metadata = prepared
        self.english_title_edit.setText(prepared.english_title)
        self.translated_title_edit.setText(prepared.translated_title)
        self.translated_description_edit.setPlainText(prepared.translated_description)
        self.genres_edit.setText(", ".join(prepared.genres))
        self.tags_edit.setText(", ".join(prepared.tags))
        if prepared.cover_prompt:
            self.cover_prompt_edit.setPlainText(prepared.cover_prompt)
        self._update_action_state()

    def _apply_cover_prompt(self, prompt: str) -> None:
        self.cover_prompt_edit.setPlainText(prompt)
        self._update_action_state()

    def _collect_qidian_metadata(self) -> QidianBookMetadata:
        return QidianBookMetadata(
            source_url=self.source_url_edit.text().strip() or self.qidian_url_edit.text().strip(),
            title_original=self.original_title_edit.text().strip(),
            author_name=self.author_edit.text().strip(),
            description=self.description_edit.toPlainText().strip(),
            cover_url=self.cover_url_edit.text().strip(),
        )

    def _collect_prepared_metadata(self) -> PreparedRulateMetadata:
        return PreparedRulateMetadata(
            english_title=self.english_title_edit.text().strip(),
            translated_title=self.translated_title_edit.text().strip(),
            translated_description=self.translated_description_edit.toPlainText().strip(),
            genres=_split_csv(self.genres_edit.text()),
            tags=_split_csv(self.tags_edit.text()),
            cover_prompt=self.cover_prompt_edit.toPlainText().strip(),
        )

    def _update_action_state(self) -> None:
        self.prepare_ai_btn.setEnabled(True)
        self.login_rulate_btn.setEnabled(True)
        self.fill_rulate_btn.setEnabled(True)

    def _worker_finished(self, worker, button: QPushButton) -> None:
        if worker in self._workers:
            self._workers.remove(worker)
        button.setEnabled(True)
        self._update_action_state()

    def _log(self, level: str, message: str) -> None:
        if level == "DEBUG" and not message:
            return
        self.log_edit.appendPlainText(f"[{level}] {message}")
        self.log_edit.verticalScrollBar().setValue(self.log_edit.verticalScrollBar().maximum())

    def _set_cover_preview(self, image_data: bytes) -> None:
        pixmap = QPixmap()
        if image_data and pixmap.loadFromData(image_data):
            scaled = pixmap.scaled(
                self.cover_preview_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.cover_preview_label.setPixmap(scaled)
            return
        self.cover_preview_label.clear()
        self.cover_preview_label.setText("Обложка не загружена")

    def _load_cover_preview_from_current_url(self) -> None:
        cover_url = self.cover_url_edit.text().strip()
        if not cover_url:
            self._set_cover_preview(b"")
            return
        image_data = _download_cover_image(
            cover_url,
            referer=self.source_url_edit.text().strip() or self.qidian_url_edit.text().strip(),
        )
        self._set_cover_preview(image_data)
