import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gemini_translator.ui.dialogs.setup import InitialSetupDialog


class _SettingsManagerStub:
    def __init__(self, raw=None, last=None, full=None):
        self.raw = dict(raw or {})
        self.last = dict(last or {})
        self.full = dict(full or {})
        self.saved_ui_state = None
        self.saved_full_session = None
        self.saved_custom_prompt = None
        self.saved_last_prompt_preset_name = None
        self.config_file = r"C:\temp\settings.json"

    def load_settings(self):
        return dict(self.raw)

    def get_last_settings(self):
        return dict(self.last)

    def load_full_session_settings(self):
        return dict(self.full)

    def save_ui_state(self, payload):
        self.saved_ui_state = dict(payload)
        return True

    def save_full_session_settings(self, payload):
        self.saved_full_session = dict(payload)
        return True

    def save_custom_prompt(self, payload):
        self.saved_custom_prompt = payload
        return True

    def save_last_prompt_preset_name(self, payload):
        self.saved_last_prompt_preset_name = payload
        return True


class _DictWidgetStub:
    def __init__(self, settings):
        self._settings = dict(settings)

    def get_settings(self):
        return dict(self._settings)

    def get_glossary(self):
        return list(self._settings)


class _PromptWidgetStub:
    def __init__(self, prompt="prompt", preset_name="preset"):
        self._prompt = prompt
        self._preset_name = preset_name

    def get_prompt(self):
        return self._prompt

    def get_current_preset_name(self):
        return self._preset_name


class _KeyManagementWidgetStub:
    def __init__(self, provider_id="gemini"):
        self._provider_id = provider_id

    def get_selected_provider(self):
        return self._provider_id


class _SpinBoxStub:
    def __init__(self, value):
        self._value = value

    def value(self):
        return self._value


class _SetupSettingsHarness:
    _get_ui_state_for_saving = InitialSetupDialog._get_ui_state_for_saving
    _collect_global_ui_settings_for_restore = InitialSetupDialog._collect_global_ui_settings_for_restore
    _restore_global_ui_settings = InitialSetupDialog._restore_global_ui_settings
    _save_global_ui_settings = InitialSetupDialog._save_global_ui_settings
    _prepare_for_close = InitialSetupDialog._prepare_for_close
    _return_to_main_menu_from_button = InitialSetupDialog._return_to_main_menu_from_button

    def __init__(self, settings_manager=None):
        self.settings_manager = settings_manager or _SettingsManagerStub()
        self.model_settings_widget = _DictWidgetStub(
            {
                "model": "Gemini 3.0 Flash Preview",
                "use_warmup": True,
                "temperature": 0.8,
            }
        )
        self.translation_options_widget = _DictWidgetStub(
            {
                "use_batching": True,
                "chunking": False,
                "chunk_on_error": True,
                "task_size_limit": 15000,
            }
        )
        self.preset_widget = _PromptWidgetStub()
        self.auto_translate_widget = _DictWidgetStub(
            {
                "enabled": True,
                "filter_redirect_enabled": True,
                "filter_redirect_provider": "deepseek",
                "filter_redirect_model": "deepseek-chat NonThink",
            }
        )
        self.key_management_widget = _KeyManagementWidgetStub("workascii_chatgpt")
        self.instances_spin = _SpinBoxStub(3)
        self.glossary_widget = _DictWidgetStub([])
        self.output_folder = None
        self.local_set = False
        self.initial_glossary_state = []
        self._returning_to_main_menu = False
        self.close_called = False
        self.is_settings_dirty = True
        self._window_title = "Настройка сессии*"
        self.applied_settings = None

    def _get_full_ui_settings(self):
        return {
            "model": "Gemini 3.0 Flash Preview",
            "use_warmup": True,
            "provider": "workascii_chatgpt",
            "num_instances": 3,
            "use_batching": True,
            "chunking": False,
            "chunk_on_error": True,
            "task_size_limit": 15000,
            "auto_translation": {
                "enabled": True,
                "filter_redirect_enabled": True,
                "filter_redirect_provider": "deepseek",
                "filter_redirect_model": "deepseek-chat NonThink",
            },
        }

    def _apply_full_ui_settings(self, settings):
        self.applied_settings = dict(settings)

    def windowTitle(self):
        return self._window_title

    def setWindowTitle(self, title):
        self._window_title = title

    def close(self):
        self.close_called = True


class SetupSettingsPersistenceTests(unittest.TestCase):
    def test_ui_state_for_saving_includes_translation_options_and_session_context(self):
        harness = _SetupSettingsHarness()

        state = harness._get_ui_state_for_saving()

        self.assertEqual(state["task_size_limit"], 15000)
        self.assertTrue(state["use_batching"])
        self.assertTrue(state["use_warmup"])
        self.assertEqual(state["provider"], "workascii_chatgpt")
        self.assertEqual(state["num_instances"], 3)

    def test_collect_global_ui_settings_merges_legacy_and_full_session(self):
        settings_manager = _SettingsManagerStub(
            raw={
                "temperature": 1.0,
                "use_warmup": True,
                "task_size_limit": 15000,
            },
            last={
                "model": "Legacy Model",
                "rpm_limit": 5,
                "chunking": True,
                "dynamic_glossary": False,
            },
            full={
                "model": "Saved Session Model",
                "provider": "workascii_chatgpt",
                "num_instances": 4,
                "task_size_limit": 22000,
            },
        )
        harness = _SetupSettingsHarness(settings_manager=settings_manager)

        restored = harness._collect_global_ui_settings_for_restore()

        self.assertEqual(restored["model"], "Saved Session Model")
        self.assertEqual(restored["provider"], "workascii_chatgpt")
        self.assertEqual(restored["num_instances"], 4)
        self.assertEqual(restored["task_size_limit"], 22000)
        self.assertEqual(restored["rpm_limit"], 5)
        self.assertFalse(restored["dynamic_glossary"])
        self.assertTrue(restored["use_warmup"])

    def test_restore_global_ui_settings_applies_merged_settings(self):
        settings_manager = _SettingsManagerStub(full={"task_size_limit": 18000, "use_warmup": True})
        harness = _SetupSettingsHarness(settings_manager=settings_manager)

        harness._restore_global_ui_settings()

        self.assertIsNotNone(harness.applied_settings)
        self.assertEqual(harness.applied_settings["task_size_limit"], 18000)
        self.assertTrue(harness.applied_settings["use_warmup"])

    def test_save_global_ui_settings_persists_full_session_and_clears_dirty_state(self):
        settings_manager = _SettingsManagerStub()
        harness = _SetupSettingsHarness(settings_manager=settings_manager)

        harness._save_global_ui_settings()

        self.assertEqual(settings_manager.saved_ui_state["task_size_limit"], 15000)
        self.assertEqual(settings_manager.saved_full_session["task_size_limit"], 15000)
        self.assertEqual(settings_manager.saved_full_session["provider"], "workascii_chatgpt")
        self.assertTrue(settings_manager.saved_full_session["auto_translation"]["filter_redirect_enabled"])
        self.assertEqual(
            settings_manager.saved_full_session["auto_translation"]["filter_redirect_provider"],
            "deepseek",
        )
        self.assertFalse(harness.is_settings_dirty)
        self.assertNotIn("*", harness.windowTitle())

    def test_prepare_for_close_without_unsaved_changes_saves_prompt_and_global_state(self):
        settings_manager = _SettingsManagerStub()
        harness = _SetupSettingsHarness(settings_manager=settings_manager)
        harness.is_settings_dirty = False

        result = harness._prepare_for_close()

        self.assertTrue(result)
        self.assertEqual(settings_manager.saved_custom_prompt, "prompt")
        self.assertEqual(settings_manager.saved_last_prompt_preset_name, "preset")
        self.assertEqual(settings_manager.saved_full_session["task_size_limit"], 15000)

    def test_return_to_main_menu_button_runs_pre_close_flow_before_closing(self):
        settings_manager = _SettingsManagerStub()
        harness = _SetupSettingsHarness(settings_manager=settings_manager)
        harness.is_settings_dirty = False

        harness._return_to_main_menu_from_button()

        self.assertTrue(harness._returning_to_main_menu)
        self.assertTrue(harness.close_called)
        self.assertEqual(settings_manager.saved_full_session["task_size_limit"], 15000)


if __name__ == "__main__":
    unittest.main()
