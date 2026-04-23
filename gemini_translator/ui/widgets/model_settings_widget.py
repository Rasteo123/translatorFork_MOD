# gemini_translator/ui/widgets/model_settings_widget.py

import os
import subprocess

from PyQt6 import QtWidgets, QtCore, QtGui
from PyQt6.QtWidgets import (
    QGroupBox, QGridLayout, QLabel, QComboBox, QSpinBox, QDoubleSpinBox,
    QHBoxLayout, QCheckBox, QWidget, QVBoxLayout, QPushButton, QDialog, QDialogButtonBox
)
from PyQt6.QtCore import pyqtSignal, pyqtSlot
from .common_widgets import NoScrollSpinBox, NoScrollDoubleSpinBox, NoScrollComboBox
from ..widgets.preset_widget import PresetWidget
# --- Импорты из нашего проекта ---
# Мы импортируем config напрямую, чтобы виджет был самодостаточным
from ...api import config as api_config
from ...utils import markdown_viewer

CHATGPT_LOGIN_URL = "https://chatgpt.com/auth/login"
CHATGPT_SIGNUP_URL = "https://chatgpt.com/auth/login?mode=signup"

class ModelSettingsWidget(QGroupBox):
    """
    Виджет для инкапсуляции всех настроек, связанных с API-моделью.
    """
    recalibrate_requested = pyqtSignal()
    settings_changed = pyqtSignal() # <--- ДОБАВЬТЕ ЭТУ СТРОКУ
    def __init__(self, parent=None, settings_manager=None, server_manager=None):
        super().__init__("Настройки модели", parent)

        self.is_cjk_recommended = False
        self.server_manager = server_manager
        app = QtWidgets.QApplication.instance()
        
        
        if settings_manager:
            self.settings_manager = settings_manager
        else:
            if hasattr(app, 'settings_manager'):
                self.settings_manager = app.get_settings_manager()
            else:
                raise RuntimeError("SettingsManager не был предоставлен и не найден в QApplication.")
        
        
        if not hasattr(app, 'event_bus'):
            raise RuntimeError("EventBus не найден.")
        self.bus = app.event_bus
        self.bus.event_posted.connect(self.on_event)
        
        self.system_instruction_editor_dialog = SystemInstructionEditorDialog(self.settings_manager, self)
        # --- Принудительно загружаем состояние при создании ---
        # Это "пробуждает" дочерний PresetWidget и заставляет его считать
        # из настроек последний использованный пресет.
        self.system_instruction_editor_dialog.preset_widget.load_last_session_state()
        
        self._init_ui()
        self._update_system_instruction_button_text()

    
        
        # 1. Подключаем все виджеты к нашему новому слоту-извещателю
        self.model_combo.currentIndexChanged.connect(self._emit_settings_changed)
        self.rpm_spin.valueChanged.connect(self._emit_settings_changed)
        self.max_concurrent_spin.valueChanged.connect(self._emit_settings_changed)
        self.temperature_override_checkbox.stateChanged.connect(self._emit_settings_changed)
        self.temperature_override_checkbox.stateChanged.connect(self._on_temperature_override_toggled)
        self.temperature_spin.valueChanged.connect(self._emit_settings_changed)
        self.thinking_checkbox.stateChanged.connect(self._emit_settings_changed)
        self.thinking_budget_spin.valueChanged.connect(self._emit_settings_changed)
        self.use_jieba_glossary_checkbox.stateChanged.connect(self._emit_settings_changed)
        self.segment_text_checkbox.stateChanged.connect(self._emit_settings_changed)
        self.dynamic_glossary_checkbox.stateChanged.connect(self._emit_settings_changed)
        self.fuzzy_threshold_spin.valueChanged.connect(self._emit_settings_changed)
        
        self.system_instruction_checkbox.stateChanged.connect(self._emit_settings_changed)
        self.force_accept_checkbox.stateChanged.connect(self._emit_settings_changed)
        self.use_json_epub_pipeline_checkbox.stateChanged.connect(self._emit_settings_changed)
        self.warmup_checkbox.stateChanged.connect(self._emit_settings_changed)
        self.workascii_workspace_name_edit.textChanged.connect(self._emit_settings_changed)
        self.workascii_workspace_index_spin.valueChanged.connect(self._emit_settings_changed)
        self.workascii_timeout_spin.valueChanged.connect(self._emit_settings_changed)
        self.workascii_headless_checkbox.stateChanged.connect(self._emit_settings_changed)
        self.workascii_profile_template_edit.textChanged.connect(self._emit_settings_changed)
        self.workascii_refresh_every_spin.valueChanged.connect(self._emit_settings_changed)
        self.debug_logging_checkbox.stateChanged.connect(self._emit_settings_changed)
        self.debug_operation_filters_edit.textChanged.connect(self._emit_settings_changed)
        self.debug_max_log_mb_spin.valueChanged.connect(self._emit_settings_changed)
    
        # 2. Основной обработчик смены модели остается
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
    

        
        
    def _init_ui(self):
        main_layout = QHBoxLayout(self)
        
        left_column_widget = QWidget()
        left_layout = QGridLayout(left_column_widget)
        left_layout.setContentsMargins(0, 9, 10, 0)
        
        left_layout.addWidget(QLabel("Модель:"), 0, 0)
        self.model_combo = NoScrollComboBox()
        left_layout.addWidget(self.model_combo, 0, 1)
        self.refresh_models_btn = QPushButton("↻")
        self.refresh_models_btn.setFixedWidth(34)
        self.refresh_models_btn.setToolTip("Обновить список моделей от локального сервера.")
        self.refresh_models_btn.clicked.connect(self._refresh_current_provider_models)
        self.refresh_models_btn.setVisible(False)
        left_layout.addWidget(self.refresh_models_btn, 0, 2)
        
        self.rpm_row_widget = QWidget()
        self.rpm_row_widget.setObjectName("rpm_row")
        rpm_layout = QHBoxLayout(self.rpm_row_widget)
        rpm_layout.setContentsMargins(0,0,0,0)
        
        rpm_layout.addWidget(QLabel("RPM (лимит частоты):"))
        self.rpm_spin = NoScrollSpinBox()
        self.rpm_spin.setRange(1, 1000)
        self.rpm_spin.valueChanged.connect(lambda: self._update_control_style(self.rpm_spin, "rpm"))
        rpm_layout.addWidget(self.rpm_spin)
        self.rpm_recommendation_label = QLabel("(рек: ?)")
        self.rpm_recommendation_label.setStyleSheet("color: #aaa; font-size: 9pt;")
        rpm_layout.addWidget(self.rpm_recommendation_label)
        
        left_layout.addWidget(self.rpm_row_widget, 1, 0, 1, 3)
    
        self.concurrent_row_widget = QWidget()
        self.concurrent_row_widget.setObjectName("concurrent_row")
        concurrent_layout = QHBoxLayout(self.concurrent_row_widget)
        concurrent_layout.setContentsMargins(0,0,0,0)
    
        concurrent_layout.addWidget(QLabel("Параллельные запросы:"))
        self.max_concurrent_spin = NoScrollSpinBox()
        self.max_concurrent_spin.setRange(0, 1000)
        self.max_concurrent_spin.setToolTip("Макс. кол-во одновременных запросов. 0 = безлимитный режим.")
        self.max_concurrent_spin.valueChanged.connect(lambda: self._update_control_style(self.max_concurrent_spin, "max_concurrent_requests"))
        concurrent_layout.addWidget(self.max_concurrent_spin)
        self.max_concurrent_recommendation_label = QLabel("(рек: ?)")
        self.max_concurrent_recommendation_label.setStyleSheet("color: #aaa; font-size: 9pt;")
        concurrent_layout.addWidget(self.max_concurrent_recommendation_label)
    
        left_layout.addWidget(self.concurrent_row_widget, 2, 0, 1, 3)
    
        
        
        
        
        
        
        
        
        
        
        
        
        # --- BLOCK START: RPD Widget ---
        self.rpd_row_widget = QWidget()
        self.rpd_row_widget.setObjectName("rpd_row")
        rpd_layout = QHBoxLayout(self.rpd_row_widget)
        rpd_layout.setContentsMargins(0,0,0,0)
        
        rpd_layout.addWidget(QLabel("RPD (лимит в сутки):"))
        self.rpd_spin = NoScrollSpinBox()
        self.rpd_spin.setRange(0, 100000)
        self.rpd_spin.setToolTip("Макс. кол-во запросов в сутки. 0 = безлимит.\nПри достижении 90% любая ошибка сразу блокирует ключ.")
        self.rpd_spin.valueChanged.connect(lambda: self._update_control_style(self.rpd_spin, "rpd"))
        self.rpd_spin.valueChanged.connect(self._emit_settings_changed)
        rpd_layout.addWidget(self.rpd_spin)
        
        self.rpd_recommendation_label = QLabel("(рек: ?)")
        self.rpd_recommendation_label.setStyleSheet("color: #aaa; font-size: 9pt;")
        rpd_layout.addWidget(self.rpd_recommendation_label)
        
        left_layout.addWidget(self.rpd_row_widget, 3, 0, 1, 3)

        # --- Temperature (Row 4) ---
        left_layout.addWidget(QLabel("Температура:"), 4, 0)
        temp_layout = QHBoxLayout()
        self.temperature_override_checkbox = QCheckBox("Override")
        self.temperature_override_checkbox.setToolTip(
            "Отправлять температуру в API-запросах. Если выключено, используется дефолт модели/сервера."
        )
        self.temperature_spin = NoScrollDoubleSpinBox()
        self.temperature_spin.setMinimumWidth(70); self.temperature_spin.setDecimals(1)
        self.temperature_spin.setRange(0.0, 2.0); self.temperature_spin.setSingleStep(0.1)
        self.temperature_spin.setValue(1.0)
        self.temperature_spin.setEnabled(False)
        self.temperature_spin.valueChanged.connect(self.update_temperature_indicator)
        self.temp_indicator = QLabel("Сбалансированный")
        self.temp_indicator.setStyleSheet("color: green; font-size: 10px;")
        temp_layout.addWidget(self.temperature_override_checkbox)
        temp_layout.addWidget(self.temperature_spin)
        temp_layout.addWidget(self.temp_indicator)
        left_layout.addLayout(temp_layout, 4, 1, 1, 2)
    
        # --- Thinking (Row 5) ---
        left_layout.addWidget(QLabel("Thinking:"), 5, 0)
        thinking_layout = QHBoxLayout()
        self.thinking_checkbox = QCheckBox()
        self.thinking_checkbox.stateChanged.connect(self.on_thinking_toggled)
        
        self.thinking_preset_combo = NoScrollComboBox()
        self.thinking_preset_combo.addItems(["Динам.", "1024", "2048", "4096", "8192"])
        self.thinking_preset_combo.setEnabled(False)
        self.thinking_preset_combo.currentTextChanged.connect(self.on_thinking_preset_changed)
        
        self.thinking_budget_spin = NoScrollSpinBox()
        self.thinking_budget_spin.setRange(-1, 32768)
        self.thinking_budget_spin.setValue(-1)
        self.thinking_budget_spin.setEnabled(False)
        self.thinking_budget_spin.valueChanged.connect(self.on_thinking_budget_changed)
        
        self.thinking_level_combo = NoScrollComboBox()
        self.thinking_level_combo.setVisible(False)
        self.thinking_level_combo.currentTextChanged.connect(self._emit_settings_changed)

        self.thinking_info_label = QLabel("(выключено)")
        self.thinking_info_label.setStyleSheet("color: gray; font-size: 10px;")
        
        thinking_layout.addWidget(self.thinking_checkbox)
        thinking_layout.addWidget(self.thinking_preset_combo)
        thinking_layout.addWidget(self.thinking_budget_spin)
        thinking_layout.addWidget(self.thinking_level_combo)
        thinking_layout.addWidget(self.thinking_info_label)
        
        thinking_layout.addStretch()
        left_layout.addLayout(thinking_layout, 5, 1, 1, 2)
    
        # --- System Instructions (Row 6) ---
        self.system_instruction_checkbox = QCheckBox("Сист. инструкции")
        self.system_instruction_checkbox.setToolTip("Включить передачу отдельной системной инструкции (System Prompt).")
        self.system_instruction_btn = QPushButton("Редактор…")
        self.system_instruction_btn.setToolTip(
        "Открыть редактор для создания или выбора постоянной инструкции,\n"
        "определяющей роль или личность AI на всю сессию."
        )
        self.system_instruction_btn.clicked.connect(self._open_system_instruction_editor)
        
        self.system_instruction_checkbox.toggled.connect(self._update_system_instruction_button_text)
        self.system_instruction_btn.setEnabled(False) 
        
        system_instruction_layout = QHBoxLayout()
        system_instruction_layout.setContentsMargins(0,0,0,0)
        system_instruction_layout.setSpacing(10)
        
        self.system_instruction_help_btn = QPushButton("[?]")
        self.system_instruction_help_btn.setFixedSize(28, 28)
        self.system_instruction_help_btn.setStyleSheet("font-size: 14pt; border-radius: 14px;")
        self.system_instruction_help_btn.setToolTip("Открыть справку по системным инструкциям")
        self.system_instruction_help_btn.clicked.connect(self._show_system_instruction_help)
    
        system_instruction_layout.addWidget(self.system_instruction_checkbox)
        system_instruction_layout.addWidget(self.system_instruction_btn, 1)
        system_instruction_layout.addWidget(self.system_instruction_help_btn)
    
        left_layout.addLayout(system_instruction_layout, 6, 0, 1, 3)
        
        # --- Stretch (Row 7) ---
        left_layout.setRowStretch(7, 1)
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        right_column_widget = QWidget()
        right_column_widget.setObjectName("right_column_widget")
        right_layout = QVBoxLayout(right_column_widget)
        right_layout.setContentsMargins(10, 0, 0, 0)
    
        cjk_group = QGroupBox("Опции CJK")
        cjk_group.setObjectName("cjk_group_box")
        cjk_layout = QVBoxLayout(cjk_group)
        self.use_jieba_glossary_checkbox = QCheckBox("Jieba для поиска в глоссарии")
        self.segment_text_checkbox = QCheckBox("Сегментировать CJK текст для перевода")
        self.cjk_options_label = QLabel("Выберите EPUB файл…")
        self.cjk_options_label.setStyleSheet("color: gray; font-size: 9pt;")
        self.use_jieba_glossary_checkbox.setEnabled(False)
        self.segment_text_checkbox.setEnabled(False)
        self.use_jieba_glossary_checkbox.stateChanged.connect(self._update_cjk_checkbox_styles)
        self.segment_text_checkbox.stateChanged.connect(self._update_cjk_checkbox_styles)
        cjk_layout.addWidget(self.use_jieba_glossary_checkbox)
        cjk_layout.addWidget(self.segment_text_checkbox)
        cjk_layout.addWidget(self.cjk_options_label)
        right_layout.addWidget(cjk_group)
    
        glossary_group = QGroupBox("Настройки Динамического Глоссария")
        glossary_group.setObjectName("glossary_group_box")
        glossary_layout = QGridLayout(glossary_group)
        self.dynamic_glossary_checkbox = QCheckBox("Включить")
        self.dynamic_glossary_checkbox.setChecked(True)
        self.dynamic_glossary_checkbox.setToolTip("Фильтровать глобальный глоссарий для каждой главы.")
        
        self.fuzzy_threshold_spin = NoScrollSpinBox()
        self.fuzzy_threshold_spin.setRange(75, 100); self.fuzzy_threshold_spin.setValue(100); self.fuzzy_threshold_spin.setSuffix(" %")
        self.fuzzy_threshold_spin.setToolTip("Порог схожести для поиска терминов (в процентах)\nИспользовать с осторожностью. Ресурсозатратно.\n100 - Выкл.")
        
        self.dynamic_glossary_checkbox.toggled.connect(self.fuzzy_threshold_spin.setEnabled)
    
        recalibrate_btn = QPushButton("🔄")
        recalibrate_btn.setToolTip("Пересчитать производительность Fuzzy-поиска\nна основе текущих глав и глоссария\nПервый запуск будет долгим при значении 84 и ниже. Второй и далее - быстрыми.")
        recalibrate_btn.setFixedSize(28, 28)
        recalibrate_btn.clicked.connect(self.recalibrate_requested.emit)
        
        self.fuzzy_status_label = QLabel("Fuzzy-поиск: (требуется калибровка)\nПервый запуск будет долгим при значении 84 и ниже. Второй и далее - быстрыми.")
        self.fuzzy_status_label.setStyleSheet("color: #aaa; font-size: 10px;")
    
        glossary_layout.addWidget(self.dynamic_glossary_checkbox, 0, 0)
        glossary_layout.addWidget(QLabel("Порог Fuzzy:"), 0, 1)
        glossary_layout.addWidget(self.fuzzy_threshold_spin, 0, 2)
        glossary_layout.addWidget(recalibrate_btn, 0, 3)
        glossary_layout.addWidget(self.fuzzy_status_label, 1, 0, 1, 4)
        
        right_layout.addWidget(glossary_group)
        
        misc_group = QGroupBox("Прочие опции")
        misc_group.setObjectName("misc_group_box")
        misc_layout = QHBoxLayout(misc_group) 
        
        self.warmup_checkbox = QCheckBox("Прогрев")
        self.warmup_checkbox.setToolTip("Отправляет тестовый запрос для 'прогрева' модели перед основной работой.")
        self.warmup_checkbox.setChecked(False)
        self.warmup_checkbox.setVisible(False)
    
        self.force_accept_checkbox = QCheckBox("Без валидации")
        self.force_accept_checkbox.setToolTip("Отключает проверку ответа на корректность HTML.")
        self.force_accept_checkbox.setChecked(False)

        self.use_json_epub_pipeline_checkbox = QCheckBox("JSON EPUB")
        self.use_json_epub_pipeline_checkbox.setToolTip(
            "Включает внутренний пайплайн EPUB -> JSON -> EPUB.\n"
            "По умолчанию остается HTML-обработка."
        )
        self.use_json_epub_pipeline_checkbox.setChecked(False)
        
        self.prettify_checkbox = QCheckBox("Постобработка")
        self.prettify_checkbox.setToolTip("Включает типографическое улучшение ответов ИИ.")
        self.prettify_checkbox.setChecked(True)
        self.prettify_checkbox.setVisible(False)
        
        misc_layout.addWidget(self.warmup_checkbox)
        misc_layout.addStretch(1)
        misc_layout.addWidget(self.force_accept_checkbox)
        misc_layout.addStretch(1)
        misc_layout.addWidget(self.use_json_epub_pipeline_checkbox)
        misc_layout.addStretch(1)
        misc_layout.addWidget(self.prettify_checkbox)
        misc_layout.addStretch(1)
        
        right_layout.addWidget(misc_group)

        self.workascii_group = QGroupBox("Настройки ChatGPT Web")
        self.workascii_group.setObjectName("workascii_group_box")
        workascii_layout = QGridLayout(self.workascii_group)

        self.workascii_workspace_name_edit = QtWidgets.QLineEdit("")

        self.workascii_workspace_index_spin = NoScrollSpinBox()
        self.workascii_workspace_index_spin.setRange(1, 99)
        self.workascii_workspace_index_spin.setValue(1)

        self.workascii_timeout_spin = NoScrollSpinBox()
        self.workascii_timeout_spin.setRange(60, 7200)
        self.workascii_timeout_spin.setValue(1800)
        self.workascii_timeout_spin.setSuffix(" сек")

        self.workascii_headless_checkbox = QCheckBox("Headless")
        self.workascii_headless_checkbox.setChecked(False)
        self.workascii_profile_template_edit = QtWidgets.QLineEdit("")
        self.workascii_profile_template_edit.setPlaceholderText("Необязательно: чистый снимок залогиненного профиля")

        self.workascii_refresh_every_spin = NoScrollSpinBox()
        self.workascii_refresh_every_spin.setRange(0, 9999)
        self.workascii_refresh_every_spin.setValue(0)
        for advanced_control in (
            self.workascii_workspace_name_edit,
            self.workascii_workspace_index_spin,
            self.workascii_headless_checkbox,
            self.workascii_profile_template_edit,
            self.workascii_refresh_every_spin,
        ):
            advanced_control.setParent(self.workascii_group)
        self.workascii_refresh_every_spin.setSpecialValueText("Выкл")
        self.workascii_refresh_every_spin.setSuffix(" запр.")

        runtime_hint = QLabel(
            "Runtime, браузерный профиль и Playwright определяются автоматически. Ручные пути для ChatGPT Web не требуются."
        )
        runtime_hint.setWordWrap(True)
        runtime_hint.setStyleSheet("color: #888; font-size: 9pt;")
        workascii_layout.addWidget(runtime_hint, 0, 0, 1, 3)

        workascii_layout.addWidget(QLabel("Workspace name:"), 1, 0)
        workascii_layout.addWidget(self.workascii_workspace_name_edit, 1, 1, 1, 2)

        workascii_layout.addWidget(QLabel("Workspace index:"), 2, 0)
        workascii_layout.addWidget(self.workascii_workspace_index_spin, 2, 1)

        workascii_layout.addWidget(QLabel("Timeout:"), 3, 0)
        workascii_layout.addWidget(self.workascii_timeout_spin, 3, 1)
        workascii_layout.addWidget(self.workascii_headless_checkbox, 3, 2)
        template_browse_btn = QPushButton("...")
        template_browse_btn.setFixedWidth(34)
        template_browse_btn.clicked.connect(
            lambda: self._browse_workascii_directory(
                self.workascii_profile_template_edit,
                "Папка шаблона профиля ChatGPT",
            )
        )
        workascii_layout.addWidget(QLabel("Fresh profile template:"), 4, 0)
        workascii_layout.addWidget(self.workascii_profile_template_edit, 4, 1)
        workascii_layout.addWidget(template_browse_btn, 4, 2)
        workascii_layout.addWidget(QLabel("Refresh every:"), 5, 0)
        workascii_layout.addWidget(self.workascii_refresh_every_spin, 5, 1)

        workascii_hint = QLabel(
            "Используется сохраненный браузерный профиль ChatGPT. Формат промпта остается прежним, меняется только transport-обработчик."
        )
        workascii_hint.setWordWrap(True)
        workascii_hint.setStyleSheet("color: #888; font-size: 9pt;")
        workascii_hint.setText(
            "РСЃРїРѕР»СЊР·СѓРµС‚СЃСЏ СЃРѕС…СЂР°РЅРµРЅРЅС‹Р№ Р±СЂР°СѓР·РµСЂРЅС‹Р№ РїСЂРѕС„РёР»СЊ ChatGPT. "
            "Если задан шаблон, runtime-профиль будет пересоздаваться из него "
            "перед каждым запуском bridge. Параметр Refresh every перезапускает "
            "браузер после N успешных запросов."
        )
        workascii_layout.addWidget(workascii_hint, 6, 0, 1, 3)
        workascii_hint.setText(
            "Используется сохраненный браузерный профиль ChatGPT. "
            "Если задан шаблон, runtime-профиль будет пересоздаваться из него "
            "перед каждым запуском bridge. Параметр Refresh every перезапускает "
            "браузер после N успешных запросов."
        )

        def hide_workascii_layout_widget(row, column):
            item = workascii_layout.itemAtPosition(row, column)
            if item and item.widget():
                item.widget().hide()

        for row, column in (
            (1, 0),
            (1, 1),
            (2, 0),
            (2, 1),
            (3, 0),
            (3, 2),
            (4, 0),
            (4, 1),
            (4, 2),
            (5, 0),
            (5, 1),
        ):
            hide_workascii_layout_widget(row, column)

        runtime_hint.setText(
            "Авторизация ChatGPT сохраняется автоматически в профиле приложения. "
            "Используйте кнопки ниже для входа или регистрации."
        )
        workascii_hint.setText(
            "Кнопки открывают отдельное окно ChatGPT с тем же сохраненным профилем, который использует переводчик. "
            "После входа или регистрации просто закройте окно браузера."
        )

        auth_buttons_widget = QWidget()
        auth_buttons_layout = QHBoxLayout(auth_buttons_widget)
        auth_buttons_layout.setContentsMargins(0, 0, 0, 0)
        auth_buttons_layout.setSpacing(8)

        self.workascii_login_btn = QPushButton("Войти в ChatGPT")
        self.workascii_login_btn.clicked.connect(self._open_chatgpt_login)
        auth_buttons_layout.addWidget(self.workascii_login_btn)

        self.workascii_signup_btn = QPushButton("Регистрация в ChatGPT")
        self.workascii_signup_btn.clicked.connect(self._open_chatgpt_signup)
        auth_buttons_layout.addWidget(self.workascii_signup_btn)
        auth_buttons_layout.addStretch(1)

        workascii_layout.addWidget(auth_buttons_widget, 7, 0, 1, 3)
        workascii_layout.addWidget(QLabel("Таймаут:"), 8, 0)
        workascii_layout.addWidget(self.workascii_timeout_spin, 8, 1)

        right_layout.addWidget(self.workascii_group)

        self.debug_group = QGroupBox("Debug-логи")
        self.debug_group.setObjectName("debug_group_box")
        debug_layout = QGridLayout(self.debug_group)

        self.debug_logging_checkbox = QCheckBox("Сохранять сырые request/response")
        self.debug_logging_checkbox.setToolTip("JSONL-логи с санитизацией секретов и привязкой к главам/операциям.")
        self.debug_operation_filters_edit = QtWidgets.QLineEdit("")
        self.debug_operation_filters_edit.setPlaceholderText("epub, epub_chunk, glossary_batch_task")
        self.debug_operation_filters_edit.setToolTip("Фильтр по типам операций. Пусто = логировать всё.")

        self.debug_max_log_mb_spin = NoScrollSpinBox()
        self.debug_max_log_mb_spin.setRange(16, 2048)
        self.debug_max_log_mb_spin.setValue(256)
        self.debug_max_log_mb_spin.setSuffix(" MB")
        self.debug_max_log_mb_spin.setToolTip("Максимальный суммарный объем debug-логов на проект с авторотацией.")

        self.open_debug_logs_btn = QPushButton("Открыть debug-папку")
        self.open_debug_logs_btn.clicked.connect(self._open_debug_logs_folder)

        debug_hint = QLabel("Конкретный JSONL-лог операции можно открыть прямо из окна лога.")
        debug_hint.setWordWrap(True)
        debug_hint.setStyleSheet("color: #888; font-size: 9pt;")

        debug_layout.addWidget(self.debug_logging_checkbox, 0, 0, 1, 3)
        debug_layout.addWidget(QLabel("Фильтр операций:"), 1, 0)
        debug_layout.addWidget(self.debug_operation_filters_edit, 1, 1, 1, 2)
        debug_layout.addWidget(QLabel("Лимит логов:"), 2, 0)
        debug_layout.addWidget(self.debug_max_log_mb_spin, 2, 1)
        debug_layout.addWidget(self.open_debug_logs_btn, 2, 2)
        debug_layout.addWidget(debug_hint, 3, 0, 1, 3)

        right_layout.addWidget(self.debug_group)
        right_layout.addStretch(1)
    
        main_layout.addWidget(left_column_widget, 1)
        main_layout.addWidget(right_column_widget, 1)
        
        self.update_temperature_indicator(self.temperature_spin.value())
        self._update_provider_specific_controls(None)

    def _resolve_debug_logs_root(self) -> str:
        candidates = []

        try:
            session_settings = self.settings_manager.load_full_session_settings() or {}
            if isinstance(session_settings, dict):
                candidates.append(session_settings.get('output_folder'))
        except Exception:
            pass

        try:
            candidates.append(self.settings_manager.get_last_project_folder())
        except Exception:
            pass

        for candidate in candidates:
            if candidate and str(candidate).strip():
                return os.path.join(str(candidate), ".debug_logs")

        return os.path.join(self.settings_manager.config_dir, ".debug_logs")

    def _open_debug_logs_folder(self):
        debug_root = self._resolve_debug_logs_root()
        os.makedirs(debug_root, exist_ok=True)
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(debug_root))

    def _open_chatgpt_login(self):
        self._launch_chatgpt_profile_browser(CHATGPT_LOGIN_URL, "входа")

    def _open_chatgpt_signup(self):
        self._launch_chatgpt_profile_browser(CHATGPT_SIGNUP_URL, "регистрации")

    def _launch_chatgpt_profile_browser(self, start_url: str, action_label: str):
        runtime_root = api_config.default_workascii_runtime_root()
        profile_dir = api_config.default_workascii_profile_dir(runtime_root)
        node_path = api_config.find_node_executable(runtime_root)
        playwright_package_root = api_config.find_playwright_package_root(runtime_root)
        playwright_browsers_path = api_config.find_playwright_browsers_path(runtime_root)
        launcher_script = api_config.get_resource_path(
            "gemini_translator/scripts/chatgpt_profile_launcher.cjs"
        )

        missing_parts = []
        if not runtime_root or not os.path.isdir(runtime_root):
            missing_parts.append("runtime")
        if not profile_dir:
            missing_parts.append("profile")
        if not node_path or not os.path.exists(node_path):
            missing_parts.append("node")
        if not launcher_script or not os.path.exists(launcher_script):
            missing_parts.append("launcher")
        if not playwright_package_root or not os.path.isdir(playwright_package_root):
            missing_parts.append("playwright package")

        if missing_parts:
            QtWidgets.QMessageBox.warning(
                self,
                "ChatGPT Web",
                "Не удалось подготовить встроенный браузер ChatGPT.\n"
                f"Отсутствуют компоненты: {', '.join(missing_parts)}.",
            )
            return

        os.makedirs(profile_dir, exist_ok=True)
        command = [
            str(node_path),
            str(launcher_script),
            os.path.normpath(str(profile_dir)),
            str(start_url),
            os.path.normpath(str(playwright_package_root)),
            os.path.normpath(str(playwright_browsers_path)) if playwright_browsers_path else "",
        ]

        try:
            subprocess.Popen(
                command,
                cwd=str(runtime_root),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as error:
            QtWidgets.QMessageBox.warning(
                self,
                "ChatGPT Web",
                "Не удалось открыть встроенный браузер ChatGPT для "
                f"{action_label}: {error}",
            )

    def _browse_workascii_directory(self, target_edit, caption: str):
        current_path = str(target_edit.text() or "").strip()
        initial_dir = current_path if current_path and os.path.isdir(current_path) else ""

        if not initial_dir:
            try:
                initial_dir = self.settings_manager.get_last_project_folder() or ""
            except Exception:
                initial_dir = ""

        if initial_dir and not os.path.isdir(initial_dir):
            initial_dir = os.path.dirname(initial_dir)

        selected_dir = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            caption,
            initial_dir or os.path.expanduser("~"),
        )
        if selected_dir:
            target_edit.setText(os.path.normpath(selected_dir))

    def _update_provider_specific_controls(self, provider_id):
        self._current_provider_id = provider_id
        is_workascii = provider_id == "workascii_chatgpt"
        is_local_provider = provider_id == "local"
        self.workascii_group.setVisible(is_workascii)
        self.refresh_models_btn.setVisible(is_local_provider)
        self.refresh_models_btn.setEnabled(is_local_provider)
    # ----------------------------------------------------
    # Публичные методы
    # ----------------------------------------------------

    def get_settings(self):
        """Возвращает словарь с текущими настройками этого виджета."""
        max_concurrent = self.max_concurrent_spin.value()
        if max_concurrent == 0:
            max_concurrent = None
        current_display_name = self.model_combo.currentText()
        
        system_instruction_text = None
        if self.system_instruction_checkbox.isVisible() and self.system_instruction_checkbox.isChecked():
            system_instruction_text = self.system_instruction_editor_dialog.get_prompt()
    
        # Определяем, какой режим Thinking активен (уровни или бюджет)
        thinking_level = None
        thinking_budget = None
        
        if self.thinking_checkbox.isChecked():
            if self.thinking_level_combo.isVisible():
                # Если видим комбобокс уровней - берем значение оттуда
                thinking_level = self.thinking_level_combo.currentText()
            else:
                # Иначе берем бюджет
                thinking_budget = self.thinking_budget_spin.value()

        return {
            'model': current_display_name,
            'dynamic_glossary': self.dynamic_glossary_checkbox.isChecked(),
            'use_jieba': self.use_jieba_glossary_checkbox.isChecked(),
            'segment_cjk_text': self.segment_text_checkbox.isChecked(),
            'fuzzy_threshold': self.fuzzy_threshold_spin.value(),
            'rpm_limit': self.rpm_spin.value(),
            'rpd_limit': self.rpd_spin.value(), # <-- Добавлено поле RPD
            'temperature': self.temperature_spin.value(),
            'temperature_override_enabled': self.temperature_override_checkbox.isChecked(),
            'use_system_instruction': self.system_instruction_checkbox.isChecked(),
            'system_instruction': system_instruction_text,
            'thinking_enabled': self.thinking_checkbox.isChecked(),
            'max_concurrent_requests': max_concurrent,
            'thinking_budget': thinking_budget,
            'thinking_level': thinking_level, # <-- Новое поле
            'force_accept': self.force_accept_checkbox.isChecked(),
            'use_json_epub_pipeline': self.use_json_epub_pipeline_checkbox.isChecked(),
            'use_prettify': self.prettify_checkbox.isChecked(),
            'use_warmup': self.warmup_checkbox.isVisible() and self.warmup_checkbox.isChecked(),
            'workascii_workspace_name': self.workascii_workspace_name_edit.text().strip(),
            'workascii_workspace_index': self.workascii_workspace_index_spin.value(),
            'workascii_timeout_sec': self.workascii_timeout_spin.value(),
            'workascii_headless': self.workascii_headless_checkbox.isChecked(),
            'workascii_profile_template_dir': self.workascii_profile_template_edit.text().strip(),
            'workascii_refresh_every_requests': self.workascii_refresh_every_spin.value(),
            'debug_logging_enabled': self.debug_logging_checkbox.isChecked(),
            'debug_operation_filters': self.debug_operation_filters_edit.text().strip(),
            'debug_max_log_mb': self.debug_max_log_mb_spin.value(),
        }
        
    
    def set_settings(self, settings: dict):
        """Применяет настройки из словаря к виджетам, блокируя сигналы."""
        self.blockSignals(True)
        for widget in self.findChildren(QtWidgets.QWidget):
            widget.blockSignals(True)
            
        try:
            provider_id = settings.get('provider') or getattr(self, '_current_provider_id', None)
            self._update_provider_specific_controls(provider_id)

            model_name = settings.get('model')
            if isinstance(model_name, (tuple, list)):
                model_name = model_name[0] if model_name else None
            elif model_name is not None and not isinstance(model_name, str):
                model_name = str(model_name)

            if model_name:
                index = self.model_combo.findText(model_name)
                if index != -1:
                    self.model_combo.setCurrentIndex(index)
                elif self.model_combo.count() > 0:
                    self.model_combo.setCurrentIndex(0)
            
            self.rpm_spin.setValue(settings.get('rpm_limit', 10))
            self.rpd_spin.setValue(settings.get('rpd_limit', 0)) # <-- Восстановление RPD
            self.max_concurrent_spin.setValue(settings.get('max_concurrent_requests') or 0)
            
            
            
            temperature_override_enabled = bool(settings.get('temperature_override_enabled', False))
            self.temperature_override_checkbox.setChecked(temperature_override_enabled)
            self.temperature_spin.setEnabled(temperature_override_enabled)
            if temperature_override_enabled:
                self.temperature_spin.setValue(settings.get('temperature', self._model_default_temperature()))
            else:
                self._apply_model_default_temperature()
            self.thinking_checkbox.setChecked(settings.get('thinking_enabled', False))
            
            # Восстанавливаем бюджет
            self.thinking_budget_spin.setValue(settings.get('thinking_budget') if settings.get('thinking_budget') is not None else -1)
            
            # Восстанавливаем уровень (если виджет видим и есть в настройках)
            t_level = settings.get('thinking_level')
            if t_level and self.thinking_level_combo.isVisible():
                idx = self.thinking_level_combo.findText(t_level)
                if idx != -1:
                    self.thinking_level_combo.setCurrentIndex(idx)

            self.use_jieba_glossary_checkbox.setChecked(settings.get('use_jieba', False))
            
            
            
            
            self.segment_text_checkbox.setChecked(settings.get('segment_cjk_text', False))
            
            self.dynamic_glossary_checkbox.setChecked(settings.get('dynamic_glossary', True))
            self.fuzzy_threshold_spin.setValue(settings.get('fuzzy_threshold', 100))
            
            system_instruction_text = settings.get('system_instruction')
            is_checked = bool(system_instruction_text)
            self.system_instruction_checkbox.setChecked(is_checked)
            self.system_instruction_btn.setEnabled(is_checked)
            if is_checked:
                self.system_instruction_editor_dialog.set_prompt(system_instruction_text)
    
            self.force_accept_checkbox.setChecked(settings.get('force_accept', False))
            self.use_json_epub_pipeline_checkbox.setChecked(settings.get('use_json_epub_pipeline', False))
            self.warmup_checkbox.setChecked(settings.get('use_warmup', False))

            self.workascii_workspace_name_edit.setText(settings.get('workascii_workspace_name', ''))
            self.workascii_workspace_index_spin.setValue(int(settings.get('workascii_workspace_index', 1) or 1))
            self.workascii_timeout_spin.setValue(int(settings.get('workascii_timeout_sec', 1800) or 1800))
            self.workascii_headless_checkbox.setChecked(bool(settings.get('workascii_headless', False)))
            self.workascii_profile_template_edit.setText(settings.get('workascii_profile_template_dir', '') or '')
            self.workascii_refresh_every_spin.setValue(int(settings.get('workascii_refresh_every_requests', 0) or 0))
            self.debug_logging_checkbox.setChecked(bool(settings.get('debug_logging_enabled', False)))
            self.debug_operation_filters_edit.setText(settings.get('debug_operation_filters', '') or '')
            self.debug_max_log_mb_spin.setValue(int(settings.get('debug_max_log_mb', 256) or 256))
            
        finally:
            self.blockSignals(False)
            for widget in self.findChildren(QtWidgets.QWidget):
                widget.blockSignals(False)
    
        self._update_system_instruction_button_text()
        self._on_model_changed(self.model_combo.currentIndex(), apply_recommended_limits=False)
    
    # --- ИЗМЕНЕНИЕ 2: Новый слот для прослушки шины ---
    @pyqtSlot(dict)
    def on_event(self, event: dict):
        if event.get('event') == 'provider_changed':
            provider_id = event.get('data', {}).get('provider_id')
            if provider_id:
                self.set_available_models(provider_id)
    
    @pyqtSlot()
    def _emit_settings_changed(self):
        """Просто испускает сигнал об изменении настроек."""
        self.settings_changed.emit()

    @pyqtSlot()
    def _refresh_current_provider_models(self):
        provider_id = getattr(self, "_current_provider_id", None)
        if not provider_id:
            return

        api_config.refresh_dynamic_models(provider_id)
        self.set_available_models(provider_id)
        self._emit_settings_changed()
    
    
    @pyqtSlot(str) # <-- Делаем его слотом
    def set_available_models(self, provider_id: str): # <-- Теперь принимает ID
        """Обновляет список доступных моделей на основе ID провайдера."""
        current_model_name = self.model_combo.currentText()
        current_model_id = self.model_combo.currentData()
        saved_model_name = None
        saved_model_id = None
        try:
            saved_model_name = self.settings_manager.get_last_settings().get('model')
            saved_model_config = api_config.all_models().get(saved_model_name)
            if isinstance(saved_model_config, dict):
                saved_model_id = saved_model_config.get('id')
        except Exception:
            saved_model_name = None
            saved_model_id = None

        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self._update_provider_specific_controls(provider_id)
        
        if provider_id:
            api_config.ensure_dynamic_provider_models(provider_id)
            provider_config = api_config.api_providers().get(provider_id, {})
            models = provider_config.get("models", {})
            if models:
                # --- КЛЮЧЕВОЕ ИЗМЕНЕНИЕ: Сохраняем ID в userData ---
                for display_name, config in models.items():
                    self.model_combo.addItem(display_name, userData=config.get('id'))
        
        if self.model_combo.count() > 0:
            preferred_index = self._find_model_index(model_id=current_model_id)
            if preferred_index == -1:
                preferred_index = self._find_model_index(model_name=current_model_name)
            if preferred_index == -1:
                preferred_index = self._find_model_index(model_id=saved_model_id)
            if preferred_index == -1:
                preferred_index = self._find_model_index(model_name=saved_model_name)
            if preferred_index == -1:
                preferred_index = self._find_model_index(model_name=api_config.default_model_name())
            if preferred_index == -1:
                preferred_index = 0
            self.model_combo.setCurrentIndex(preferred_index)
        
        self.model_combo.blockSignals(False)
        self._on_model_changed(self.model_combo.currentIndex())

    def _find_model_index(self, model_name=None, model_id=None):
        if model_id is not None:
            for index in range(self.model_combo.count()):
                if self.model_combo.itemData(index) == model_id:
                    return index
        if model_name:
            return self.model_combo.findText(model_name)
        return -1
        
    def set_default_model(self, model_display_name: str): # <-- Принимает имя
        """Устанавливает модель по умолчанию по ее ОТОБРАЖАЕМОМУ ИМЕНИ."""
        index = self.model_combo.findText(model_display_name)
        if index != -1:
            self.model_combo.setCurrentIndex(index)
        elif self.model_combo.count() > 0:
            self.model_combo.setCurrentIndex(0)
    
    def update_cjk_options_availability(self, enabled, is_cjk_recommended=False, error=False):
        """Обновляет состояние CJK-опций извне."""
        self.is_cjk_recommended = is_cjk_recommended
        self.use_jieba_glossary_checkbox.setEnabled(enabled)
        self.segment_text_checkbox.setEnabled(enabled)

        if not enabled:
            self.cjk_options_label.setText("Выберите EPUB файл для активации этих опций.")
            self.cjk_options_label.setStyleSheet("color: gray; font-size: 9pt;")
            return

        if error:
            self.use_jieba_glossary_checkbox.setChecked(False)
            self.segment_text_checkbox.setChecked(False)
            self.cjk_options_label.setText("⚠️ Ошибка определения языка, опции доступны вручную.")
            self.cjk_options_label.setStyleSheet("color: orange; font-size: 9pt;")
        elif is_cjk_recommended:
            self.use_jieba_glossary_checkbox.setChecked(True)
            self.segment_text_checkbox.setChecked(False)
            self.cjk_options_label.setText("✅ Обнаружен CJK-текст. Рекомендуемые\nнастройки применены.")
            self.cjk_options_label.setStyleSheet("color: green; font-size: 9pt;")
        else:
            self.use_jieba_glossary_checkbox.setChecked(False)
            self.segment_text_checkbox.setChecked(False)
            self.cjk_options_label.setText("ℹ️ CJK-текст не найден. Опции доступны\nдля ручного включения.")
            self.cjk_options_label.setStyleSheet("color: gray; font-size: 9pt;")
        
        self._update_cjk_checkbox_styles()
        
    # ----------------------------------------------------
    # Внутренние/приватные методы (перенесены из InitialSetupDialog)
    # ----------------------------------------------------


    @pyqtSlot(int)
    def _on_model_changed(self, index: int, apply_recommended_limits: bool = True):
        if index < 0: return
        model_id = self.model_combo.itemData(index)
        model_name = self.model_combo.itemText(index)
        
        recommended_rpm, recommended_max_concurrent, recommended_rpd = 10, 0, 0
        needs_warmup = False
    
        if model_name in api_config.all_models():
            model_cfg = api_config.all_models()[model_name]
            provider_id = model_cfg.get('provider')
            
            # Базовые настройки провайдера
            if provider_id:
                provider_config = api_config.api_providers().get(provider_id, {})
                needs_warmup = provider_config.get("needs_warmup", False)
                # Берем RPD провайдера как базовый
                recommended_rpd = provider_config.get("rpd", 0)
            
            # Настройки конкретной модели перекрывают провайдера
            recommended_rpm = model_cfg.get("rpm", 10)
            recommended_max_concurrent = model_cfg.get("max_concurrent_requests", 0)
            if "rpd" in model_cfg:
                recommended_rpd = model_cfg.get("rpd", 0)
            
            # --- Логика Thinking UI ---
            thinking_levels_list = model_cfg.get("thinkingLevel")
            min_budget = model_cfg.get("min_thinking_budget")
            
            supports_thinking = (thinking_levels_list is not None) or (min_budget is not False)
            self.thinking_checkbox.setEnabled(supports_thinking)
            
            if not supports_thinking:
                self.thinking_checkbox.setChecked(False)
                self.thinking_preset_combo.setVisible(True)
                self.thinking_budget_spin.setVisible(True)
                self.thinking_level_combo.setVisible(False)
                self.thinking_info_label.setVisible(True)
                self.on_thinking_toggled(0)
            else:
                if thinking_levels_list and isinstance(thinking_levels_list, list):
                    self.thinking_preset_combo.setVisible(False)
                    self.thinking_budget_spin.setVisible(False)
                    self.thinking_level_combo.setVisible(True)
                    self.thinking_info_label.setVisible(False) 
                    
                    self.thinking_level_combo.blockSignals(True)
                    self.thinking_level_combo.clear()
                    self.thinking_level_combo.addItems([str(lvl).upper() for lvl in thinking_levels_list])
                    self.thinking_level_combo.blockSignals(False)
                else:
                    self.thinking_preset_combo.setVisible(True)
                    self.thinking_budget_spin.setVisible(True)
                    self.thinking_level_combo.setVisible(False)
                    self.thinking_info_label.setVisible(True)

                self.on_thinking_toggled(self.thinking_checkbox.checkState().value)
        
        self.warmup_checkbox.setVisible(needs_warmup)
        if not needs_warmup:
            self.warmup_checkbox.setChecked(False)

        if not self.temperature_override_checkbox.isChecked():
            self._apply_model_default_temperature()
            
        self.rpm_recommendation_label.setProperty("recommendation", recommended_rpm)
        self.max_concurrent_recommendation_label.setProperty("recommendation", recommended_max_concurrent)
        self.rpd_recommendation_label.setProperty("recommendation", recommended_rpd)
        
        self.rpm_recommendation_label.setText(f"(рек: {recommended_rpm})")
        max_conc_text = "безлимит" if recommended_max_concurrent == 0 else str(recommended_max_concurrent)
        self.max_concurrent_recommendation_label.setText(f"(рек: {max_conc_text})")
        rpd_text = "безлимит" if recommended_rpd == 0 else str(recommended_rpd)
        self.rpd_recommendation_label.setText(f"(рек: {rpd_text})")
        
        if apply_recommended_limits:
            for spin_box in [self.rpm_spin, self.max_concurrent_spin, self.rpd_spin]:
                spin_box.blockSignals(True)
            self.rpm_spin.setValue(recommended_rpm)
            self.max_concurrent_spin.setValue(recommended_max_concurrent)
            self.rpd_spin.setValue(recommended_rpd)
            for spin_box in [self.rpm_spin, self.max_concurrent_spin, self.rpd_spin]:
                spin_box.blockSignals(False)
        
        self._update_control_style(self.rpm_spin, "rpm")
        self._update_control_style(self.max_concurrent_spin, "max_concurrent_requests")
        self._update_control_style(self.rpd_spin, "rpd")
    
        self.bus.event_posted.emit({
            'event': 'model_changed',
            'source': 'ModelSettingsWidget',
            'data': {
                'model_id': model_id,
                'model_name': model_name
            }
        })
        
    def _update_control_style(self, spin_box, recommendation_type):
        if recommendation_type == "rpm": label = self.rpm_recommendation_label
        elif recommendation_type == "max_concurrent_requests": label = self.max_concurrent_recommendation_label
        elif recommendation_type == "rpd": label = self.rpd_recommendation_label
        else: return
        
        recommendation = label.property("recommendation")
        if recommendation is None: return
        current_value = spin_box.value()
        COLOR_LOWER, COLOR_HIGHER = "#A9CCE3", "#F5B7B1"

        if current_value == recommendation:
            spin_box.setStyleSheet("")
            label.setStyleSheet("color: #aaa; font-size: 9pt;")
        elif current_value < recommendation and recommendation != 0:
             # Для RPD и RPM меньше рекомендация -> синий (safe)
             # Но для 0 (безлимит) в рекомендации любое значение будет > 0 (красным)
             # Здесь упрощенная логика:
            spin_box.setStyleSheet(f"color: {COLOR_LOWER};")
            label.setStyleSheet(f"color: {COLOR_LOWER};")
        else:
            spin_box.setStyleSheet(f"color: {COLOR_HIGHER};")
            label.setStyleSheet(f"color: {COLOR_HIGHER};")

    def _model_default_temperature(self):
        model_name = self.model_combo.currentText()
        model_cfg = api_config.all_models().get(model_name, {})
        raw_value = model_cfg.get("default_temperature") if isinstance(model_cfg, dict) else None
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            value = 1.0
        return max(self.temperature_spin.minimum(), min(self.temperature_spin.maximum(), value))

    def _apply_model_default_temperature(self):
        default_temperature = self._model_default_temperature()
        self.temperature_spin.blockSignals(True)
        try:
            self.temperature_spin.setValue(default_temperature)
        finally:
            self.temperature_spin.blockSignals(False)
        self.update_temperature_indicator(default_temperature)

    def _on_temperature_override_toggled(self, state):
        enabled = (state == QtCore.Qt.CheckState.Checked.value) if isinstance(state, int) else bool(state)
        self.temperature_spin.setEnabled(enabled)
        if not enabled:
            self._apply_model_default_temperature()

    def update_temperature_indicator(self, value):
        if value <= 0.7: text, color = "Точный (для серьезных текстов)", "blue"
        elif value <= 1.3: text, color = "Сбалансированный (универсальный)", "green"
        else: text, color = "Креативный (для 'веселых' текстов)", "orange"
        self.temp_indicator.setText(text)
        self.temp_indicator.setStyleSheet(f"color: {color}; font-size: 10px;")

    def _update_cjk_checkbox_styles(self):
        jieba_matches = (self.use_jieba_glossary_checkbox.isChecked() == self.is_cjk_recommended)
        self.use_jieba_glossary_checkbox.setStyleSheet("color: green;" if jieba_matches else "color: #D35400;")
        
        recommended_segment = False
        if self.is_cjk_recommended: recommended_segment = self.segment_text_checkbox.isChecked()
        segment_matches = (self.segment_text_checkbox.isChecked() == recommended_segment) or self.is_cjk_recommended
        self.segment_text_checkbox.setStyleSheet("color: green;" if segment_matches else "color: #D35400;")
        
    def on_thinking_toggled(self, state):
        enabled = (state == QtCore.Qt.CheckState.Checked.value) if isinstance(state, int) else state
        
        # Управляем доступностью контролов
        self.thinking_preset_combo.setEnabled(enabled)
        self.thinking_budget_spin.setEnabled(enabled)
        self.thinking_level_combo.setEnabled(enabled)
        
        # Обновляем текст лейбла ТОЛЬКО если мы в режиме бюджета
        # (в режиме уровней лейбл скрыт в _on_model_changed)
        if self.thinking_budget_spin.isVisible():
            if enabled: 
                self.on_thinking_budget_changed(self.thinking_budget_spin.value())
            else: 
                self.thinking_info_label.setText("(выключено)")
                self.thinking_info_label.setStyleSheet("color: gray; font-size: 10px;")

    def on_thinking_preset_changed(self, preset_text):
        values = {"Динам.": -1, "1024": 1024, "2048": 2048, "4096": 4096, "8192": 8192}
        if preset_text in values: self.thinking_budget_spin.setValue(values[preset_text])

    def on_thinking_budget_changed(self, value):
        if not self.thinking_checkbox.isChecked(): return
        if value == -1: text, color = "(динамический)", "green"
        elif value == 0: text, color = "(отключен)", "red"
        else: text, color = f"({value} токенов)", "blue"
        self.thinking_info_label.setText(text); self.thinking_info_label.setStyleSheet(f"color: {color}; font-size: 10px;")
        
        
    def set_concurrent_requests_visible(self, visible: bool):
        """
        Управляет видимостью строки с настройкой параллельных запросов.
        """
        # Находим виджет-контейнер по его уникальному имени
        # (мы задали его в _init_ui)
        self.concurrent_row_widget.setVisible(visible)
    
    def set_cjk_options_visible(self, visible: bool):
        """Управляет видимостью группы настроек CJK."""
        group = self.findChild(QGroupBox, "cjk_group_box")
        if group:
            group.setVisible(visible)
            
    def set_glossary_options_visible(self, visible: bool):
        """Управляет видимостью группы настроек глоссария."""
        group = self.findChild(QGroupBox, "glossary_group_box")
        if group:
            group.setVisible(visible)
            
    def set_misc_options_visible(self, visible: bool):
        """Управляет видимостью группы прочих настроек."""
        group = self.findChild(QGroupBox, "misc_group_box")
        if group:
            group.setVisible(visible)

    def _open_system_instruction_editor(self):
        # Просто запускаем наш постоянный экземпляр диалога
        self.system_instruction_editor_dialog.exec()
        
        # После его закрытия (неважно, "Принять" или "Отмена")
        # обновляем текст на кнопке, так как состояние могло измениться
        self._update_system_instruction_button_text()
        
        # Сообщаем, что настройки могли измениться, чтобы сработал флаг "*"
        self._emit_settings_changed()
    
    def _update_system_instruction_button_text(self):
        """Обновляет текст и состояние кнопки редактора системных инструкций."""
        is_enabled = self.system_instruction_checkbox.isChecked()
        self.system_instruction_btn.setEnabled(is_enabled)
    
        if is_enabled:
            preset_name = self.system_instruction_editor_dialog.get_current_preset_name()
            if preset_name:
                self.system_instruction_btn.setText(f"Редактор: {preset_name}")
            else:
                self.system_instruction_btn.setText("Редактор: [Пользовательский]")
        else:
            self.system_instruction_btn.setText("Редактор…")
        
    def _show_system_instruction_help(self):
        """Открывает модальное окно справки с переходом к нужному разделу."""
        markdown_viewer.show_markdown_viewer(
            parent_window=self.window(),
            modal=True,
            section="### ⚙️ Системные инструкции (System Prompt)"
        )

        
class SystemInstructionEditorDialog(QDialog):
    """Диалог, инкапсулирующий PresetWidget для редактирования системных инструкций."""
    def __init__(self, settings_manager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Редактор системных промптов")
        self.setMinimumSize(700, 500)
        
        # settings_manager теперь передается напрямую и используется в PresetWidget
        
        layout = QVBoxLayout(self)
        self.preset_widget = PresetWidget(
            parent=self,
            preset_name="Системный Промпт", # <-- Ваше улучшение
            default_prompt_func=lambda: "", 
            load_presets_func=settings_manager.load_system_prompts,
            save_presets_func=settings_manager.save_system_prompts,
            get_last_text_func=settings_manager.get_last_system_prompt_text,
            get_last_preset_func=settings_manager.get_last_system_prompt_preset_name,
            save_last_preset_func=settings_manager.save_last_system_prompt_preset_name,
            show_default_button=False
        )
        self.preset_widget.load_last_session_state()
        layout.addWidget(self.preset_widget)

        button_box = QDialogButtonBox()
        ok_button = button_box.addButton("Принять", QDialogButtonBox.ButtonRole.AcceptRole)
        cancel_button = button_box.addButton("Отмена", QDialogButtonBox.ButtonRole.RejectRole)
        
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def accept(self):
        """Переопределяем, чтобы сохранить состояние перед закрытием."""
        # Вызываем наш новый метод сохранения состояния у дочернего виджета
        self.preset_widget.save_last_session_state()
        # Вызываем стандартный accept, чтобы закрыть диалог с результатом "ОК"
        super().accept()

    def get_prompt(self):
        return self.preset_widget.get_prompt()

    def set_prompt(self, text):
        self.preset_widget.set_prompt(text)

    def get_current_preset_name(self):
        """Прокси-метод для получения имени текущего пресета из вложенного виджета."""
        return self.preset_widget.get_current_preset_name()
