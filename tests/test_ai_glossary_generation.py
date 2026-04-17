import unittest

from gemini_translator.ui.dialogs.glossary_dialogs.ai_generation import GenerationSessionDialog


class _KeyWidgetStub:
    def __init__(self):
        self.provider_id = None
        self.active_keys = []

    def set_active_keys_for_provider(self, provider_id, active_keys):
        self.provider_id = provider_id
        self.active_keys = list(active_keys or [])

    def _load_and_refresh_keys(self):
        return None

    def get_selected_provider(self):
        return self.provider_id

    def get_active_keys(self):
        return list(self.active_keys)


class _SpinBoxStub:
    def __init__(self):
        self._maximum = 1
        self._value = 1

    def maximum(self):
        return self._maximum

    def setMaximum(self, value):
        self._maximum = int(value)
        if self._value > self._maximum:
            self._value = self._maximum

    def setValue(self, value):
        self._value = int(value)

    def value(self):
        return self._value


class _ModelSettingsWidgetStub:
    def __init__(self):
        self.received_settings = None

    def set_settings(self, settings):
        self.received_settings = dict(settings)


class _TranslationOptionsWidgetStub:
    def __init__(self):
        self.received_settings = None

    def set_settings(self, settings):
        self.received_settings = dict(settings)


class _PromptWidgetStub:
    def __init__(self):
        self.prompt = None

    def set_prompt(self, prompt):
        self.prompt = prompt


class _CheckboxStub:
    def __init__(self):
        self.checked = False

    def setChecked(self, value):
        self.checked = bool(value)


class _GenerationSettingsHarness:
    _apply_initial_settings = GenerationSessionDialog._apply_initial_settings
    _get_available_session_capacity = GenerationSessionDialog._get_available_session_capacity
    _update_instances_spinbox_limit = GenerationSessionDialog._update_instances_spinbox_limit

    def __init__(self):
        self.key_widget = _KeyWidgetStub()
        self.instances_spin = _SpinBoxStub()
        self.model_settings_widget = _ModelSettingsWidgetStub()
        self.translation_options_widget = _TranslationOptionsWidgetStub()
        self.prompt_widget = _PromptWidgetStub()
        self.pipeline_enabled_checkbox = _CheckboxStub()
        self.pipeline_replaced_with = None
        self.dependent_widgets_updated = 0
        self.start_button_updates = 0
        self.batch_mode_forced = 0
        self.task_size_override = None

    def _force_glossary_batch_mode(self):
        self.batch_mode_forced += 1

    def _apply_glossary_task_size_override(self, size_limit=None, reason=None):
        self.task_size_override = (size_limit, reason)

    def _replace_pipeline_steps(self, steps):
        self.pipeline_replaced_with = steps

    def _update_dependent_widgets(self):
        self.dependent_widgets_updated += 1

    def _update_start_button_state(self):
        self.start_button_updates += 1


class AiGlossaryGenerationTests(unittest.TestCase):
    def test_initial_settings_restore_saved_instances_after_loading_active_keys(self):
        harness = _GenerationSettingsHarness()

        harness._apply_initial_settings(
            {
                "provider": "gemini",
                "api_keys": ["key-1", "key-2", "key-3"],
                "num_instances": 3,
            }
        )

        self.assertEqual(harness.instances_spin.maximum(), 3)
        self.assertEqual(harness.instances_spin.value(), 3)
        self.assertEqual(harness.key_widget.get_active_keys(), ["key-1", "key-2", "key-3"])


if __name__ == "__main__":
    unittest.main()
