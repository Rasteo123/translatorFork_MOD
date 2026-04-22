# -*- coding: utf-8 -*-

from copy import deepcopy

from PyQt6 import QtCore, QtWidgets
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
    QInputDialog,
)

from ...api import config as api_config
from ...utils.settings import SettingsManager
from .common_widgets import NoScrollComboBox, NoScrollDoubleSpinBox, NoScrollSpinBox


AUTO_TRANSLATION_DEFAULTS = {
    "enabled": False,
    "max_rounds": 3,
    "auto_restart_after_retry": True,
    "log_each_step": True,
    "glossary_enabled": False,
    "glossary_prompt_preset": None,
    "translate_chapters_enabled": True,
    "translation_mode_override": "inherit",
    "model_override": None,
    "thinking_mode_override": "inherit",
    "batch_token_limit_override": 0,
    "retry_short_enabled": True,
    "retry_short_ratio": 0.70,
    "retry_short_ratio_mode": "translation_over_original",
    "retry_untranslated_enabled": True,
    "filter_repack_enabled": True,
    "filter_repack_batch_size": 3,
    "filter_repack_dilute": True,
    "filter_redirect_enabled": False,
    "filter_redirect_provider": None,
    "filter_redirect_model": None,
    "retry_network_failed_enabled": True,
    "retry_network_failed_delay_sec": 60,
    "ai_consistency_enabled": False,
    "ai_consistency_auto_fix": True,
    "ai_consistency_fix_confidences": ["high", "medium", "low"],
    "ai_consistency_mode": "standard",
    "ai_consistency_chunk_size": 3,
}

BUILTIN_AUTO_TRANSLATION_PRESETS = {
    "Только перевод": {
        "enabled": True,
        "glossary_enabled": False,
        "retry_short_enabled": False,
        "retry_untranslated_enabled": False,
        "filter_repack_enabled": False,
        "retry_network_failed_enabled": False,
        "ai_consistency_enabled": False,
    },
    "+глоссарий": {
        "enabled": True,
        "glossary_enabled": True,
        "retry_short_enabled": False,
        "retry_untranslated_enabled": False,
        "filter_repack_enabled": False,
        "retry_network_failed_enabled": False,
        "ai_consistency_enabled": False,
    },
    "+недоперевод +глоссарий": {
        "enabled": True,
        "glossary_enabled": True,
        "retry_short_enabled": False,
        "retry_untranslated_enabled": True,
        "filter_repack_enabled": False,
        "retry_network_failed_enabled": False,
        "ai_consistency_enabled": False,
    },
    "+все функции": {
        "enabled": True,
        "glossary_enabled": True,
        "retry_short_enabled": True,
        "retry_untranslated_enabled": True,
        "filter_repack_enabled": True,
        "retry_network_failed_enabled": True,
        "ai_consistency_enabled": True,
        "ai_consistency_auto_fix": True,
    },
}


class AutoTranslateWidget(QWidget):
    settings_changed = QtCore.pyqtSignal()
    open_glossary_requested = QtCore.pyqtSignal()
    open_validator_requested = QtCore.pyqtSignal()
    open_consistency_requested = QtCore.pyqtSignal()

    def __init__(self, parent=None, settings_manager: SettingsManager = None):
        super().__init__(parent)
        app = QtWidgets.QApplication.instance()
        self.settings_manager = settings_manager or app.get_settings_manager()

        self.loaded_preset_name = None
        self.loaded_preset_source = None
        self.original_preset_state = None
        self.is_preset_modified = False
        self._is_loading = False
        self._current_provider_id = None
        self._current_model_name = api_config.default_model_name()
        self._current_task_size_limit = 0
        self._current_uses_cjk = False
        self._current_model_settings = {}

        self._save_state_timer = QtCore.QTimer(self)
        self._save_state_timer.setSingleShot(True)
        self._save_state_timer.setInterval(900)
        self._save_state_timer.timeout.connect(self._save_last_state)

        self._init_ui()
        self.load_last_session_state()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(8)

        preset_layout = QHBoxLayout()
        preset_layout.setContentsMargins(0, 0, 0, 0)

        self.preset_combo = NoScrollComboBox()
        self.preset_combo.currentTextChanged.connect(self._on_preset_selected)
        self.save_as_btn = QPushButton("Сохранить как…")
        self.save_as_btn.clicked.connect(self._save_new_preset)
        self.overwrite_btn = QPushButton("Перезаписать")
        self.overwrite_btn.clicked.connect(self._overwrite_preset)
        self.delete_btn = QPushButton("Удалить")
        self.delete_btn.clicked.connect(self._delete_preset)

        preset_layout.addWidget(QLabel("Пресеты:"))
        preset_layout.addWidget(self.preset_combo, 1)
        preset_layout.addWidget(self.save_as_btn)
        preset_layout.addWidget(self.overwrite_btn)
        preset_layout.addWidget(self.delete_btn)
        main_layout.addLayout(preset_layout)

        scroll_area = QScrollArea(self)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)

        scroll_content = QWidget()
        content_layout = QVBoxLayout(scroll_content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(10)

        tools_group = QGroupBox("Быстрые блоки")
        tools_layout = QHBoxLayout(tools_group)
        self.open_glossary_btn = QPushButton("AI-глоссарий…")
        self.open_glossary_btn.clicked.connect(self.open_glossary_requested.emit)
        self.open_validator_btn = QPushButton("Проверка переводов…")
        self.open_validator_btn.clicked.connect(self.open_validator_requested.emit)
        self.open_consistency_btn = QPushButton("Согласованность AI…")
        self.open_consistency_btn.clicked.connect(self.open_consistency_requested.emit)
        tools_layout.addWidget(self.open_glossary_btn)
        tools_layout.addWidget(self.open_validator_btn)
        tools_layout.addWidget(self.open_consistency_btn)
        content_layout.addWidget(tools_group)

        general_group = QGroupBox("Общий сценарий")
        general_layout = QGridLayout(general_group)
        self.enabled_checkbox = QCheckBox("Включить автопайплайн для этой сессии")
        self.auto_restart_checkbox = QCheckBox("Автоматически перезапускать перевод после автодействий")
        self.auto_restart_checkbox.setChecked(True)
        self.log_each_step_checkbox = QCheckBox("Писать шаги автопайплайна в лог")
        self.log_each_step_checkbox.setChecked(True)
        self.max_rounds_spin = NoScrollSpinBox()
        self.max_rounds_spin.setRange(1, 20)
        self.max_rounds_spin.setValue(3)

        general_layout.addWidget(self.enabled_checkbox, 0, 0, 1, 3)
        general_layout.addWidget(self.auto_restart_checkbox, 1, 0, 1, 3)
        general_layout.addWidget(self.log_each_step_checkbox, 2, 0, 1, 3)
        general_layout.addWidget(QLabel("Макс. автоциклов:"), 3, 0)
        general_layout.addWidget(self.max_rounds_spin, 3, 1)
        general_layout.addWidget(QLabel("Нужно, чтобы не уйти в бесконечные повторы."), 3, 2)
        content_layout.addWidget(general_group)

        glossary_group = QGroupBox("1. Составление глоссария")
        glossary_layout = QGridLayout(glossary_group)
        self.glossary_checkbox = QCheckBox("Подготовить/дополнить глоссарий перед переводом")
        self.glossary_prompt_combo = NoScrollComboBox()
        glossary_layout.addWidget(self.glossary_checkbox, 0, 0, 1, 3)
        glossary_layout.addWidget(QLabel("Шаблон/пресет:"), 1, 0)
        glossary_layout.addWidget(self.glossary_prompt_combo, 1, 1, 1, 2)
        glossary_hint = QLabel(
            "Выбранный пресет будет подставляться как основной шаблон для AI-глоссария. "
            "Сам генератор открывается теми же существующими инструментами."
        )
        glossary_hint.setWordWrap(True)
        glossary_hint.setStyleSheet("color: #9aa4b2;")
        glossary_layout.addWidget(glossary_hint, 2, 0, 1, 3)
        content_layout.addWidget(glossary_group)

        translation_group = QGroupBox("2. Перевод глав")
        translation_layout = QGridLayout(translation_group)
        self.translation_required_checkbox = QCheckBox("Обязательный шаг")
        self.translation_required_checkbox.setChecked(True)
        self.translation_required_checkbox.setEnabled(False)
        translation_intro = QLabel(
            "Это основной прогон автосценария. Здесь можно задать отдельный профиль запуска, "
            "не меняя общие настройки всей программы."
        )
        translation_intro.setWordWrap(True)
        translation_intro.setStyleSheet("color: #9aa4b2;")
        self.translation_mode_combo = NoScrollComboBox()
        self.translation_mode_combo.addItem("Как в общих настройках", userData="inherit")
        self.translation_mode_combo.addItem("Всегда пакетами", userData="batch")
        self.translation_mode_combo.addItem("Всегда по одной главе", userData="single")
        self.translation_mode_combo.addItem("Всегда чанками", userData="chunk")
        self.model_override_combo = NoScrollComboBox()
        self.thinking_override_combo = NoScrollComboBox()
        self.batch_tokens_spin = NoScrollSpinBox()
        self.batch_tokens_spin.setRange(0, 200000)
        self.batch_tokens_spin.setSingleStep(250)
        self.batch_tokens_spin.setValue(0)
        self.batch_tokens_spin.setToolTip(
            "Примерный лимит входных токенов на пакет/чанк для основного автопрогона.\n"
            "0 = использовать общий лимит из вкладки 'Список задач'."
        )

        profile_group = QGroupBox("Профиль основного прогона")
        profile_layout = QGridLayout(profile_group)
        profile_layout.addWidget(QLabel("Режим очереди:"), 0, 0)
        profile_layout.addWidget(self.translation_mode_combo, 0, 1)
        profile_layout.addWidget(QLabel("Модель:"), 1, 0)
        profile_layout.addWidget(self.model_override_combo, 1, 1)
        profile_layout.addWidget(QLabel("Режим размышления:"), 2, 0)
        profile_layout.addWidget(self.thinking_override_combo, 2, 1)
        profile_layout.addWidget(QLabel("Лимит пакета (~входные токены):"), 3, 0)
        profile_layout.addWidget(self.batch_tokens_spin, 3, 1)

        self.translation_profile_hint = QLabel(
            "0 в лимите пакета = наследовать общий размер задачи. Значение в токенах "
            "автовкладка пересчитает в символьный лимит по общей токен-оценке проекта."
        )
        self.translation_profile_hint.setWordWrap(True)
        self.translation_profile_hint.setStyleSheet("color: #9aa4b2;")
        profile_layout.addWidget(self.translation_profile_hint, 4, 0, 1, 2)

        self.translation_summary_frame = QFrame()
        self.translation_summary_frame.setObjectName("autoTranslationSummaryFrame")
        self.translation_summary_frame.setFrameShape(QFrame.Shape.StyledPanel)
        self.translation_summary_frame.setStyleSheet(
            "QFrame#autoTranslationSummaryFrame {"
            "border: 1px solid #49566b;"
            "border-radius: 8px;"
            "background-color: rgba(73, 86, 107, 0.18);"
            "}"
        )
        translation_summary_layout = QVBoxLayout(self.translation_summary_frame)
        translation_summary_layout.setContentsMargins(10, 8, 10, 8)
        translation_summary_layout.setSpacing(4)
        self.translation_summary_title = QLabel("Итог профиля")
        self.translation_summary_title.setStyleSheet("font-weight: 600; color: #d7e3f4;")
        self.translation_summary_label = QLabel()
        self.translation_summary_label.setWordWrap(True)
        self.translation_summary_label.setStyleSheet("color: #d7e3f4;")
        self.translation_summary_note = QLabel()
        self.translation_summary_note.setWordWrap(True)
        self.translation_summary_note.setStyleSheet("color: #9aa4b2;")
        translation_summary_layout.addWidget(self.translation_summary_title)
        translation_summary_layout.addWidget(self.translation_summary_label)
        translation_summary_layout.addWidget(self.translation_summary_note)

        translation_layout.addWidget(self.translation_required_checkbox, 0, 0)
        translation_layout.addWidget(translation_intro, 0, 1, 1, 2)
        translation_layout.addWidget(profile_group, 1, 0, 1, 3)
        translation_layout.addWidget(self.translation_summary_frame, 2, 0, 1, 3)
        translation_hint = QLabel(
            "Переопределяет общий режим оптимизации только для основного авто-прогона. "
            "Спец-пакеты для обхода content filter по-прежнему собираются своим сценарием."
        )
        translation_hint.setWordWrap(True)
        translation_hint.setStyleSheet("color: #9aa4b2;")
        translation_layout.addWidget(translation_hint, 3, 0, 1, 3)
        content_layout.addWidget(translation_group)

        validator_group = QGroupBox("3. Автопроверка после перевода")
        validator_layout = QGridLayout(validator_group)
        self.retry_short_checkbox = QCheckBox("Переводить заново, если ratio перевод / оригинал ниже порога")
        self.retry_short_ratio_spin = NoScrollDoubleSpinBox()
        self.retry_short_ratio_spin.setDecimals(2)
        self.retry_short_ratio_spin.setRange(0.1, 5.0)
        self.retry_short_ratio_spin.setSingleStep(0.05)
        self.retry_short_ratio_spin.setValue(0.70)
        self.retry_short_ratio_spin.setToolTip(
            "Порог для ratio в формате перевод / оригинал.\n"
            "Чем ниже значение, тем короче получился перевод относительно оригинала.\n"
            "Для CJK автосценарий всё равно применит более строгий минимум x1.8."
        )
        self.retry_untranslated_checkbox = QCheckBox("Точечно исправлять недоперевод без повтора главы")
        self.retry_untranslated_checkbox.setChecked(True)

        validator_layout.addWidget(self.retry_short_checkbox, 0, 0, 1, 2)
        validator_layout.addWidget(QLabel("Мин. ratio (перевод / оригинал):"), 1, 0)
        validator_layout.addWidget(self.retry_short_ratio_spin, 1, 1)
        validator_layout.addWidget(self.retry_untranslated_checkbox, 2, 0, 1, 2)
        validator_hint = QLabel(
            "Проверка использует существующий валидатор проекта. Для CJK глав применяется более строгий порог: "
            "если перевод не расширился хотя бы примерно в x1.8, глава считается подозрительной."
        )
        validator_hint.setWordWrap(True)
        validator_hint.setStyleSheet("color: #9aa4b2;")
        validator_layout.addWidget(validator_hint, 3, 0, 1, 2)
        content_layout.addWidget(validator_group)

        filter_group = QGroupBox("4. Обход Content Filter")
        filter_layout = QGridLayout(filter_group)
        self.filter_repack_checkbox = QCheckBox("Автоматически перепаковывать отфильтрованные главы")
        self.filter_repack_checkbox.setChecked(True)
        self.filter_batch_size_spin = NoScrollSpinBox()
        self.filter_batch_size_spin.setRange(2, 50)
        self.filter_batch_size_spin.setValue(3)
        self.filter_dilute_checkbox = QCheckBox("Разбавлять проблемные главы успешными")
        self.filter_dilute_checkbox.setChecked(True)
        self.filter_redirect_checkbox = QCheckBox('Перенаправлять главы с пометкой "Фильтр" в другую нейросеть')
        self.filter_redirect_provider_combo = NoScrollComboBox()
        self.filter_redirect_model_combo = NoScrollComboBox()

        filter_layout.addWidget(self.filter_repack_checkbox, 0, 0, 1, 2)
        filter_layout.addWidget(QLabel("Глав в пакете:"), 1, 0)
        filter_layout.addWidget(self.filter_batch_size_spin, 1, 1)
        filter_layout.addWidget(self.filter_dilute_checkbox, 2, 0, 1, 2)
        filter_layout.addWidget(self.filter_redirect_checkbox, 3, 0, 1, 2)
        filter_layout.addWidget(QLabel("Сервис для redirect:"), 4, 0)
        filter_layout.addWidget(self.filter_redirect_provider_combo, 4, 1)
        filter_layout.addWidget(QLabel("Модель для redirect:"), 5, 0)
        filter_layout.addWidget(self.filter_redirect_model_combo, 5, 1)
        filter_hint = QLabel(
            "Сетевые и временные ошибки движок уже ретраит сам. Здесь настраивается верхнеуровневый сценарий для content filter. "
            "Redirect применяется к следующему автоперезапуску после перепаковки."
        )
        filter_hint.setWordWrap(True)
        filter_hint.setStyleSheet("color: #9aa4b2;")
        filter_layout.addWidget(filter_hint, 6, 0, 1, 2)
        content_layout.addWidget(filter_group)

        consistency_group = QGroupBox("5. Согласованность AI")
        network_group = QGroupBox("5. Повтор сбоев сети")
        network_layout = QGridLayout(network_group)
        self.retry_network_checkbox = QCheckBox("Автоматически возвращать в очередь главы с сетевыми ошибками")
        self.retry_network_checkbox.setChecked(True)
        self.retry_network_delay_spin = NoScrollSpinBox()
        self.retry_network_delay_spin.setRange(5, 3600)
        self.retry_network_delay_spin.setValue(60)
        network_layout.addWidget(self.retry_network_checkbox, 0, 0, 1, 2)
        network_layout.addWidget(QLabel("Пауза перед повтором, сек:"), 1, 0)
        network_layout.addWidget(self.retry_network_delay_spin, 1, 1)
        network_hint = QLabel(
            "Базовые ретраи уже делает движок. Этот шаг нужен для тех задач, которые "
            "все равно остались в статусе ошибки после завершения сессии."
        )
        network_hint.setWordWrap(True)
        network_hint.setStyleSheet("color: #9aa4b2;")
        network_layout.addWidget(network_hint, 2, 0, 1, 2)
        content_layout.addWidget(network_group)

        consistency_layout = QGridLayout(consistency_group)
        self.ai_consistency_checkbox = QCheckBox("Запускать AI-проверку согласованности после успешного пайплайна")
        self.ai_consistency_auto_fix_checkbox = QCheckBox("Сразу применять и сохранять AI-исправления")
        self.ai_consistency_auto_fix_checkbox.setChecked(True)
        self.ai_consistency_mode_combo = NoScrollComboBox()
        self.ai_consistency_mode_combo.addItem("Обычный анализ", userData="standard")
        self.ai_consistency_mode_combo.addItem("Сначала собрать контекст/глоссарий", userData="glossary_first")
        self.ai_consistency_fix_high_checkbox = QCheckBox("high")
        self.ai_consistency_fix_high_checkbox.setChecked(True)
        self.ai_consistency_fix_high_checkbox.setToolTip("Исправлять проблемы, в которых модель уверена сильнее всего.")
        self.ai_consistency_fix_medium_checkbox = QCheckBox("medium")
        self.ai_consistency_fix_medium_checkbox.setChecked(True)
        self.ai_consistency_fix_medium_checkbox.setToolTip("Исправлять проблемы со средней уверенностью.")
        self.ai_consistency_fix_low_checkbox = QCheckBox("low")
        self.ai_consistency_fix_low_checkbox.setChecked(True)
        self.ai_consistency_fix_low_checkbox.setToolTip("Исправлять проблемы с низкой уверенностью. Самый рискованный режим.")
        confidence_fix_levels_layout = QHBoxLayout()
        confidence_fix_levels_layout.setContentsMargins(0, 0, 0, 0)
        confidence_fix_levels_layout.setSpacing(10)
        confidence_fix_levels_layout.addWidget(self.ai_consistency_fix_high_checkbox)
        confidence_fix_levels_layout.addWidget(self.ai_consistency_fix_medium_checkbox)
        confidence_fix_levels_layout.addWidget(self.ai_consistency_fix_low_checkbox)
        confidence_fix_levels_layout.addStretch(1)
        self.ai_consistency_chunk_spin = NoScrollSpinBox()
        self.ai_consistency_chunk_spin.setRange(1, 10)
        self.ai_consistency_chunk_spin.setValue(3)

        consistency_layout.addWidget(self.ai_consistency_checkbox, 0, 0, 1, 2)
        consistency_layout.addWidget(self.ai_consistency_auto_fix_checkbox, 1, 0, 1, 2)
        consistency_layout.addWidget(QLabel("Режим:"), 2, 0)
        consistency_layout.addWidget(self.ai_consistency_mode_combo, 2, 1)
        consistency_layout.addWidget(QLabel("Автоисправление по уровням:"), 3, 0)
        consistency_layout.addLayout(confidence_fix_levels_layout, 3, 1)
        consistency_layout.addWidget(QLabel("Глав в чанке:"), 4, 0)
        consistency_layout.addWidget(self.ai_consistency_chunk_spin, 4, 1)
        consistency_hint = QLabel(
            "AI-consistency всегда анализирует все найденные проблемы. Эти флажки управляют только тем, "
            "какие уровни уверенности будут автоматически исправляться и сохраняться."
        )
        consistency_hint.setWordWrap(True)
        consistency_hint.setStyleSheet("color: #9aa4b2;")
        consistency_layout.addWidget(consistency_hint, 5, 0, 1, 2)
        content_layout.addWidget(consistency_group)

        footer_label = QLabel(
            "Идея вкладки: собрать существующие функции в один сценарий с сохранением пресетов. "
            "Сетевые ретраи и паузы уже частично живут в ядре, поэтому здесь хранится именно orchestration-слой."
        )
        footer_label.setWordWrap(True)
        footer_label.setStyleSheet("color: #7f8c8d; padding: 4px 2px;")
        content_layout.addWidget(footer_label)
        content_layout.addStretch()

        scroll_area.setWidget(scroll_content)
        main_layout.addWidget(scroll_area, 1)

        self._refresh_glossary_presets()
        self._rebuild_model_override_combo()
        self._rebuild_thinking_override_combo()
        self._rebuild_filter_redirect_provider_combo()
        self._rebuild_filter_redirect_model_combo()
        self._connect_setting_signals()
        self._update_control_states()
        self._update_translation_profile_summary()
        self._update_preset_buttons()

    def _connect_setting_signals(self):
        widgets = [
            self.enabled_checkbox,
            self.auto_restart_checkbox,
            self.log_each_step_checkbox,
            self.max_rounds_spin,
            self.glossary_checkbox,
            self.glossary_prompt_combo,
            self.translation_mode_combo,
            self.model_override_combo,
            self.thinking_override_combo,
            self.batch_tokens_spin,
            self.retry_short_checkbox,
            self.retry_short_ratio_spin,
            self.retry_untranslated_checkbox,
            self.filter_repack_checkbox,
            self.filter_batch_size_spin,
            self.filter_dilute_checkbox,
            self.filter_redirect_checkbox,
            self.filter_redirect_provider_combo,
            self.filter_redirect_model_combo,
            self.retry_network_checkbox,
            self.retry_network_delay_spin,
            self.ai_consistency_checkbox,
            self.ai_consistency_auto_fix_checkbox,
            self.ai_consistency_mode_combo,
            self.ai_consistency_fix_high_checkbox,
            self.ai_consistency_fix_medium_checkbox,
            self.ai_consistency_fix_low_checkbox,
            self.ai_consistency_chunk_spin,
        ]
        for widget in widgets:
            if isinstance(widget, QCheckBox):
                widget.toggled.connect(self._on_any_setting_changed)
            elif isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(self._on_any_setting_changed)
            else:
                widget.valueChanged.connect(self._on_any_setting_changed)

        self.model_override_combo.currentIndexChanged.connect(self._on_profile_model_changed)
        self.filter_redirect_provider_combo.currentIndexChanged.connect(self._on_filter_redirect_provider_changed)

    def _refresh_glossary_presets(self):
        current_name = self._current_glossary_preset_name()
        presets = self.settings_manager.load_glossary_prompts()
        builtin_presets = api_config.builtin_glossary_prompt_variants()
        last_prompt_text = self.settings_manager.get_last_glossary_prompt_text()
        self.glossary_prompt_combo.blockSignals(True)
        self.glossary_prompt_combo.clear()
        if isinstance(last_prompt_text, str) and last_prompt_text.strip():
            self.glossary_prompt_combo.addItem("[Как в диалоге глоссария / последний текст]", userData=None)
        else:
            self.glossary_prompt_combo.addItem("[Стандартный промпт глоссария]", userData=None)
        for preset_id, meta in builtin_presets.items():
            label = meta.get("label") if isinstance(meta, dict) else None
            if isinstance(label, str) and label.strip():
                self.glossary_prompt_combo.addItem(f"[Встроенный] {label}", userData=preset_id)
        for name in sorted(presets.keys()):
            self.glossary_prompt_combo.addItem(name, userData=name)
        if current_name:
            index = self.glossary_prompt_combo.findData(current_name)
            if index != -1:
                self.glossary_prompt_combo.setCurrentIndex(index)
        self.glossary_prompt_combo.blockSignals(False)

    def refresh_glossary_presets(self):
        self._refresh_glossary_presets()
        self._update_preset_dirty_state()

    def _current_glossary_preset_name(self):
        data = self.glossary_prompt_combo.currentData()
        return data if isinstance(data, str) and data.strip() else None

    def set_runtime_context(
        self,
        provider_id: str | None = None,
        current_model_name: str | None = None,
        current_task_size_limit: int | None = None,
        uses_cjk: bool | None = None,
        current_model_settings: dict | None = None,
    ):
        selected_model = self.model_override_combo.currentData()
        selected_thinking = self.thinking_override_combo.currentData()
        selected_filter_provider = self.filter_redirect_provider_combo.currentData()
        selected_filter_model = self.filter_redirect_model_combo.currentData()
        if provider_id is not None:
            self._current_provider_id = provider_id
        if current_model_name:
            self._current_model_name = current_model_name
        if current_task_size_limit is not None:
            self._current_task_size_limit = max(0, int(current_task_size_limit))
        if uses_cjk is not None:
            self._current_uses_cjk = bool(uses_cjk)
        if isinstance(current_model_settings, dict):
            self._current_model_settings = deepcopy(current_model_settings)
            inferred_model_name = current_model_settings.get("model")
            if isinstance(inferred_model_name, str) and inferred_model_name.strip():
                self._current_model_name = inferred_model_name

        self._rebuild_model_override_combo(selected_model=selected_model)
        self._rebuild_thinking_override_combo(selected_override=selected_thinking)
        self._rebuild_filter_redirect_provider_combo(selected_provider=selected_filter_provider)
        self._rebuild_filter_redirect_model_combo(selected_model=selected_filter_model)
        self._update_translation_profile_summary()

    def _rebuild_model_override_combo(self, selected_model=None):
        if selected_model is None:
            selected_model = self.model_override_combo.currentData()

        self.model_override_combo.blockSignals(True)
        self.model_override_combo.clear()
        if self._current_model_name:
            inherit_label = f"Как в общих настройках ({self._current_model_name})"
        else:
            inherit_label = "Как в общих настройках"
        self.model_override_combo.addItem(inherit_label, userData=None)

        added_models = []
        if self._current_provider_id:
            provider_config = api_config.api_providers().get(self._current_provider_id, {})
            model_names = list(provider_config.get("models", {}).keys())
        else:
            model_names = list(api_config.all_models().keys())

        for model_name in model_names:
            self.model_override_combo.addItem(model_name, userData=model_name)
            added_models.append(model_name)

        if selected_model and selected_model not in added_models:
            self.model_override_combo.addItem(
                f"{selected_model} (недоступно для текущего сервиса)",
                userData=selected_model,
            )

        target_index = self.model_override_combo.findData(selected_model)
        self.model_override_combo.setCurrentIndex(target_index if target_index != -1 else 0)
        self.model_override_combo.blockSignals(False)

    def _rebuild_filter_redirect_provider_combo(self, selected_provider=None):
        if selected_provider is None:
            selected_provider = self.filter_redirect_provider_combo.currentData()

        self.filter_redirect_provider_combo.blockSignals(True)
        self.filter_redirect_provider_combo.clear()

        current_provider_display = api_config.provider_display_map().get(
            self._current_provider_id,
            self._current_provider_id or "текущий сервис",
        )
        if current_provider_display:
            inherit_label = f"Как текущий сервис ({current_provider_display})"
        else:
            inherit_label = "Как текущий сервис"
        self.filter_redirect_provider_combo.addItem(inherit_label, userData=None)

        added_providers = []
        for provider_id, provider_config in api_config.api_providers().items():
            if not provider_config.get("visible", True):
                continue
            self.filter_redirect_provider_combo.addItem(
                provider_config.get("display_name") or provider_id,
                userData=provider_id,
            )
            added_providers.append(provider_id)

        if selected_provider and selected_provider not in added_providers:
            self.filter_redirect_provider_combo.addItem(
                f"{selected_provider} (недоступно)",
                userData=selected_provider,
            )

        target_index = self.filter_redirect_provider_combo.findData(selected_provider)
        self.filter_redirect_provider_combo.setCurrentIndex(target_index if target_index != -1 else 0)
        self.filter_redirect_provider_combo.blockSignals(False)

    def _effective_filter_redirect_provider_id(self):
        return self.filter_redirect_provider_combo.currentData() or self._current_provider_id

    def _rebuild_filter_redirect_model_combo(self, selected_model=None):
        if selected_model is None:
            selected_model = self.filter_redirect_model_combo.currentData()

        provider_id = self._effective_filter_redirect_provider_id()
        if provider_id:
            provider_config = api_config.api_providers().get(provider_id, {})
            model_names = list(provider_config.get("models", {}).keys())
        else:
            model_names = list(api_config.all_models().keys())

        self.filter_redirect_model_combo.blockSignals(True)
        self.filter_redirect_model_combo.clear()
        self.filter_redirect_model_combo.addItem("Выберите модель…", userData=None)

        for model_name in model_names:
            self.filter_redirect_model_combo.addItem(model_name, userData=model_name)

        if selected_model and self.filter_redirect_model_combo.findData(selected_model) == -1:
            self.filter_redirect_model_combo.addItem(
                f"{selected_model} (недоступно для выбранного сервиса)",
                userData=selected_model,
            )

        target_index = self.filter_redirect_model_combo.findData(selected_model)
        self.filter_redirect_model_combo.setCurrentIndex(target_index if target_index != -1 else 0)
        self.filter_redirect_model_combo.blockSignals(False)

    def _get_effective_profile_model_name(self):
        return self.model_override_combo.currentData() or self._current_model_name

    def _get_effective_profile_model_config(self):
        model_name = self._get_effective_profile_model_name()
        model_config = api_config.all_models().get(model_name)
        return model_config if isinstance(model_config, dict) else {}

    def _describe_current_inherited_thinking(self):
        model_settings = self._current_model_settings if isinstance(self._current_model_settings, dict) else {}
        model_name = model_settings.get("model") or self._current_model_name
        model_config = api_config.all_models().get(model_name, {}) if model_name else {}
        min_budget_cfg = model_config.get("min_thinking_budget") if isinstance(model_config, dict) else False
        has_thinking_config = isinstance(model_config, dict) and (
            "thinkingLevel" in model_config or "min_thinking_budget" in model_config
        )
        supports_thinking = has_thinking_config and min_budget_cfg is not False

        if not supports_thinking:
            return "не поддерживается"

        if not model_settings.get("thinking_enabled", False):
            if isinstance(min_budget_cfg, str):
                return f"выключить нельзя, минимум {str(min_budget_cfg).upper()}"
            if isinstance(min_budget_cfg, (int, float)) and min_budget_cfg > 0:
                return f"выключить нельзя, минимум {int(min_budget_cfg)} ток."
            return "выключен"

        thinking_level = model_settings.get("thinking_level")
        if thinking_level:
            return str(thinking_level).upper()

        thinking_budget = model_settings.get("thinking_budget")
        if thinking_budget == -1:
            return "динамический"
        if isinstance(thinking_budget, (int, float)):
            return f"{int(thinking_budget)} токенов"
        return "включён"

    def _thinking_override_label(self, value, model_config=None):
        if value in (None, "inherit"):
            return f"Как в общих настройках ({self._describe_current_inherited_thinking()})"
        if value == "disabled":
            return "Выключить"
        if isinstance(value, str) and value.startswith("level:"):
            return value.split(":", 1)[1].upper()
        if isinstance(value, str) and value.startswith("budget:"):
            budget_value = value.split(":", 1)[1]
            if budget_value == "dynamic":
                return "Динамический"
            return f"{budget_value} токенов"
        return str(value)

    def _rebuild_thinking_override_combo(self, selected_override=None):
        if selected_override is None:
            selected_override = self.thinking_override_combo.currentData() or "inherit"

        model_config = self._get_effective_profile_model_config()
        min_budget_cfg = model_config.get("min_thinking_budget") if isinstance(model_config, dict) else False
        thinking_levels = model_config.get("thinkingLevel") if isinstance(model_config, dict) else None
        has_thinking_config = isinstance(model_config, dict) and (
            "thinkingLevel" in model_config or "min_thinking_budget" in model_config
        )
        supports_thinking = has_thinking_config and min_budget_cfg is not False

        self.thinking_override_combo.blockSignals(True)
        self.thinking_override_combo.clear()
        self.thinking_override_combo.addItem(self._thinking_override_label("inherit"), userData="inherit")

        if supports_thinking:
            self.thinking_override_combo.addItem("Выключить", userData="disabled")
            if isinstance(thinking_levels, list) and thinking_levels:
                for level in thinking_levels:
                    level_name = str(level).lower()
                    self.thinking_override_combo.addItem(level_name.upper(), userData=f"level:{level_name}")
            else:
                self.thinking_override_combo.addItem("Динамический", userData="budget:dynamic")
                for budget in (1024, 2048, 4096, 8192):
                    self.thinking_override_combo.addItem(f"{budget} токенов", userData=f"budget:{budget}")
        else:
            self.thinking_override_combo.addItem("Thinking не поддерживается выбранной моделью", userData="inherit")

        target_index = self.thinking_override_combo.findData(selected_override)
        self.thinking_override_combo.setCurrentIndex(target_index if target_index != -1 else 0)
        self.thinking_override_combo.blockSignals(False)

    def _on_profile_model_changed(self, *args):
        selected_override = self.thinking_override_combo.currentData()
        self._rebuild_thinking_override_combo(selected_override=selected_override)
        if not self._is_loading:
            self._update_translation_profile_summary()

    def _on_filter_redirect_provider_changed(self, *args):
        selected_model = self.filter_redirect_model_combo.currentData()
        self._rebuild_filter_redirect_model_combo(selected_model=selected_model)
        if not self._is_loading:
            self._on_any_setting_changed()

    def _format_number(self, value: int | float | None) -> str:
        if value is None:
            return "0"
        return f"{int(value):,}".replace(",", " ")

    def _estimate_batch_chars_from_tokens(self, token_count: int):
        if token_count <= 0:
            return None, None
        chars_per_token = api_config.UNIFIED_INPUT_CHARS_PER_TOKEN
        profile_name = "единая токен-оценка"
        estimated_chars = int(round(token_count * chars_per_token))
        estimated_chars = max(500, min(estimated_chars, 350000))
        return estimated_chars, profile_name

    def _update_translation_profile_summary(self):
        mode_titles = {
            "inherit": "как в общих настройках",
            "batch": "всегда пакетами",
            "single": "всегда по одной главе",
            "chunk": "всегда чанками",
        }
        mode_value = self.translation_mode_combo.currentData() or "inherit"
        mode_text = mode_titles.get(mode_value, "как в общих настройках")

        model_config = self._get_effective_profile_model_config()
        min_budget_cfg = model_config.get("min_thinking_budget") if isinstance(model_config, dict) else False
        has_thinking_config = isinstance(model_config, dict) and (
            "thinkingLevel" in model_config or "min_thinking_budget" in model_config
        )
        supports_thinking = has_thinking_config and min_budget_cfg is not False
        selected_model = self.model_override_combo.currentData()
        if selected_model:
            model_text = selected_model
            model_note = "Для автосценария будет использована отдельная модель."
        else:
            model_text = self._current_model_name or "как в общих настройках"
            model_note = "Модель наследуется из основной вкладки настроек."

        thinking_override = self.thinking_override_combo.currentData() or "inherit"
        thinking_text = self._thinking_override_label(thinking_override, model_config=model_config)
        if not supports_thinking:
            thinking_note = "Выбранная модель не поддерживает Thinking, поэтому override не применяется."
        elif thinking_override == "inherit":
            thinking_note = "Thinking наследуется из основной вкладки модели."
        elif thinking_override == "disabled":
            if isinstance(min_budget_cfg, str):
                thinking_note = (
                    f"У модели есть обязательный минимальный уровень {str(min_budget_cfg).upper()}, "
                    "поэтому API может не дать полностью отключить размышление."
                )
            elif isinstance(min_budget_cfg, (int, float)) and min_budget_cfg > 0:
                thinking_note = (
                    f"У модели есть минимальный бюджет {int(min_budget_cfg)} токенов, "
                    "поэтому полное отключение может быть недоступно."
                )
            else:
                thinking_note = "Для автосценария будет принудительно отключён Thinking."
        elif isinstance(thinking_override, str) and thinking_override.startswith("level:"):
            thinking_note = "Для автосценария будет использован отдельный уровень Thinking."
        else:
            thinking_note = "Для автосценария будет использован отдельный бюджет Thinking."

        batch_tokens = int(self.batch_tokens_spin.value())
        if batch_tokens > 0:
            estimated_chars, profile_name = self._estimate_batch_chars_from_tokens(batch_tokens)
            batch_text = (
                f"Лимит пакета: ~{self._format_number(batch_tokens)} входных токенов "
                f"(≈ {self._format_number(estimated_chars)} символов, профиль: {profile_name})."
            )
        elif self._current_task_size_limit > 0:
            batch_text = (
                "Лимит пакета: как в общих настройках "
                f"({self._format_number(self._current_task_size_limit)} символов)."
            )
        else:
            batch_text = "Лимит пакета: как в общих настройках."

        self.translation_summary_label.setText(
            f"Основной прогон пойдёт в режиме «{mode_text}». "
            f"Модель: {model_text}. Thinking: {thinking_text}. {batch_text}"
        )

        notes = [model_note, thinking_note]
        if mode_value == "single":
            notes.append("В режиме «по одной главе» лимит пакета почти не влияет на основной прогон.")
        elif batch_tokens > 0:
            notes.append("Перед сборкой задач токены будут автоматически переведены в символьный лимит.")
        else:
            notes.append("Если оставить 0, будет взят текущий размер задачи из общей вкладки.")
        self.translation_summary_note.setText(" ".join(notes))

    def _update_control_states(self):
        self.glossary_prompt_combo.setEnabled(self.glossary_checkbox.isChecked())
        self.retry_short_ratio_spin.setEnabled(self.retry_short_checkbox.isChecked())
        self.filter_batch_size_spin.setEnabled(self.filter_repack_checkbox.isChecked())
        self.filter_dilute_checkbox.setEnabled(self.filter_repack_checkbox.isChecked())
        self.filter_redirect_checkbox.setEnabled(self.filter_repack_checkbox.isChecked())
        redirect_enabled = self.filter_repack_checkbox.isChecked() and self.filter_redirect_checkbox.isChecked()
        self.filter_redirect_provider_combo.setEnabled(redirect_enabled)
        self.filter_redirect_model_combo.setEnabled(redirect_enabled)
        self.retry_network_delay_spin.setEnabled(self.retry_network_checkbox.isChecked())
        consistency_enabled = self.ai_consistency_checkbox.isChecked()
        auto_fix_enabled = consistency_enabled and self.ai_consistency_auto_fix_checkbox.isChecked()
        self.ai_consistency_auto_fix_checkbox.setEnabled(consistency_enabled)
        self.ai_consistency_mode_combo.setEnabled(consistency_enabled)
        self.ai_consistency_fix_high_checkbox.setEnabled(auto_fix_enabled)
        self.ai_consistency_fix_medium_checkbox.setEnabled(auto_fix_enabled)
        self.ai_consistency_fix_low_checkbox.setEnabled(auto_fix_enabled)
        self.ai_consistency_chunk_spin.setEnabled(consistency_enabled)

    def _get_ai_consistency_fix_confidences(self):
        selected = []
        if self.ai_consistency_fix_high_checkbox.isChecked():
            selected.append("high")
        if self.ai_consistency_fix_medium_checkbox.isChecked():
            selected.append("medium")
        if self.ai_consistency_fix_low_checkbox.isChecked():
            selected.append("low")
        return selected

    def get_settings(self):
        return {
            "enabled": self.enabled_checkbox.isChecked(),
            "max_rounds": int(self.max_rounds_spin.value()),
            "auto_restart_after_retry": self.auto_restart_checkbox.isChecked(),
            "log_each_step": self.log_each_step_checkbox.isChecked(),
            "glossary_enabled": self.glossary_checkbox.isChecked(),
            "glossary_prompt_preset": self._current_glossary_preset_name(),
            "translate_chapters_enabled": True,
            "translation_mode_override": self.translation_mode_combo.currentData() or "inherit",
            "model_override": self.model_override_combo.currentData(),
            "thinking_mode_override": self.thinking_override_combo.currentData() or "inherit",
            "batch_token_limit_override": int(self.batch_tokens_spin.value()),
            "retry_short_enabled": self.retry_short_checkbox.isChecked(),
            "retry_short_ratio": round(float(self.retry_short_ratio_spin.value()), 2),
            "retry_short_ratio_mode": "translation_over_original",
            "retry_untranslated_enabled": self.retry_untranslated_checkbox.isChecked(),
            "filter_repack_enabled": self.filter_repack_checkbox.isChecked(),
            "filter_repack_batch_size": int(self.filter_batch_size_spin.value()),
            "filter_repack_dilute": self.filter_dilute_checkbox.isChecked(),
            "filter_redirect_enabled": self.filter_redirect_checkbox.isChecked(),
            "filter_redirect_provider": self.filter_redirect_provider_combo.currentData(),
            "filter_redirect_model": self.filter_redirect_model_combo.currentData(),
            "retry_network_failed_enabled": self.retry_network_checkbox.isChecked(),
            "retry_network_failed_delay_sec": int(self.retry_network_delay_spin.value()),
            "ai_consistency_enabled": self.ai_consistency_checkbox.isChecked(),
            "ai_consistency_auto_fix": self.ai_consistency_auto_fix_checkbox.isChecked(),
            "ai_consistency_fix_confidences": self._get_ai_consistency_fix_confidences(),
            "ai_consistency_mode": self.ai_consistency_mode_combo.currentData() or "standard",
            "ai_consistency_chunk_size": int(self.ai_consistency_chunk_spin.value()),
        }

    def set_settings(self, settings: dict | None):
        merged = deepcopy(AUTO_TRANSLATION_DEFAULTS)
        if isinstance(settings, dict):
            merged.update(settings)

        self._is_loading = True
        self.blockSignals(True)
        try:
            self.enabled_checkbox.setChecked(bool(merged.get("enabled", False)))
            self.max_rounds_spin.setValue(int(merged.get("max_rounds", 3)))
            self.auto_restart_checkbox.setChecked(bool(merged.get("auto_restart_after_retry", True)))
            self.log_each_step_checkbox.setChecked(bool(merged.get("log_each_step", True)))
            self.glossary_checkbox.setChecked(bool(merged.get("glossary_enabled", False)))

            glossary_preset = merged.get("glossary_prompt_preset")
            glossary_index = self.glossary_prompt_combo.findData(glossary_preset)
            self.glossary_prompt_combo.setCurrentIndex(glossary_index if glossary_index != -1 else 0)

            translation_mode = merged.get("translation_mode_override", "inherit")
            translation_mode_index = self.translation_mode_combo.findData(translation_mode)
            self.translation_mode_combo.setCurrentIndex(translation_mode_index if translation_mode_index != -1 else 0)

            selected_model = merged.get("model_override")
            self._rebuild_model_override_combo(selected_model=selected_model)
            selected_thinking = merged.get("thinking_mode_override", "inherit")
            self._rebuild_thinking_override_combo(selected_override=selected_thinking)
            self.batch_tokens_spin.setValue(int(merged.get("batch_token_limit_override", 0) or 0))

            legacy_ratio_value = None
            if isinstance(settings, dict) and "retry_short_ratio" in settings:
                legacy_ratio_value = float(settings.get("retry_short_ratio", AUTO_TRANSLATION_DEFAULTS["retry_short_ratio"]))
            ratio_mode = settings.get("retry_short_ratio_mode") if isinstance(settings, dict) else None
            if legacy_ratio_value is not None and ratio_mode == "orig_over_translation":
                if legacy_ratio_value > 0:
                    merged["retry_short_ratio"] = round(1.0 / legacy_ratio_value, 2)

            self.retry_short_checkbox.setChecked(bool(merged.get("retry_short_enabled", True)))
            self.retry_short_ratio_spin.setValue(float(merged.get("retry_short_ratio", 0.70)))
            self.retry_untranslated_checkbox.setChecked(bool(merged.get("retry_untranslated_enabled", True)))
            self.filter_repack_checkbox.setChecked(bool(merged.get("filter_repack_enabled", True)))
            self.filter_batch_size_spin.setValue(int(merged.get("filter_repack_batch_size", 3)))
            self.filter_dilute_checkbox.setChecked(bool(merged.get("filter_repack_dilute", True)))
            self._rebuild_filter_redirect_provider_combo(selected_provider=merged.get("filter_redirect_provider"))
            self._rebuild_filter_redirect_model_combo(selected_model=merged.get("filter_redirect_model"))
            self.filter_redirect_checkbox.setChecked(bool(merged.get("filter_redirect_enabled", False)))
            self.retry_network_checkbox.setChecked(bool(merged.get("retry_network_failed_enabled", True)))
            self.retry_network_delay_spin.setValue(int(merged.get("retry_network_failed_delay_sec", 60)))
            self.ai_consistency_checkbox.setChecked(bool(merged.get("ai_consistency_enabled", False)))
            self.ai_consistency_auto_fix_checkbox.setChecked(bool(merged.get("ai_consistency_auto_fix", True)))
            confidence_values = merged.get("ai_consistency_fix_confidences")
            if not isinstance(confidence_values, (list, tuple, set)):
                confidence_values = AUTO_TRANSLATION_DEFAULTS["ai_consistency_fix_confidences"]
            normalized_confidences = {str(value).strip().lower() for value in confidence_values if str(value).strip()}
            self.ai_consistency_fix_high_checkbox.setChecked("high" in normalized_confidences)
            self.ai_consistency_fix_medium_checkbox.setChecked("medium" in normalized_confidences)
            self.ai_consistency_fix_low_checkbox.setChecked("low" in normalized_confidences)

            mode_value = merged.get("ai_consistency_mode", "standard")
            mode_index = self.ai_consistency_mode_combo.findData(mode_value)
            self.ai_consistency_mode_combo.setCurrentIndex(mode_index if mode_index != -1 else 0)
            self.ai_consistency_chunk_spin.setValue(int(merged.get("ai_consistency_chunk_size", 3)))
        finally:
            self.blockSignals(False)
            self._is_loading = False

        self._update_control_states()
        self._update_translation_profile_summary()
        self._update_preset_dirty_state()
        self._update_preset_buttons()

    def load_last_session_state(self):
        self._load_presets_into_combo()
        last_settings = self.settings_manager.get_last_auto_translation_settings()
        self.set_settings(last_settings if isinstance(last_settings, dict) else {})

        last_preset_name = self.settings_manager.get_last_auto_translation_preset_name()
        if last_preset_name:
            index = self.preset_combo.findText(last_preset_name)
            if index != -1:
                self.preset_combo.setCurrentIndex(index)
                return

        self.preset_combo.setCurrentIndex(0)

    def save_last_state_now(self):
        self._save_state_timer.stop()
        self._save_last_state()

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_glossary_presets()

    def get_current_preset_name(self):
        return self.loaded_preset_name

    def _get_builtin_presets(self):
        presets = {}
        for name, overrides in BUILTIN_AUTO_TRANSLATION_PRESETS.items():
            state = deepcopy(AUTO_TRANSLATION_DEFAULTS)
            state.update(overrides)
            presets[name] = state
        return presets

    def _get_user_presets(self):
        presets = self.settings_manager.load_auto_translation_presets()
        return presets if isinstance(presets, dict) else {}

    def _get_combined_presets(self):
        presets = self._get_builtin_presets()
        presets.update(self._get_user_presets())
        return presets

    def _get_preset_state(self, name: str):
        user_presets = self._get_user_presets()
        state = user_presets.get(name)
        if isinstance(state, dict):
            return deepcopy(state), "user"

        builtin_presets = self._get_builtin_presets()
        state = builtin_presets.get(name)
        if isinstance(state, dict):
            return deepcopy(state), "builtin"

        return None, None

    def _load_presets_into_combo(self):
        current_text = self.preset_combo.currentText()
        builtin_presets = self._get_builtin_presets()
        user_presets = self._get_user_presets()
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.preset_combo.addItem("[Текущие настройки]")
        for name in builtin_presets.keys():
            self.preset_combo.addItem(name)
        for name in sorted(user_presets.keys()):
            if name in builtin_presets:
                continue
            self.preset_combo.addItem(name)
        target_text = current_text if current_text else "[Текущие настройки]"
        index = self.preset_combo.findText(target_text)
        self.preset_combo.setCurrentIndex(index if index != -1 else 0)
        self.preset_combo.blockSignals(False)
        self._update_preset_buttons()

    def _on_preset_selected(self, name: str):
        if self._is_loading:
            return

        clean_name = name.replace("*", "")
        if clean_name == "[Текущие настройки]" or not clean_name:
            self.loaded_preset_name = None
            self.loaded_preset_source = None
            self.original_preset_state = None
            self.is_preset_modified = False
            self._save_state_timer.start()
            self._update_preset_buttons()
            return

        state, source = self._get_preset_state(clean_name)
        if not isinstance(state, dict):
            return

        self.loaded_preset_name = clean_name
        self.loaded_preset_source = source
        self.original_preset_state = deepcopy(state)
        self.is_preset_modified = False
        self.set_settings(state)
        self._save_state_timer.start()
        self._update_preset_buttons()

    def _save_new_preset(self):
        dialog = QInputDialog(self)
        dialog.setWindowTitle("Сохранить автоперевод")
        dialog.setLabelText("Имя для нового пресета:")
        dialog.setOkButtonText("Сохранить")
        dialog.setCancelButtonText("Отмена")
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return

        name = dialog.textValue().strip()
        if not name:
            return

        presets = self._get_user_presets()
        if name in self._get_combined_presets():
            QMessageBox.warning(self, "Имя занято", f"Пресет '{name}' уже существует.")
            return

        presets[name] = self.get_settings()
        if self.settings_manager.save_auto_translation_presets(presets):
            self.loaded_preset_name = name
            self.loaded_preset_source = "user"
            self.original_preset_state = deepcopy(presets[name])
            self.is_preset_modified = False
            self._load_presets_into_combo()
            self.preset_combo.setCurrentText(name)
            self.save_last_state_now()

    def _overwrite_preset(self):
        if not self.loaded_preset_name:
            return
        if self.loaded_preset_source != "user":
            QMessageBox.information(self, "Встроенный пресет", "Базовый пресет нельзя перезаписать. Используйте 'Сохранить как…'.")
            return

        reply = QMessageBox.question(
            self,
            "Перезапись пресета",
            f"Перезаписать пресет '{self.loaded_preset_name}' текущими настройками?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        presets = self._get_user_presets()
        presets[self.loaded_preset_name] = self.get_settings()
        if self.settings_manager.save_auto_translation_presets(presets):
            self.original_preset_state = deepcopy(presets[self.loaded_preset_name])
            self.is_preset_modified = False
            current_name = self.loaded_preset_name
            self._load_presets_into_combo()
            self.preset_combo.setCurrentText(current_name)
            self.save_last_state_now()

    def _delete_preset(self):
        if not self.loaded_preset_name:
            return
        if self.loaded_preset_source != "user":
            QMessageBox.information(self, "Встроенный пресет", "Базовый пресет нельзя удалить.")
            return

        reply = QMessageBox.question(
            self,
            "Удаление пресета",
            f"Удалить пресет '{self.loaded_preset_name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        presets = self._get_user_presets()
        if self.loaded_preset_name in presets:
            del presets[self.loaded_preset_name]
            if self.settings_manager.save_auto_translation_presets(presets):
                self.loaded_preset_name = None
                self.loaded_preset_source = None
                self.original_preset_state = None
                self.is_preset_modified = False
                self._load_presets_into_combo()
                self.preset_combo.setCurrentIndex(0)
                self.save_last_state_now()

    def _on_any_setting_changed(self, *args):
        if self._is_loading or self.signalsBlocked():
            return
        self._update_control_states()
        self._update_translation_profile_summary()
        self._update_preset_dirty_state()
        self._save_state_timer.start()
        self.settings_changed.emit()

    def _update_preset_dirty_state(self):
        if not self.loaded_preset_name or not isinstance(self.original_preset_state, dict):
            self.is_preset_modified = False
        else:
            self.is_preset_modified = self.get_settings() != self.original_preset_state
        self._update_preset_buttons()

    def _update_preset_buttons(self):
        is_user_preset = self.loaded_preset_source == "user"
        self.overwrite_btn.setEnabled(bool(is_user_preset and self.loaded_preset_name and self.is_preset_modified))
        self.delete_btn.setEnabled(bool(is_user_preset and self.loaded_preset_name))

        self.preset_combo.blockSignals(True)
        presets = self._get_combined_presets()
        for index in range(1, self.preset_combo.count()):
            clean_name = self.preset_combo.itemText(index).replace("*", "")
            if clean_name in presets:
                self.preset_combo.setItemText(index, clean_name)
        if self.loaded_preset_name:
            base_name = self.loaded_preset_name
            current_index = self.preset_combo.findText(base_name)
            starred_index = self.preset_combo.findText(f"{base_name}*")
            target_index = current_index if current_index != -1 else starred_index
            if target_index != -1:
                self.preset_combo.setItemText(
                    target_index,
                    f"{base_name}*" if self.is_preset_modified else base_name
                )
        self.preset_combo.blockSignals(False)

    def _save_last_state(self):
        self.settings_manager.save_last_auto_translation_settings(self.get_settings())
        self.settings_manager.save_last_auto_translation_preset_name(self.loaded_preset_name)
