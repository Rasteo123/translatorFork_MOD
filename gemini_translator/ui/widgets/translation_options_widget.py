# gemini_translator/ui/widgets/translation_options_widget.py

import math
import os
import zipfile
import time
import functools
from PyQt6 import QtWidgets, QtCore
from PyQt6.QtWidgets import (
    QGroupBox, QGridLayout, QCheckBox, QWidget, QSpinBox, QLabel, QHBoxLayout, QVBoxLayout
)
from PyQt6.QtCore import pyqtSignal

from ...api import config as api_config
from ...utils.language_tools import LanguageDetector
from ...utils.epub_tools import get_epub_chapter_sizes_with_cache
from .common_widgets import NoScrollSpinBox, NoScrollDoubleSpinBox # <-- НОВЫЙ ИМПОРТ

# В файле gemini_translator/ui/widgets/translation_options_widget.py

class TranslationOptionsWidget(QGroupBox):
    """
    Универсальный виджет для управления оптимизацией задач перевода.
    Версия 11.0: Упрощенная, управляемая извне.
    """
    settings_changed = pyqtSignal()
    def __init__(self, parent=None):
        super().__init__("Оптимизация (Пакеты / Чанки)", parent)
        
        self.html_files = []
        self.chapter_compositions = {}
        self.model_settings_widget = None
        self._analysis_signature = None
        
        self._init_ui()
        
    def _init_ui(self):
        main_layout = QGridLayout(self)
        
        modes_group = QGroupBox("Режим обработки")
        modes_layout = QVBoxLayout(modes_group)
        modes_group.setObjectName("modes_group") # <--- ВОТ ЭТА СТРОКА
        self.batch_checkbox = QCheckBox("Пакетная обработка")
        self.chunking_checkbox = QCheckBox("Авто-чанкинг (разделение)")
        self.chunk_on_error_checkbox = QCheckBox("Чанкинг при ошибках")
        self.chunk_on_error_checkbox.setChecked(True)
        modes_layout.addWidget(self.batch_checkbox)
        modes_layout.addWidget(self.chunking_checkbox)
        modes_layout.addWidget(self.chunk_on_error_checkbox)
        
        settings_group = QGroupBox("Настройки и Рекомендации")
        settings_layout = QGridLayout(settings_group)
        
        self.task_size_spin = NoScrollSpinBox()
        self.task_size_spin.setRange(500, 350000)
        self.task_size_spin.setSingleStep(500)
        self.task_size_spin.setValue(10000)
        self.task_size_spin.setToolTip("Целевой размер ВХОДНЫХ данных для одной задачи (пакета или чанка) в символах.")
        
        # --- УДАЛЕНО ---
        # self.fuzzy_status_label = QLabel("Fuzzy-поиск: …")
        # --- КОНЕЦ УДАЛЕНИЯ ---

        self.info_label = QLabel("Выберите главы для анализа.")
        self.info_label.setStyleSheet("color: #aaa; font-size: 10px; font-weight: bold;")
        self.info_label.setWordWrap(True)
        self.info_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        settings_layout.addWidget(QLabel("Размер задачи (символы):"), 0, 0)
        settings_layout.addWidget(self.task_size_spin, 0, 1)
        # --- УДАЛЕНО ---
        # settings_layout.addWidget(self.fuzzy_status_label, 1, 0, 1, 2)
        # --- КОНЕЦ УДАЛЕНИЯ ---
        settings_layout.addWidget(self.info_label, 2, 0, 1, 2) # <-- ИЗМЕНЕНИЕ: Поставим на вторую строку
        
        main_layout.addWidget(modes_group, 0, 0)
        main_layout.addWidget(settings_group, 0, 1)

        self.batch_checkbox.toggled.connect(self._on_mode_changed)
        self.chunking_checkbox.toggled.connect(self._on_mode_changed)
        self.chunk_on_error_checkbox.toggled.connect(self._on_mode_changed)
        self.task_size_spin.valueChanged.connect(self.settings_changed.emit) # <-- ИЗМЕНЕНИЕ: Сигнал отсюда больше не будет вызывать бенчмарк
        
        self._on_mode_changed()


    # --- МЕТОД get_settings ТЕПЕРЬ ПРОСТОЙ ---
    def get_settings(self):
        return {
            'use_batching': self.batch_checkbox.isChecked(),
            'chunking': self.chunking_checkbox.isChecked(),
            'chunk_on_error': self.chunk_on_error_checkbox.isChecked(),
            'task_size_limit': self.task_size_spin.value(),
        }
    
    def set_settings(self, settings: dict):
        """Применяет настройки из словаря к виджетам."""
        settings = settings or {}
        current_batching = self.batch_checkbox.isChecked()
        current_chunking = self.chunking_checkbox.isChecked()
        current_chunk_on_error = self.chunk_on_error_checkbox.isChecked()
        current_task_size = self.task_size_spin.value()

        self.blockSignals(True)
        self.batch_checkbox.setChecked(settings.get('use_batching', current_batching))
        self.chunking_checkbox.setChecked(settings.get('chunking', current_chunking))
        self.chunk_on_error_checkbox.setChecked(settings.get('chunk_on_error', current_chunk_on_error))
        self.task_size_spin.setValue(settings.get('task_size_limit', current_task_size))
        self.blockSignals(False)
        self._on_mode_changed() # Обновляем UI после установки
   

   # --- МЕТОДЫ АНАЛИЗА  ---
    def update_chapter_data(self, html_files, epub_path, project_manager=None):
        self.html_files = list(html_files or [])
        self._analyze_chapters(epub_path, project_manager=project_manager)
        self._update_batching_availability()

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
            with open(epub_path, 'rb') as epub_file, zipfile.ZipFile(epub_file, 'r') as epub_zip:
                chapter_info_list = [
                    (info.filename, info.file_size)
                    for info in epub_zip.infolist()
                    if info.filename.lower().endswith(('.html', '.xhtml', '.htm'))
                ]
        except (OSError, zipfile.BadZipFile, FileNotFoundError):
            return None

        return {
            'epub_name': os.path.basename(epub_path),
            'epub_size': epub_stat.st_size,
            'content_checksum': sum(size for _, size in chapter_info_list),
        }

    def _load_cached_chapter_analysis(self, project_manager, epub_path):
        if not project_manager:
            return {}, {}

        cache_data = project_manager.load_chapter_analysis_cache()
        if not isinstance(cache_data, dict):
            return {}, {}

        current_metadata = self._build_epub_analysis_metadata(epub_path)
        if not current_metadata or cache_data.get('metadata') != current_metadata:
            return {}, current_metadata or {}

        cached_chapters = cache_data.get('chapters', {})
        if not isinstance(cached_chapters, dict):
            cached_chapters = {}
        return cached_chapters, current_metadata

    def _save_cached_chapter_analysis(self, project_manager, metadata, chapters):
        if not project_manager or not metadata:
            return
        project_manager.save_chapter_analysis_cache({
            'metadata': metadata,
            'chapters': chapters,
        })

    def _analyze_chapters(self, epub_path, project_manager=None):
        """
        Анализирует главы и переиспользует кэш, чтобы не перепарсивать EPUB
        на каждом локальном обновлении UI.
        """
        signature = self._build_analysis_signature(epub_path)
        if signature and signature == self._analysis_signature and self.chapter_compositions:
            return

        self.chapter_compositions = {}
        self._analysis_signature = signature
        if not self.html_files or not epub_path:
            return
    
        try:
            from bs4 import BeautifulSoup
            chapter_sizes = get_epub_chapter_sizes_with_cache(project_manager, epub_path) if project_manager else {}
            cached_chapters, cache_metadata = self._load_cached_chapter_analysis(project_manager, epub_path)
            merged_cached_chapters = dict(cached_chapters)
            missing_files = []

            for file in self.html_files:
                cached_entry = cached_chapters.get(file)
                if isinstance(cached_entry, dict):
                    total_size = int(cached_entry.get('total_size', chapter_sizes.get(file, 0)) or 0)
                    self.chapter_compositions[file] = {
                        'code_size': int(cached_entry.get('code_size', 0) or 0),
                        'text_size': int(cached_entry.get('text_size', 0) or 0),
                        'is_cjk': bool(cached_entry.get('is_cjk', False)),
                        'total_size': total_size,
                    }
                else:
                    missing_files.append(file)

            if missing_files:
                with open(epub_path, 'rb') as epub_file, zipfile.ZipFile(epub_file, 'r') as epub_zip:
                    for file in missing_files:
                        content_str = epub_zip.read(file).decode('utf-8', errors='ignore')
                        soup = BeautifulSoup(content_str, 'html.parser')
                        visible_text = soup.get_text()

                        text_size = len(visible_text)
                        total_size = int(chapter_sizes.get(file, len(content_str)) or len(content_str))
                        chapter_data = {
                            'code_size': max(0, total_size - text_size),
                            'text_size': text_size,
                            'is_cjk': LanguageDetector.is_cjk_text(visible_text),
                            'total_size': total_size
                        }
                        self.chapter_compositions[file] = chapter_data
                        merged_cached_chapters[file] = chapter_data

            if missing_files and cache_metadata:
                self._save_cached_chapter_analysis(project_manager, cache_metadata, merged_cached_chapters)
        except Exception as e:
            print(f"[WIDGET ERROR] Ошибка при анализе глав '{epub_path}': {e}")
            self.chapter_compositions = {}

    def update_recommendations_from_model(self, model_name: str):
        """
        Рассчитывает и устанавливает рекомендуемый размер задачи на основе имени модели.
        """
        # --- ПРОВЕРКА, ЧТО ВСЕ ДАННЫЕ ГОТОВЫ ---
        if not self.chapter_compositions:
            self.task_size_spin.setValue(30000)
            self._update_info_text()
            return

        # --- ПОЛУЧАЕМ ДАННЫЕ ---
        model_config = api_config.all_models().get(model_name, {}) # <-- ИСПОЛЬЗУЕМ АРГУМЕНТ
        limit_out_tokens = model_config.get('max_output_tokens', api_config.default_max_output_tokens())
        safe_limit_out_tokens = limit_out_tokens * api_config.MODEL_OUTPUT_SAFETY_MARGIN

        total_code = sum(comp['code_size'] for comp in self.chapter_compositions.values())
        total_text = sum(comp['text_size'] for comp in self.chapter_compositions.values())
        
        if (total_code + total_text) == 0:
            self.task_size_spin.setValue(30000)
            self._update_info_text()
            return
            
        is_any_cjk = any(comp['is_cjk'] for comp in self.chapter_compositions.values())

        # --- ВСЯ ЛОГИКА РАСЧЕТА ---
        avg_code_ratio = total_code / (total_code + total_text)
        expansion_factor = api_config.CJK_EXPANSION_FACTOR if is_any_cjk else api_config.ALPHABETIC_EXPANSION_FACTOR
        
        output_token_weight = ((avg_code_ratio / api_config.CHARS_PER_ASCII_TOKEN) +
                               ((1 - avg_code_ratio) * expansion_factor / api_config.CHARS_PER_CYRILLIC_TOKEN))

        recommended_input_size = int(safe_limit_out_tokens / output_token_weight) if output_token_weight > 0 else 30000
        recommended_input_size = max(5000, min(recommended_input_size, 300000))

        # --- УСТАНАВЛИВАЕМ ЗНАЧЕНИЕ И ОБНОВЛЯЕМ UI ---
        self.task_size_spin.setValue(recommended_input_size)
        self._update_info_text()


    def _update_info_text(self):

        if not self.html_files or not self.chapter_compositions:
            self.info_label.setText("Выберите главы для анализа.")
            self.info_label.setStyleSheet("color: #aaa;")
            return

        current_target_size = self.task_size_spin.value()
        
        if self.chunking_checkbox.isChecked():
            total_tasks = sum(
                math.ceil(comp['total_size'] / current_target_size) if comp['total_size'] > 0 else 1
                for comp in self.chapter_compositions.values()
            )
            self.info_label.setText(f"Будет создано ~{total_tasks} задач (чанков).")
        elif self.batch_checkbox.isChecked():
            batches, current_size = 0, 0
            for f in self.html_files:
                size = self.chapter_compositions.get(f, {}).get('total_size', 0)
                if current_size + size > current_target_size and current_size > 0:
                    batches += 1
                    current_size = 0
                current_size += size
            if current_size > 0: batches += 1
            self.info_label.setText(f"Будет создано ~{batches} пакетов.")
        else:
            self.info_label.setText(f"Будет {len(self.html_files)} индивидуальных задач.")

    def _on_mode_changed(self):
        sender = self.sender()
        is_batch = self.batch_checkbox.isChecked()
        is_chunk = self.chunking_checkbox.isChecked()

        self.batch_checkbox.blockSignals(True)
        self.chunking_checkbox.blockSignals(True)
        self.chunk_on_error_checkbox.blockSignals(True)

        if sender == self.batch_checkbox and is_batch:
            self.chunking_checkbox.setChecked(False)
            self.chunk_on_error_checkbox.setChecked(False)
        elif sender == self.chunking_checkbox and is_chunk:
            self.batch_checkbox.setChecked(False)
        elif sender == self.chunk_on_error_checkbox and self.chunk_on_error_checkbox.isChecked():
            self.batch_checkbox.setChecked(False)

        self.batch_checkbox.blockSignals(False)
        self.chunking_checkbox.blockSignals(False)
        self.chunk_on_error_checkbox.blockSignals(False)
        
        self.settings_changed.emit()

    def _update_batching_availability(self):
        can_batch = len(self.html_files) > 1
        self.batch_checkbox.setEnabled(can_batch)
        if not can_batch:
            self.batch_checkbox.setChecked(False)
