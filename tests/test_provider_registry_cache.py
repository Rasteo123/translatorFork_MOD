import os
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.api import config as api_config
from gemini_translator.utils.settings import SettingsManager


class ApiProvidersViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        api_config.initialize_configs()

    def tearDown(self):
        api_config.set_custom_provider_models({})

    def test_view_returns_same_object_between_calls(self):
        first = api_config.api_providers_view()
        second = api_config.api_providers_view()
        self.assertIs(first, second)

    def test_view_reflects_custom_model_changes(self):
        api_config.set_custom_provider_models(
            {"gemini": {"My Custom Model": {"id": "my-custom-model"}}})
        models = api_config.api_providers_view()["gemini"]["models"]
        self.assertIn("My Custom Model", models)

        api_config.set_custom_provider_models({})
        models = api_config.api_providers_view()["gemini"]["models"]
        self.assertNotIn("My Custom Model", models)

    def test_api_providers_returns_independent_copy(self):
        mutated = api_config.api_providers()
        mutated["gemini"]["models"]["INJECTED"] = {"id": "injected"}

        self.assertNotIn(
            "INJECTED", api_config.api_providers_view()["gemini"]["models"])
        self.assertNotIn(
            "INJECTED", api_config.api_providers()["gemini"]["models"])


class SettingsLimitCheckHotPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        api_config.initialize_configs()

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.manager = SettingsManager(
            config_file=os.path.join(self.temp_dir.name, "settings.json"))
        now = int(time.time())
        self.manager.save_key_statuses([
            {"key": f"KEY_{index}", "provider": "gemini"} for index in range(30)
        ])
        # save_key_statuses пишет только конфигурацию; runtime живёт в SQLite.
        self.manager._key_runtime_store.merge_statuses({
            f"KEY_{index}": {
                f"model-{suffix}": {
                    "exhausted_at": None,
                    "exhausted_level": 0,
                    "requests": [now - 10, now - 5],
                }
                for suffix in range(3)
            }
            for index in range(30)
        })

    def test_limit_check_does_not_recompose_provider_registry(self):
        api_config.api_providers_view()  # прогреваем кэш

        original = api_config._compose_runtime_providers
        with mock.patch.object(
                api_config, "_compose_runtime_providers",
                side_effect=original) as compose_spy:
            self.manager._check_and_reset_limits_in_cache()

        self.assertEqual(compose_spy.call_count, 0)
        statuses = self.manager.load_key_statuses()
        self.assertEqual(len(statuses), 30)
        for key_info in statuses:
            for suffix in range(3):
                self.assertEqual(self.manager.get_request_count(key_info, f"model-{suffix}"), 2)


if __name__ == "__main__":
    unittest.main()
