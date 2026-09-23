"""
Переключение на настройки проекта у книги, где project_settings.json ещё нет.

Раньше кнопка меняла надпись на «Настройки проекта», но local_set оставался
False: всё, что меняли «для проекта» (например, флажок «Системный текст
LitRPG»), при старте перевода уходило в глобальные настройки всех книг.
Подсказка кнопки обещает создать файл, если его нет, — так и должно быть.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest

from PyQt6 import QtWidgets

from gemini_translator.api import config as api_config
from gemini_translator.utils.settings import SettingsManager
from gemini_translator.ui.dialogs.setup import InitialSetupDialog
from test_setup_settings_persistence import _SetupSettingsHarness, _SettingsManagerStub


class ProjectSettingsToggleWithoutFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        api_config.initialize_configs()

    def setUp(self):
        self.addCleanup(
            api_config.set_custom_provider_models,
            api_config.custom_provider_models_snapshot(),
        )
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.project_file = os.path.join(self.temp_dir.name, "project_settings.json")

    def _make_harness(self):
        harness = _SetupSettingsHarness(settings_manager=_SettingsManagerStub())
        harness.output_folder = self.temp_dir.name
        harness.global_settings = None
        harness.is_settings_dirty = False
        harness._save_project_settings_only = (
            InitialSetupDialog._save_project_settings_only.__get__(harness)
        )
        harness._toggle_project_settings_mode = (
            InitialSetupDialog._toggle_project_settings_mode.__get__(harness)
        )
        harness._update_context_button_style = lambda *_a, **_kw: None
        harness.translation_options_widget._settings["system_text_rules"] = False
        return harness

    def _project_session(self):
        return SettingsManager(config_file=self.project_file).load_full_session_settings()

    def test_switch_creates_project_file_and_enters_project_mode(self):
        harness = self._make_harness()
        self.assertFalse(os.path.exists(self.project_file))

        harness._toggle_project_settings_mode(True)

        self.assertTrue(harness.local_set)
        self.assertTrue(os.path.exists(self.project_file))
        session = self._project_session()
        self.assertEqual(session.get("task_size_limit"), 15000)
        self.assertIs(session.get("system_text_rules"), False)
        self.assertIsNone(harness.applied_settings)

    def test_project_change_stays_in_project_and_global_state_comes_back(self):
        harness = self._make_harness()
        harness._toggle_project_settings_mode(True)

        harness.translation_options_widget._settings["system_text_rules"] = True
        harness._save_project_settings_only()

        self.assertIs(self._project_session().get("system_text_rules"), True)
        self.assertIsNone(
            harness.settings_manager.saved_full_session,
            "Настройки проекта не должны попадать в глобальные",
        )

        harness._toggle_project_settings_mode(False)

        self.assertFalse(harness.local_set)
        self.assertIs(harness.applied_settings.get("system_text_rules"), False)


if __name__ == "__main__":
    unittest.main()
