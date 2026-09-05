"""
Регресс для ui-dialogs-setup/logic/3-project-settings-manager-wipes.

_save_project_settings_only и _toggle_project_settings_mode создают временный
SettingsManager на файл project_settings.json. Конструктор/сохранение
SettingsManager безусловно применяет api_config.set_custom_provider_models(
cache.get('custom_provider_models', {})) — а в проектном файле этого ключа
нет, поэтому глобальный реестр custom-моделей приложения обнуляется.

Тест использует НАСТОЯЩИЙ SettingsManager (не заглушку) и настоящий
api_config, чтобы воспроизвести реальное поведение.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest

from PyQt6 import QtWidgets

from gemini_translator.api import config as api_config
from gemini_translator.ui.dialogs.setup import InitialSetupDialog
from test_setup_settings_persistence import _SetupSettingsHarness, _SettingsManagerStub


class ProjectSettingsManagerPreservesCustomModelsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        api_config.initialize_configs()

    def setUp(self):
        self._previous_custom_models = api_config.custom_provider_models_snapshot()
        self.addCleanup(api_config.set_custom_provider_models, self._previous_custom_models)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

    def _make_harness(self):
        harness = _SetupSettingsHarness(settings_manager=_SettingsManagerStub())
        harness.output_folder = self.temp_dir.name
        harness._save_project_settings_only = (
            InitialSetupDialog._save_project_settings_only.__get__(harness)
        )
        harness._toggle_project_settings_mode = (
            InitialSetupDialog._toggle_project_settings_mode.__get__(harness)
        )
        harness._update_context_button_style = lambda *_a, **_kw: None
        return harness

    def test_save_project_settings_only_preserves_global_custom_models(self):
        api_config.set_custom_provider_models(
            {"gemini": {"My Local 70B": {"id": "my/local-70b"}}}
        )
        self.assertIn("My Local 70B", api_config.all_models())

        harness = self._make_harness()
        harness._save_project_settings_only()

        self.assertIn(
            "My Local 70B",
            api_config.all_models(),
            "Сохранение настроек проекта обнулило глобальный реестр custom-моделей",
        )

    def test_toggle_project_settings_mode_preserves_global_custom_models(self):
        api_config.set_custom_provider_models(
            {"gemini": {"My Local 70B": {"id": "my/local-70b"}}}
        )
        self.assertIn("My Local 70B", api_config.all_models())

        harness = self._make_harness()
        harness.is_settings_dirty = False
        # Файл проекта должен существовать, чтобы сработала ветка загрузки.
        project_settings_path = os.path.join(harness.output_folder, "project_settings.json")
        with open(project_settings_path, "w", encoding="utf-8") as handle:
            handle.write('{"last_full_session": {}}')

        harness._toggle_project_settings_mode(True)

        self.assertIn(
            "My Local 70B",
            api_config.all_models(),
            "Переключение на настройки проекта обнулило глобальный реестр custom-моделей",
        )


if __name__ == "__main__":
    unittest.main()
