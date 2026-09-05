"""SettingsManager над файлом без ключа custom_provider_models не должен трогать глобальный реестр.

Корневая причина находки ui-dialogs-setup/logic/3: конструктор и сохранение
SettingsManager безусловно применяли cache.get('custom_provider_models', {}) к
api_config, а у project_settings.json этого ключа нет — пользовательские
custom-модели пропадали из глобального реестра при любом обращении к проектным
настройкам. Обёртка в setup.py (снимок/восстановление) уже стоит; здесь —
исправление самого источника.
"""
import json
import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from gemini_translator.api import config as api_config  # noqa: E402
from gemini_translator.utils.settings import SettingsManager  # noqa: E402

_APP = QApplication.instance() or QApplication([])

CUSTOM = {"Deepseek API": {"Моя модель": {"id": "deepseek-custom-x"}}}


class ProjectSettingsFileKeepsGlobalCustomModelsTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(api_config.set_custom_provider_models, api_config.custom_provider_models_snapshot())
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.expected = api_config.set_custom_provider_models(CUSTOM)
        self.assertTrue(self.expected, "тест бессмысленен, если custom-модель не зарегистрировалась")

    def _project_file(self, payload):
        path = os.path.join(self.tmp.name, "project_settings.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        return path

    def test_loading_a_file_without_the_key_keeps_the_registry(self):
        SettingsManager(config_file=self._project_file({"target_language": "ru"}))
        self.assertEqual(api_config.custom_provider_models_snapshot(), self.expected)

    def test_saving_a_file_without_the_key_keeps_the_registry(self):
        manager = SettingsManager(config_file=self._project_file({"target_language": "ru"}))
        api_config.set_custom_provider_models(CUSTOM)
        manager._save_to_disk_unsafe()
        self.assertEqual(api_config.custom_provider_models_snapshot(), self.expected)
        with open(manager.config_file, encoding="utf-8") as handle:
            self.assertNotIn("custom_provider_models", json.load(handle))

    def test_a_file_that_declares_the_key_still_applies_it(self):
        SettingsManager(config_file=self._project_file({"custom_provider_models": {}}))
        self.assertEqual(api_config.custom_provider_models_snapshot(), {})
