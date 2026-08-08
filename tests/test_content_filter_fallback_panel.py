import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.api import config as api_config
from gemini_translator.ui.widgets.content_filter_fallback_panel import (
    ContentFilterFallbackPanel,
)


class FakeSettings:
    def __init__(self, keys=None):
        self.keys = keys if keys is not None else [
            {"provider": "gemini", "key": "green-gemini-key"},
        ]

    def load_key_statuses(self):
        return list(self.keys)

    def is_key_limit_active(self, key_info, model_id):
        return False


class ContentFilterFallbackPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.providers = {
            "gemini": {
                "display_name": "Gemini",
                "models": {
                    "Flash": {
                        "id": "gemini-flash",
                        "provider": "gemini",
                        "thinkingLevel": ["LOW", "HIGH"],
                    },
                    "Pro": {
                        "id": "gemini-pro",
                        "provider": "gemini",
                        "min_thinking_budget": 128,
                    },
                },
            },
            "nvidia": {
                "display_name": "NVIDIA",
                "models": {
                    "Big": {
                        "id": "big-1",
                        "provider": "nvidia",
                        "min_thinking_budget": False,
                    },
                },
            },
        }
        self.all_models = {
            name: model
            for provider in self.providers.values()
            for name, model in provider["models"].items()
        }
        self.patches = [
            patch.object(api_config, "api_providers_view", return_value=self.providers),
            patch.object(api_config, "all_models_view", return_value=self.all_models),
            patch.object(api_config, "ensure_dynamic_provider_models"),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _create_panel(self, settings_manager=None):
        panel = ContentFilterFallbackPanel(settings_manager=settings_manager)
        self.addCleanup(panel.close)
        return panel

    def test_disabled_by_default_and_config_contains_all_keys(self):
        panel = self._create_panel(FakeSettings())

        config = panel.get_config()

        self.assertFalse(panel.enable_checkbox.isChecked())
        self.assertEqual(
            set(config),
            {
                "content_filter_fallback_enabled",
                "content_filter_fallback_provider",
                "content_filter_fallback_model",
                "content_filter_fallback_temperature",
                "content_filter_fallback_temperature_override",
                "content_filter_fallback_thinking_enabled",
                "content_filter_fallback_thinking_budget",
                "content_filter_fallback_thinking_level",
            },
        )
        self.assertFalse(config["content_filter_fallback_enabled"])

    def test_set_get_round_trip_for_gemini_flash_with_level_thinking(self):
        panel = self._create_panel(FakeSettings())

        panel.set_config(
            {
                "content_filter_fallback_enabled": True,
                "content_filter_fallback_provider": "gemini",
                "content_filter_fallback_model": "Flash",
                "content_filter_fallback_temperature": 0.5,
                "content_filter_fallback_temperature_override": True,
                "content_filter_fallback_thinking_enabled": True,
                "content_filter_fallback_thinking_budget": -1,
                "content_filter_fallback_thinking_level": "HIGH",
            }
        )

        config = panel.get_config()

        self.assertTrue(config["content_filter_fallback_enabled"])
        self.assertEqual(config["content_filter_fallback_provider"], "gemini")
        self.assertEqual(config["content_filter_fallback_model"], "Flash")
        self.assertAlmostEqual(config["content_filter_fallback_temperature"], 0.5)
        self.assertTrue(config["content_filter_fallback_temperature_override"])
        self.assertTrue(config["content_filter_fallback_thinking_enabled"])
        self.assertIsNone(config["content_filter_fallback_thinking_budget"])
        self.assertEqual(config["content_filter_fallback_thinking_level"], "HIGH")

    def test_non_thinking_model_disables_thinking_even_if_config_asks_for_it(self):
        panel = self._create_panel(FakeSettings())

        panel.set_config(
            {
                "content_filter_fallback_enabled": True,
                "content_filter_fallback_provider": "nvidia",
                "content_filter_fallback_model": "Big",
                "content_filter_fallback_thinking_enabled": True,
                "content_filter_fallback_thinking_budget": 2048,
                "content_filter_fallback_thinking_level": "HIGH",
            }
        )

        config = panel.get_config()

        self.assertFalse(panel.thinking_checkbox.isEnabled())
        self.assertFalse(panel.thinking_checkbox.isChecked())
        self.assertFalse(config["content_filter_fallback_thinking_enabled"])
        self.assertIsNone(config["content_filter_fallback_thinking_budget"])
        self.assertIsNone(config["content_filter_fallback_thinking_level"])

    def test_green_key_indicator_includes_count_for_gemini_flash(self):
        panel = self._create_panel(FakeSettings())

        panel.set_config(
            {
                "content_filter_fallback_provider": "gemini",
                "content_filter_fallback_model": "Flash",
            }
        )

        self.assertIn("1", panel.keys_label.text())

    def test_no_key_indicator_warns_when_settings_manager_returns_no_keys(self):
        panel = self._create_panel(FakeSettings(keys=[]))

        panel.set_config(
            {
                "content_filter_fallback_provider": "gemini",
                "content_filter_fallback_model": "Flash",
            }
        )

        self.assertIn("Нет зелёных", panel.keys_label.text())

    def test_set_config_ensures_dynamic_provider_models(self):
        panel = self._create_panel(FakeSettings())
        ensure_mock = api_config.ensure_dynamic_provider_models

        panel.set_config(
            {
                "content_filter_fallback_enabled": True,
                "content_filter_fallback_provider": "nvidia",
                "content_filter_fallback_model": "Big",
            }
        )

        ensure_mock.assert_called_with("nvidia")

    def test_budget_thinking_round_trips_zero(self):
        panel = self._create_panel(FakeSettings())

        panel.set_config(
            {
                "content_filter_fallback_enabled": True,
                "content_filter_fallback_provider": "gemini",
                "content_filter_fallback_model": "Pro",
                "content_filter_fallback_thinking_enabled": True,
                "content_filter_fallback_thinking_budget": 0,
            }
        )

        config = panel.get_config()

        self.assertTrue(config["content_filter_fallback_thinking_enabled"])
        self.assertEqual(config["content_filter_fallback_thinking_budget"], 0)
        self.assertIsNone(config["content_filter_fallback_thinking_level"])

    def test_set_config_preserves_preexisting_signal_block_state(self):
        panel = self._create_panel(FakeSettings())
        panel.thinking_level_combo.blockSignals(True)

        panel.set_config(
            {
                "content_filter_fallback_enabled": True,
                "content_filter_fallback_provider": "gemini",
                "content_filter_fallback_model": "Flash",
                "content_filter_fallback_thinking_enabled": True,
                "content_filter_fallback_thinking_level": "HIGH",
            }
        )

        self.assertTrue(panel.thinking_level_combo.signalsBlocked())


if __name__ == "__main__":
    unittest.main()
