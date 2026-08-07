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
        with self.manager.file_lock:
            self.manager._cache["api_keys_with_status"] = [
                {
                    "key": f"KEY_{index}",
                    "provider": "gemini",
                    "status_by_model": {
                        f"model-{suffix}": {
                            "exhausted_at": None,
                            "exhausted_level": 0,
                            "requests": [now - 10, now - 5],
                        }
                        for suffix in range(3)
                    },
                }
                for index in range(30)
            ]

    def test_limit_check_does_not_recompose_provider_registry(self):
        api_config.api_providers_view()  # прогреваем кэш

        original = api_config._compose_runtime_providers
        with mock.patch.object(
                api_config, "_compose_runtime_providers",
                side_effect=original) as compose_spy:
            with self.manager.file_lock:
                self.manager._check_and_reset_limits_in_cache()

        self.assertEqual(compose_spy.call_count, 0)


if __name__ == "__main__":
    unittest.main()
