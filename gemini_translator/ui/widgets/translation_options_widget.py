# gemini_translator/ui/widgets/translation_options_widget.py

import math
import os
import zipfile

from PyQt6 import QtCore
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from ...api import config as api_config
from ...utils.epub_tools import (
    CHAPTER_SIZE_CACHE_METRIC,
    CHAPTER_SIZE_CACHE_VERSION,
    TASK_SIZE_UNIT_CHARS,
    estimate_epub_chapter_input_tokens,
    get_epub_chapter_sizes_with_cache,
)
from ...utils.helpers import estimate_gemini_tokens
from ...utils.language_tools import LanguageDetector
from .common_widgets import NoScrollSpinBox
from gemini_translator.ui import theme_manager


class TranslationOptionsWidget(QGroupBox):
    """Widget for task batching/chunking settings and recommendations."""

    settings_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(
            "\u041e\u043f\u0442\u0438\u043c\u0438\u0437\u0430\u0446\u0438\u044f "
            "(\u041f\u0430\u043a\u0435\u0442\u044b / \u0427\u0430\u043d\u043a\u0438)",
            parent,
        )

        self.html_files = []
        self.chapter_compositions = {}
        self.model_settings_widget = None
        self._analysis_signature = None
        self._recommended_task_size = 10000
        self._task_size_user_defined = False
        self._changing_task_size_programmatically = False

        self._init_ui()

    def _init_ui(self):
        main_layout = QGridLayout(self)

        modes_group = QGroupBox("\u0420\u0435\u0436\u0438\u043c \u043e\u0431\u0440\u0430\u0431\u043e\u0442\u043a\u0438")
        modes_group.setObjectName("modes_group")
        modes_layout = QVBoxLayout(modes_group)

        self.batch_checkbox = QCheckBox(
            "\u041f\u0430\u043a\u0435\u0442\u043d\u0430\u044f \u043e\u0431\u0440\u0430\u0431\u043e\u0442\u043a\u0430"
        )
        self.chunking_checkbox = QCheckBox(
            "\u0410\u0432\u0442\u043e-\u0447\u0430\u043d\u043a\u0438\u043d\u0433 "
            "(\u0440\u0430\u0437\u0434\u0435\u043b\u0435\u043d\u0438\u0435)"
        )
        self.chunk_on_error_checkbox = QCheckBox(
            "\u0427\u0430\u043d\u043a\u0438\u043d\u0433 \u043f\u0440\u0438 "
            "\u043e\u0448\u0438\u0431\u043a\u0430\u0445"
        )
        self.chunk_on_error_checkbox.setChecked(False)
        self.sequential_checkbox = QCheckBox(
            "\u041f\u043e\u0441\u043b\u0435\u0434\u043e\u0432\u0430\u0442\u0435\u043b\u044c\u043d\u044b\u0439 "
            "\u043f\u0435\u0440\u0435\u0432\u043e\u0434 \u0433\u043b\u0430\u0432"
        )
        self.sequential_checkbox.setToolTip(
            "\u041f\u0435\u0440\u0435\u0432\u043e\u0434\u0438\u0442 \u0433\u043b\u0430\u0432\u044b "
            "\u0432 \u043f\u043e\u0440\u044f\u0434\u043a\u0435 \u043e\u0447\u0435\u0440\u0435\u0434\u0438 \u0438 \u043f\u043e\u0434\u0441\u0442\u0430\u0432\u043b\u044f\u0435\u0442 "
            "\u043f\u0440\u043e\u0448\u043b\u0443\u044e \u043f\u0435\u0440\u0435\u0432\u0435\u0434\u0435\u043d\u043d\u0443\u044e "
            "\u0433\u043b\u0430\u0432\u0443 \u043a\u0430\u043a \u0440\u0435\u0444\u0435\u0440\u0435\u043d\u0441. "
            "\u041f\u0430\u043a\u0435\u0442\u044b \u0438 \u0447\u0430\u043d\u043a\u0438 "
            "\u0440\u0430\u0431\u043e\u0442\u0430\u044e\u0442 \u043a\u0430\u043a \u043e\u0431\u044b\u0447\u043d\u043e."
        )

        modes_layout.addWidget(self.batch_checkbox)
        modes_layout.addWidget(self.chunking_checkbox)
        modes_layout.addWidget(self.chunk_on_error_checkbox)
        modes_layout.addWidget(self.sequential_checkbox)

        settings_group = QGroupBox(
            "\u041d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438 \u0438 "
            "\u0420\u0435\u043a\u043e\u043c\u0435\u043d\u0434\u0430\u0446\u0438\u0438"
        )
        settings_layout = QGridLayout(settings_group)

        self.task_size_spin = NoScrollSpinBox()
        self.task_size_spin.setRange(500, 350000)
        self.task_size_spin.setSingleStep(500)
        self.task_size_spin.setValue(10000)
        self.task_size_spin.setToolTip(
            "\u0426\u0435\u043b\u0435\u0432\u043e\u0439 \u0440\u0430\u0437\u043c\u0435\u0440 "
            "\u0432\u0445\u043e\u0434\u043d\u044b\u0445 \u0434\u0430\u043d\u043d\u044b\u0445 "
            "\u0434\u043b\u044f \u043e\u0434\u043d\u043e\u0439 \u0437\u0430\u0434\u0430\u0447\u0438 "
            "(\u043f\u0430\u043a\u0435\u0442\u0430 \u0438\u043b\u0438 \u0447\u0430\u043d\u043a\u0430) "
            "\u0432 HTML-\u0441\u0438\u043c\u0432\u043e\u043b\u0430\u0445."
        )
        self._recommended_task_size = self.task_size_spin.value()
        self.task_size_chars_checkbox = QCheckBox("\u0421\u0438\u043c\u0432\u043e\u043b\u044b")
        self.task_size_chars_checkbox.setChecked(True)
        self.task_size_chars_checkbox.setVisible(False)
        self.task_size_chars_checkbox.setToolTip(
            "\u0420\u0430\u0437\u043c\u0435\u0440 \u0437\u0430\u0434\u0430\u0447\u0438 "
            "\u0441\u0447\u0438\u0442\u0430\u0435\u0442\u0441\u044f \u0432 HTML-"
            "\u0441\u0438\u043c\u0432\u043e\u043b\u0430\u0445."
        )
        task_size_input_widget = QWidget()
        task_size_input_layout = QHBoxLayout(task_size_input_widget)
        task_size_input_layout.setContentsMargins(0, 0, 0, 0)
        task_size_input_layout.setSpacing(8)
        task_size_input_layout.addWidget(self.task_size_spin, 1)
        task_size_input_layout.addWidget(self.task_size_chars_checkbox)

        self.sequential_splits_spin = NoScrollSpinBox()
        self.sequential_splits_spin.setRange(1, 32)
        self.sequential_splits_spin.setValue(1)
        self.sequential_splits_spin.setToolTip(
            "\u0421\u043a\u043e\u043b\u044c\u043a\u043e \u043f\u0430\u0440\u0430\u043b\u043b\u0435\u043b\u044c\u043d\u044b\u0445 "
            "\u043f\u043e\u0441\u043b\u0435\u0434\u043e\u0432\u0430\u0442\u0435\u043b\u044c\u043d\u044b\u0445 "
            "\u0446\u0435\u043f\u043e\u0447\u0435\u043a \u0437\u0430\u043f\u0443\u0441\u0442\u0438\u0442\u044c: "
            "1 = \u0441 \u043d\u0430\u0447\u0430\u043b\u0430, 2 = \u0441 \u043d\u0430\u0447\u0430\u043b\u0430 "
            "\u0438 \u0441 \u0441\u0435\u0440\u0435\u0434\u0438\u043d\u044b, \u0438 \u0442.\u0434."
        )
        self.sequential_splits_spin.setEnabled(False)

        self.info_label = QLabel(
            "\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 "
            "\u0433\u043b\u0430\u0432\u044b \u0434\u043b\u044f \u0430\u043d\u0430\u043b\u0438\u0437\u0430."
        )
        self.info_label.setStyleSheet(f"color: {theme_manager.color('text_muted')}; font-size: 10px; font-weight: bold;")
        self.info_label.setWordWrap(True)
        self.info_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        self.task_size_label = QLabel()
        settings_layout.addWidget(self.task_size_label, 0, 0)
        settings_layout.addWidget(task_size_input_widget, 0, 1)
        settings_layout.addWidget(
            QLabel("\u0420\u0430\u0437\u0431\u0438\u0435\u043d\u0438\u0435 \u043f\u043e\u0441\u043b. \u043f\u0435\u0440\u0435\u0432\u043e\u0434\u0430:"),
            1,
            0,
        )
        settings_layout.addWidget(self.sequential_splits_spin, 1, 1)
        settings_layout.addWidget(self.info_label, 3, 0, 1, 2)

        orchestration_group = QGroupBox("Оркестрация провайдеров")
        orchestration_layout = QGridLayout(orchestration_group)

        self.parallel_providers_checkbox = QCheckBox("Параллельные провайдеры")
        self.parallel_providers_edit = QLineEdit("")
        self.parallel_providers_edit.setPlaceholderText("provider[:model], provider[:model]")

        self.parallel_provider_strategy_combo = QComboBox()
        self.parallel_provider_strategy_combo.addItem("Свести в итог", "merge")
        self.parallel_provider_strategy_combo.addItem("Лучший по оценке", "best_score")
        self.parallel_provider_strategy_combo.addItem("Первый успешный", "first_success")

        self.multi_pass_checkbox = QCheckBox("Несколько вариантов главы")
        self.multi_pass_count_spin = NoScrollSpinBox()
        self.multi_pass_count_spin.setRange(1, 8)
        self.multi_pass_count_spin.setValue(3)

        self.multi_pass_strategy_combo = QComboBox()
        self.multi_pass_strategy_combo.addItem("Свести в итог", "merge")
        self.multi_pass_strategy_combo.addItem("Лучший по оценке", "best_score")
        self.multi_pass_strategy_combo.addItem("Первый успешный", "first_success")

        orchestration_layout.addWidget(self.parallel_providers_checkbox, 0, 0, 1, 2)
        orchestration_layout.addWidget(QLabel("Провайдеры:"), 1, 0)
        orchestration_layout.addWidget(self.parallel_providers_edit, 1, 1)
        orchestration_layout.addWidget(QLabel("Стратегия:"), 2, 0)
        orchestration_layout.addWidget(self.parallel_provider_strategy_combo, 2, 1)
        orchestration_layout.addWidget(self.multi_pass_checkbox, 3, 0, 1, 2)
        orchestration_layout.addWidget(QLabel("Вариантов:"), 4, 0)
        orchestration_layout.addWidget(self.multi_pass_count_spin, 4, 1)
        orchestration_layout.addWidget(QLabel("Стратегия:"), 5, 0)
        orchestration_layout.addWidget(self.multi_pass_strategy_combo, 5, 1)

        main_layout.addWidget(modes_group, 0, 0)
        main_layout.addWidget(settings_group, 0, 1)
        main_layout.addWidget(orchestration_group, 1, 0, 1, 2)
        # Keep the group boxes at their natural height: any extra vertical space
        # given to this widget (e.g. by the tasks-tab splitter) is absorbed by an
        # empty trailing row instead of stretching the boxes above.
        main_layout.setRowStretch(2, 1)

        self.batch_checkbox.toggled.connect(self._on_mode_changed)
        self.chunking_checkbox.toggled.connect(self._on_mode_changed)
        self.chunk_on_error_checkbox.toggled.connect(self._on_mode_changed)
        self.sequential_checkbox.toggled.connect(self._on_mode_changed)
        self.sequential_splits_spin.valueChanged.connect(self._on_mode_changed)
        self.parallel_providers_checkbox.toggled.connect(self._on_mode_changed)
        self.parallel_providers_edit.textChanged.connect(self._on_mode_changed)
        self.parallel_provider_strategy_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.multi_pass_checkbox.toggled.connect(self._on_mode_changed)
        self.multi_pass_count_spin.valueChanged.connect(self._on_mode_changed)
        self.multi_pass_strategy_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.task_size_spin.valueChanged.connect(self._on_task_size_changed)
        self.task_size_chars_checkbox.toggled.connect(self._on_task_size_unit_changed)
        line_edit = self.task_size_spin.lineEdit()
        if line_edit is not None:
            line_edit.textEdited.connect(self._mark_task_size_user_defined)

        self._apply_task_size_unit_ui()
        self._on_mode_changed(emit_signal=False)

    def get_settings(self):
        return {
            "use_batching": self.batch_checkbox.isChecked(),
            "chunking": self.chunking_checkbox.isChecked(),
            "chunk_on_error": self.chunk_on_error_checkbox.isChecked(),
            "sequential_translation": self.sequential_checkbox.isChecked(),
            "sequential_translation_splits": self.sequential_splits_spin.value(),
            "parallel_providers_enabled": self.parallel_providers_checkbox.isChecked(),
            "parallel_provider_list": self.parallel_providers_edit.text().strip(),
            "parallel_provider_strategy": self.parallel_provider_strategy_combo.currentData() or "merge",
            "multi_pass_enabled": self.multi_pass_checkbox.isChecked(),
            "multi_pass_chapter_translation": self.multi_pass_checkbox.isChecked(),
            "multi_pass_count": self.multi_pass_count_spin.value(),
            "multi_pass_strategy": self.multi_pass_strategy_combo.currentData() or "merge",
            "task_size_limit": self.task_size_spin.value(),
            "task_size_unit": self.task_size_unit(),
            "task_size_limit_user_defined": self._task_size_user_defined,
        }

    def task_size_unit(self):
        return TASK_SIZE_UNIT_CHARS

    def is_task_size_user_defined(self):
        return self._task_size_user_defined

    def _apply_task_size_unit_ui(self):
        self.task_size_label.setText(
            "\u0420\u0430\u0437\u043c\u0435\u0440 \u0437\u0430\u0434\u0430\u0447\u0438 "
            "(\u0441\u0438\u043c\u0432\u043e\u043b\u044b):"
        )
        self.task_size_spin.setSingleStep(100)
        self.task_size_spin.setToolTip(
            "\u0426\u0435\u043b\u0435\u0432\u043e\u0439 \u0440\u0430\u0437\u043c\u0435\u0440 "
            "\u0432\u0445\u043e\u0434\u043d\u044b\u0445 HTML-\u0434\u0430\u043d\u043d\u044b\u0445 "
            "\u0434\u043b\u044f \u043e\u0434\u043d\u043e\u0439 \u0437\u0430\u0434\u0430\u0447\u0438 "
            "(\u043f\u0430\u043a\u0435\u0442\u0430 \u0438\u043b\u0438 \u0447\u0430\u043d\u043a\u0430) "
            "\u0432 \u0441\u0438\u043c\u0432\u043e\u043b\u0430\u0445."
        )

    def _chapter_size_for_current_unit(self, file_name):
        composition = self.chapter_compositions.get(file_name, {})
        if self.task_size_unit() == TASK_SIZE_UNIT_CHARS:
            return int(composition.get("total_chars", composition.get("total_size", 0)) or 0)
        return int(composition.get("total_size", 0) or 0)

    def chapter_sizes_for_current_unit(self):
        return {
            file_name: self._chapter_size_for_current_unit(file_name)
            for file_name in self.chapter_compositions
        }

    def _chunk_count_for_size(self, size, target_size):
        if size <= 0:
            return 1
        return max(1, math.ceil(size / max(1, target_size)))

    def _count_sequential_batch_tasks(self, file_names, target_size, include_chunking):
        task_count = 0
        current_size = 0
        for file_name in file_names:
            size = self._chapter_size_for_current_unit(file_name)
            if size > target_size:
                if current_size > 0:
                    task_count += 1
                    current_size = 0
                task_count += self._chunk_count_for_size(size, target_size) if include_chunking else 1
                continue

            if current_size and current_size + size > target_size:
                task_count += 1
                current_size = 0
            current_size += size

        if current_size > 0:
            task_count += 1
        return task_count

    def _count_non_sequential_batch_tasks(self, file_names, target_size, include_chunking):
        task_count = 0
        open_batch_sizes = []
        for file_name in file_names:
            size = self._chapter_size_for_current_unit(file_name)
            if size > target_size:
                task_count += self._chunk_count_for_size(size, target_size) if include_chunking else 1
                continue

            for index, batch_size in enumerate(open_batch_sizes):
                if batch_size + size <= target_size:
                    open_batch_sizes[index] = batch_size + size
                    break
            else:
                open_batch_sizes.append(size)

        return task_count + len(open_batch_sizes)

    def _sequential_chapter_chains_for_preview(self):
        split_count = self.sequential_splits_spin.value() if self.sequential_checkbox.isChecked() else 1
        split_count = max(1, min(int(split_count or 1), len(self.html_files)))
        chains = []
        for index in range(split_count):
            start = (index * len(self.html_files)) // split_count
            end = ((index + 1) * len(self.html_files)) // split_count
            if start < end:
                chains.append(self.html_files[start:end])
        return chains or [self.html_files]

    def _estimate_batch_task_count(self, include_chunking=False):
        target_size = max(1, self.task_size_spin.value())
        if self.sequential_checkbox.isChecked():
            return sum(
                self._count_sequential_batch_tasks(chain, target_size, include_chunking)
                for chain in self._sequential_chapter_chains_for_preview()
            )
        return self._count_non_sequential_batch_tasks(
            self.html_files,
            target_size,
            include_chunking,
        )

    def _set_task_size_value(self, value, *, user_defined=None):
        self._changing_task_size_programmatically = True
        try:
            self.task_size_spin.setValue(int(value))
        except (TypeError, ValueError):
            return
        finally:
            self._changing_task_size_programmatically = False

        if user_defined is not None:
            self._task_size_user_defined = bool(user_defined)

    def set_task_size_limit(self, value, *, user_defined=False):
        self._set_task_size_value(value, user_defined=user_defined)

    def _mark_task_size_user_defined(self, *_args):
        if not self._changing_task_size_programmatically:
            self._task_size_user_defined = True

    def set_settings(self, settings: dict):
        settings = settings or {}
        current_batching = self.batch_checkbox.isChecked()
        current_chunking = self.chunking_checkbox.isChecked()
        current_chunk_on_error = self.chunk_on_error_checkbox.isChecked()
        current_sequential = self.sequential_checkbox.isChecked()
        current_sequential_splits = self.sequential_splits_spin.value()
        current_parallel_enabled = self.parallel_providers_checkbox.isChecked()
        current_parallel_list = self.parallel_providers_edit.text()
        current_parallel_strategy = self.parallel_provider_strategy_combo.currentData() or "merge"
        current_multi_pass_enabled = self.multi_pass_checkbox.isChecked()
        current_multi_pass_count = self.multi_pass_count_spin.value()
        current_multi_pass_strategy = self.multi_pass_strategy_combo.currentData() or "merge"
        current_task_size = self.task_size_spin.value()
        has_explicit_task_size = (
            "task_size_limit" in settings and settings.get("task_size_limit") is not None
        )

        try:
            target_task_size = int(settings.get("task_size_limit", current_task_size))
        except (TypeError, ValueError):
            target_task_size = current_task_size
        self.blockSignals(True)
        try:
            self.batch_checkbox.setChecked(settings.get("use_batching", current_batching))
            self.chunking_checkbox.setChecked(settings.get("chunking", current_chunking))
            self.chunk_on_error_checkbox.setChecked(
                settings.get("chunk_on_error", current_chunk_on_error)
            )
            self.sequential_checkbox.setChecked(
                settings.get("sequential_translation", current_sequential)
            )
            self.sequential_splits_spin.setValue(
                settings.get("sequential_translation_splits", current_sequential_splits)
            )
            self.parallel_providers_checkbox.setChecked(
                settings.get("parallel_providers_enabled", current_parallel_enabled)
            )
            parallel_list_value = settings.get("parallel_provider_list", current_parallel_list)
            if isinstance(parallel_list_value, (list, tuple)):
                parallel_list_value = ", ".join(str(item) for item in parallel_list_value)
            self.parallel_providers_edit.setText(str(parallel_list_value or ""))
            parallel_strategy = settings.get("parallel_provider_strategy", current_parallel_strategy)
            parallel_index = self.parallel_provider_strategy_combo.findData(parallel_strategy)
            if parallel_index != -1:
                self.parallel_provider_strategy_combo.setCurrentIndex(parallel_index)
            self.multi_pass_checkbox.setChecked(
                settings.get(
                    "multi_pass_enabled",
                    settings.get("multi_pass_chapter_translation", current_multi_pass_enabled),
                )
            )
            self.multi_pass_count_spin.setValue(
                settings.get("multi_pass_count", current_multi_pass_count)
            )
            multi_pass_strategy = settings.get("multi_pass_strategy", current_multi_pass_strategy)
            multi_pass_index = self.multi_pass_strategy_combo.findData(multi_pass_strategy)
            if multi_pass_index != -1:
                self.multi_pass_strategy_combo.setCurrentIndex(multi_pass_index)
            if has_explicit_task_size:
                user_defined = bool(settings.get("task_size_limit_user_defined", True))
                self._set_task_size_value(target_task_size, user_defined=user_defined)
            self.task_size_chars_checkbox.setChecked(True)
        finally:
            self.blockSignals(False)

        self._apply_task_size_unit_ui()
        self._on_mode_changed(emit_signal=False)
        self._update_info_text()

    def update_chapter_data(self, html_files, epub_path, project_manager=None):
        self.html_files = list(html_files or [])
        self._analyze_chapters(epub_path, project_manager=project_manager)
        self._update_batching_availability()
        self._update_info_text()

    def _build_analysis_signature(self, epub_path):
        if not epub_path or not os.path.exists(epub_path):
            return None
        try:
            stat = os.stat(epub_path)
        except OSError:
            return None
        return (
            os.path.abspath(epub_path),
            stat.st_mtime_ns,
            stat.st_size,
            tuple(self.html_files),
        )

    def _build_epub_analysis_metadata(self, epub_path):
        try:
            epub_stat = os.stat(epub_path)
            with open(epub_path, "rb") as epub_file, zipfile.ZipFile(epub_file, "r") as epub_zip:
                chapter_info_list = [
                    (info.filename, info.file_size)
                    for info in epub_zip.infolist()
                    if info.filename.lower().endswith((".html", ".xhtml", ".htm"))
                ]
        except (OSError, zipfile.BadZipFile, FileNotFoundError):
            return None

        return {
            "epub_name": os.path.basename(epub_path),
            "epub_size": epub_stat.st_size,
            "content_checksum": sum(size for _, size in chapter_info_list),
            "metric": CHAPTER_SIZE_CACHE_METRIC,
            "version": CHAPTER_SIZE_CACHE_VERSION,
        }

    def _load_cached_chapter_analysis(self, project_manager, epub_path):
        if not project_manager:
            return {}, {}

        cache_data = project_manager.load_chapter_analysis_cache()
        if not isinstance(cache_data, dict):
            return {}, {}

        current_metadata = self._build_epub_analysis_metadata(epub_path)
        if not current_metadata or cache_data.get("metadata") != current_metadata:
            return {}, current_metadata or {}

        cached_chapters = cache_data.get("chapters", {})
        if not isinstance(cached_chapters, dict):
            cached_chapters = {}
        return cached_chapters, current_metadata

    def _save_cached_chapter_analysis(self, project_manager, metadata, chapters):
        if not project_manager or not metadata:
            return
        project_manager.save_chapter_analysis_cache(
            {
                "metadata": metadata,
                "chapters": chapters,
            }
        )

    def _analyze_chapters(self, epub_path, project_manager=None):
        signature = self._build_analysis_signature(epub_path)
        if signature and signature == self._analysis_signature and self.chapter_compositions:
            return

        self.chapter_compositions = {}
        self._analysis_signature = signature
        if not self.html_files or not epub_path:
            return

        try:
            from bs4 import BeautifulSoup

            chapter_sizes = (
                get_epub_chapter_sizes_with_cache(project_manager, epub_path)
                if project_manager
                else {}
            )
            cached_chapters, cache_metadata = self._load_cached_chapter_analysis(
                project_manager, epub_path
            )
            merged_cached_chapters = dict(cached_chapters)
            missing_files = []

            for file_name in self.html_files:
                cached_entry = cached_chapters.get(file_name)
                if (
                    isinstance(cached_entry, dict)
                    and "total_chars" in cached_entry
                    and "text_chars" in cached_entry
                ):
                    total_size = int(
                        cached_entry.get(
                            "input_tokens",
                            cached_entry.get("total_size", chapter_sizes.get(file_name, 0)),
                        )
                        or 0
                    )
                    code_size = int(
                        cached_entry.get("code_tokens", cached_entry.get("code_size", 0)) or 0
                    )
                    text_size = int(
                        cached_entry.get("text_tokens", cached_entry.get("text_size", 0)) or 0
                    )
                    self.chapter_compositions[file_name] = {
                        "code_size": code_size,
                        "text_size": text_size,
                        "is_cjk": bool(cached_entry.get("is_cjk", False)),
                        "total_size": total_size,
                        "code_tokens": code_size,
                        "text_tokens": text_size,
                        "input_tokens": total_size,
                        "code_chars": int(cached_entry.get("code_chars", 0) or 0),
                        "text_chars": int(cached_entry.get("text_chars", 0) or 0),
                        "total_chars": int(cached_entry.get("total_chars", 0) or 0),
                    }
                else:
                    missing_files.append(file_name)

            if missing_files:
                with open(epub_path, "rb") as epub_file, zipfile.ZipFile(epub_file, "r") as epub_zip:
                    for file_name in missing_files:
                        content_str = epub_zip.read(file_name).decode("utf-8", errors="ignore")
                        soup = BeautifulSoup(content_str, "html.parser")
                        visible_text = soup.get_text()

                        text_tokens = estimate_gemini_tokens(visible_text)
                        total_tokens = int(
                            chapter_sizes.get(file_name, 0)
                            or estimate_epub_chapter_input_tokens(content_str)
                        )
                        code_tokens = max(0, total_tokens - text_tokens)
                        total_chars = len(content_str)
                        text_chars = len(visible_text)
                        code_chars = max(0, total_chars - text_chars)
                        chapter_data = {
                            "code_size": code_tokens,
                            "text_size": text_tokens,
                            "is_cjk": LanguageDetector.is_cjk_text(visible_text),
                            "total_size": total_tokens,
                            "code_tokens": code_tokens,
                            "text_tokens": text_tokens,
                            "input_tokens": total_tokens,
                            "code_chars": code_chars,
                            "text_chars": text_chars,
                            "total_chars": total_chars,
                        }
                        self.chapter_compositions[file_name] = chapter_data
                        merged_cached_chapters[file_name] = chapter_data

            if missing_files and cache_metadata:
                self._save_cached_chapter_analysis(
                    project_manager, cache_metadata, merged_cached_chapters
                )
        except Exception as exc:
            print(f"[WIDGET ERROR] chapter analysis failed for '{epub_path}': {exc}")
            self.chapter_compositions = {}

    def update_recommendations_from_model(self, model_name: str):
        if not self.chapter_compositions:
            self._recommended_task_size = 30000
            if not self._task_size_user_defined:
                self._set_task_size_value(30000, user_defined=False)
            self._update_info_text()
            return

        model_config = api_config.all_models().get(model_name, {})
        limit_out_tokens = model_config.get(
            "max_output_tokens", api_config.default_max_output_tokens()
        )
        safe_limit_out_tokens = limit_out_tokens * api_config.MODEL_OUTPUT_SAFETY_MARGIN

        total_code = sum(comp["code_size"] for comp in self.chapter_compositions.values())
        total_text = sum(comp["text_size"] for comp in self.chapter_compositions.values())

        if (total_code + total_text) == 0:
            self._recommended_task_size = 30000
            if not self._task_size_user_defined:
                self._set_task_size_value(30000, user_defined=False)
            self._update_info_text()
            return

        is_any_cjk = any(comp["is_cjk"] for comp in self.chapter_compositions.values())
        avg_code_ratio = total_code / (total_code + total_text)
        expansion_factor = (
            api_config.CJK_EXPANSION_FACTOR
            if is_any_cjk
            else api_config.ALPHABETIC_EXPANSION_FACTOR
        )

        source_chars_per_text_token = (
            1.5 if is_any_cjk else api_config.CHARS_PER_ASCII_TOKEN
        )
        translated_text_tokens_per_input_token = (
            source_chars_per_text_token * expansion_factor
        ) / api_config.CHARS_PER_CYRILLIC_TOKEN
        output_token_weight = (
            avg_code_ratio
            + ((1 - avg_code_ratio) * translated_text_tokens_per_input_token)
        )

        recommended_input_size = (
            int(safe_limit_out_tokens / output_token_weight)
            if output_token_weight > 0
            else 30000
        )
        if self.task_size_unit() == TASK_SIZE_UNIT_CHARS:
            total_input_tokens = sum(
                int(comp.get("total_size", 0) or 0)
                for comp in self.chapter_compositions.values()
            )
            total_input_chars = sum(
                int(comp.get("total_chars", 0) or 0)
                for comp in self.chapter_compositions.values()
            )
            chars_per_token = (
                total_input_chars / total_input_tokens
                if total_input_tokens > 0 and total_input_chars > 0
                else (1.5 if is_any_cjk else api_config.CHARS_PER_ASCII_TOKEN)
            )
            recommended_input_size = int(recommended_input_size * chars_per_token)
        recommended_input_size = max(500, min(recommended_input_size, 300000))
        self._recommended_task_size = recommended_input_size

        if not self._task_size_user_defined:
            self._set_task_size_value(recommended_input_size, user_defined=False)

        self._update_info_text()

    def _update_info_text(self):
        if not self.html_files or not self.chapter_compositions:
            self.info_label.setText(
                "\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 "
                "\u0433\u043b\u0430\u0432\u044b \u0434\u043b\u044f \u0430\u043d\u0430\u043b\u0438\u0437\u0430."
            )
            self.info_label.setStyleSheet(f"color: {theme_manager.color('text_muted')};")
            return

        current_target_size = self.task_size_spin.value()
        sequential_enabled = self.sequential_checkbox.isChecked()
        sequential_splits = self.sequential_splits_spin.value() if sequential_enabled else 1
        sequential_suffix = (
            f" \u0432 {sequential_splits} \u0446\u0435\u043f\u043e\u0447\u043a\u0430\u0445"
            if sequential_enabled and sequential_splits > 1
            else ""
        )

        if self.batch_checkbox.isChecked() and self.chunking_checkbox.isChecked():
            total_tasks = self._estimate_batch_task_count(include_chunking=True)
            sequential_prefix = (
                "\u043f\u043e\u0441\u043b\u0435\u0434\u043e\u0432\u0430\u0442\u0435\u043b\u044c\u043d\u044b\u0445 "
                if sequential_enabled
                else ""
            )
            self.info_label.setText(
                f"\u0411\u0443\u0434\u0435\u0442 \u0441\u043e\u0437\u0434\u0430\u043d\u043e ~{total_tasks} "
                f"{sequential_prefix}\u0437\u0430\u0434\u0430\u0447 "
                f"(\u043f\u0430\u043a\u0435\u0442\u044b + \u0447\u0430\u043d\u043a\u0438){sequential_suffix}."
            )
            return

        if self.chunking_checkbox.isChecked():
            total_tasks = sum(
                math.ceil(self._chapter_size_for_current_unit(file_name) / current_target_size)
                if self._chapter_size_for_current_unit(file_name) > 0
                else 1
                for file_name in self.chapter_compositions
            )
            sequential_prefix = (
                "\u043f\u043e\u0441\u043b\u0435\u0434\u043e\u0432\u0430\u0442\u0435\u043b\u044c\u043d\u044b\u0445 "
                if sequential_enabled
                else ""
            )
            self.info_label.setText(
                f"\u0411\u0443\u0434\u0435\u0442 \u0441\u043e\u0437\u0434\u0430\u043d\u043e ~{total_tasks} "
                f"{sequential_prefix}\u0437\u0430\u0434\u0430\u0447 (\u0447\u0430\u043d\u043a\u043e\u0432){sequential_suffix}."
            )
        elif self.batch_checkbox.isChecked():
            batches = self._estimate_batch_task_count(include_chunking=False)
            sequential_prefix = (
                "\u043f\u043e\u0441\u043b\u0435\u0434\u043e\u0432\u0430\u0442\u0435\u043b\u044c\u043d\u044b\u0445 "
                if sequential_enabled
                else ""
            )
            self.info_label.setText(
                f"\u0411\u0443\u0434\u0435\u0442 \u0441\u043e\u0437\u0434\u0430\u043d\u043e ~{batches} "
                f"{sequential_prefix}\u043f\u0430\u043a\u0435\u0442\u043e\u0432{sequential_suffix}."
            )
        else:
            sequential_prefix = (
                "\u043f\u043e\u0441\u043b\u0435\u0434\u043e\u0432\u0430\u0442\u0435\u043b\u044c\u043d\u044b\u0445 "
                if sequential_enabled
                else ""
            )
            self.info_label.setText(
                f"\u0411\u0443\u0434\u0435\u0442 {len(self.html_files)} "
                f"{sequential_prefix}\u0438\u043d\u0434\u0438\u0432\u0438\u0434\u0443\u0430\u043b\u044c\u043d\u044b\u0445 "
                f"\u0437\u0430\u0434\u0430\u0447{sequential_suffix}."
            )

    def _on_task_size_changed(self, _value: int):
        self._mark_task_size_user_defined()

        self._update_info_text()
        self.settings_changed.emit()

    def _on_task_size_unit_changed(self, _checked: bool):
        self._apply_task_size_unit_ui()
        self._update_info_text()
        self.settings_changed.emit()

    def _on_mode_changed(self, *_args, emit_signal=True):
        sender = self.sender()
        is_batch = self.batch_checkbox.isChecked()
        is_chunk = self.chunking_checkbox.isChecked()

        self.batch_checkbox.blockSignals(True)
        self.chunking_checkbox.blockSignals(True)
        self.chunk_on_error_checkbox.blockSignals(True)
        self.sequential_checkbox.blockSignals(True)
        self.sequential_splits_spin.blockSignals(True)

        if sender == self.batch_checkbox and is_batch:
            self.chunk_on_error_checkbox.setChecked(False)
        elif sender == self.chunk_on_error_checkbox and self.chunk_on_error_checkbox.isChecked():
            self.batch_checkbox.setChecked(False)

        self.batch_checkbox.blockSignals(False)
        self.chunking_checkbox.blockSignals(False)
        self.chunk_on_error_checkbox.blockSignals(False)
        self.sequential_checkbox.blockSignals(False)
        self.sequential_splits_spin.blockSignals(False)

        self.batch_checkbox.setEnabled(len(self.html_files) > 1)
        self.sequential_splits_spin.setEnabled(self.sequential_checkbox.isChecked())
        self.parallel_providers_edit.setEnabled(self.parallel_providers_checkbox.isChecked())
        self.parallel_provider_strategy_combo.setEnabled(self.parallel_providers_checkbox.isChecked())
        self.multi_pass_count_spin.setEnabled(self.multi_pass_checkbox.isChecked())
        self.multi_pass_strategy_combo.setEnabled(self.multi_pass_checkbox.isChecked())

        self._update_info_text()
        if emit_signal:
            self.settings_changed.emit()

    def _update_batching_availability(self):
        can_batch = len(self.html_files) > 1
        self.batch_checkbox.setEnabled(can_batch)
        if not can_batch:
            self.batch_checkbox.setChecked(False)
