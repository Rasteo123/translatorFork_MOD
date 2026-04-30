import unittest
from unittest.mock import patch

from gemini_translator.api import config as api_config
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
        self._minimum = 1
        self._maximum = 1
        self._value = 1

    def minimum(self):
        return self._minimum

    def maximum(self):
        return self._maximum

    def setMinimum(self, value):
        self._minimum = int(value)
        if self._value < self._minimum:
            self._value = self._minimum

    def setMaximum(self, value):
        self._maximum = int(value)
        if self._value > self._maximum:
            self._value = self._maximum

    def setRange(self, minimum, maximum):
        self._minimum = int(minimum)
        self.setMaximum(maximum)

    def setValue(self, value):
        self._value = int(value)

    def value(self):
        return self._value

    def blockSignals(self, _value):
        return None


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

    def isChecked(self):
        return self.checked


class _SettingsManagerStub:
    def __init__(self, saved=None):
        self.saved = dict(saved or {})
        self.persisted = None

    def get_last_glossary_generation_settings(self):
        return dict(self.saved)

    def save_last_glossary_generation_settings(self, settings):
        self.persisted = dict(settings)
        return True


class _GenerationSettingsHarness:
    _apply_initial_settings = GenerationSessionDialog._apply_initial_settings
    _apply_instances_value = GenerationSessionDialog._apply_instances_value
    _apply_new_terms_limit_value = GenerationSessionDialog._apply_new_terms_limit_value
    _merge_initial_ui_settings = GenerationSessionDialog._merge_initial_ui_settings
    _save_persistent_ui_settings = GenerationSessionDialog._save_persistent_ui_settings
    _get_available_session_capacity = GenerationSessionDialog._get_available_session_capacity
    _update_instances_spinbox_limit = GenerationSessionDialog._update_instances_spinbox_limit

    def __init__(self):
        self.settings_manager = _SettingsManagerStub()
        self._restore_saved_ui_settings = True
        self._persist_ui_settings = True
        self._pending_new_terms_limit = None
        self.key_widget = _KeyWidgetStub()
        self.instances_spin = _SpinBoxStub()
        self.new_terms_limit_spin = _SpinBoxStub()
        self.new_terms_limit_spin.setRange(5, 500)
        self.model_settings_widget = _ModelSettingsWidgetStub()
        self.translation_options_widget = _TranslationOptionsWidgetStub()
        self.prompt_widget = _PromptWidgetStub()
        self.pipeline_enabled_checkbox = _CheckboxStub()
        self.sequential_mode_checkbox = _CheckboxStub()
        self.send_notes_checkbox = _CheckboxStub()
        self.ai_mode_update_radio = _CheckboxStub()
        self.ai_mode_supplement_radio = _CheckboxStub()
        self.ai_mode_accumulate_radio = _CheckboxStub()
        self.pipeline_replaced_with = None
        self.dependent_widgets_updated = 0
        self.start_button_updates = 0
        self.batch_mode_forced = 0
        self.task_size_override = None
        self.sequential_widget_updates = []

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

    def _update_sequential_mode_widgets(self, is_sequential):
        self.sequential_widget_updates.append(bool(is_sequential))

    def _get_full_ui_settings(self):
        return {
            "model": "saved-model",
            "is_sequential": self.sequential_mode_checkbox.isChecked(),
            "send_notes_in_sequence": self.send_notes_checkbox.isChecked(),
            "merge_mode": self.get_merge_mode(),
            "num_instances": self.instances_spin.value(),
            "new_terms_limit": self.new_terms_limit_spin.value(),
        }

    def get_merge_mode(self):
        if self.ai_mode_accumulate_radio.isChecked():
            return "accumulate"
        if self.ai_mode_update_radio.isChecked():
            return "update"
        return "supplement"


class _InfoLabelStub:
    def __init__(self):
        self.text = None

    def setText(self, value):
        self.text = value


class _TaskSizeSpinStub:
    def __init__(self, value=12000, maximum=350000):
        self._value = int(value)
        self._maximum = int(maximum)

    def value(self):
        return self._value

    def maximum(self):
        return self._maximum

    def setValue(self, value):
        self._value = int(value)


class _TranslationOptionsForBatchSizeStub:
    def __init__(self, value=12000, user_defined=False):
        self.task_size_spin = _TaskSizeSpinStub(value=value)
        self.info_label = _InfoLabelStub()
        self.user_defined = bool(user_defined)
        self.info_updates = 0

    def is_task_size_user_defined(self):
        return self.user_defined

    def set_task_size_limit(self, value, *, user_defined=False):
        self.task_size_spin.setValue(value)
        self.user_defined = bool(user_defined)

    def _update_info_text(self):
        self.info_updates += 1


class _ModelSettingsForBatchSizeStub:
    def get_settings(self):
        return {"model": "test-model"}


class _GlossaryBatchSizeHarness:
    _calculate_optimal_batch_size = GenerationSessionDialog._calculate_optimal_batch_size

    def __init__(self, user_defined=False):
        self.model_settings_widget = _ModelSettingsForBatchSizeStub()
        self.translation_options_widget = _TranslationOptionsForBatchSizeStub(
            value=15404,
            user_defined=user_defined,
        )
        self._glossary_task_size_locked = False
        self._glossary_task_size_lock_reason = None
        self.new_terms_limit_updates = 0

    def _update_new_terms_limit_from_current_size(self):
        self.new_terms_limit_updates += 1


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

    def test_initial_settings_restore_glossary_generation_controls(self):
        harness = _GenerationSettingsHarness()

        harness._apply_initial_settings(
            {
                "provider": "gemini",
                "api_keys": ["key-1", "key-2"],
                "is_sequential": True,
                "send_notes_in_sequence": False,
                "merge_mode": "accumulate",
                "num_instances": 2,
                "new_terms_limit": 123,
            }
        )

        self.assertTrue(harness.sequential_mode_checkbox.isChecked())
        self.assertFalse(harness.send_notes_checkbox.isChecked())
        self.assertTrue(harness.ai_mode_accumulate_radio.isChecked())
        self.assertEqual(harness.instances_spin.value(), 2)
        self.assertEqual(harness.new_terms_limit_spin.value(), 123)
        self.assertEqual(harness._pending_new_terms_limit, 123)

    def test_saved_glossary_generation_settings_override_parent_defaults(self):
        harness = _GenerationSettingsHarness()
        harness.settings_manager = _SettingsManagerStub(
            {
                "is_sequential": True,
                "merge_mode": "update",
                "new_terms_limit": 77,
            }
        )

        merged = harness._merge_initial_ui_settings(
            {
                "is_sequential": False,
                "merge_mode": "supplement",
                "new_terms_limit": 50,
                "provider": "gemini",
            }
        )

        self.assertTrue(merged["is_sequential"])
        self.assertEqual(merged["merge_mode"], "update")
        self.assertEqual(merged["new_terms_limit"], 77)
        self.assertEqual(merged["provider"], "gemini")

    def test_persistent_settings_save_current_glossary_generation_state(self):
        harness = _GenerationSettingsHarness()
        harness.sequential_mode_checkbox.setChecked(True)
        harness.send_notes_checkbox.setChecked(False)
        harness.ai_mode_update_radio.setChecked(True)
        harness.instances_spin.setMaximum(4)
        harness.instances_spin.setValue(3)
        harness.new_terms_limit_spin.setValue(88)

        harness._save_persistent_ui_settings()

        self.assertEqual(harness.settings_manager.persisted["merge_mode"], "update")
        self.assertTrue(harness.settings_manager.persisted["is_sequential"])
        self.assertFalse(harness.settings_manager.persisted["send_notes_in_sequence"])
        self.assertEqual(harness.settings_manager.persisted["num_instances"], 3)
        self.assertEqual(harness.settings_manager.persisted["new_terms_limit"], 88)

    def test_initial_settings_do_not_treat_inherited_task_size_as_glossary_user_size(self):
        harness = _GenerationSettingsHarness()

        harness._apply_initial_settings(
            {
                "task_size_limit": 15404,
                "task_size_limit_user_defined": True,
            }
        )

        self.assertFalse(harness.translation_options_widget.received_settings["task_size_limit_user_defined"])

    def test_optimal_batch_size_does_not_replace_glossary_user_task_size(self):
        harness = _GlossaryBatchSizeHarness(user_defined=True)

        with patch.object(api_config, "all_models", return_value={"test-model": {"context_length": 200000}}):
            harness._calculate_optimal_batch_size()

        self.assertEqual(harness.translation_options_widget.task_size_spin.value(), 15404)
        self.assertEqual(harness.translation_options_widget.info_updates, 1)

    def test_optimal_batch_size_updates_auto_task_size(self):
        harness = _GlossaryBatchSizeHarness(user_defined=False)

        with patch.object(api_config, "all_models", return_value={"test-model": {"context_length": 230000}}):
            harness._calculate_optimal_batch_size()

        self.assertEqual(harness.translation_options_widget.task_size_spin.value(), 69000)
        self.assertEqual(harness.translation_options_widget.info_updates, 0)


if __name__ == "__main__":
    unittest.main()
