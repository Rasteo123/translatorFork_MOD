from PyQt6 import QtWidgets
from PyQt6.QtCore import pyqtSignal

from gemini_translator.api import config as api_config
from gemini_translator.core.worker_helpers.content_filter_fallback import (
    green_keys_for_provider,
)
from gemini_translator.ui import theme_manager
from gemini_translator.ui.widgets.common_widgets import (
    NoScrollComboBox,
    NoScrollDoubleSpinBox,
    NoScrollSpinBox,
)
from gemini_translator.ui.widgets.dynamic_models_refresher import (
    DynamicModelsRefresher,
)


class ContentFilterFallbackPanel(QtWidgets.QGroupBox):
    config_changed = pyqtSignal()

    def __init__(self, settings_manager=None, parent=None):
        super().__init__("Резерв при блокировке контента", parent)
        self.settings_manager = settings_manager
        self._restoring = False
        self._models_refresher = DynamicModelsRefresher(self)
        self._models_refresher.refreshed.connect(self._on_dynamic_models_refreshed)
        self._build_ui()
        self._connect_signals()
        self._populate_providers()
        self._reload_models()
        self._update_enabled_state()

    def _build_ui(self):
        layout = QtWidgets.QGridLayout(self)

        self.enable_checkbox = QtWidgets.QCheckBox(
            "Включить резерв при блокировке (Prohibited content)"
        )
        layout.addWidget(self.enable_checkbox, 0, 0, 1, 2)

        layout.addWidget(QtWidgets.QLabel("Сервис:"), 1, 0)
        self.provider_combo = NoScrollComboBox()
        layout.addWidget(self.provider_combo, 1, 1)

        layout.addWidget(QtWidgets.QLabel("Модель:"), 2, 0)
        self.model_combo = NoScrollComboBox()
        layout.addWidget(self.model_combo, 2, 1)

        self.keys_label = QtWidgets.QLabel()
        layout.addWidget(self.keys_label, 3, 1)

        layout.addWidget(QtWidgets.QLabel("Температура:"), 4, 0)
        temp_layout = QtWidgets.QHBoxLayout()
        self.temp_override_checkbox = QtWidgets.QCheckBox("Override")
        self.temp_spin = NoScrollDoubleSpinBox()
        self.temp_spin.setDecimals(1)
        self.temp_spin.setRange(0.0, 2.0)
        self.temp_spin.setSingleStep(0.1)
        self.temp_spin.setValue(1.0)
        temp_layout.addWidget(self.temp_override_checkbox)
        temp_layout.addWidget(self.temp_spin)
        temp_layout.addStretch()
        layout.addLayout(temp_layout, 4, 1)

        layout.addWidget(QtWidgets.QLabel("Thinking:"), 5, 0)
        thinking_layout = QtWidgets.QHBoxLayout()
        self.thinking_checkbox = QtWidgets.QCheckBox()
        self.thinking_budget_spin = NoScrollSpinBox()
        self.thinking_budget_spin.setRange(-1, 32768)
        self.thinking_budget_spin.setValue(-1)
        self.thinking_level_combo = NoScrollComboBox()
        self.thinking_level_combo.setHidden(True)
        thinking_layout.addWidget(self.thinking_checkbox)
        thinking_layout.addWidget(self.thinking_budget_spin)
        thinking_layout.addWidget(self.thinking_level_combo)
        thinking_layout.addStretch()
        layout.addLayout(thinking_layout, 5, 1)

        layout.setColumnStretch(1, 1)

    def _connect_signals(self):
        self.enable_checkbox.stateChanged.connect(self._on_interactive_change)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        self.temp_override_checkbox.stateChanged.connect(self._on_interactive_change)
        self.temp_spin.valueChanged.connect(self._on_interactive_change)
        self.thinking_checkbox.stateChanged.connect(self._on_interactive_change)
        self.thinking_budget_spin.valueChanged.connect(self._on_interactive_change)
        self.thinking_level_combo.currentTextChanged.connect(self._on_interactive_change)

    def _on_interactive_change(self, *args):
        self._update_enabled_state()
        self._emit_config_changed()

    def _emit_config_changed(self):
        if not self._restoring:
            self.config_changed.emit()

    def _capture_signal_states(self):
        widgets = [self, *self.findChildren(QtWidgets.QWidget)]
        return [(widget, widget.signalsBlocked()) for widget in widgets]

    def _populate_providers(self, selected_provider=None):
        if selected_provider is None:
            selected_provider = self.provider_combo.currentData()

        was_blocked = self.provider_combo.signalsBlocked()
        self.provider_combo.blockSignals(True)
        try:
            self.provider_combo.clear()
            for provider_id, provider_cfg in api_config.api_providers_view().items():
                self.provider_combo.addItem(
                    provider_cfg.get("display_name") or provider_id,
                    userData=provider_id,
                )
            index = self.provider_combo.findData(selected_provider)
            if index != -1:
                self.provider_combo.setCurrentIndex(index)
            elif self.provider_combo.count() > 0:
                self.provider_combo.setCurrentIndex(0)
        finally:
            self.provider_combo.blockSignals(was_blocked)

    def _on_provider_changed(self, *args):
        provider_id = self.provider_combo.currentData()
        if provider_id:
            self._models_refresher.refresh_async(provider_id)
        self._reload_models()
        self._update_enabled_state()
        self._emit_config_changed()

    def _on_dynamic_models_refreshed(self, provider_id):
        if provider_id != self.provider_combo.currentData():
            return
        selected_before = self.model_combo.currentText()
        self._reload_models()
        self._update_model_dependent_controls()
        self._update_enabled_state()
        if self.model_combo.currentText() != selected_before:
            self._emit_config_changed()

    def _reload_models(self, selected_model=None):
        if selected_model is None:
            selected_model = self.model_combo.currentText()

        provider_id = self.provider_combo.currentData()
        provider_cfg = api_config.api_providers_view().get(provider_id, {})
        models = provider_cfg.get("models", {}) if isinstance(provider_cfg, dict) else {}

        was_blocked = self.model_combo.signalsBlocked()
        self.model_combo.blockSignals(True)
        try:
            self.model_combo.clear()
            for model_name, model_cfg in models.items():
                model_id = model_cfg.get("id") if isinstance(model_cfg, dict) else model_name
                self.model_combo.addItem(model_name, userData=model_id)

            index = self.model_combo.findText(str(selected_model)) if selected_model else -1
            if index != -1:
                self.model_combo.setCurrentIndex(index)
            elif self.model_combo.count() > 0:
                self.model_combo.setCurrentIndex(0)
        finally:
            self.model_combo.blockSignals(was_blocked)

        self._update_model_dependent_controls()

    def _on_model_changed(self, *args):
        self._update_model_dependent_controls()
        self._update_enabled_state()
        self._emit_config_changed()

    def _current_model_config(self):
        model_name = self.model_combo.currentText()
        model_cfg = api_config.all_models_view().get(model_name, {})
        return model_cfg if isinstance(model_cfg, dict) else {}

    def _update_model_dependent_controls(self):
        model_cfg = self._current_model_config()
        thinking_levels = model_cfg.get("thinkingLevel")
        min_budget = model_cfg.get("min_thinking_budget")
        supports_thinking = (thinking_levels is not None) or (min_budget is not False)

        self.thinking_checkbox.setEnabled(supports_thinking)
        if not supports_thinking:
            self.thinking_checkbox.setChecked(False)
            self.thinking_budget_spin.setHidden(False)
            self.thinking_level_combo.setHidden(True)
        elif isinstance(thinking_levels, list) and thinking_levels:
            selected_level = self.thinking_level_combo.currentText()
            self.thinking_budget_spin.setHidden(True)
            self.thinking_level_combo.setHidden(False)
            was_blocked = self.thinking_level_combo.signalsBlocked()
            self.thinking_level_combo.blockSignals(True)
            try:
                self.thinking_level_combo.clear()
                self.thinking_level_combo.addItems([str(level).upper() for level in thinking_levels])
                index = self.thinking_level_combo.findText(str(selected_level).upper())
                if index != -1:
                    self.thinking_level_combo.setCurrentIndex(index)
            finally:
                self.thinking_level_combo.blockSignals(was_blocked)
        else:
            self.thinking_budget_spin.setHidden(False)
            self.thinking_level_combo.setHidden(True)

        self._update_key_indicator()
        self._update_enabled_state()

    def _update_key_indicator(self):
        provider_id = self.provider_combo.currentData()
        model_id = self.model_combo.currentData()
        count = len(green_keys_for_provider(self.settings_manager, provider_id, model_id))
        if count:
            self.keys_label.setText(f"Зелёных ключей: {count}")
            self.keys_label.setStyleSheet(f"color: {theme_manager.color('success')};")
        else:
            self.keys_label.setText("Нет зелёных ключей для выбранной модели")
            self.keys_label.setStyleSheet(f"color: {theme_manager.color('danger')};")

    def _update_enabled_state(self):
        panel_enabled = self.enable_checkbox.isChecked()
        thinking_supported = self.thinking_checkbox.isEnabled()
        thinking_enabled = panel_enabled and thinking_supported and self.thinking_checkbox.isChecked()

        for widget in (
            self.provider_combo,
            self.model_combo,
            self.temp_override_checkbox,
            self.thinking_checkbox,
        ):
            widget.setEnabled(panel_enabled)

        if not thinking_supported:
            self.thinking_checkbox.setEnabled(False)

        self.keys_label.setEnabled(panel_enabled)
        self.temp_spin.setEnabled(panel_enabled and self.temp_override_checkbox.isChecked())
        self.thinking_budget_spin.setEnabled(
            thinking_enabled and self.thinking_budget_spin.isHidden() is False
        )
        self.thinking_level_combo.setEnabled(
            thinking_enabled and self.thinking_level_combo.isHidden() is False
        )

    def get_config(self):
        thinking_enabled = self.thinking_checkbox.isEnabled() and self.thinking_checkbox.isChecked()
        thinking_budget = None
        thinking_level = None
        if thinking_enabled:
            if not self.thinking_level_combo.isHidden():
                thinking_level = self.thinking_level_combo.currentText()
            else:
                thinking_budget = self.thinking_budget_spin.value()

        return {
            "content_filter_fallback_enabled": self.enable_checkbox.isChecked(),
            "content_filter_fallback_provider": self.provider_combo.currentData() or "",
            "content_filter_fallback_model": self.model_combo.currentText(),
            "content_filter_fallback_temperature": self.temp_spin.value(),
            "content_filter_fallback_temperature_override": self.temp_override_checkbox.isChecked(),
            "content_filter_fallback_thinking_enabled": thinking_enabled,
            "content_filter_fallback_thinking_budget": thinking_budget,
            "content_filter_fallback_thinking_level": thinking_level,
        }

    def set_config(self, settings):
        settings = settings or {}
        self._restoring = True
        signal_states = self._capture_signal_states()
        for widget, _blocked in signal_states:
            widget.blockSignals(True)
        try:
            self.enable_checkbox.setChecked(
                bool(settings.get("content_filter_fallback_enabled", False))
            )

            provider_id = settings.get("content_filter_fallback_provider")
            if provider_id:
                index = self.provider_combo.findData(provider_id)
                if index != -1:
                    self.provider_combo.setCurrentIndex(index)
                    self._models_refresher.refresh_async(provider_id)

            self._reload_models(settings.get("content_filter_fallback_model"))

            self.temp_override_checkbox.setChecked(
                bool(settings.get("content_filter_fallback_temperature_override", False))
            )
            self.temp_spin.setValue(
                float(settings.get("content_filter_fallback_temperature", 1.0))
            )

            self._update_model_dependent_controls()
            self.thinking_checkbox.setChecked(
                bool(settings.get("content_filter_fallback_thinking_enabled", False))
                and self.thinking_checkbox.isEnabled()
            )
            budget = settings.get("content_filter_fallback_thinking_budget")
            self.thinking_budget_spin.setValue(int(budget) if budget is not None else -1)

            thinking_level = settings.get("content_filter_fallback_thinking_level")
            if thinking_level and not self.thinking_level_combo.isHidden():
                index = self.thinking_level_combo.findText(str(thinking_level).upper())
                if index != -1:
                    self.thinking_level_combo.setCurrentIndex(index)
        finally:
            for widget, was_blocked in signal_states:
                widget.blockSignals(was_blocked)
            self._restoring = False

        self._update_model_dependent_controls()
        self._update_enabled_state()
