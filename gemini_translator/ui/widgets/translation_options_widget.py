# gemini_translator/ui/widgets/translation_options_widget.py

import math
import os
import zipfile

from PyQt6 import QtCore
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QGridLayout, QGroupBox, QLabel, QVBoxLayout

from ...api import config as api_config
from ...utils.epub_tools import get_epub_chapter_sizes_with_cache
from ...utils.language_tools import LanguageDetector
from .common_widgets import NoScrollSpinBox


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
            "\u0432 \u0441\u0438\u043c\u0432\u043e\u043b\u0430\u0445."
        )
        self._recommended_task_size = self.task_size_spin.value()

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
        self.info_label.setStyleSheet("color: #aaa; font-size: 10px; font-weight: bold;")
        self.info_label.setWordWrap(True)
        self.info_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        settings_layout.addWidget(
            QLabel("\u0420\u0430\u0437\u043c\u0435\u0440 \u0437\u0430\u0434\u0430\u0447\u0438 (\u0441\u0438\u043c\u0432\u043e\u043b\u044b):"),
            0,
            0,
        )
        settings_layout.addWidget(self.task_size_spin, 0, 1)
        settings_layout.addWidget(
            QLabel("\u0420\u0430\u0437\u0431\u0438\u0435\u043d\u0438\u0435 \u043f\u043e\u0441\u043b. \u043f\u0435\u0440\u0435\u0432\u043e\u0434\u0430:"),
            1,
            0,
        )
        settings_layout.addWidget(self.sequential_splits_spin, 1, 1)
        settings_layout.addWidget(self.info_label, 3, 0, 1, 2)

        main_layout.addWidget(modes_group, 0, 0)
        main_layout.addWidget(settings_group, 0, 1)

        self.batch_checkbox.toggled.connect(self._on_mode_changed)
        self.chunking_checkbox.toggled.connect(self._on_mode_changed)
        self.chunk_on_error_checkbox.toggled.connect(self._on_mode_changed)
        self.sequential_checkbox.toggled.connect(self._on_mode_changed)
        self.sequential_splits_spin.valueChanged.connect(self._on_mode_changed)
        self.task_size_spin.valueChanged.connect(self._on_task_size_changed)
        line_edit = self.task_size_spin.lineEdit()
        if line_edit is not None:
            line_edit.textEdited.connect(self._mark_task_size_user_defined)

        self._on_mode_changed(emit_signal=False)

    def get_settings(self):
        return {
            "use_batching": self.batch_checkbox.isChecked(),
            "chunking": self.chunking_checkbox.isChecked(),
            "chunk_on_error": self.chunk_on_error_checkbox.isChecked(),
            "sequential_translation": self.sequential_checkbox.isChecked(),
            "sequential_translation_splits": self.sequential_splits_spin.value(),
            "task_size_limit": self.task_size_spin.value(),
            "task_size_limit_user_defined": self._task_size_user_defined,
        }

    def is_task_size_user_defined(self):
        return self._task_size_user_defined

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
            if has_explicit_task_size:
                user_defined = bool(settings.get("task_size_limit_user_defined", True))
                self._set_task_size_value(target_task_size, user_defined=user_defined)
        finally:
            self.blockSignals(False)

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
                if isinstance(cached_entry, dict):
                    total_size = int(
                        cached_entry.get("total_size", chapter_sizes.get(file_name, 0)) or 0
                    )
                    self.chapter_compositions[file_name] = {
                        "code_size": int(cached_entry.get("code_size", 0) or 0),
                        "text_size": int(cached_entry.get("text_size", 0) or 0),
                        "is_cjk": bool(cached_entry.get("is_cjk", False)),
                        "total_size": total_size,
                    }
                else:
                    missing_files.append(file_name)

            if missing_files:
                with open(epub_path, "rb") as epub_file, zipfile.ZipFile(epub_file, "r") as epub_zip:
                    for file_name in missing_files:
                        content_str = epub_zip.read(file_name).decode("utf-8", errors="ignore")
                        soup = BeautifulSoup(content_str, "html.parser")
                        visible_text = soup.get_text()

                        text_size = len(visible_text)
                        total_size = int(
                            chapter_sizes.get(file_name, len(content_str)) or len(content_str)
                        )
                        chapter_data = {
                            "code_size": max(0, total_size - text_size),
                            "text_size": text_size,
                            "is_cjk": LanguageDetector.is_cjk_text(visible_text),
                            "total_size": total_size,
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

        output_token_weight = (
            (avg_code_ratio / api_config.CHARS_PER_ASCII_TOKEN)
            + ((1 - avg_code_ratio) * expansion_factor / api_config.CHARS_PER_CYRILLIC_TOKEN)
        )

        recommended_input_size = (
            int(safe_limit_out_tokens / output_token_weight)
            if output_token_weight > 0
            else 30000
        )
        recommended_input_size = max(5000, min(recommended_input_size, 300000))
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
            self.info_label.setStyleSheet("color: #aaa;")
            return

        current_target_size = self.task_size_spin.value()
        sequential_enabled = self.sequential_checkbox.isChecked()
        sequential_splits = self.sequential_splits_spin.value() if sequential_enabled else 1
        sequential_suffix = (
            f" \u0432 {sequential_splits} \u0446\u0435\u043f\u043e\u0447\u043a\u0430\u0445"
            if sequential_enabled and sequential_splits > 1
            else ""
        )

        if self.chunking_checkbox.isChecked():
            total_tasks = sum(
                math.ceil(comp["total_size"] / current_target_size)
                if comp["total_size"] > 0
                else 1
                for comp in self.chapter_compositions.values()
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
            batches, current_size = 0, 0
            for file_name in self.html_files:
                size = self.chapter_compositions.get(file_name, {}).get("total_size", 0)
                if current_size + size > current_target_size and current_size > 0:
                    batches += 1
                    current_size = 0
                current_size += size
            if current_size > 0:
                batches += 1
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
            self.chunking_checkbox.setChecked(False)
            self.chunk_on_error_checkbox.setChecked(False)
        elif sender == self.chunking_checkbox and is_chunk:
            self.batch_checkbox.setChecked(False)
        elif sender == self.chunk_on_error_checkbox and self.chunk_on_error_checkbox.isChecked():
            self.batch_checkbox.setChecked(False)

        if self.batch_checkbox.isChecked():
            self.chunking_checkbox.setChecked(False)

        self.batch_checkbox.blockSignals(False)
        self.chunking_checkbox.blockSignals(False)
        self.chunk_on_error_checkbox.blockSignals(False)
        self.sequential_checkbox.blockSignals(False)
        self.sequential_splits_spin.blockSignals(False)

        self.batch_checkbox.setEnabled(len(self.html_files) > 1)
        self.sequential_splits_spin.setEnabled(self.sequential_checkbox.isChecked())

        self._update_info_text()
        if emit_signal:
            self.settings_changed.emit()

    def _update_batching_availability(self):
        can_batch = len(self.html_files) > 1
        self.batch_checkbox.setEnabled(can_batch)
        if not can_batch:
            self.batch_checkbox.setChecked(False)
