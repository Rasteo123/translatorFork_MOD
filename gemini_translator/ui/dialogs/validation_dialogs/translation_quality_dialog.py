# -*- coding: utf-8 -*-
"""The «Качество перевода» section: one report, four actions, one setup place."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTableView,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ....qa.capabilities import CAPABILITY_DESCRIPTIONS, QaCapabilityKey, QaCapabilitySettings
from ....qa.settings import QaSettings
from .translation_quality_models import (
    BookQaReportSnapshot,
    ChapterQaTableModel,
    DECISION_LABELS,
)


EMBEDDING_PROVIDER_CHOICES = (
    ("Автоматически (ключ сессии)", "auto"),
    ("Gemini", "gemini"),
    ("OpenAI-совместимый", "openai_compatible"),
)
EMBEDDING_MODEL_SUGGESTIONS = {
    "auto": ("gemini-embedding-001", "text-embedding-004"),
    "gemini": ("gemini-embedding-001", "text-embedding-004"),
    "openai_compatible": (
        "text-embedding-3-small",
        "text-embedding-3-large",
        "text-embedding-ada-002",
    ),
    "local_onnx": (),
}
CAPABILITY_ORDER = (
    QaCapabilityKey.RAZDEL,
    QaCapabilityKey.LANGUAGE_TOOL,
    QaCapabilityKey.SLOVNET,
    QaCapabilityKey.COMETKIWI,
)


def mask_key(value: str) -> str:
    """Show enough of a key to recognise it and never enough to leak it."""
    text = str(value or "")
    if len(text) <= 8:
        return "•" * len(text)
    return f"{text[:4]}…{text[-4:]}"


class TranslationQualityDialog(QDialog):
    """Show what quality control found and let the user act on it."""

    check_chapter_requested = pyqtSignal(str)
    check_all_requested = pyqtSignal()
    undo_chapter_requested = pyqtSignal(str)
    undo_all_requested = pyqtSignal()
    cancel_requested = pyqtSignal()
    settings_changed = pyqtSignal(object)
    embedding_test_requested = pyqtSignal(object)

    def __init__(self, parent=None, *, settings: QaSettings | None = None, api_keys=()) -> None:
        super().__init__(parent)
        self.setWindowTitle("Качество перевода")
        self.setMinimumSize(1040, 640)
        self._settings = settings or QaSettings()
        self._api_keys = tuple(api_keys or ())
        self._loading = True

        self.table_model = ChapterQaTableModel(self)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        self.tabs.addTab(self._build_report_tab(), "Отчёт")
        self.tabs.addTab(self._build_settings_tab(), "Настройки проверки")
        layout.addWidget(self.tabs)
        layout.addLayout(self._build_action_bar())

        self._apply_settings_to_widgets()
        self._loading = False
        self._update_action_state()

    # -- report ------------------------------------------------------------

    def _build_report_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        self.summary_label = QLabel("Проверка ещё не выполнялась.", page)
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        splitter = QSplitter(Qt.Orientation.Vertical, page)
        self.table = QTableView(splitter)
        self.table.setModel(self.table_model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.selectionModel().selectionChanged.connect(self._on_selection_changed)
        splitter.addWidget(self.table)

        self.details = QTextEdit(splitter)
        self.details.setReadOnly(True)
        self.details.setPlainText("Выберите главу, чтобы увидеть решения проверки.")
        splitter.addWidget(self.details)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter)

        self.progress = QProgressBar(page)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        return page

    def _build_action_bar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.check_chapter_button = QPushButton("Проверить и исправить главу", self)
        self.check_all_button = QPushButton("Проверить и исправить все главы", self)
        self.undo_chapter_button = QPushButton("Отменить исправления главы", self)
        self.undo_all_button = QPushButton("Отменить все автоматические исправления", self)
        self.cancel_button = QPushButton("Остановить проверку", self)
        self.cancel_button.setEnabled(False)

        self.check_chapter_button.clicked.connect(self._request_check_chapter)
        self.check_all_button.clicked.connect(self.check_all_requested.emit)
        self.undo_chapter_button.clicked.connect(self._request_undo_chapter)
        self.undo_all_button.clicked.connect(self._request_undo_all)
        self.cancel_button.clicked.connect(self.cancel_requested.emit)

        for button in (
            self.check_chapter_button,
            self.check_all_button,
            self.undo_chapter_button,
            self.undo_all_button,
            self.cancel_button,
        ):
            row.addWidget(button)
        row.addStretch(1)
        self.close_button = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Close, parent=self
        )
        self.close_button.button(QDialogButtonBox.StandardButton.Close).setText(
            "Закрыть"
        )
        self.close_button.rejected.connect(self.reject)
        row.addWidget(self.close_button)
        return row

    # -- settings ----------------------------------------------------------

    def _build_settings_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.addWidget(self._build_stage_group(page))
        layout.addWidget(self._build_embedding_group(page))
        layout.addWidget(self._build_capability_group(page))
        layout.addStretch(1)
        area = QScrollArea(self)
        area.setWidgetResizable(True)
        area.setWidget(page)
        return area

    def _build_stage_group(self, parent) -> QGroupBox:
        group = QGroupBox("Что проверять после каждой главы", parent)
        layout = QVBoxLayout(group)
        self.completeness_check = QCheckBox("Проверять полноту перевода", group)
        self.repair_omissions_check = QCheckBox(
            "Автоматически допереводить подтверждённые пропуски", group
        )
        self.language_check = QCheckBox("Проверять язык перевода", group)
        self.repair_language_check = QCheckBox(
            "Автоматически исправлять объективные языковые дефекты", group
        )
        self.final_pass_check = QCheckBox(
            "Делать итоговый проход по книге в конце сессии", group
        )
        for widget in (
            self.completeness_check,
            self.repair_omissions_check,
            self.language_check,
            self.repair_language_check,
            self.final_pass_check,
        ):
            widget.toggled.connect(self._on_settings_edited)
            layout.addWidget(widget)
        return group

    def _build_embedding_group(self, parent) -> QGroupBox:
        group = QGroupBox("Смысловое сравнение (embeddings)", parent)
        layout = QFormLayout(group)
        self.embedding_provider_combo = QComboBox(group)
        for label, value in EMBEDDING_PROVIDER_CHOICES:
            self.embedding_provider_combo.addItem(label, value)
        self.embedding_provider_combo.currentIndexChanged.connect(
            self._on_embedding_provider_changed
        )
        layout.addRow("Провайдер:", self.embedding_provider_combo)

        self.embedding_key_combo = QComboBox(group)
        self.embedding_key_combo.setEditable(False)
        self.embedding_key_combo.currentIndexChanged.connect(self._on_key_choice_changed)
        layout.addRow("Ключ:", self.embedding_key_combo)

        self.embedding_key_edit = QLineEdit(group)
        self.embedding_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.embedding_key_edit.setPlaceholderText("Свой ключ для эмбеддингов")
        self.embedding_key_edit.textChanged.connect(self._on_settings_edited)
        layout.addRow("Свой ключ:", self.embedding_key_edit)

        self.embedding_model_combo = QComboBox(group)
        self.embedding_model_combo.setEditable(True)
        self.embedding_model_combo.currentTextChanged.connect(self._on_settings_edited)
        layout.addRow("Модель:", self.embedding_model_combo)

        self.embedding_base_url_edit = QLineEdit(group)
        self.embedding_base_url_edit.setPlaceholderText("https://api.openai.com/v1")
        self.embedding_base_url_edit.textChanged.connect(self._on_settings_edited)
        layout.addRow("Адрес сервиса:", self.embedding_base_url_edit)

        self.embedding_status_label = QLabel("", group)
        self.embedding_status_label.setWordWrap(True)
        layout.addRow("", self.embedding_status_label)

        self.embedding_test_button = QPushButton("Проверить подключение", group)
        self.embedding_test_button.clicked.connect(
            lambda: self.embedding_test_requested.emit(self.qa_settings())
        )
        layout.addRow("", self.embedding_test_button)
        return group

    def _build_capability_group(self, parent) -> QGroupBox:
        group = QGroupBox("Дополнительные анализаторы", parent)
        layout = QVBoxLayout(group)
        self.capability_checks: dict[QaCapabilityKey, QCheckBox] = {}
        for key in CAPABILITY_ORDER:
            description = CAPABILITY_DESCRIPTIONS[key]
            check = QCheckBox(description.title, group)
            check.setToolTip(
                "\n".join(
                    (
                        description.summary,
                        f"Нагрузка: {description.load_level} ({', '.join(description.resources)})",
                        f"Скорость: {description.speed_impact}",
                        f"Польза: {description.quality_benefit}",
                        f"Риск: {description.quality_risk}",
                        f"Сеть: {description.network_policy}",
                    )
                )
            )
            check.toggled.connect(self._on_settings_edited)
            layout.addWidget(check)
            caption = QLabel(f"    {description.summary}", group)
            caption.setWordWrap(True)
            layout.addWidget(caption)
            self.capability_checks[key] = check

        endpoint_row = QHBoxLayout()
        endpoint_row.addWidget(QLabel("Адрес LanguageTool:", group))
        self.language_tool_endpoint_edit = QLineEdit(group)
        self.language_tool_endpoint_edit.setPlaceholderText(
            "например http://localhost:8081/v2/check"
        )
        self.language_tool_endpoint_edit.textChanged.connect(self._on_settings_edited)
        endpoint_row.addWidget(self.language_tool_endpoint_edit)
        layout.addLayout(endpoint_row)
        self.capability_checks[QaCapabilityKey.LANGUAGE_TOOL].toggled.connect(
            self.language_tool_endpoint_edit.setEnabled
        )
        self.language_tool_endpoint_edit.setEnabled(
            self.capability_checks[QaCapabilityKey.LANGUAGE_TOOL].isChecked()
        )

        self.capability_status_label = QLabel("", group)
        self.capability_status_label.setWordWrap(True)
        layout.addWidget(self.capability_status_label)
        return group

    # -- public API --------------------------------------------------------

    def set_report(self, snapshot: BookQaReportSnapshot) -> None:
        """Replace the report with an immutable snapshot from the journal."""
        self.table_model.set_snapshot(snapshot)
        blocked = snapshot.blocked_chapters
        repaired = snapshot.repaired_chapters
        parts = [f"Глав в отчёте: {len(snapshot.rows)}"]
        if repaired:
            parts.append(f"с автоматическими исправлениями: {len(repaired)}")
        if blocked:
            parts.append(
                "перевод остановлен на: " + ", ".join(blocked[:5])
                + ("…" if len(blocked) > 5 else "")
            )
        if snapshot.limited_mode_chapters:
            parts.append(
                f"без смыслового сравнения: {len(snapshot.limited_mode_chapters)}"
            )
        self.summary_label.setText(". ".join(parts) + ".")
        self._update_action_state()

    def set_busy(self, busy: bool) -> None:
        """Disable everything a running check must own exclusively."""
        self._busy = bool(busy)
        self.progress.setVisible(bool(busy))
        self.cancel_button.setEnabled(bool(busy))
        self._update_action_state()

    def set_progress(self, checked: int, total: int, chapter_id: str = "") -> None:
        """Show honest progress of a whole-book pass."""
        total = max(int(total), 0)
        self.progress.setVisible(True)
        self.progress.setRange(0, total or 0)
        self.progress.setValue(min(int(checked), total) if total else 0)
        suffix = f" — {chapter_id}" if chapter_id else ""
        self.progress.setFormat(f"Проверено %v из %m{suffix}")

    def set_status(self, message: str) -> None:
        """Report one short outcome or failure without touching the report."""
        self.embedding_status_label.setText(str(message or ""))

    def selected_chapter_id(self) -> str:
        """Return the chapter the user is acting on, if any."""
        indexes = self.table.selectionModel().selectedRows()
        if not indexes:
            return ""
        row = self.table_model.row_at(indexes[0].row())
        return row.chapter_id if row else ""

    def select_chapter(self, chapter_id: str) -> bool:
        """Move the table to one chapter; used when navigating from a finding."""
        row = self.table_model.row_for_chapter(chapter_id)
        if row < 0:
            return False
        self.table.selectRow(row)
        return True

    def qa_settings(self) -> QaSettings:
        """Return the settings exactly as the widgets currently express them."""
        provider = self.embedding_provider_combo.currentData() or "auto"
        chosen_key = self.embedding_key_combo.currentData() or ""
        manual_key = self.embedding_key_edit.text().strip()
        return QaSettings(
            check_completeness_after_chapter=self.completeness_check.isChecked(),
            auto_repair_confirmed_omissions=self.repair_omissions_check.isChecked(),
            check_language_after_chapter=self.language_check.isChecked(),
            auto_repair_objective_language_issues=self.repair_language_check.isChecked(),
            embedding_provider=str(provider),
            embedding_model=self.embedding_model_combo.currentText().strip(),
            embedding_api_key=manual_key or str(chosen_key),
            embedding_base_url=self.embedding_base_url_edit.text().strip(),
            correction_model_mode=self._settings.correction_model_mode,
            correction_provider=self._settings.correction_provider,
            correction_model=self._settings.correction_model,
            final_book_pass=self.final_pass_check.isChecked(),
            capabilities=QaCapabilitySettings(
                razdel_enabled=self.capability_checks[QaCapabilityKey.RAZDEL].isChecked(),
                language_tool_enabled=self.capability_checks[
                    QaCapabilityKey.LANGUAGE_TOOL
                ].isChecked(),
                slovnet_enabled=self.capability_checks[QaCapabilityKey.SLOVNET].isChecked(),
                cometkiwi_enabled=self.capability_checks[
                    QaCapabilityKey.COMETKIWI
                ].isChecked(),
            ),
            language_tool_endpoint=self.language_tool_endpoint_edit.text().strip(),
            language_tool_mode=self._settings.language_tool_mode,
            language_tool_disabled_rules=self._settings.language_tool_disabled_rules,
            slovnet_cpu_threads=self._settings.slovnet_cpu_threads,
            slovnet_batch_size=self._settings.slovnet_batch_size,
            cometkiwi_runner_path=self._settings.cometkiwi_runner_path,
            cometkiwi_model=self._settings.cometkiwi_model,
            cometkiwi_device=self._settings.cometkiwi_device,
            cometkiwi_license_accepted=self._settings.cometkiwi_license_accepted,
        )

    # -- internals ---------------------------------------------------------

    def _apply_settings_to_widgets(self) -> None:
        settings = self._settings
        self.completeness_check.setChecked(settings.check_completeness_after_chapter)
        self.repair_omissions_check.setChecked(settings.auto_repair_confirmed_omissions)
        self.language_check.setChecked(settings.check_language_after_chapter)
        self.repair_language_check.setChecked(
            settings.auto_repair_objective_language_issues
        )
        self.final_pass_check.setChecked(settings.final_book_pass)

        index = self.embedding_provider_combo.findData(settings.embedding_provider)
        self.embedding_provider_combo.setCurrentIndex(max(index, 0))
        self._reload_key_choices(settings.embedding_api_key)
        self._reload_model_choices(settings.embedding_provider, settings.embedding_model)
        self.embedding_base_url_edit.setText(settings.embedding_base_url)
        self.embedding_base_url_edit.setEnabled(
            settings.embedding_provider == "openai_compatible"
        )

        self.capability_checks[QaCapabilityKey.RAZDEL].setChecked(
            settings.capabilities.razdel_enabled
        )
        self.capability_checks[QaCapabilityKey.LANGUAGE_TOOL].setChecked(
            settings.capabilities.language_tool_enabled
        )
        self.capability_checks[QaCapabilityKey.SLOVNET].setChecked(
            settings.capabilities.slovnet_enabled
        )
        self.capability_checks[QaCapabilityKey.COMETKIWI].setChecked(
            settings.capabilities.cometkiwi_enabled
        )
        self.language_tool_endpoint_edit.setText(settings.language_tool_endpoint)
        self._refresh_setup_warnings()

    def _reload_key_choices(self, selected_key: str) -> None:
        self.embedding_key_combo.blockSignals(True)
        self.embedding_key_combo.clear()
        self.embedding_key_combo.addItem("Ключ сессии перевода", "")
        matched = False
        for item in self._api_keys:
            key = str(item.get("key", "") if isinstance(item, dict) else item or "")
            if not key:
                continue
            provider = str(item.get("provider", "")) if isinstance(item, dict) else ""
            label = f"{mask_key(key)} ({provider})" if provider else mask_key(key)
            self.embedding_key_combo.addItem(label, key)
            if key == selected_key:
                self.embedding_key_combo.setCurrentIndex(
                    self.embedding_key_combo.count() - 1
                )
                matched = True
        self.embedding_key_combo.blockSignals(False)
        self.embedding_key_edit.blockSignals(True)
        self.embedding_key_edit.setText("" if matched or not selected_key else selected_key)
        self.embedding_key_edit.blockSignals(False)

    def _reload_model_choices(self, provider: str, selected_model: str) -> None:
        suggestions = EMBEDDING_MODEL_SUGGESTIONS.get(provider, ())
        self.embedding_model_combo.blockSignals(True)
        self.embedding_model_combo.clear()
        for name in suggestions:
            self.embedding_model_combo.addItem(name)
        # An empty model field would silently fall back to a default the user
        # never saw; show the one that will actually be used.
        self.embedding_model_combo.setEditText(
            selected_model or (suggestions[0] if suggestions else "")
        )
        self.embedding_model_combo.blockSignals(False)

    def _on_embedding_provider_changed(self) -> None:
        provider = str(self.embedding_provider_combo.currentData() or "auto")
        self._reload_model_choices(provider, self.embedding_model_combo.currentText().strip())
        self.embedding_base_url_edit.setEnabled(provider == "openai_compatible")
        self._on_settings_edited()

    def _on_key_choice_changed(self) -> None:
        if self.embedding_key_combo.currentData():
            self.embedding_key_edit.blockSignals(True)
            self.embedding_key_edit.clear()
            self.embedding_key_edit.blockSignals(False)
        self._on_settings_edited()

    def _on_settings_edited(self) -> None:
        if self._loading:
            return
        self._settings = self.qa_settings()
        self._refresh_setup_warnings()
        self.settings_changed.emit(self._settings)

    def _refresh_setup_warnings(self) -> None:
        problem = self._settings.embedding_setup_problem()
        self.embedding_status_label.setText(
            problem or "Готово к смысловому сравнению."
        )
        missing = self._settings.unsatisfied_requirements()
        self.capability_status_label.setText(
            "Не настроены и поэтому выключены: " + ", ".join(missing)
            if missing
            else ""
        )

    def _on_selection_changed(self, *_args) -> None:
        chapter_id = self.selected_chapter_id()
        row = self.table_model.row_at(
            self.table_model.row_for_chapter(chapter_id)
        ) if chapter_id else None
        if row is None:
            self.details.setPlainText("Выберите главу, чтобы увидеть решения проверки.")
        else:
            decisions = self.table_model.snapshot.decisions_by_chapter.get(chapter_id, ())
            lines = [
                f"Глава: {row.chapter_id}",
                f"Языковая пара: {row.language_pair}",
                f"Коэффициент длины: {row.length_ratio:.2f} — {row.profile_status}",
                f"Книжная норма: {row.book_position}",
                f"Возможные пропуски: {row.possible_gaps}, "
                f"подтверждённые: {row.confirmed_gaps}, "
                f"исправлено: {row.applied_repairs}",
                f"Конфликты терминов: {row.glossary_conflicts}, "
                f"остатки исходника: {row.untranslated_fragments}, "
                f"языковые дефекты: {row.language_issues}",
                f"Риск: {row.risk_label}",
            ]
            if row.blocked_reason:
                lines.append(f"Перевод остановлен: {row.blocked_reason}")
            if decisions:
                lines.append("")
                lines.append("Решения проверки:")
                lines.extend(
                    f"  • {DECISION_LABELS.get(decision, decision)}"
                    for decision in decisions
                )
            self.details.setPlainText("\n".join(lines))
        self._update_action_state()

    def _update_action_state(self) -> None:
        busy = getattr(self, "_busy", False)
        has_rows = self.table_model.rowCount() > 0
        chapter_id = self.selected_chapter_id()
        repaired = set(self.table_model.snapshot.repaired_chapters)
        self.check_chapter_button.setEnabled(bool(chapter_id) and not busy)
        self.check_all_button.setEnabled(not busy)
        self.undo_chapter_button.setEnabled(
            bool(chapter_id) and chapter_id in repaired and not busy
        )
        self.undo_all_button.setEnabled(bool(repaired) and not busy)
        self.cancel_button.setEnabled(busy)
        self.table.setEnabled(has_rows)

    def _request_check_chapter(self) -> None:
        chapter_id = self.selected_chapter_id()
        if chapter_id:
            self.check_chapter_requested.emit(chapter_id)

    def _request_undo_chapter(self) -> None:
        chapter_id = self.selected_chapter_id()
        if not chapter_id:
            return
        if self._confirm_undo([chapter_id]):
            self.undo_chapter_requested.emit(chapter_id)

    def _request_undo_all(self) -> None:
        chapters = list(self.table_model.snapshot.repaired_chapters)
        if chapters and self._confirm_undo(chapters):
            self.undo_all_requested.emit()

    def _confirm_undo(self, chapters) -> bool:
        listing = "\n".join(f"  • {chapter}" for chapter in chapters[:20])
        if len(chapters) > 20:
            listing += f"\n  … и ещё {len(chapters) - 20}"
        answer = QMessageBox.question(
            self,
            "Отменить автоматические исправления",
            "Будут восстановлены исходные версии глав:\n"
            f"{listing}\n\nГлавы, изменённые вручную после исправления, "
            "останутся нетронутыми.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes
