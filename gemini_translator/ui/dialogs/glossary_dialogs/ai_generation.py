# НОВЫЙ ФАЙЛ: gemini_translator\ui\dialogs\glossary_dialogs\ai_generation.py

import json
import os
import io
import zipfile
import time
import json
import math
from os_patch import PatientLock
from PyQt6 import QtWidgets, QtCore, QtGui
from PyQt6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QPushButton, QDialogButtonBox, QLabel,
    QWidget, QGroupBox, QHBoxLayout, QTableWidget, QHeaderView, QTableWidgetItem,
    QMessageBox, QCheckBox, QPlainTextEdit
)
from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot, QObject, QTimer

# Импорты виджетов, которые мы переиспользуем
from gemini_translator.ui.widgets.glossary_widget import GlossaryWidget 
from gemini_translator.ui.widgets.key_management_widget import KeyManagementWidget
from gemini_translator.ui.widgets.model_settings_widget import ModelSettingsWidget
from gemini_translator.ui.widgets.log_widget import LogWidget
from gemini_translator.ui.widgets.preset_widget import PresetWidget
from gemini_translator.ui.widgets.chapter_list_widget import ChapterListWidget
from gemini_translator.utils.language_tools import LanguageDetector
# Импорты для работы движка
from gemini_translator.api import config as api_config
from gemini_translator.utils.glossary_tools import GlossaryAggregator, ContextManager
from gemini_translator.utils.settings import SettingsManager
from gemini_translator.core.task_manager import TaskDBWorker
from gemini_translator.core.glossary_pipeline import (
    PIPELINE_STATUS_CANCELLED,
    PIPELINE_STATUS_FAILED,
    PIPELINE_STATUS_SUCCESS,
    STEP_STATUS_CANCELLED,
    STEP_STATUS_FAILED,
    STEP_STATUS_LABELS,
    STEP_STATUS_PENDING,
    STEP_STATUS_RUNNING,
    STEP_STATUS_SUCCESS,
    GlossaryPipelineRun,
    build_default_step_name,
    classify_shutdown_reason,
    create_step_from_settings,
    steps_from_template_payload,
    steps_to_template_payload,
    summarize_step_settings,
)
from gemini_translator.ui.widgets.common_widgets import NoScrollSpinBox
from gemini_translator.core.worker_helpers.rpm_limiter import RPMLimiter
from gemini_translator.utils.text import prettify_html_for_ai
from ..menu_utils import post_session_separator
from .numbers_master import NumeralsExtractionWorker

class SequentialTaskProvider(QObject):
    """
    Класс-оркестратор для последовательной генерации глоссария.
    Общается с TranslationEngine исключительно через глобальную шину событий.
    """

    def __init__(self, settings_getter, parent=None, event_bus=None, translate_engine=None): # <-- Аргументы изменились
        super().__init__(parent)
        
        self.bus = event_bus
        if self.bus is None:
            app = QtWidgets.QApplication.instance()
            if hasattr(app, 'event_bus'):
                self.bus = app.event_bus
            else:
                raise RuntimeError("SequentialTaskProvider requires an event bus.")
        
        self.MANAGED_SESSION_FLAG_KEY = f"managed_session_active_{id(self)}"
        
        self.engine = translate_engine
        if self.engine is None:
            app = QtWidgets.QApplication.instance()
            if hasattr(app, 'engine'):
                self.engine = app.engine
            else:
                raise RuntimeError("SequentialTaskProvider requires an engine.")
        
        self.task_manager = self.engine.task_manager
        
        # --- НОВЫЕ, УПРОЩЕННЫЕ АТРИБУТЫ ---
        self.settings_getter = settings_getter
        self.current_task_index = -1
        self.total_tasks = 0
        self._is_running = False
        self._is_stopping = False
        self._task_in_flight = False
        self.rpm_limiter = None
        self.bus.event_posted.connect(self.on_event)
        
    def _post_event(self, name: str, data: dict = None):
        session_id = self.engine.session_id if self.engine and self.engine.session_id else None
        event = {
            'event': name,
            'source': 'SequentialTaskProvider',
            'session_id': session_id,
            'data': data or {}
        }
        self.bus.event_posted.emit(event)

    def start(self):
        """
        Запускает управляемую последовательную сессию.
        1. "Замораживает" все реальные задачи.
        2. Добавляет в очередь "задачу-стража" в качестве "якоря".
        3. Запускает TranslationEngine.
        4. С небольшой задержкой инициирует выполнение первой реальной задачи.
        """
        # Проверяем наличие задач в центральном TaskManager
        if not (self.engine and self.engine.task_manager and self.engine.task_manager.has_pending_tasks() ):
            self._post_event('log_message', {'message': "[ORCHESTRATOR] Нет задач для запуска последовательной генерации."})
            return
        if self._is_running:
            return

        # Считаем общее количество РЕАЛЬНЫХ задач для UI
        if self.engine and self.engine.task_manager:
            self.total_tasks = len(self.engine.task_manager.get_all_pending_tasks())
        
        self._is_running = True
        self._is_stopping = False

        # Получаем и настраиваем параметры сессии
        settings = self.settings_getter()
        rpm_value = settings.get('rpm_limit', 10)
        self.rpm_limiter = RPMLimiter(rpm_limit=rpm_value)
        settings['num_instances'] = 1
        settings['max_concurrent_requests'] = 1
        
        if self.engine and self.engine.task_manager:
            self.engine.task_manager.hold_all_pending_tasks()
            self.bus.set_data(self.MANAGED_SESSION_FLAG_KEY, True)
            self._post_event('log_message', {'message': "[ORCHESTRATOR] Установлен флаг управляемой сессии."})
            
            # 1. Немедленно готовим первую задачу. Состояние TaskManager обновлено.
            self._run_next_task()
            
        else:
            self._post_event('log_message', {'message': "[ORCHESTRATOR-ERROR] TaskManager не найден. Невозможно запустить сессию."})
            self._is_running = False
            return
            
        # --- КЛЮЧЕВОЕ ИЗМЕНЕНИЕ (ВАШЕ ПРЕДЛОЖЕНИЕ) ---
        # 2. Запускаем сессию с гарантированной задержкой.
        # Это дает 100% уверенность, что движок увидит задачу, которую мы подготовили выше.
        QTimer.singleShot(
            100, 
            lambda: self._post_event('start_session_requested', {'settings': settings})
        )

    
    def _get_request_data_snapshot(self, settings):
        """Создает 'снимок' списков временных меток для всех ключей сессии."""
        snapshot = {}
        model_id = settings.get('model_config', {}).get('id')
        if not model_id:
            return {}
            
        for key in self.session_keys:
            key_info = self.settings_manager.get_key_info(key)
            if key_info:
                # Используем новый метод из SettingsManager
                snapshot[key] = self.settings_manager.get_request_timestamps(key_info, model_id)
        return snapshot

    def _get_request_counts_snapshot(self, settings):
        """Создает 'снимок' текущих счетчиков запросов для всех ключей сессии."""
        counts = {}
        model_id = settings.get('model_config', {}).get('id')
        if not model_id:
            return {}
            
        for key in self.session_keys:
            key_info = self.settings_manager.get_key_info(key)
            if key_info:
                counts[key] = self.settings_manager.get_request_count(key_info, model_id)
        return counts
    
    @pyqtSlot(dict)
    def on_event(self, event: dict):
        if not self._is_running: return
        event_name = event.get('event')
        if event_name == 'session_finished':
            self._is_running = False
            # Если сессия завершилась, а мы этого не ожидали (например, из-за ошибки)
            if not self._is_stopping:
                self._post_event('generation_finished', {'was_cancelled': True})
        
        # --- Слушаем стандартное событие --- 
        if event_name == 'task_finished':
            data = event.get('data', {})
            task_info = data.get('task_info') # Получаем (id, payload)
            if task_info and isinstance(task_info, tuple):
                task_id, task_payload = task_info
                if task_payload[0] == 'glossary_batch_task' and self._task_in_flight == task_id:
                # Наша задача выполнена! Запускаем обработку.
                    self._on_batch_finished(event)



    def _on_batch_finished(self, finish_event):
        if not self._task_in_flight:
            return
        self._task_in_flight = None

        if self._is_stopping:
            self._finish_session(was_cancelled=True)
            return
        
        # Просто запускаем следующую задачу. Больше никакой логики слияния.
        self._run_next_task()

    def _run_next_task(self):
        
        if self._is_stopping:
            self._finish_session(was_cancelled=True)
            return

        if self.engine:
            if self.task_manager:
                if not self.task_manager.has_held_tasks():
                    self._finish_session(was_cancelled=False)
                    return

        if self.rpm_limiter and not self.rpm_limiter.can_proceed():
            QTimer.singleShot(100, self._run_next_task)
            return
        


        next_task_info = self.engine.task_manager.peek_next_held_task()
        if not next_task_info:
            self._finish_session(was_cancelled=False)
            return
            
        task_id, task_payload = next_task_info

        self.current_task_index += 1
        self._post_event('progress_updated', {'current': self.current_task_index, 'total': self.total_tasks})
        task_name = f"пакет #{self.current_task_index + 1}/{self.total_tasks}"
        
        # --- ГЛАВНОЕ УПРОЩЕНИЕ ---
        # Мы больше НЕ модифицируем payload. Он уже был подготовлен UI.
        # Просто "пробуждаем" задачу в ее исходном виде.
        self.engine.task_manager.promote_held_task(task_id, task_payload)
        
        self._post_event('log_message', {'message': f"Задача {task_name} отправлена на выполнение..."})
        self._task_in_flight = task_id

    def stop(self):
        """Инициирует ТОЛЬКО плавную остановку со стороны оркестратора."""
        if not self._is_running or self._is_stopping: 
            return
            
        self._is_stopping = True
        self._post_event('log_message', {'message': "[ORCHESTRATOR] Инициирована плавная остановка. Новые задачи выдаваться не будут."})
        self.bus.pop_data(self.MANAGED_SESSION_FLAG_KEY, None)
        # Если в данный момент нет активной задачи, то _on_batch_finished не будет вызван.
        # Значит, мы должны сами запустить процесс завершения.
        if not self._task_in_flight:
            self._finish_session(was_cancelled=True)
             
    def _finish_session(self, was_cancelled):
        """Финальная стадия завершения. Снимает флаг и отправляет финальные события."""
        if not self._is_running: 
            return

        if self.bus.pop_data(self.MANAGED_SESSION_FLAG_KEY, None):
            self._post_event('log_message', {'message': "[ORCHESTRATOR] Флаг управляемой сессии снят."})
        
        self.bus.pop_data(self.MANAGED_SESSION_FLAG_KEY, None)
        self._post_event('manual_stop_requested')
        
        # Просто сообщаем о факте завершения.
        self._post_event('generation_finished', {'was_cancelled': was_cancelled})
        
        self._is_running = False

class GenerationSessionDialog(QDialog):
    generation_finished = pyqtSignal(list, set)

    def __init__(self, settings_manager, initial_glossary, merge_mode, html_files, epub_path, project_manager, initial_ui_settings, parent=None, event_bus=None, translate_engine=None):
        super().__init__(parent)
        self.settings_manager = settings_manager
        self._accumulated_processed_chapters = set()

        self._recovery_lock = PatientLock()
        self.html_files = html_files
        self.epub_path = epub_path
        self.project_manager = project_manager

        self.orchestrator = None
        self.recovery_file_path = None
        self.is_session_active = False
        self.is_soft_stopping = False
        self.force_exit_on_interrupt = False
        self._session_was_restored = False
        self._initial_load_done = False
        self._glossary_task_size_locked = False
        self._glossary_task_size_lock_reason = None
        self._session_finished_successfully = False
        self._manual_glossary_edits_after_finish = False
        self.pipeline_steps = []
        self.pipeline_run = None
        self._pipeline_active_step_id = None
        self._pipeline_waiting_for_next_step = False
        self._pipeline_stop_requested = False
        self._is_refreshing_pipeline_table = False
        
        app = QtWidgets.QApplication.instance()
        self.bus = event_bus
        if self.bus is None:
            if hasattr(app, 'event_bus'): self.bus = app.event_bus
            else: raise RuntimeError("GenerationSessionDialog requires an event bus.")
        
        self.engine = translate_engine
        if self.engine is None:
            if hasattr(app, 'engine'): self.engine = app.engine
            else: raise RuntimeError("GenerationSessionDialog requires an engine.")
        
        self.task_manager = self.engine.task_manager if self.engine.task_manager else None
        
        self.bus.event_posted.connect(self._on_global_event)

        self.setWindowTitle("Генерация Глоссария с помощью AI")
            
        # --- Геометрия окна ---
        available_geometry = self.screen().availableGeometry()
        
        height = min(int(available_geometry.height() * 0.75), 650)
        width = min(int(available_geometry.width() * 0.65), 1000)
        self.setMinimumSize(width, height)
       
       
        height = max(int(available_geometry.height() * 0.75), 650)
        width = max(int(available_geometry.width() * 0.65), 1000)
        
        self.resize(width, height)
        self.move(
            available_geometry.center().x() - self.width() // 2,
            available_geometry.center().y() - self.height() // 2
        )

        self.setWindowFlags(
            self.windowFlags() | 
            Qt.WindowType.WindowMaximizeButtonHint | 
            Qt.WindowType.WindowCloseButtonHint
        )
        
        
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setSingleShot(True)
        self.autosave_timer.setInterval(1500)
        self.autosave_timer.timeout.connect(self._trigger_autosave)
        
        self._init_ui()
        
        self._check_for_recovery_session()

        if not self._session_was_restored:
            if initial_ui_settings:
                self._apply_initial_settings(initial_ui_settings)
            
            # Просто устанавливаем начальный глоссарий в виджет
            if initial_glossary:
                self.glossary_widget.set_glossary(initial_glossary)
        
        
    def _post_event(self, name: str, data: dict = None):
        session_id = self.engine.session_id if self.engine and self.engine.session_id else None
        event = {
            'event': name,
            'source': 'GenerationSessionDialog',
            'session_id': session_id,
            'data': data or {}
        }
        self.bus.event_posted.emit(event)
    
    
    
    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        self.tabs = QtWidgets.QTabWidget()

        settings_tab = self._create_settings_tab()
        tasks_tab = self._create_tasks_tab()
        pipeline_tab = self._create_pipeline_tab()
        prompt_tab = self._create_prompt_tab()
        results_tab = self._create_results_tab()

        self.tabs.addTab(settings_tab, "⚙️ Настройки")
        self.tabs.addTab(tasks_tab, "📋 Список задач")
        self.tabs.addTab(pipeline_tab, "🔃 Очередь")
        self.tabs.addTab(prompt_tab, "📝 Промпт")
        self.tabs.addTab(results_tab, "📊 Результаты и лог")

        main_layout.addWidget(self.tabs)

        # --- НИЖНЯЯ ПАНЕЛЬ УПРАВЛЕНИЯ ---
        bottom_control_layout = QHBoxLayout()
        bottom_control_layout.setContentsMargins(0, 5, 0, 0)

        # Секция лимита новых терминов
        limit_container = QWidget()
        limit_layout = QHBoxLayout(limit_container)
        limit_layout.setContentsMargins(0, 0, 0, 0)
        
        limit_label = QLabel("Лимит новых терминов:")
        limit_label.setToolTip("Сколько МАКСИМУМ новых терминов брать из ответа AI за один проход. Ноль — без лимита.\n"
                               "Отбирает те термины, что в начале, отбрасывая те, что в конце ответа.\n"
                               "Термины, которые уже есть в базе (обновление), принимаются всегда без лимита.")
        
        self.new_terms_limit_spin = NoScrollSpinBox()
        self.new_terms_limit_spin.setRange(5, 500)
        self.new_terms_limit_spin.setValue(50) # Дефолт, будет пересчитан
        self.new_terms_limit_spin.setSuffix(" шт.")
        self.new_terms_limit_spin.setToolTip(limit_label.toolTip())
        
        limit_layout.addWidget(limit_label)
        limit_layout.addWidget(self.new_terms_limit_spin)
        
        bottom_control_layout.addWidget(limit_container)
        bottom_control_layout.addStretch() # Распорка между спинбоксом и кнопками

        # Стандартные кнопки
        self.button_box = QDialogButtonBox()
        self.start_btn = self.button_box.addButton("🚀 Начать", QDialogButtonBox.ButtonRole.ActionRole)
        self.soft_stop_btn = self.button_box.addButton("Завершить плавно", QDialogButtonBox.ButtonRole.ActionRole)
        self.hard_stop_btn = self.button_box.addButton("❌ Прервать", QDialogButtonBox.ButtonRole.DestructiveRole)

        self.apply_btn = self.button_box.addButton("Применить и Закрыть", QDialogButtonBox.ButtonRole.AcceptRole)
        self.close_btn = self.button_box.addButton("Закрыть", QDialogButtonBox.ButtonRole.RejectRole)
        
        self.soft_stop_btn.setVisible(False)
        self.hard_stop_btn.setVisible(False)
        self.apply_btn.setVisible(False)

        self.start_btn.clicked.connect(self._on_start_stop_clicked)
        self.soft_stop_btn.clicked.connect(self._on_soft_stop_clicked)
        self.hard_stop_btn.clicked.connect(self._on_hard_stop_clicked)
        self.apply_btn.clicked.connect(self.accept)
        self.close_btn.clicked.connect(self.reject)

        bottom_control_layout.addWidget(self.button_box)
        main_layout.addLayout(bottom_control_layout)
        
        # Подключения сигналов
        self.key_widget.active_keys_changed.connect(self._update_start_button_state)
        self.glossary_widget.glossary_changed.connect(self._update_start_button_state)
        self.key_widget.provider_combo.currentIndexChanged.emit(
            self.key_widget.provider_combo.currentIndex()
        )
    
    def _create_tasks_tab(self):
        """Создает и настраивает вкладку со списком задач."""
        tasks_tab_widget = QWidget()
        layout = QVBoxLayout(tasks_tab_widget)

        from gemini_translator.ui.widgets.translation_options_widget import TranslationOptionsWidget
        self.translation_options_widget = TranslationOptionsWidget(self)
        
        self.translation_options_widget.batch_checkbox.setChecked(True)
        modes_group = self.translation_options_widget.findChild(QGroupBox, "modes_group")
        if modes_group:
            modes_group.setVisible(False)

        layout.addWidget(self.translation_options_widget)
        
        action_panel_layout = QHBoxLayout()
        self.reselect_chapters_btn = QPushButton("Главы: ...")
        self.reselect_chapters_btn.setToolTip("Нажмите, чтобы выбрать главы заново")
        self.reselect_chapters_btn.clicked.connect(self._reselect_chapters_for_glossary)
        
        self.extract_numerals_btn = QPushButton("🔢 Найти числительные")
        self.extract_numerals_btn.setToolTip("Просканировать текст всех глав и найти числа на разных языках,\nчтобы добавить их в глоссарий в русской транскрипции.")
        self.extract_numerals_btn.clicked.connect(self._on_extract_numerals_clicked)
        
        self.rebuild_tasks_btn = QPushButton("🔄 Применить и пересобрать")
        self.rebuild_tasks_btn.clicked.connect(self._rebuild_glossary_tasks)
        
        self.remove_generated_btn = QPushButton("🗑️ Убрать сгенерированные")
        self.remove_generated_btn.setToolTip("Убрать из списка главы, для которых глоссарий уже был успешно сгенерирован ранее.")
        self.remove_generated_btn.clicked.connect(self._remove_generated_chapters)
        self.remove_generated_btn.setEnabled(bool(self._get_all_processed_chapters()))
    
        action_panel_layout.addWidget(self.reselect_chapters_btn)
        action_panel_layout.addWidget(self.extract_numerals_btn)
        action_panel_layout.addWidget(self.remove_generated_btn)
        action_panel_layout.addWidget(self.rebuild_tasks_btn)
        action_panel_layout.addStretch()
        layout.addLayout(action_panel_layout)
        
        
        
        

        self.chapter_list_widget = ChapterListWidget(self)
        self.chapter_list_widget.set_copy_originals_visible(False)
        # --- Подключаем сигналы к новым слотам ---
        self.chapter_list_widget.reorder_requested.connect(self._handle_task_reorder)
        self.chapter_list_widget.duplicate_requested.connect(self._handle_task_duplication)
        self.chapter_list_widget.remove_selected_requested.connect(self._handle_task_removal)
        self.chapter_list_widget.reanimate_requested.connect(self._handle_task_reanimation)
        layout.addWidget(self.chapter_list_widget, 1)

        self.reselect_chapters_btn.setText(f"Главы: {len(self.html_files)}")

        return tasks_tab_widget

    def _create_pipeline_tab(self):
        pipeline_tab = QWidget()
        layout = QVBoxLayout(pipeline_tab)

        self.pipeline_enabled_checkbox = QCheckBox("Запускать очередь шагов вместо одиночного прохода")
        self.pipeline_enabled_checkbox.setToolTip(
            "Если включено, выполняется весь сценарий по порядку.\n"
            "Если выключено, работает текущий одиночный запуск."
        )
        self.pipeline_enabled_checkbox.toggled.connect(self._update_start_button_state)
        self.pipeline_enabled_checkbox.toggled.connect(self._update_pipeline_buttons_state)
        layout.addWidget(self.pipeline_enabled_checkbox)

        hint_label = QLabel(
            "Соберите шаги из текущих настроек. Каждый шаг сохраняет свою температуру, merge mode, "
            "режим последовательности, размер пакета и лимит новых терминов."
        )
        hint_label.setWordWrap(True)
        hint_label.setStyleSheet("color: #9aa4b1;")
        layout.addWidget(hint_label)

        template_row = QHBoxLayout()
        template_row.addWidget(QLabel("Шаблон сценария:"))
        self.pipeline_template_combo = QtWidgets.QComboBox()
        self.pipeline_template_combo.setMinimumWidth(260)
        self.pipeline_template_combo.currentTextChanged.connect(
            lambda name: self.settings_manager.save_last_glossary_pipeline_template_name(name or None)
        )
        self.pipeline_load_template_btn = QPushButton("Загрузить")
        self.pipeline_save_template_btn = QPushButton("Сохранить")
        self.pipeline_delete_template_btn = QPushButton("Удалить")
        self.pipeline_load_template_btn.clicked.connect(self._load_selected_template_into_queue)
        self.pipeline_save_template_btn.clicked.connect(self._save_current_pipeline_template)
        self.pipeline_delete_template_btn.clicked.connect(self._delete_selected_pipeline_template)
        template_row.addWidget(self.pipeline_template_combo, 1)
        template_row.addWidget(self.pipeline_load_template_btn)
        template_row.addWidget(self.pipeline_save_template_btn)
        template_row.addWidget(self.pipeline_delete_template_btn)
        layout.addLayout(template_row)

        actions_row = QHBoxLayout()
        self.pipeline_add_step_btn = QPushButton("Добавить из формы")
        self.pipeline_apply_step_btn = QPushButton("Загрузить в форму")
        self.pipeline_update_step_btn = QPushButton("Обновить шаг")
        self.pipeline_duplicate_step_btn = QPushButton("Дублировать")
        self.pipeline_move_up_btn = QPushButton("Вверх")
        self.pipeline_move_down_btn = QPushButton("Вниз")
        self.pipeline_remove_step_btn = QPushButton("Удалить")
        self.pipeline_clear_steps_btn = QPushButton("Очистить")

        self.pipeline_add_step_btn.clicked.connect(self._add_pipeline_step_from_current_settings)
        self.pipeline_apply_step_btn.clicked.connect(self._apply_selected_pipeline_step_to_form)
        self.pipeline_update_step_btn.clicked.connect(self._update_selected_pipeline_step_from_form)
        self.pipeline_duplicate_step_btn.clicked.connect(self._duplicate_selected_pipeline_step)
        self.pipeline_move_up_btn.clicked.connect(lambda: self._move_selected_pipeline_step(-1))
        self.pipeline_move_down_btn.clicked.connect(lambda: self._move_selected_pipeline_step(1))
        self.pipeline_remove_step_btn.clicked.connect(self._remove_selected_pipeline_step)
        self.pipeline_clear_steps_btn.clicked.connect(self._clear_pipeline_steps)

        actions_row.addWidget(self.pipeline_add_step_btn)
        actions_row.addWidget(self.pipeline_apply_step_btn)
        actions_row.addWidget(self.pipeline_update_step_btn)
        actions_row.addWidget(self.pipeline_duplicate_step_btn)
        actions_row.addWidget(self.pipeline_move_up_btn)
        actions_row.addWidget(self.pipeline_move_down_btn)
        actions_row.addWidget(self.pipeline_remove_step_btn)
        actions_row.addStretch()
        actions_row.addWidget(self.pipeline_clear_steps_btn)
        layout.addLayout(actions_row)

        self.pipeline_table = QTableWidget(0, 8)
        self.pipeline_table.setHorizontalHeaderLabels(
            ["#", "Название", "Слияние", "T", "Режим", "Пакет", "Новых", "Статус"]
        )
        self.pipeline_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.pipeline_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.pipeline_table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.DoubleClicked
            | QtWidgets.QAbstractItemView.EditTrigger.SelectedClicked
            | QtWidgets.QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.pipeline_table.verticalHeader().setVisible(False)
        header = self.pipeline_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col in (2, 3, 4, 5, 6, 7):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.pipeline_table.itemSelectionChanged.connect(self._on_pipeline_selection_changed)
        self.pipeline_table.itemChanged.connect(self._on_pipeline_table_item_changed)
        layout.addWidget(self.pipeline_table, 1)

        log_group = QGroupBox("Лог выбранного шага")
        log_layout = QVBoxLayout(log_group)
        self.pipeline_step_log = QPlainTextEdit()
        self.pipeline_step_log.setReadOnly(True)
        self.pipeline_step_log.setPlaceholderText("Выберите шаг, чтобы посмотреть его журнал.")
        log_layout.addWidget(self.pipeline_step_log)
        layout.addWidget(log_group, 1)

        self._refresh_pipeline_templates()
        self._refresh_pipeline_table()
        self._update_pipeline_buttons_state()

        return pipeline_tab

    def _get_pipeline_display_steps(self):
        if self.pipeline_run:
            return self.pipeline_run.steps
        return self.pipeline_steps

    def _get_configured_pipeline_step_by_id(self, step_id):
        for step in self.pipeline_steps:
            if step.step_id == step_id:
                return step
        return None

    def _clear_pipeline_run_state(self):
        self.pipeline_run = None
        self._pipeline_active_step_id = None
        self._pipeline_waiting_for_next_step = False
        self._pipeline_stop_requested = False

    def _get_selected_pipeline_step_id(self):
        if not hasattr(self, 'pipeline_table'):
            return None
        selected_rows = self.pipeline_table.selectionModel().selectedRows()
        if not selected_rows:
            return None
        item = self.pipeline_table.item(selected_rows[0].row(), 0)
        if not item:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _get_pipeline_step_settings_snapshot(self):
        settings = self._get_full_ui_settings()
        settings.pop('pipeline_enabled', None)
        settings.pop('pipeline_steps', None)
        return settings

    def _replace_pipeline_steps(self, steps):
        self._clear_pipeline_run_state()
        self.pipeline_steps = []
        for step in steps or []:
            cloned_step = step.clone()
            cloned_step.reset_runtime()
            self.pipeline_steps.append(cloned_step)
        self._refresh_pipeline_table()
        self._update_start_button_state()
        self._update_pipeline_buttons_state()

    def _refresh_pipeline_templates(self, selected_name=None):
        templates = self.settings_manager.load_glossary_pipeline_templates() or {}
        if not isinstance(templates, dict):
            templates = {}

        self.pipeline_template_combo.blockSignals(True)
        self.pipeline_template_combo.clear()
        self.pipeline_template_combo.addItems(sorted(templates.keys(), key=str.casefold))
        preferred_name = selected_name or self.settings_manager.get_last_glossary_pipeline_template_name()
        if preferred_name:
            index = self.pipeline_template_combo.findText(preferred_name)
            if index >= 0:
                self.pipeline_template_combo.setCurrentIndex(index)
        self.pipeline_template_combo.blockSignals(False)
        self._update_pipeline_buttons_state()

    def _save_current_pipeline_template(self):
        if not self.pipeline_steps:
            QMessageBox.information(self, "Очередь пуста", "Сначала добавьте хотя бы один шаг.")
            return

        current_name = self.pipeline_template_combo.currentText().strip()
        template_name, ok = QtWidgets.QInputDialog.getText(
            self,
            "Сохранить шаблон",
            "Название шаблона сценария:",
            text=current_name or "Новый сценарий",
        )
        if not ok:
            return

        template_name = template_name.strip()
        if not template_name:
            QMessageBox.warning(self, "Пустое имя", "Название шаблона не может быть пустым.")
            return

        templates = self.settings_manager.load_glossary_pipeline_templates() or {}
        if not isinstance(templates, dict):
            templates = {}
        templates[template_name] = steps_to_template_payload(self.pipeline_steps)
        self.settings_manager.save_glossary_pipeline_templates(templates)
        self.settings_manager.save_last_glossary_pipeline_template_name(template_name)
        self._refresh_pipeline_templates(selected_name=template_name)

    def _load_selected_template_into_queue(self):
        template_name = self.pipeline_template_combo.currentText().strip()
        if not template_name:
            QMessageBox.information(self, "Нет шаблона", "Выберите шаблон сценария.")
            return

        templates = self.settings_manager.load_glossary_pipeline_templates() or {}
        payload = templates.get(template_name)
        steps = steps_from_template_payload(payload)
        if not steps:
            QMessageBox.warning(self, "Пустой шаблон", "В выбранном шаблоне нет шагов.")
            return

        if self.pipeline_steps:
            answer = QMessageBox.question(
                self,
                "Заменить очередь",
                "Текущая очередь будет заменена шагами из шаблона. Продолжить?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        self._replace_pipeline_steps(steps)
        self.pipeline_enabled_checkbox.setChecked(True)
        self.settings_manager.save_last_glossary_pipeline_template_name(template_name)

    def _delete_selected_pipeline_template(self):
        template_name = self.pipeline_template_combo.currentText().strip()
        if not template_name:
            return

        answer = QMessageBox.question(self, "Удалить шаблон", f"Удалить шаблон '{template_name}'?")
        if answer != QMessageBox.StandardButton.Yes:
            return

        templates = self.settings_manager.load_glossary_pipeline_templates() or {}
        if template_name in templates:
            templates.pop(template_name, None)
            self.settings_manager.save_glossary_pipeline_templates(templates)
        if self.settings_manager.get_last_glossary_pipeline_template_name() == template_name:
            self.settings_manager.save_last_glossary_pipeline_template_name(None)
        self._refresh_pipeline_templates()

    def _add_pipeline_step_from_current_settings(self):
        self._clear_pipeline_run_state()
        step = create_step_from_settings(
            self._get_pipeline_step_settings_snapshot(),
            index=len(self.pipeline_steps) + 1,
        )
        self.pipeline_steps.append(step)
        self.pipeline_enabled_checkbox.setChecked(True)
        self._refresh_pipeline_table()
        self._select_pipeline_step(step.step_id)
        self._update_start_button_state()

    def _apply_selected_pipeline_step_to_form(self):
        step_id = self._get_selected_pipeline_step_id()
        step = self._get_configured_pipeline_step_by_id(step_id)
        if not step and self.pipeline_run:
            step = self.pipeline_run.get_step(step_id)
        if not step:
            return

        self._apply_full_ui_settings(step.settings)
        self._rebuild_glossary_tasks()
        self.tabs.setCurrentIndex(0)

    def _update_selected_pipeline_step_from_form(self):
        step_id = self._get_selected_pipeline_step_id()
        configured_step = self._get_configured_pipeline_step_by_id(step_id)
        if not configured_step:
            return

        self._clear_pipeline_run_state()
        configured_step.settings = self._get_pipeline_step_settings_snapshot()
        self._refresh_pipeline_table()
        self._select_pipeline_step(configured_step.step_id)

    def _duplicate_selected_pipeline_step(self):
        step_id = self._get_selected_pipeline_step_id()
        configured_step = self._get_configured_pipeline_step_by_id(step_id)
        if not configured_step:
            return

        self._clear_pipeline_run_state()
        duplicated = create_step_from_settings(
            configured_step.settings,
            name=f"{configured_step.name} (копия)",
        )
        insert_index = self.pipeline_steps.index(configured_step) + 1
        self.pipeline_steps.insert(insert_index, duplicated)
        self._refresh_pipeline_table()
        self._select_pipeline_step(duplicated.step_id)

    def _move_selected_pipeline_step(self, direction):
        step_id = self._get_selected_pipeline_step_id()
        configured_step = self._get_configured_pipeline_step_by_id(step_id)
        if not configured_step:
            return

        current_index = self.pipeline_steps.index(configured_step)
        target_index = current_index + direction
        if target_index < 0 or target_index >= len(self.pipeline_steps):
            return

        self._clear_pipeline_run_state()
        self.pipeline_steps[current_index], self.pipeline_steps[target_index] = (
            self.pipeline_steps[target_index],
            self.pipeline_steps[current_index],
        )
        self._refresh_pipeline_table()
        self._select_pipeline_step(step_id)

    def _remove_selected_pipeline_step(self):
        step_id = self._get_selected_pipeline_step_id()
        if not step_id:
            return

        self._clear_pipeline_run_state()
        self.pipeline_steps = [step for step in self.pipeline_steps if step.step_id != step_id]
        self._refresh_pipeline_table()
        self._update_start_button_state()

    def _clear_pipeline_steps(self):
        if not self.pipeline_steps:
            return

        answer = QMessageBox.question(self, "Очистить очередь", "Удалить все шаги из очереди?")
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._clear_pipeline_run_state()
        self.pipeline_steps = []
        self._refresh_pipeline_table()
        self._update_start_button_state()

    def _select_pipeline_step(self, step_id):
        if not step_id or not hasattr(self, 'pipeline_table'):
            return
        for row in range(self.pipeline_table.rowCount()):
            item = self.pipeline_table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == step_id:
                self.pipeline_table.selectRow(row)
                return

    def _status_brush_for_pipeline_step(self, status):
        color_map = {
            STEP_STATUS_RUNNING: "#3498DB",
            STEP_STATUS_SUCCESS: "#2ECC71",
            STEP_STATUS_FAILED: "#E74C3C",
            STEP_STATUS_CANCELLED: "#F39C12",
        }
        color = color_map.get(status)
        return QtGui.QBrush(QtGui.QColor(color)) if color else None

    def _refresh_pipeline_table(self):
        if not hasattr(self, 'pipeline_table'):
            return

        selected_step_id = self._get_selected_pipeline_step_id()
        display_steps = self._get_pipeline_display_steps()

        self._is_refreshing_pipeline_table = True
        try:
            self.pipeline_table.setRowCount(len(display_steps))
            for row, step in enumerate(display_steps):
                summary = summarize_step_settings(step.settings)
                values = [
                    str(row + 1),
                    step.name,
                    summary["merge_mode"],
                    summary["temperature"],
                    summary["execution_mode"],
                    summary["task_size"],
                    summary["new_terms_limit"],
                    STEP_STATUS_LABELS.get(step.status, step.status),
                ]
                for col, value in enumerate(values):
                    item = self.pipeline_table.item(row, col)
                    if item is None:
                        item = QTableWidgetItem()
                        self.pipeline_table.setItem(row, col, item)
                    item.setText(value)
                    flags = item.flags() | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
                    if col == 1 and not self.is_session_active:
                        flags |= Qt.ItemFlag.ItemIsEditable
                    else:
                        flags &= ~Qt.ItemFlag.ItemIsEditable
                    item.setFlags(flags)
                    if col == 0:
                        item.setData(Qt.ItemDataRole.UserRole, step.step_id)

                status_item = self.pipeline_table.item(row, 7)
                brush = self._status_brush_for_pipeline_step(step.status)
                for col in range(self.pipeline_table.columnCount()):
                    row_item = self.pipeline_table.item(row, col)
                    if row_item:
                        row_item.setForeground(brush or QtGui.QBrush())
                if status_item and step.last_reason:
                    status_item.setToolTip(step.last_reason)
        finally:
            self._is_refreshing_pipeline_table = False

        if selected_step_id:
            self._select_pipeline_step(selected_step_id)
        elif display_steps:
            self.pipeline_table.selectRow(0)

        self._render_selected_pipeline_step_log()
        self._update_pipeline_buttons_state()

    def _render_selected_pipeline_step_log(self):
        if not hasattr(self, 'pipeline_step_log'):
            return

        step_id = self._get_selected_pipeline_step_id()
        selected_step = None
        for step in self._get_pipeline_display_steps():
            if step.step_id == step_id:
                selected_step = step
                break

        if not selected_step:
            self.pipeline_step_log.clear()
            return

        if selected_step.log_lines:
            self.pipeline_step_log.setPlainText("\n".join(selected_step.log_lines))
            return

        summary = summarize_step_settings(selected_step.settings)
        lines = [
            f"Название: {selected_step.name}",
            f"Слияние: {summary['merge_mode']}",
            f"Температура: {summary['temperature']}",
            f"Режим: {summary['execution_mode']}",
            f"Пакет: {summary['task_size']}",
            f"Новых терминов: {summary['new_terms_limit']}",
        ]
        if selected_step.last_reason:
            lines.extend(["", f"Последнее сообщение: {selected_step.last_reason}"])
        self.pipeline_step_log.setPlainText("\n".join(lines))

    def _on_pipeline_selection_changed(self):
        self._render_selected_pipeline_step_log()
        self._update_pipeline_buttons_state()

    def _on_pipeline_table_item_changed(self, item):
        if self._is_refreshing_pipeline_table or not item or item.column() != 1:
            return

        id_item = self.pipeline_table.item(item.row(), 0)
        step_id = id_item.data(Qt.ItemDataRole.UserRole) if id_item else None
        configured_step = self._get_configured_pipeline_step_by_id(step_id)
        if not configured_step:
            return

        self._clear_pipeline_run_state()
        configured_step.name = item.text().strip() or build_default_step_name(
            configured_step.settings,
            index=item.row() + 1,
        )
        if configured_step.name != item.text():
            self._is_refreshing_pipeline_table = True
            try:
                item.setText(configured_step.name)
            finally:
                self._is_refreshing_pipeline_table = False
        self._render_selected_pipeline_step_log()

    def _update_pipeline_buttons_state(self):
        if not hasattr(self, 'pipeline_table'):
            return

        has_selection = self._get_selected_pipeline_step_id() is not None
        has_steps = bool(self.pipeline_steps)
        has_templates = self.pipeline_template_combo.count() > 0
        editable = not self.is_session_active

        self.pipeline_enabled_checkbox.setEnabled(editable)
        self.pipeline_template_combo.setEnabled(editable)
        self.pipeline_load_template_btn.setEnabled(editable and has_templates)
        self.pipeline_save_template_btn.setEnabled(editable and has_steps)
        self.pipeline_delete_template_btn.setEnabled(editable and has_templates)
        self.pipeline_add_step_btn.setEnabled(editable)
        self.pipeline_apply_step_btn.setEnabled(editable and has_selection)
        self.pipeline_update_step_btn.setEnabled(editable and has_selection)
        self.pipeline_duplicate_step_btn.setEnabled(editable and has_selection)
        self.pipeline_move_up_btn.setEnabled(editable and has_selection and len(self.pipeline_steps) > 1)
        self.pipeline_move_down_btn.setEnabled(editable and has_selection and len(self.pipeline_steps) > 1)
        self.pipeline_remove_step_btn.setEnabled(editable and has_selection)
        self.pipeline_clear_steps_btn.setEnabled(editable and has_steps)
        self.pipeline_table.setEnabled(editable)

    def _force_glossary_batch_mode(self):
        """Glossary generation always prefers batches; single tasks are only a size fallback."""
        if not hasattr(self, 'translation_options_widget'):
            return

        widget = self.translation_options_widget
        controls = (
            widget.batch_checkbox,
            widget.chunking_checkbox,
            widget.chunk_on_error_checkbox,
        )
        for control in controls:
            control.blockSignals(True)

        widget.batch_checkbox.setChecked(True)
        widget.chunking_checkbox.setChecked(False)
        widget.chunk_on_error_checkbox.setChecked(False)

        for control in controls:
            control.blockSignals(False)

    def _apply_glossary_task_size_override(self, size_limit=None, reason=None):
        """Keeps an explicit glossary batch size from the auto pipeline from being auto-recomputed."""
        self._glossary_task_size_locked = False
        self._glossary_task_size_lock_reason = None

        if not hasattr(self, 'translation_options_widget') or size_limit is None:
            return

        try:
            value = int(size_limit)
        except (TypeError, ValueError):
            return

        if value <= 0:
            return

        spin = self.translation_options_widget.task_size_spin
        value = max(spin.minimum(), min(value, spin.maximum()))
        self._glossary_task_size_locked = True
        self._glossary_task_size_lock_reason = reason or "Внешнее ограничение"
        if hasattr(self.translation_options_widget, 'set_task_size_limit'):
            self.translation_options_widget.set_task_size_limit(value, user_defined=False)
        else:
            spin.setValue(value)
        self._update_new_terms_limit_from_current_size()
        self.translation_options_widget.info_label.setText(
            f"Фиксированный размер пакета: {value:,} симв.\n"
            f"({self._glossary_task_size_lock_reason})"
        )

    def _update_new_terms_limit_from_current_size(self):
        """
        Обновляет лимит новых терминов на основе ТЕКУЩЕГО значения размера пакета.
        Вызывается автоматически при изменении task_size_spin.
        """
        if not hasattr(self, 'new_terms_limit_spin') or not hasattr(self, 'translation_options_widget'):
            return

        current_chars = self.translation_options_widget.task_size_spin.value()
        
        # Для токен-оценок используем единый коэффициент, чтобы лимиты не прыгали от языка книги.
        chars_per_token = api_config.UNIFIED_INPUT_CHARS_PER_TOKEN

        # Расчет в токенах: ~1 новый термин на каждые 500 токенов контента
        estimated_tokens = current_chars / chars_per_token
        recommended_limit = self.round_up_to_tens(max(10, int(estimated_tokens / 500)))
        
        clamped_limit = max(self.new_terms_limit_spin.minimum(), 
                            min(recommended_limit, self.new_terms_limit_spin.maximum()))
        
        self.new_terms_limit_spin.blockSignals(True)
        self.new_terms_limit_spin.setValue(clamped_limit)
        self.new_terms_limit_spin.blockSignals(False)
    def round_up_to_tens(self, n):
        """
        Округляет число n до ближайшего десятка в большую сторону.
        """
        # 1. Делим на 10.0, чтобы получить число с плавающей точкой (например, 23 -> 2.3)
        # 2. math.ceil() округляет его до ближайшего целого ВВЕРХ (2.3 -> 3.0)
        # 3. Умножаем обратно на 10 (3.0 * 10 -> 30.0)
        # 4. Преобразуем в целое число (30.0 -> 30)
        return int(math.ceil(n / 10.0)) * 10
  
    def _apply_initial_settings(self, settings: dict):
        """Применяет начальные настройки, принудительно отключая системные инструкции."""
        if not hasattr(self, 'model_settings_widget'):
            return

        initial_settings = dict(settings)
        # Не наследуем системные инструкции из основного окна.
        initial_settings['system_instruction'] = None
        initial_settings['use_system_instruction'] = False
        if not initial_settings.get('glossary_task_size_limit_override'):
            # The glossary dialog has its own model-based batch-size heuristic.
            # A task_size_limit inherited from the main translation window is only
            # a seed value and must not block the glossary auto-size (e.g. 69k for Gemini).
            initial_settings['task_size_limit_user_defined'] = False

        provider_id = initial_settings.get('provider')
        active_keys = initial_settings.get('api_keys', [])
        if provider_id:
            self.key_widget.set_active_keys_for_provider(provider_id, active_keys)
        else:
            self.key_widget._load_and_refresh_keys()
        self._update_instances_spinbox_limit()

        self.model_settings_widget.set_settings(initial_settings)

        if hasattr(self, 'translation_options_widget'):
            self.translation_options_widget.set_settings(initial_settings)
            self._force_glossary_batch_mode()
            self._apply_glossary_task_size_override(
                initial_settings.get('glossary_task_size_limit_override'),
                initial_settings.get('glossary_task_size_limit_override_reason'),
            )

        glossary_prompt = initial_settings.get('glossary_generation_prompt')
        if glossary_prompt:
            self.prompt_widget.set_prompt(glossary_prompt)

        if 'pipeline_steps' in initial_settings:
            pipeline_payload = initial_settings.get('pipeline_steps')
            if pipeline_payload:
                self._replace_pipeline_steps(steps_from_template_payload(pipeline_payload))
            else:
                self._replace_pipeline_steps([])
        if 'pipeline_enabled' in initial_settings:
            self.pipeline_enabled_checkbox.setChecked(initial_settings.get('pipeline_enabled', False))

        saved_instances = initial_settings.get('num_instances')
        if saved_instances is not None and hasattr(self, 'instances_spin'):
            try:
                saved_instances = int(saved_instances)
            except (TypeError, ValueError):
                saved_instances = 1
            saved_instances = max(1, min(saved_instances, self.instances_spin.maximum()))
            self.instances_spin.setValue(saved_instances)

        self._update_dependent_widgets()
        self._update_start_button_state()

    def _get_available_session_capacity(self) -> int:
        provider_id = self.key_widget.get_selected_provider()
        active_sessions = len(self.key_widget.get_active_keys())
        if active_sessions <= 0:
            return 0
        provider_limit = api_config.provider_max_instances(provider_id)
        if provider_limit is None or provider_limit <= 0:
            provider_limit = active_sessions
        return min(active_sessions, provider_limit)

    def _update_instances_spinbox_limit(self):
        if not hasattr(self, 'instances_spin'):
            return
        session_capacity = self._get_available_session_capacity()
        self.instances_spin.setMaximum(session_capacity if session_capacity > 0 else 1)
    
    def _update_task_status_in_list(self, task_tuple, status):
        """
        Находит строку с задачей в списке и обновляет ее статус и цвет.
        Теперь это ЕДИНЫЙ источник для всех статусов в этом диалоге.
        """
        table = self.chapter_list_widget.table
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item and item.data(QtCore.Qt.ItemDataRole.UserRole) == task_tuple:
                status_item = table.item(row, 1)
                if not status_item:
                    status_item = QTableWidgetItem()
                    table.setItem(row, 1, status_item)
    
                # Ваше изменение для консистентности UI
                status_map = {
                    'success': ("✅ Сгенерировано", "#2ECC71"),
                    'error': ("❌ Ошибка", "#E74C3C"),
                    'filtered': ("🛡️ Фильтр", "#9B59B6"),
                    'held': ("Заморожено", "#7F8C8D"),
                    'pending': ("⏳ Ожидание", self.palette().color(QtGui.QPalette.ColorRole.Text).name())
                }
                display_text, color_hex = status_map.get(status, ("?", "#FFFFFF"))
    
                status_item.setText(display_text)
                
                brush = QtGui.QBrush(QtGui.QColor(color_hex))
                if item: item.setForeground(brush)
                status_item.setForeground(brush)
                break
                
    def _check_and_sync_active_session(self):
        """
        Принудительно проверяет наличие активной сессии в глобальном состоянии (EventBus/Engine).
        Используется для восстановления UI, если событие 'session_started' было пропущено.
        """
        # 1. Спрашиваем у Шины (Главный источник правды)
        active_session_id = None
        if self.bus and hasattr(self.bus, 'get_data'):
            active_session_id = self.bus.get_data("current_active_session")
        
        # 2. Если Шина молчит, спрашиваем у Движка напрямую (Резерв)
        if not active_session_id and self.engine and self.engine.session_id:
             active_session_id = self.engine.session_id

        # 3. АНАЛИЗ: Если сессия ЕСТЬ, но мы думаем, что СПИМ (is_session_active=False)
        if active_session_id and not self.is_session_active:
            print(f"[UI RECOVERY] ⚠️ Обнаружена рассинхронизация! Сессия {active_session_id} работает, а диалог спит. Блокирую интерфейс.")
            # Принудительно переводим UI в режим "Сессия идет"
            self._set_ui_active(True)
            return True
        
        # 4. Если сессия ЕСТЬ и мы ЗНАЕМ об этом — просто подтверждаем статус
        if active_session_id and self.is_session_active:
            return True

        # Сессии нет
        return False
    
    # --- Проверка и восстановление сессии ---
    def _check_for_recovery_session(self):
        """Проверяет наличие цепочки файлов восстановления и пытается загрузить самый свежий валидный."""
        self._session_was_restored = False
        if not (self.project_manager and self.project_manager.project_folder):
            return

        candidates = self._get_recovery_candidates()
        if not candidates:
            return

        # Пытаемся прочитать файлы, начиная с последнего (i+1, затем i)
        recovery_data = None
        valid_candidate_path = None
        
        for idx, path in candidates:
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                # Если успешно прочитали JSON, считаем файл живым
                recovery_data = data
                valid_candidate_path = path
                break
            except (json.JSONDecodeError, OSError):
                # Если файл битый (свет моргнул при записи), пробуем предыдущий
                continue
        
        if not recovery_data:
            # Если ни один файл не прочитался
            QMessageBox.warning(self, "Ошибка восстановления", 
                                "Обнаружены файлы восстановления, но все они повреждены.\nСессия будет начата с нуля.")
            self._discard_interrupted_recovery_state()
            return

        # Если нашли рабочий файл
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Обнаружена прерванная сессия")
        msg_box.setText(f"Найден файл восстановления (версия {os.path.basename(valid_candidate_path)}).")
        msg_box.setInformativeText("Хотите восстановить все настройки, прогресс и продолжить с того места, где остановились?")
        resume_btn = msg_box.addButton("Да, восстановить сессию", QMessageBox.ButtonRole.YesRole)
        restart_btn = msg_box.addButton("Нет, начать заново", QMessageBox.ButtonRole.NoRole)
        msg_box.exec()
        
        if msg_box.clickedButton() == resume_btn:
            try:
                recovered_glossary = recovery_data.get("progress", {}).get("glossary", [])
                recovered_chapters = set(recovery_data.get("progress", {}).get("processed_chapters", []))
                recovered_settings = recovery_data.get("settings", {})
                
                self._accumulated_processed_chapters.update(recovered_chapters)
                self._apply_full_ui_settings(recovered_settings)
                
                self.html_files = [ch for ch in self.html_files if ch not in recovered_chapters]
                self._rebuild_glossary_tasks()
                
                self._session_was_restored = True
                QMessageBox.information(self, "Сессия восстановлена", 
                                        f"Загружено {len(recovered_glossary)} терминов.\n"
                                        f"Исключено {len(recovered_chapters)} готовых глав.")
                
                self.apply_btn.setVisible(True)
                self.glossary_widget.set_glossary(recovered_glossary)
                
                # Удаляем старые файлы, чтобы начать чистую цепочку сохранений
                self._cleanup_all_recovery_files()

            except Exception as e:
                QMessageBox.critical(self, "Ошибка восстановления", f"Сбой при применении данных: {e}")
                self._discard_interrupted_recovery_state()
        else:
            # Пользователь выбрал "Нет" -> сбрасываем весь временный прогресс
            self._discard_interrupted_recovery_state()
            
    def _get_recovery_candidates(self):
        """Возвращает список (index, full_path) найденных файлов восстановления, от новых к старым."""
        if not (self.project_manager and self.project_manager.project_folder):
            return []
        
        candidates = []
        try:
            # Ищем файлы вида ~glossary_session_recovery_123.json
            prefix = "~glossary_session_recovery_"
            suffix = ".json"
            for fname in os.listdir(self.project_manager.project_folder):
                if fname.startswith(prefix) and fname.endswith(suffix):
                    try:
                        # Парсим индекс из имени файла
                        idx_str = fname[len(prefix):-len(suffix)]
                        idx = int(idx_str)
                        candidates.append((idx, os.path.join(self.project_manager.project_folder, fname)))
                    except ValueError:
                        continue
        except OSError:
            pass
        
        # Сортируем: сначала самые большие индексы (самые свежие)
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates
    
    def _perform_safe_recovery_save(self):
        """Сохраняет состояние с инкрементом версии и удалением старых файлов (схема i и i+1)."""
        if not (self.project_manager and self.project_manager.project_folder):
            return

        snapshot = self._create_recovery_snapshot()
        
        with self._recovery_lock:
            # 1. Вычисляем следующий индекс
            candidates = self._get_recovery_candidates()
            last_idx = candidates[0][0] if candidates else 0
            next_idx = last_idx + 1
            
            base_name = os.path.join(self.project_manager.project_folder, f"~glossary_session_recovery_{next_idx}.json")
            
            try:
                # 2. Пишем новый файл (i+1)
                with open(base_name, 'w', encoding='utf-8') as f:
                    json.dump(snapshot, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno()) # Принудительный сброс на диск для защиты от сбоев питания
                
                # 3. Удаляем ВСЕ старые файлы (i, i-1...)
                # Удаляем только после успешной записи нового.
                for _, old_path in candidates:
                    try:
                        os.remove(old_path)
                    except OSError:
                        pass
            except Exception as e:
                # Если запись не удалась, старый файл (i) остается нетронутым!
                error_msg = f"[SYSTEM-WARN] Не удалось сохранить файл восстановления: {e}"
                if hasattr(self, 'log_widget'):
                    self.log_widget.append_message({"message": error_msg})
                print(error_msg)
    
    def _cleanup_all_recovery_files(self):
        """Удаляет все найденные файлы восстановления."""
        candidates = self._get_recovery_candidates()
        for _, path in candidates:
            try:
                os.remove(path)
            except OSError:
                pass

    def _discard_interrupted_recovery_state(self):
        """
        Полностью сбрасывает временный прогресс незавершенной сессии:
        recovery-файлы, накопленную память диалога и промежуточные записи в БД.
        """
        self._cleanup_all_recovery_files()
        self._accumulated_processed_chapters.clear()
        if self.task_manager:
            try:
                self.task_manager.clear_glossary_results()
            except Exception as e:
                print(f"[WARN] Не удалось очистить временные данные glossary_results: {e}")
                
    def _on_extract_numerals_clicked(self):
        if not self.epub_path or not self.html_files:
            QMessageBox.warning(self, "Нет данных", "Сначала выберите EPUB файл и главы.")
            return

        msg = QMessageBox(self)
        msg.setWindowTitle("Поиск числительных")
        msg.setIcon(QMessageBox.Icon.Information) # Добавим иконку для красоты
        msg.setText("Этот процесс просканирует текст и найдет числа (Английские, Китайские, Японские, Корейские и др.), "
                    "преобразовав их в русские слова (например: 'twenty-one' -> 'двадцать один').")
        msg.setInformativeText("Это может занять некоторое время. Добавить найденное в текущий глоссарий?")
        
        # --- КАСТОМНЫЕ КНОПКИ ---
        start_btn = msg.addButton("Начать поиск", QMessageBox.ButtonRole.AcceptRole)
        cancel_btn = msg.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
        
        msg.exec()
        
        # Проверяем, какая именно кнопка была нажата
        if msg.clickedButton() != start_btn:
            return

        # Блокируем UI
        self.extract_numerals_btn.setEnabled(False)
        self.extract_numerals_btn.setText("Сканирование...")
        
        # Запускаем Worker
        self.num_worker = NumeralsExtractionWorker(self.epub_path, self.html_files, self)
        self.num_worker.progress.connect(self._on_numerals_progress)
        self.num_worker.finished.connect(self._on_numerals_finished)
        self.num_worker.start()

    def _on_numerals_progress(self, current, total):
        self.extract_numerals_btn.setText(f"Сканирование... {current}/{total}")

    def _on_numerals_finished(self, new_items, status_msg):
        self.extract_numerals_btn.setEnabled(True)
        self.extract_numerals_btn.setText("🔢 Найти числительные")
        self.num_worker = None # Очистка
        
        if not new_items:
            QMessageBox.information(self, "Результат", status_msg)
            return

        # Записываем данные в базу, имитируя результат работы AI-движка.
        # Используем текущее время, чтобы эти данные считались "свежими".
        initial_glossary_for_session = self.glossary_widget.get_glossary()
        if self.task_manager:
            try:
                current_timestamp = time.time()
                
                self.task_manager.clear_glossary_results()
                if initial_glossary_for_session:
                    data_to_insert = [
                        ('initial', 0, '[]', item.get('original'), item.get('rus'), item.get('note'))
                        for item in initial_glossary_for_session
                    ]
                    with self.task_manager._get_write_conn() as conn:
                        conn.executemany("INSERT INTO glossary_results (task_id, timestamp, chapters_json, original, rus, note) VALUES (?, ?, ?, ?, ?, ?)", data_to_insert)
                
                # Подготавливаем данные для SQL: (task_id, timestamp, chapters_json, original, rus, note)
                # '[]' в chapters_json означает, что термин глобальный (найден во всей книге)
                data_to_insert = [
                    ('numerals', current_timestamp, '[]', item['original'], item['rus'], item['note'])
                    for item in new_items
                ]
                
                with self.task_manager._get_write_conn() as conn:
                    conn.executemany(
                        "INSERT INTO glossary_results (task_id, timestamp, chapters_json, original, rus, note) VALUES (?, ?, ?, ?, ?, ?)", 
                        data_to_insert
                    )
                
                # Самый важный шаг: вызываем стандартное обновление.
                # Этот метод сам сходит в базу, применит режим (Дополнить/Обновить/Накопить)
                # и отобразит результат в таблице.
                self._refresh_glossary_from_db()
                
                # Обновляем файл восстановления на случай сбоя
                self._perform_safe_recovery_save()

                QMessageBox.information(self, "Готово", f"{status_msg}\nЗаписано в базу: {len(data_to_insert)} записей.\nТаблица обновлена.")
                
            except Exception as e:
                QMessageBox.critical(self, "Ошибка базы данных", f"Не удалось сохранить числительные:\n{e}")
    
    
    def clear_glossary_results(self):
        """Очищает таблицу с результатами глоссария."""
        with self._get_write_conn() as conn:
            conn.execute("DELETE FROM glossary_results")
    
    # --- Сбор всех настроек UI ---
    def _get_full_ui_settings(self):
        """Собирает полный 'слепок' настроек из всех виджетов этого диалога."""
        settings = self._get_common_settings()
        
        # Добавляем специфичные для UI настройки
        settings['is_sequential'] = self.sequential_mode_checkbox.isChecked()
        settings['merge_mode'] = self.get_merge_mode()
        settings.update(self.translation_options_widget.get_settings())
        settings['pipeline_enabled'] = bool(getattr(self, 'pipeline_enabled_checkbox', None) and self.pipeline_enabled_checkbox.isChecked())
        settings['pipeline_steps'] = steps_to_template_payload(self.pipeline_steps)
        
        # Удаляем "тяжелые" данные, которые не являются настройками
        settings.pop('full_glossary_data', None)
        settings.pop('initial_glossary_list', None)
        settings.pop('file_path', None)
        
        return settings

    # --- Применение всех настроек к UI ---
    def _apply_full_ui_settings(self, settings: dict):
        """Применяет полный 'слепок' настроек ко всем виджетам."""
        if not settings: return
        
        # Блокируем сигналы, чтобы избежать каскадных обновлений
        # ... (здесь можно добавить блокировку сигналов для всех виджетов ??? TODO) ...

        # Восстанавливаем ключи (провайдер и активные)
        self.key_widget.set_active_keys_for_provider(
            settings.get('provider'), 
            settings.get('api_keys', [])
        )

        # Восстанавливаем настройки модели
        self.model_settings_widget.set_settings(settings)

        # Восстанавливаем промпт генерации глоссария
        prompt_text = settings.get('glossary_generation_prompt') or settings.get('custom_prompt', '')
        self.prompt_widget.set_prompt(prompt_text)

        # Восстанавливаем опции трансляции (размер пакета и т.д.)
        self.translation_options_widget.set_settings(settings)
        self._force_glossary_batch_mode()
        self._apply_glossary_task_size_override(
            settings.get('glossary_task_size_limit_override'),
            settings.get('glossary_task_size_limit_override_reason'),
        )
        
        # Восстанавливаем режимы
        is_sequential = settings.get('is_sequential', False) # <-- Сохраняем значение
        self.sequential_mode_checkbox.setChecked(is_sequential)

        self.send_notes_checkbox.setChecked(settings.get('send_notes_in_sequence', True))

        merge_mode = settings.get('merge_mode', 'supplement')
        if merge_mode == 'update': self.ai_mode_update_radio.setChecked(True)
        elif merge_mode == 'accumulate': self.ai_mode_accumulate_radio.setChecked(True)
        else: self.ai_mode_supplement_radio.setChecked(True)

        if 'pipeline_steps' in settings:
            pipeline_payload = settings.get('pipeline_steps')
            if pipeline_payload:
                self._replace_pipeline_steps(steps_from_template_payload(pipeline_payload))
            else:
                self._replace_pipeline_steps([])
        if 'pipeline_enabled' in settings:
            self.pipeline_enabled_checkbox.setChecked(settings.get('pipeline_enabled', False))
        
        # --- Вызываем чистый метод для обновления UI ---
        self._update_sequential_mode_widgets(is_sequential)

    
    
    
    def _update_dependent_widgets(self):
        """
        Централизованно обновляет виджеты, зависящие от списка ГЛАВ,
        такие как CJK-опции.
        """
        if not self.html_files:
            self.model_settings_widget.update_cjk_options_availability(enabled=False)
            return
            
        is_any_cjk = False
        try:
            with zipfile.ZipFile(open(self.epub_path, 'rb'), 'r') as zf:
                # Проверяем до 3 глав из списка self.html_files
                for chapter_path in self.html_files[:3]:
                    content = zf.read(chapter_path).decode('utf-8', 'ignore')
                    if LanguageDetector.is_cjk_text(content):
                        is_any_cjk = True
                        break
            self.model_settings_widget.update_cjk_options_availability(enabled=True, is_cjk_recommended=is_any_cjk)
        except Exception as e:
            print(f"[WARN] Не удалось определить CJK для генерации глоссария: {e}")
            self.model_settings_widget.update_cjk_options_availability(enabled=True, error=True)

    def _reselect_chapters_for_glossary(self):
        """Открывает диалог выбора глав для генерации глоссария."""
        if not self.epub_path:
            QMessageBox.warning(self, "Ошибка", "Исходный EPUB файл не определен.")
            return

        from ..epub import EpubHtmlSelectorDialog
        success, selected_files = EpubHtmlSelectorDialog.get_selection(
            parent=self,
            epub_filename=self.epub_path,
            pre_selected_chapters=self.html_files,
            project_manager=self.project_manager
        )

        if success:
            self.html_files = selected_files
            self.reselect_chapters_btn.setText(f"Главы: {len(self.html_files)}")
            self._rebuild_glossary_tasks()
            self._update_dependent_widgets()
            

    def _emit_task_manipulation_signal(self, action: str, task_ids: list):
        """
        Общий метод для ЗАПУСКА фоновых команд в TaskManager.
        Использует QThread для предотвращения зависания UI.
        """
        if not (self.engine and self.task_manager):
            return

        target_method = None
        args = []

        if action in ['top', 'bottom', 'up', 'down']:
            target_method = self.task_manager.reorder_tasks
            args = [action, task_ids]
        elif action == 'remove':
            target_method = self.task_manager.remove_tasks
            args = [task_ids]
        elif action == 'duplicate':
            target_method = self.task_manager.duplicate_tasks
            args = [task_ids]

        if not target_method:
            return

        # Блокируем UI на время операции
        self.chapter_list_widget.setEnabled(False)
        self.rebuild_tasks_btn.setEnabled(False)

        # Создаем и запускаем "грузчика"
        self.db_worker = TaskDBWorker(target_method, *args)
        
        # После завершения - разблокируем
        self.db_worker.finished.connect(self._on_db_worker_finished)
        self.db_worker.start()

    def _on_db_worker_finished(self):
        """Слот, который вызывается по завершении фоновой DB-задачи."""
        self.chapter_list_widget.setEnabled(True)
        self.rebuild_tasks_btn.setEnabled(True)
        # TaskManager сам отправит сигнал _notify_ui_of_change,
        # который будет пойман в _on_global_event и вызовет перерисовку.

    @pyqtSlot(str, list)
    def _handle_task_reorder(self, action: str, task_ids: list):
        self._emit_task_manipulation_signal(action, task_ids)
    
    @pyqtSlot(list)
    def _handle_task_duplication(self, task_ids: list):
        self._emit_task_manipulation_signal('duplicate', task_ids)
    
    @pyqtSlot(list)
    def _handle_task_removal(self, task_ids: list):
        self._emit_task_manipulation_signal('remove', task_ids)

    # Также нужно обновить _handle_task_reanimation
    def _handle_task_reanimation(self, task_ids: list):
        if self.engine and self.task_manager:
            self.chapter_list_widget.setEnabled(False)
            self.rebuild_tasks_btn.setEnabled(False)
            
            self.db_worker = TaskDBWorker(self.task_manager.reanimate_tasks, task_ids)
            self.db_worker.finished.connect(self._on_db_worker_finished)
            self.db_worker.start()

    
    def _remove_generated_chapters(self):
        """Убирает из текущего списка глав те, что есть в базе данных."""
        processed_chapters = self._get_all_processed_chapters()
        
        if not processed_chapters:
            QMessageBox.information(self, "Нечего убирать", "Список сгенерированных глав в базе данных пуст.")
            return
    
        initial_count = len(self.html_files)
        # Находим только те главы из текущего списка, которые есть в истории
        chapters_to_remove = [ch for ch in self.html_files if ch in processed_chapters]
        removed_count = len(chapters_to_remove)

        if removed_count == 0:
            QMessageBox.information(self, "Нет совпадений", "В текущем списке нет глав, для которых глоссарий был сгенерирован ранее.")
            return

        # --- НАЧАЛО НОВОЙ ЛОГИКИ С ПОДТВЕРЖДЕНИЕМ ---
        msg_box = QtWidgets.QMessageBox(self)
        msg_box.setWindowTitle("Подтверждение фильтрации")
        msg_box.setText(f"Будет убрано {removed_count} глав из текущего списка задач, так как для них уже есть сгенерированный глоссарий.")
        msg_box.setInformativeText("Вы уверены, что хотите продолжить?")
        msg_box.setIcon(QtWidgets.QMessageBox.Icon.Question)
        yes_button = msg_box.addButton("Да, убрать", QtWidgets.QMessageBox.ButtonRole.YesRole)
        no_button = msg_box.addButton("Нет", QtWidgets.QMessageBox.ButtonRole.NoRole)
        msg_box.exec()

        if msg_box.clickedButton() != yes_button:
            return # Пользователь отменил
        # --- КОНЕЦ НОВОЙ ЛОГИКИ ---

        self.html_files = [ch for ch in self.html_files if ch not in chapters_to_remove]
        final_count = len(self.html_files)
        
        QMessageBox.information(self, "Главы убраны", f"Убрано {removed_count} глав.\nОсталось: {final_count} глав.\n\nТеперь нажмите 'Применить и пересобрать', чтобы обновить список задач.")
        self.reselect_chapters_btn.setText(f"Главы: {len(self.html_files)}")
        self._rebuild_glossary_tasks()
    
    def _get_all_processed_chapters(self) -> set:
        """
        Получает ПОЛНЫЙ список обработанных глав, объединяя:
        1. Долговременную память (файл проекта).
        2. Накопленную память сессии (self._accumulated_processed_chapters).
        3. Текущую БД (self.task_manager).
        Автоматически обновляет накопитель данными из БД.
        """
        # 1. Загружаем постоянную историю из файла проекта
        persistent_chapters = set()
        if self.project_manager:
            persistent_chapters = self.project_manager.load_glossary_generation_map()

        # 2. Загружаем временный прогресс из БД
        db_chapters = set()
        if self.engine and self.task_manager:
            try:
                with self.task_manager._get_read_only_conn() as conn:
                    # Проверяем наличие таблицы перед запросом, чтобы избежать ошибок при инициализации
                    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='glossary_results'")
                    if cursor.fetchone():
                        cursor = conn.execute("SELECT chapters_json FROM glossary_results")
                        for row in cursor.fetchall():
                            if row['chapters_json'] and row['chapters_json'] != '[]':
                                db_chapters.update(json.loads(row['chapters_json']))
            except Exception as e:
                print(f"[UI ERROR] Не удалось прочитать карту глав из БД: {e}")
        
        # 3. Сливаем данные из БД в наш накопитель в памяти, 
        # чтобы они не пропали при очистке БД (например, при перезапуске сессии)
        self._accumulated_processed_chapters.update(db_chapters)
        
        # 4. Возвращаем объединение всех источников
        return persistent_chapters.union(self._accumulated_processed_chapters)
    
    def _rebuild_glossary_tasks(self):
        """
        Пересобирает задачи для глоссария, наполняет центральный ChapterQueueManager
        и инициирует обновление UI.
        """
        self._update_new_terms_limit_from_current_size()
        from gemini_translator.utils.glossary_tools import TaskPreparer
        import uuid
        if not self.task_manager: return

        if not self.html_files or not self.epub_path:
            self.task_manager.clear_all_queues()
            return

        settings = self.translation_options_widget.get_settings()
        settings['use_batching'] = True
        settings['chunking'] = False
        settings['chunk_on_error'] = False
        settings['file_path'] = self.epub_path
        
        real_chapter_sizes = {}
        try:
            with zipfile.ZipFile(open(self.epub_path, 'rb'), 'r') as zf:
                for chapter in self.html_files:
                    real_chapter_sizes[chapter] = len(zf.read(chapter).decode('utf-8', 'ignore'))
        except Exception as e:
            QMessageBox.critical(self, "Ошибка чтения файла", f"Не удалось прочитать главы из EPUB: {e}")
            return
    
        preparer = TaskPreparer(settings, real_chapter_sizes)
        epub_tasks = preparer.prepare_tasks(self.html_files)

        tasks_for_core_engine = []
        context_glossary_for_payload = {}
        for task_payload in epub_tasks:
            task_type = task_payload[0]
            if task_type == 'epub':
                _, epub_path, chapter = task_payload
                payload_for_glossary = ('glossary_batch_task', epub_path, (chapter,), context_glossary_for_payload)
            elif task_type == 'epub_batch':
                _, epub_path, chapters = task_payload
                payload_for_glossary = ('glossary_batch_task', epub_path, chapters, context_glossary_for_payload)
            else:
                continue
            
            tasks_for_core_engine.append(payload_for_glossary)
        
        # --- НАЧАЛО ИЗМЕНЕНИЯ: Установка флага ---
        self._is_rebuilding = True
        try:
            self.task_manager.set_pending_tasks(tasks_for_core_engine)
        finally:
            self._is_rebuilding = False
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---

    
    def _create_settings_tab(self):
        """Создает вкладку с основными настройками."""
        settings_tab = QWidget()
        settings_layout = QVBoxLayout(settings_tab)
        
        # --- ОБЪЕДИНЕННЫЙ БЛОК 1: Основные настройки (Остается без изменений) ---
        main_settings_group = QGroupBox("Основные настройки")
        main_settings_layout = QHBoxLayout(main_settings_group)

        # 1. Элемент слева: Режим обработки
        self.sequential_mode_checkbox = QCheckBox("Последовательный (контекст нарастает)")
        self.sequential_mode_checkbox.setChecked(False)
        self.sequential_mode_checkbox.setToolTip(
            "Включено: Задачи выполняются одна за другой, каждая следующая использует\n"
            "результаты предыдущих для лучшей консистентности.\n\n"
            "Выключено (Параллельный режим): Все задачи выполняются одновременно для максимальной скорости."
        )
        main_settings_layout.addWidget(self.sequential_mode_checkbox)

        # Распорка
        main_settings_layout.addStretch(1)

        # 2. Элемент по центру: Настройки контекста
        self.send_notes_checkbox = QCheckBox("Включать примечания в контекст")
        self.send_notes_checkbox.setToolTip(
            "При внедрении контекста, отправлять также и примечания для лучшего результата."
        )
        self.send_notes_checkbox.setChecked(True)
        main_settings_layout.addWidget(self.send_notes_checkbox)

        # Распорка
        main_settings_layout.addStretch(1)

        # 3. Элемент справа: Количество обработчиков
        self.distribution_group = QWidget()
        dist_layout = QHBoxLayout(self.distribution_group)
        dist_layout.setContentsMargins(0, 0, 0, 0)
        dist_layout.addWidget(QLabel("Обработчиков:  "))
        self.instances_spin = NoScrollSpinBox()
        self.instances_spin.setRange(1, 1)
        self.instances_spin.setToolTip(
            "Количество параллельных обработчиков.\n"
            "Этот параметр активен только в 'Параллельном режиме'."
        )
        dist_layout.addWidget(self.instances_spin)
        main_settings_layout.addWidget(self.distribution_group)
        
        settings_layout.addWidget(main_settings_group)

        # --- БЛОК 2: Настройки API и Модели ---
        api_group = QGroupBox("2. Настройки API и Модели")
        api_layout = QVBoxLayout(api_group)
        
        self.key_widget = KeyManagementWidget(self.settings_manager, self)
        self.model_settings_widget = ModelSettingsWidget(self)
        
        # --- ЧИСТКА ИНТЕРФЕЙСА ---
        # 1. Скрываем ненужные группы
        for group_name in ["cjk_group_box", "glossary_group_box"]:
            group = self.model_settings_widget.findChild(QtWidgets.QGroupBox, group_name)
            if group: group.setVisible(False)
            
        # 2. Отключаем флажки логики
        self.model_settings_widget.dynamic_glossary_checkbox.setChecked(False)
        self.model_settings_widget.use_jieba_glossary_checkbox.setChecked(False)
        self.model_settings_widget.segment_text_checkbox.setChecked(False)
        
        # --- ИНЪЕКЦИЯ: Вставляем "Режим слияния" в правую колонку ModelSettingsWidget ---
        right_column = self.model_settings_widget.findChild(QWidget, "right_column_widget")
        if right_column and right_column.layout():
            # Создаем группу слияния
            merge_mode_group = QGroupBox("3. Режим слияния результатов")
            merge_mode_layout = QVBoxLayout(merge_mode_group) # Используем Vertical для компактности в колонке
            
            self.ai_mode_update_radio = QtWidgets.QRadioButton("Обновить (перезапись)")
            self.ai_mode_update_radio.setToolTip("Если термин уже есть, он будет ОБНОВЛЕН.")
            
            self.ai_mode_supplement_radio = QtWidgets.QRadioButton("Дополнить (только новые)")
            self.ai_mode_supplement_radio.setToolTip("Добавляются ТОЛЬКО термины, которых еще нет.")
            self.ai_mode_supplement_radio.setChecked(True)
            
            self.ai_mode_accumulate_radio = QtWidgets.QRadioButton("Накопить (все подряд)")
            self.ai_mode_accumulate_radio.setToolTip("Добавляются ВСЕ термины, создавая дубликаты.")
            
            merge_mode_layout.addWidget(self.ai_mode_supplement_radio)
            merge_mode_layout.addWidget(self.ai_mode_update_radio)
            merge_mode_layout.addWidget(self.ai_mode_accumulate_radio)
            
            # Добавляем группу в конец правой колонки (под "Прочие опции")
            right_column.layout().addWidget(merge_mode_group)
            
            # Добавляем распорку в конец, чтобы поджать все вверх
            right_column.layout().addStretch(1)

        api_layout.addWidget(self.key_widget)
        api_layout.addWidget(self.model_settings_widget)
        
        settings_layout.addWidget(api_group)
        settings_layout.addStretch(1)

        # --- ПОДКЛЮЧЕНИЕ СИГНАЛОВ ---
        self.key_widget.active_keys_changed.connect(self._update_instances_spinbox_limit)
        self.key_widget.provider_combo.currentIndexChanged.connect(self._update_instances_spinbox_limit)
        self.sequential_mode_checkbox.toggled.connect(
            lambda checked: self.model_settings_widget.set_concurrent_requests_visible(not checked)
        )
        self.sequential_mode_checkbox.toggled.connect(self._on_mode_changed)

        return settings_tab


    def _create_results_tab(self):
        """Создает вкладку с результатами и логом внутри сплиттера."""
        results_tab = QWidget()
        results_layout = QVBoxLayout(results_tab)
        splitter = QtWidgets.QSplitter(Qt.Orientation.Vertical)
        
        self.glossary_widget = GlossaryWidget(self, self.settings_manager)
        self.glossary_widget.set_simplified_mode()
        
        # --- СКВОЗНАЯ ПЕРЕДАЧА ПУТИ ---
        # Передаем путь к файлу, чтобы инструменты очистки могли сверяться с оригиналом
        if self.epub_path:
            self.glossary_widget.set_epub_path(self.epub_path)
        # ------------------------------
        
        # Включаем кнопку пост-обработки
        self.glossary_widget.set_cleanup_button_visible(True) 
        
        self.glossary_widget.glossary_changed.connect(self._on_glossary_manually_changed)
        
        log_group = QGroupBox("Лог выполнения")
        log_layout = QVBoxLayout(log_group)
        self.log_widget = LogWidget(self)
        log_layout.addWidget(self.log_widget)
        
        splitter.addWidget(self.glossary_widget)
        splitter.addWidget(log_group)
        splitter.setSizes([500, 300])
        results_layout.addWidget(splitter)
        
        return results_tab


    def _create_prompt_tab(self):
        """Создает вкладку с редактором промпта."""
        self.prompt_widget = PresetWidget(
            parent=self,
            preset_name="Промпт глоссария",
            default_prompt_func=api_config.default_glossary_prompt,
            load_presets_func=self.settings_manager.load_glossary_prompts,
            save_presets_func=self.settings_manager.save_glossary_prompts,
            get_last_text_func=self.settings_manager.get_last_glossary_prompt_text,
            get_last_preset_func=self.settings_manager.get_last_glossary_prompt_preset_name,
            save_last_preset_func=self.settings_manager.save_last_glossary_prompt_preset_name
        )
        self.prompt_widget.load_last_session_state()
        return self.prompt_widget

    def _load_data(self):
        self.key_widget.provider_combo.currentIndexChanged.emit(0)
        self._update_start_button_state()

    def _update_start_button_state(self):
        """
        Обновляет доступность кнопок 'Начать' и видимость 'Применить'.
        Кнопка 'Применить' видна ВСЕГДА, когда есть данные и не идет сессия.
        """
        # 1. Логика кнопки "Начать"
        if self.is_session_active:
            self.start_btn.setEnabled(False)
        else:
            num_active_keys = len(self.key_widget.get_active_keys())
            pipeline_ready = (not self.pipeline_enabled_checkbox.isChecked()) or bool(self.pipeline_steps)
            can_start = all([
                self.epub_path,
                self.html_files, 
                num_active_keys > 0,
                pipeline_ready,
            ])
            self.start_btn.setEnabled(can_start)

        # 2. Логика кнопки "Применить"
        # Видна, если сессия НЕ активна И в глоссарии есть хотя бы одна запись
        has_glossary_items = len(self.glossary_widget.get_glossary()) > 0
        self.apply_btn.setVisible(not self.is_session_active and has_glossary_items)
    
    @pyqtSlot()
    def _on_glossary_manually_changed(self):
        """
        Слот, который реагирует на ручные правки и запускает таймер
        отложенного автосохранения.
        """
        if not self.is_session_active and self.apply_btn.isVisible():
            self._manual_glossary_edits_after_finish = True
        if self.project_manager and self.project_manager.project_folder:
            self.autosave_timer.start()

    def _snapshot_glossary_entries(self) -> list[dict]:
        snapshot = []
        for entry in self.glossary_widget.get_glossary() or []:
            if isinstance(entry, dict):
                snapshot.append(entry.copy())
        return snapshot

    def _count_valid_glossary_entries(self, glossary_entries) -> int:
        if not isinstance(glossary_entries, list):
            return 0
        return sum(
            1
            for entry in glossary_entries
            if isinstance(entry, dict) and str(entry.get('original', '')).strip()
        )
    
    def _trigger_autosave(self):
        """Безопасно инициирует сохранение в файл восстановления с ротацией."""
        self._perform_safe_recovery_save()
            
    @pyqtSlot(bool)
    def _on_mode_changed(self):
        """
        Слот, обрабатывающий ИЗМЕНЕНИЕ режима пользователем.
        Обновляет UI и пересобирает задачи.
        """
        is_sequential = self.sequential_mode_checkbox.isChecked()

        # 1. Блокируем сигналы, чтобы избежать рекурсивных вызовов
        self.sequential_mode_checkbox.blockSignals(True)
        self.send_notes_checkbox.blockSignals(True)

        # 2. Вызываем чистый метод для обновления UI
        self._update_sequential_mode_widgets(is_sequential)
        
        # 3. Разблокируем сигналы
        self.sequential_mode_checkbox.blockSignals(False)
        self.send_notes_checkbox.blockSignals(False)
        
        # 4. Пересобираем задачи, так как режим изменился (это побочный эффект)
        self._rebuild_glossary_tasks()

    def _update_sequential_mode_widgets(self, is_sequential: bool):
        """
        Обновляет только видимость и доступность виджетов, зависящих
        от последовательного режима. Не вызывает побочных эффектов.
        """
        # Управляем видимостью группы с выбором количества клиентов
        self.distribution_group.setVisible(not is_sequential)

        # Чекбокс "Примечаний" всегда доступен в этом диалоге
        self.send_notes_checkbox.setEnabled(True)

    @pyqtSlot(dict)
    def _on_global_event(self, event: dict):
        """Обрабатывает глобальные события, делегируя их нужным компонентам."""

        event_name, data = event.get('event'), event.get('data', {})

        # --- ДОБАВЛЕНО: Реагируем на смену модели ---
        if event_name == 'model_changed':
             self._calculate_optimal_batch_size() # Для вкладки задач
             return
        
        if event_name == 'log_message':
            self._handle_pipeline_log_event(data)
            return
        if event_name == 'session_started':
            self._session_finished_successfully = False
            self._set_ui_active(True)
        elif event_name == 'session_finished':
            self._shutdown_reason = data.get('reason')
            self._log_session_id = data.get('session_id_log')
            self._session_finished_successfully = (self._shutdown_reason == "Сессия успешно завершена")
            self._handle_pipeline_session_finished(self._shutdown_reason)
            QtCore.QMetaObject.invokeMethod(self, "_on_session_finished", QtCore.Qt.ConnectionType.QueuedConnection)
        elif event_name in ['task_finished', 'task_state_changed', 'generation_state_updated']:
            # --- Проверка флага ---
            if hasattr(self, '_is_rebuilding') and self._is_rebuilding:
                self._redraw_task_list_and_update_map()
                return
            
            # Обновляем прогресс задач всегда
            self._redraw_task_list_and_update_map()
            
            # ВАЖНОЕ ИЗМЕНЕНИЕ: Обновляем глоссарий из БД ТОЛЬКО если сессия активна.
            # Если сессия остановлена, пользователь может править таблицу вручную,
            # и мы не должны перезаписывать его правки устаревшими данными из БД.
            if self.is_session_active:
                self._refresh_glossary_from_db()

            # Автосохранение
            if self.is_session_active:
                self._perform_safe_recovery_save()
    
    def _on_start_stop_clicked(self):
        """Обрабатывает только нажатие на кнопку 'Начать'."""
        if self.engine and self.engine.session_id:
            QMessageBox.warning(self, "Движок занят", "Другая операция уже выполняется. Пожалуйста, дождитесь ее завершения.")
            return
        
        
        can_start = len(self.key_widget.get_active_keys()) > 0
        if not can_start:
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("Нет Ключей")
            msg_box.setText("Запуск невозможен, так как вы не выбрали ни одного ключа.")
            yes_btn = msg_box.addButton("Понял", QMessageBox.ButtonRole.YesRole)
            no_btn = msg_box.addButton("Осознал", QMessageBox.ButtonRole.NoRole)
            msg_box.exec()
            return
        
        if self.pipeline_enabled_checkbox.isChecked():
            if not self.pipeline_steps:
                QMessageBox.warning(self, "Очередь пуста", "Добавьте хотя бы один шаг или выключите режим очереди.")
                return
            self._start_pipeline()
            return

        self._start_session()

    def _append_pipeline_log(self, message, step_id=None):
        if not self.pipeline_run:
            return
        self.pipeline_run.append_log(message, step_id=step_id)
        selected_step_id = self._get_selected_pipeline_step_id()
        if step_id is None or selected_step_id == step_id:
            self._render_selected_pipeline_step_log()

    def _start_pipeline(self):
        if not self.pipeline_steps:
            QMessageBox.warning(self, "Очередь пуста", "Добавьте хотя бы один шаг.")
            return

        self._clear_pipeline_run_state()
        self.pipeline_run = GlossaryPipelineRun(self.pipeline_steps)
        self.pipeline_run.start()
        self._pipeline_stop_requested = False
        self._post_event('log_message', {'message': f"[PIPELINE] Запущен сценарий из {len(self.pipeline_steps)} шагов."})
        self._refresh_pipeline_table()
        self._start_next_pipeline_step()

    def _start_next_pipeline_step(self):
        if not self.pipeline_run:
            return

        next_step = self.pipeline_run.start_next_step()
        if not next_step:
            self._finalize_pipeline_run()
            return

        self._pipeline_active_step_id = next_step.step_id
        step_index = self.pipeline_run.get_step_index(next_step.step_id) + 1
        total_steps = len(self.pipeline_run.steps)
        summary = summarize_step_settings(next_step.settings)
        self._select_pipeline_step(next_step.step_id)
        self._refresh_pipeline_table()

        start_message = (
            f"[PIPELINE] Шаг {step_index}/{total_steps}: {next_step.name} | "
            f"{summary['merge_mode']} | T={summary['temperature']} | {summary['execution_mode']}"
        )
        self._append_pipeline_log(start_message, step_id=next_step.step_id)
        self._post_event('log_message', {'message': start_message})

        self._apply_full_ui_settings(next_step.settings)
        self._rebuild_glossary_tasks()
        self._start_session()

    def _handle_pipeline_log_event(self, data):
        if not self.pipeline_run or not self._pipeline_active_step_id:
            return

        message = data.get('message')
        if not isinstance(message, str) or not message.strip() or message == "---SEPARATOR---":
            return

        self._append_pipeline_log(message, step_id=self._pipeline_active_step_id)

    def _handle_pipeline_session_finished(self, reason):
        if not self.pipeline_run or not self._pipeline_active_step_id:
            return

        status = classify_shutdown_reason(reason)
        finished_step_id = self._pipeline_active_step_id

        if status == STEP_STATUS_SUCCESS:
            self.pipeline_run.mark_current_step_success(reason)
        elif status == STEP_STATUS_CANCELLED:
            self.pipeline_run.mark_current_step_cancelled(reason)
            self._pipeline_stop_requested = True
        else:
            self.pipeline_run.mark_current_step_failed(reason)
            self._pipeline_stop_requested = True

        finish_message = (
            f"[PIPELINE] Шаг завершен: {STEP_STATUS_LABELS.get(status, status)}"
            + (f" | {reason}" if reason else "")
        )
        self._append_pipeline_log(finish_message, step_id=finished_step_id)
        self._post_event('log_message', {'message': finish_message})
        self._pipeline_active_step_id = None
        self._pipeline_waiting_for_next_step = True
        self._refresh_pipeline_table()

    def _advance_pipeline_after_session(self):
        if not self.pipeline_run:
            return

        self._pipeline_waiting_for_next_step = False
        if self.pipeline_run.status == PIPELINE_STATUS_SUCCESS:
            self._finalize_pipeline_run()
            return

        if self.pipeline_run.status in (PIPELINE_STATUS_FAILED, PIPELINE_STATUS_CANCELLED):
            self._finalize_pipeline_run()
            return

        if self._pipeline_stop_requested:
            self._finalize_pipeline_run()
            return

        self._start_next_pipeline_step()

    def _finalize_pipeline_run(self):
        if not self.pipeline_run:
            return

        self._pipeline_waiting_for_next_step = False
        self._pipeline_active_step_id = None
        status = self.pipeline_run.status
        if status == PIPELINE_STATUS_SUCCESS:
            final_message = "[PIPELINE] Сценарий выполнен полностью."
        elif status == PIPELINE_STATUS_CANCELLED:
            final_message = "[PIPELINE] Сценарий остановлен пользователем."
        elif status == PIPELINE_STATUS_FAILED:
            final_message = "[PIPELINE] Сценарий остановлен из-за ошибки шага."
        else:
            final_message = "[PIPELINE] Сценарий завершен."

        self._post_event('log_message', {'message': final_message})
        self._refresh_pipeline_table()

    def _on_soft_stop_clicked(self):
        """Инициирует ПЛАВНУЮ остановку через оркестратор."""
        self._pipeline_stop_requested = True
        if self.orchestrator and self.orchestrator._is_running:
            self.is_soft_stopping = True
            self.soft_stop_btn.setText("Завершение...")
            self.soft_stop_btn.setEnabled(False)
            self.hard_stop_btn.setEnabled(False) 
            # Просто говорим оркестратору начать процедуру плавной остановки
            self.orchestrator.stop()

    def _on_hard_stop_clicked(self):
        """Инициирует ЭКСТРЕННУЮ, немедленную остановку."""
        self._pipeline_stop_requested = True
        if self.engine and self.engine.session_id:
            self.hard_stop_btn.setText("Прерывание...")
            self.hard_stop_btn.setEnabled(False)
            self.soft_stop_btn.setEnabled(False)
            
            # --- ЛОГИКА ЭКСТРЕННОЙ ОСТАНОВКИ ---
            # 1. Находим флаг нашего оркестратора и немедленно его снимаем
            orchestrator_flag_key = self.orchestrator.MANAGED_SESSION_FLAG_KEY if self.orchestrator else None
            if orchestrator_flag_key and self.bus.pop_data(orchestrator_flag_key, None):
                 self._post_event('log_message', {'message': "[SYSTEM] Глобальный флаг управляемой сессии снят принудительно."})

            # 2. Отправляем команду на немедленную остановку движка
            self._post_event('log_message', {'message': "[SYSTEM] Отправка запроса на ЭКСТРЕННУЮ остановку сессии…"})
            self._post_event('manual_stop_requested')

    
    def _start_session(self):
        """Запускает сессию генерации, предварительно очистив старые результаты в БД."""
        if self.engine and self.engine.session_id:
            QMessageBox.warning(self, "Движок занят", "Другая операция уже выполняется. Пожалуйста, дождитесь ее завершения.")
            return

        if not self.task_manager.has_pending_tasks():
            QMessageBox.warning(self, "Нет задач", "Список задач для генерации пуст. Пожалуйста, соберите задачи.")
            return
            
        if not self.epub_path or not os.path.exists(self.epub_path):
            QMessageBox.critical(self, "Критическая ошибка: Файл не найден", f"Не удалось найти исходный EPUB файл: {self.epub_path}")
            return
        
        # Гарантируем, что последнее редактирование сохранено
        self.glossary_widget.commit_active_editor()
        
        self._get_all_processed_chapters()
        # Очищаем таблицу глоссария в БД и добавляем начальные данные (включая ручные правки)
        initial_glossary_for_session = self.glossary_widget.get_glossary()
        if self.task_manager:
            self.task_manager.clear_glossary_results()
            if initial_glossary_for_session:
                data_to_insert = [
                    ('initial', 0, '[]', item.get('original'), item.get('rus'), item.get('note'))
                    for item in initial_glossary_for_session
                ]
                with self.task_manager._get_write_conn() as conn:
                    conn.executemany("INSERT INTO glossary_results (task_id, timestamp, chapters_json, original, rus, note) VALUES (?, ?, ?, ?, ?, ?)", data_to_insert)

        self.tabs.setCurrentIndex(self.tabs.count() - 1)
        current_pipeline_step_index = self.pipeline_run.get_step_index(self._pipeline_active_step_id) if self.pipeline_run else -1
        if current_pipeline_step_index <= 0:
            self.log_widget.clear()
        self.force_exit_on_interrupt = False
        self._session_finished_successfully = False
        self._set_ui_active(True)
        self.settings_manager.save_last_glossary_prompt_text(self.prompt_widget.get_prompt())
        self.settings_manager.save_last_glossary_prompt_preset_name(self.prompt_widget.get_current_preset_name())
        
        settings = self._get_common_settings()
        
        if self.sequential_mode_checkbox.isChecked():
            self.orchestrator = SequentialTaskProvider(
                self._get_common_settings, self,
                event_bus=self.bus, translate_engine=self.engine
            )
            self.orchestrator.start()
        else:
            settings['num_instances'] = self.instances_spin.value()
            settings['glossary_merge_mode'] = self.get_merge_mode()
            self._post_event('start_session_requested', {'settings': settings})
    
    def _refresh_glossary_from_db(self):
        """
        Читает термины из БД через умный SQL-запрос TaskManager'а и обновляет виджет.
        Автоматически применяет дедупликацию (First/Last write wins) и фоновую очистку.
        """
        try:
            # 1. Получаем режим слияния из UI или настроек
            current_mode = self.get_merge_mode()
            
            # 2. Делегируем всю работу TaskManager'у.
            # Он выполнит SQL-запрос с оконными функциями, вернет чистый список
            # и попутно удалит мусор из БД, если его > 30%.
            clean_terms = self.task_manager.fetch_and_clean_glossary(mode=current_mode, return_raw=True)
            
            # 3. Обновляем виджет
            if clean_terms:
                self.glossary_widget.set_glossary(clean_terms)
            
        except Exception as e:
            # Если есть метод логирования, используем его, иначе print
            error_msg = f"[UI ERROR] Ошибка обновления глоссария из БД: {e}"
            if hasattr(self, '_post_event'):
                self._post_event('log_message', {'message': error_msg})
            else:
                print(error_msg)
    
    # --- НОВЫЙ МЕТОД: Расчет размера пакета ---
    def _calculate_optimal_batch_size(self):
        """
        Предлагает оптимальный размер пакета на основе модели.
        При изменении значения в task_size_spin сработает цепочка обновлений для лимита.
        """
        if not hasattr(self, 'model_settings_widget') or not hasattr(self, 'translation_options_widget'):
            return

        if self._glossary_task_size_locked:
            locked_value = self.translation_options_widget.task_size_spin.value()
            lock_reason = self._glossary_task_size_lock_reason or "Внешнее ограничение"
            self._update_new_terms_limit_from_current_size()
            self.translation_options_widget.info_label.setText(
                f"Фиксированный размер пакета: {locked_value:,} симв.\n"
                f"({lock_reason})"
            )
            return

        if getattr(self.translation_options_widget, 'is_task_size_user_defined', lambda: False)():
            self._update_new_terms_limit_from_current_size()
            self.translation_options_widget._update_info_text()
            return

        settings = self.model_settings_widget.get_settings()
        model_name = settings.get('model')
        model_config = api_config.all_models().get(model_name, {})
        context_limit_tokens = model_config.get("context_length", 128000)

        chars_per_token = api_config.UNIFIED_INPUT_CHARS_PER_TOKEN

        # Glossary extraction has heavier prompt/output overhead than plain translation,
        # so a smaller share of the context stays much more stable in practice.
        target_budget_tokens = context_limit_tokens * 0.075
        recommended_chars = int(target_budget_tokens * chars_per_token)

        spin = self.translation_options_widget.task_size_spin
        final_val = max(5000, min(recommended_chars, spin.maximum()))
        
        # Установка вызовет сигнал valueChanged, который запустит _update_new_terms_limit_from_current_size
        if hasattr(self.translation_options_widget, 'set_task_size_limit'):
            self.translation_options_widget.set_task_size_limit(final_val, user_defined=False)
        else:
            spin.setValue(final_val)
        self._update_new_terms_limit_from_current_size()
        budget_share_label = "~7.5%"
        info_text = "Авто-размер: {:,} симв.\n({} контекста {}, единая токен-оценка)".format(
            final_val,
            budget_share_label,
            model_name,
        )
        self.translation_options_widget.info_label.setText(info_text)

    def _redraw_task_list_and_update_map(self):
        """Перерисовывает список задач и обновляет карту сгенерированных глав ИЗ БАЗЫ ДАННЫХ."""
        if not (self.engine and self.task_manager): return

        processed_chapters = self._get_all_processed_chapters()

        self.remove_generated_btn.setEnabled(bool(processed_chapters))
        
        ui_state_list = self.task_manager.get_ui_state_list()
        self.chapter_list_widget.update_list(ui_state_list)
        
        table = self.chapter_list_widget.table
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if not item: continue
            task_tuple = item.data(QtCore.Qt.ItemDataRole.UserRole)
            if not task_tuple: continue

            task_id, payload = task_tuple
            chapters_in_task = payload[2] if len(payload) > 2 else ()
            if isinstance(chapters_in_task, str): chapters_in_task = (chapters_in_task,)
            
            # Проверяем, все ли главы в этой задаче уже были обработаны
            if chapters_in_task and all(ch in processed_chapters for ch in chapters_in_task):
                # Если да, ДОБАВЛЯЕМ маркер к тексту, не меняя статус и цвет
                current_text = item.text()
                if "(Сгенерировано)" not in current_text:
                    item.setText(f"{current_text} (Сгенерировано)")

    
    def get_merge_mode(self):
        if self.ai_mode_accumulate_radio.isChecked(): return 'accumulate'
        if self.ai_mode_update_radio.isChecked(): return 'update'
        return 'supplement'

    def _get_common_settings(self):
        settings = self.model_settings_widget.get_settings()
        settings['provider'] = self.key_widget.get_selected_provider()
        settings['api_keys'] = self.key_widget.get_active_keys()
        model_name = settings.get('model')
        settings['model_config'] = api_config.all_models().get(model_name, {}).copy()
        
        settings['initial_glossary_list'] = self.glossary_widget.get_glossary()
        
        settings['file_path'] = self.epub_path
        settings['glossary_generation_prompt'] = self.prompt_widget.get_prompt() or api_config.default_glossary_prompt()
        settings['custom_prompt'] = api_config.default_prompt()
        settings['glossary_merge_mode'] = self.get_merge_mode()
        settings['send_notes_in_sequence'] = self.send_notes_checkbox.isChecked()
        
        # Передаем лимит новых терминов
        if hasattr(self, 'new_terms_limit_spin'):
            settings['new_terms_limit'] = self.new_terms_limit_spin.value()
        else:
            settings['new_terms_limit'] = 50 # Fallback
            
        return settings


    @pyqtSlot(list)
    def _on_engine_state_update(self, current_glossary_state):
        self.glossary_widget.set_glossary(current_glossary_state)
        self._perform_safe_recovery_save()
    
    @pyqtSlot()
    def _on_session_finished(self):
        """
        Финальная процедура. Обрабатывает результаты и чистит UI.
        """
        self._refresh_glossary_from_db()
        self._manual_glossary_edits_after_finish = False
        
        if self.engine and self.task_manager:
            self.engine.task_manager.release_held_tasks()
        
        self._post_event('log_message', {'message': "[SYSTEM] Получен сигнал завершения движка. Очистка интерфейса…"})
        self.key_widget._load_and_refresh_keys()
        
        # Возвращаем UI в "пассивное" состояние.
        # Это также обновит видимость кнопки "Применить" через _update_start_button_state
        self._set_ui_active(False)

        try:
            current_ui_model_name = self.model_settings_widget.model_combo.currentText()
            model_config = api_config.all_models().get(current_ui_model_name, {})
            model_id_to_sync = model_config.get('id')
            if model_id_to_sync:
                self.key_widget.set_current_model(model_id_to_sync)
        except Exception as e:
            print(f"[ERROR] Не удалось синхронизировать виджет ключей после сессии: {e}")
        
        QtCore.QMetaObject.invokeMethod(self, "_finalize_session_state", QtCore.Qt.ConnectionType.QueuedConnection)
    
        
    @pyqtSlot(dict)
    def _on_generation_finished(self, data: dict):
        """
        Обрабатывает РЕЗУЛЬТАТ от оркестратора.
        Содержит всю специфическую логику этого диалога.
        """
        final_glossary_from_engine = data.get('glossary')
        was_cancelled = data.get('was_cancelled', False)

        # Оркестратор больше не нужен, он свою работу сделал
        if self.orchestrator:
            self.orchestrator.setParent(None)
            self.orchestrator.deleteLater()
            self.orchestrator = None
        
        # --- Сценарий 1: Успешное штатное завершение ---
        if not was_cancelled:
            self.final_glossary = final_glossary_from_engine
            
            # 1. Сначала обновляем данные в виджете (так как _perform_safe_recovery_save берет данные оттуда)
            self.glossary_widget.set_glossary(self.final_glossary)
            
            # 2. Вместо удаления — делаем ФИНАЛЬНЫЙ СНАПШОТ.
            # Теперь, если пока пользователь пьет чай и смотрит на результаты, вырубится свет,
            # при следующем запуске он увидит полностью готовый результат.
            # Удаление произойдет только в методе accept() (кнопка "Применить").
            self._perform_safe_recovery_save()
            
            return
    
        # --- Сценарий 2: Прерывание сессии ---
        # Пытаемся найти хоть какой-то файл восстановления
        candidates = self._get_recovery_candidates()
        
        if candidates:
            # Берем самый свежий
            best_candidate_path = candidates[0][1]
            try:
                with open(best_candidate_path, 'r', encoding='utf-8') as f:
                    recovery_data = json.load(f)
                
                recovered_glossary = recovery_data.get("progress", {}).get("glossary", [])
                recovered_chapters = set(recovery_data.get("progress", {}).get("processed_chapters", []))
                
                # Здесь мы файлы НЕ удаляем, вдруг пользователь нажмет "Нет, отбросить" в диалоге ниже,
                # а потом передумает и перезапустит программу. Пусть файлы живут до явного решения.

                if recovered_glossary:
                    msg_box = QMessageBox(self)
                    msg_box.setWindowTitle("Процесс прерван")
                    msg_box.setText(f"Удалось сохранить {len(recovered_glossary)} терминов и прогресс по {len(recovered_chapters)} главам.")
                    msg_box.setInformativeText("Хотите применить эти промежуточные результаты?")
                    yes_btn = msg_box.addButton("Да, применить", QMessageBox.ButtonRole.YesRole)
                    no_btn = msg_box.addButton("Нет, отбросить", QMessageBox.ButtonRole.NoRole)
                    msg_box.exec()
                    
                    if msg_box.clickedButton() == yes_btn:
                        self.final_glossary = recovered_glossary
                        self.glossary_widget.set_glossary(self.final_glossary)
                        # Обновляем UI и делаем сейв текущего состояния
                        self._redraw_task_list_and_update_map()
                        self._perform_safe_recovery_save()
            except Exception as e:
                QMessageBox.warning(self, "Ошибка восстановления", f"Процесс был прерван, но не удалось прочитать файл восстановления: {e}")
        
        elif was_cancelled:
             QMessageBox.warning(self, "Прервано", "Процесс генерации был прерван. Промежуточные данные не применены.")

        # Если диалог должен был закрыться, но сессия прервана,
        # вызываем accept(), чтобы передать то, что успели накопить.
        if self.force_exit_on_interrupt:
            self.accept()
    
    def _create_recovery_snapshot(self):
        """Собирает все данные для сохранения в файл восстановления."""
        # Теперь мы просто берем полный глоссарий из виджета
        current_glossary = self.glossary_widget.get_glossary()
        processed_chapters = self._get_all_processed_chapters()
        current_ui_settings = self._get_full_ui_settings()
        return {"progress": {"glossary": current_glossary, "processed_chapters": sorted(list(processed_chapters))}, "settings": current_ui_settings}
        
    @pyqtSlot()
    def _finalize_session_state(self):
        """Асинхронно сбрасывает флаг сессии и показывает финальное сообщение."""
        self.is_session_active = False
        self._post_event('log_message', {'message': "[SYSTEM] Интерфейс полностью разблокирован."})
        self._update_start_button_state()
        self._update_pipeline_buttons_state()

        if hasattr(self, '_shutdown_reason') and hasattr(self, '_log_session_id'):
            session_id_log = self._log_session_id
            reason = self._shutdown_reason
            QTimer.singleShot(
                100,
                lambda: post_session_separator(self._post_event, session_id_log=session_id_log, reason=reason),
            )
        if self._pipeline_waiting_for_next_step:
            QTimer.singleShot(0, self._advance_pipeline_after_session)

        del self._shutdown_reason
        del self._log_session_id
            
    
    def _update_filter_button_state(self):
        """Обновляет состояние кнопки 'Убрать сгенерированные'."""
        if hasattr(self, 'remove_generated_btn'):
            # Запрашиваем актуальное состояние из БД
            processed_chapters = self._get_all_processed_chapters()
            self.remove_generated_btn.setEnabled(bool(processed_chapters))
            
    def _set_ui_active(self, active: bool):
        """
        Управляет состоянием всего UI в зависимости от того, активна ли сессия.
        'active = True' означает, что процесс запущен.
        'active = False' означает, что процесс остановлен.
        """
        self.is_session_active = active
        
        # Блокируем/разблокируем основные виджеты настроек
        self.key_widget.setEnabled(not active)
        self.model_settings_widget.setEnabled(not active)
        self.prompt_widget.setEnabled(not active)
        self.translation_options_widget.setEnabled(not active)
        self.send_notes_checkbox.setEnabled(not active)
        self.sequential_mode_checkbox.setEnabled(not active)
        self.glossary_widget.set_controls_enabled(not active)
        self.chapter_list_widget.set_session_mode(active)
        self.pipeline_enabled_checkbox.setEnabled(not active)
        self.pipeline_template_combo.setEnabled(not active)
        self.pipeline_table.setEnabled(not active)
        
        # Переключаем видимость кнопок управления сессией
        self.start_btn.setVisible(not active)
        self.soft_stop_btn.setVisible(active)
        self.hard_stop_btn.setVisible(active)
        
        if active:
            # Сессия ЗАПУЩЕНА
            self.is_soft_stopping = False
            self.soft_stop_btn.setEnabled(True)
            self.soft_stop_btn.setText("Завершить плавно") 
            self.hard_stop_btn.setEnabled(True)
            self.hard_stop_btn.setText("❌ Прервать")
            self.close_btn.setText("Прервать и закрыть")
            # Кнопка "Применить" скроется вызовом _update_start_button_state ниже
        else:
            # Сессия ОСТАНОВЛЕНА
            self.close_btn.setText("Закрыть")
            
        # Обновляем состояние кнопок (Start и Apply)
        self._update_start_button_state()
            
    def _cleanup(self, keep_recovery_file=False):
        """Централизованный метод для всей очистки перед закрытием."""
        if self.bus:
            try:
                self.bus.event_posted.disconnect(self._on_global_event)
                print("[DEBUG] GenerationSessionDialog отписался от глобальной шины.")
            except (TypeError, RuntimeError):
                pass
        
        if self.orchestrator:
            self.orchestrator.deleteLater()
            self.orchestrator = None

        if not keep_recovery_file:
            self._cleanup_all_recovery_files()

    def accept(self):
        """Применяет результат и удаляет файлы восстановления."""
        print("[DEBUG] GenerationSessionDialog.accept() called.")
        
        if hasattr(self, 'glossary_widget'):
            self.glossary_widget.commit_active_editor()
        
        final_glossary = self._snapshot_glossary_entries()
        if self._session_finished_successfully and not self._manual_glossary_edits_after_finish:
            try:
                self._refresh_glossary_from_db()
                refreshed_glossary = self._snapshot_glossary_entries()
                if self._count_valid_glossary_entries(refreshed_glossary) >= self._count_valid_glossary_entries(final_glossary):
                    final_glossary = refreshed_glossary
            except Exception as e:
                self._post_event('log_message', {'message': f"[SYSTEM-WARN] Не удалось подтянуть финальный глоссарий перед применением: {e}"})
        elif not final_glossary and self._session_finished_successfully:
            try:
                self._refresh_glossary_from_db()
                final_glossary = self._snapshot_glossary_entries()
            except Exception as e:
                self._post_event('log_message', {'message': f"[SYSTEM-WARN] Не удалось подтянуть финальный глоссарий перед применением: {e}"})
        processed_chapters = self._get_all_processed_chapters()
        
        self.generation_finished.emit(final_glossary, processed_chapters)
    
        self._cleanup()
        super().accept()

    def reject(self):
        """
        Обрабатывает кнопку 'Закрыть' и крестик.
        """
        print("[DEBUG] GenerationSessionDialog.reject() called.")
        
        is_running = (self.orchestrator and self.orchestrator._is_running) or (self.engine and self.engine.session_id)
        if is_running:
            self.force_exit_on_interrupt = True
            self._on_hard_stop_clicked()
            return 
        
        if self.apply_btn.isVisible():
            msg_box = QtWidgets.QMessageBox(self)
            msg_box.setWindowTitle("Несохраненные результаты")
            msg_box.setIcon(QtWidgets.QMessageBox.Icon.Question)
            msg_box.setText("Генерация завершена, но результаты не были применены.")
            msg_box.setInformativeText("Выберите действие:")
            
            save_recovery_btn = msg_box.addButton("Сохранить в резервный файл и выйти", QtWidgets.QMessageBox.ButtonRole.ActionRole)
            discard_btn = msg_box.addButton("Отбросить и выйти", QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
            cancel_btn = msg_box.addButton("Отмена", QtWidgets.QMessageBox.ButtonRole.RejectRole)
            msg_box.setDefaultButton(cancel_btn)
            
            msg_box.exec()
            clicked_button = msg_box.clickedButton()

            if clicked_button == cancel_btn:
                return 

            if clicked_button == save_recovery_btn:
                self._perform_safe_recovery_save()
                self._cleanup(keep_recovery_file=True)
                super().reject()
                return
            
        self._cleanup()
        super().reject()
    
    def showEvent(self, event):
        """Перехватывает событие первого показа окна и запускает отложенную загрузку."""
        super().showEvent(event)
        
        # Проверяем, не идет ли уже сессия (восстановление состояния)
        self._check_and_sync_active_session()

        if not self._initial_load_done:
            self._initial_load_done = True
            # Запускаем с задержкой, чтобы дать окну полностью отрисоваться
            QTimer.singleShot(50, self._deferred_initial_load)

    def _deferred_initial_load(self):
        """Выполняет все действия по заполнению UI после отрисовки."""
        self.reselect_chapters_btn.setText(f"Главы: {len(self.html_files)}")
        # 1. Считаем оптимальный размер пакета (это вызовет расчет лимита терминов через сигнал)
        self._calculate_optimal_batch_size()
        # 2. И только теперь строим задачи
        self._rebuild_glossary_tasks()
        # 3. Обновляем визуальные CJK опции
        self._update_dependent_widgets()
        
    def closeEvent(self, event):
        """Перехватываем событие закрытия (крестик) и направляем его в нашу логику reject."""
        print("[DEBUG] GenerationSessionDialog.closeEvent() called.")
        self.reject()
        event.ignore()
