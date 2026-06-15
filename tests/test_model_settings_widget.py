import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("GT_DISABLE_LOCAL_MODEL_DISCOVERY", "1")

from PyQt6 import QtCore, QtWidgets

from gemini_translator.api import config as api_config
from gemini_translator.ui.widgets.model_settings_widget import (
    CHATGPT_LOGIN_URL,
    CHATGPT_SIGNUP_URL,
    CustomModelDialog,
    ModelSettingsWidget,
)


class _DummyBus(QtCore.QObject):
    event_posted = QtCore.pyqtSignal(dict)


class _RecordingBus(QtCore.QObject):
    """Шина с topic-подписками для проверки энергоэффективной фильтрации событий."""

    event_posted = QtCore.pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.subscriptions = {}

    def subscribe(self, event_name, callback):
        self.subscriptions.setdefault(event_name, []).append(callback)

    def unsubscribe(self, event_name, callback):
        callbacks = self.subscriptions.get(event_name, [])
        if callback in callbacks:
            callbacks.remove(callback)

    def emit_topic(self, event_name, data=None):
        event = {"event": event_name, "data": data or {}}
        for callback in list(self.subscriptions.get(event_name, [])):
            callback(event)


class _WidgetSettingsStub:
    def __init__(self, config_dir):
        self.config_dir = config_dir
        self._system_prompts = {}
        self._last_system_prompt_text = ""
        self._last_system_prompt_preset_name = ""
        self._last_settings = {}
        self.custom_provider_models = {}

    def load_system_prompts(self):
        return dict(self._system_prompts)

    def save_system_prompts(self, prompts):
        self._system_prompts = dict(prompts or {})
        return True

    def get_last_system_prompt_text(self):
        return self._last_system_prompt_text

    def get_last_system_prompt_preset_name(self):
        return self._last_system_prompt_preset_name

    def save_last_system_prompt_preset_name(self, preset_name):
        self._last_system_prompt_preset_name = str(preset_name or "")

    def get_last_project_folder(self):
        return self.config_dir

    def load_full_session_settings(self):
        return {}

    def get_last_settings(self):
        return dict(self._last_settings)

    def add_custom_provider_model(self, provider_id, display_name, model_config):
        self.custom_provider_models.setdefault(provider_id, {})[display_name] = dict(model_config)
        return True


class ModelSettingsWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app.event_bus = _DummyBus()

    def _create_widget(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        settings = _WidgetSettingsStub(temp_dir.name)
        self.app.get_settings_manager = lambda settings=settings: settings
        widget = ModelSettingsWidget(settings_manager=settings)
        self.addCleanup(widget.close)
        return widget

    def _create_widget_with_bus(self, bus):
        old_bus = self.app.event_bus
        self.app.event_bus = bus
        self.addCleanup(lambda: setattr(self.app, "event_bus", old_bus))
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        settings = _WidgetSettingsStub(temp_dir.name)
        self.app.get_settings_manager = lambda settings=settings: settings
        widget = ModelSettingsWidget(settings_manager=settings)
        self.addCleanup(widget.close)
        return widget

    def test_subscribes_only_to_provider_changed_topic(self):
        bus = _RecordingBus()
        self._create_widget_with_bus(bus)

        self.assertIn("provider_changed", bus.subscriptions)
        # Энергоэффективность: виджет не должен будиться на частые чужие события.
        self.assertNotIn("log_message", bus.subscriptions)

    def test_ignores_provider_changes_from_other_key_widgets(self):
        widget = self._create_widget()
        own_key_widget = object()
        other_key_widget = object()
        calls = []

        widget.set_provider_event_source(own_key_widget)
        widget.set_available_models = lambda provider_id: calls.append(provider_id)

        widget.on_event({
            "event": "provider_changed",
            "data": {
                "provider_id": "glossary_provider",
                "provider_widget_id": id(other_key_widget),
            },
        })
        widget.on_event({
            "event": "provider_changed",
            "data": {
                "provider_id": "translation_provider",
                "provider_widget_id": id(own_key_widget),
            },
        })

        self.assertEqual(calls, ["translation_provider"])

    def test_skip_content_filter_retry_round_trips_and_defaults_off(self):
        widget = self._create_widget()

        # Default: off, and exposed in get_settings()
        self.assertIn("skip_content_filter_retry", widget.get_settings())
        self.assertFalse(widget.get_settings()["skip_content_filter_retry"])

        # set_settings restores True
        widget.set_settings({"skip_content_filter_retry": True})
        self.assertTrue(widget.skip_filter_retry_checkbox.isChecked())
        self.assertTrue(widget.get_settings()["skip_content_filter_retry"])

        # set_settings restores False
        widget.set_settings({"skip_content_filter_retry": False})
        self.assertFalse(widget.get_settings()["skip_content_filter_retry"])

    def test_workascii_section_hides_technical_fields_and_shows_auth_buttons(self):
        widget = self._create_widget()

        self.assertTrue(widget.workascii_workspace_name_edit.isHidden())
        self.assertTrue(widget.workascii_workspace_index_spin.isHidden())
        self.assertTrue(widget.workascii_headless_checkbox.isHidden())
        self.assertTrue(widget.workascii_profile_template_edit.isHidden())
        self.assertTrue(widget.workascii_refresh_every_spin.isHidden())
        self.assertFalse(widget.workascii_timeout_spin.isHidden())
        self.assertFalse(widget.workascii_login_btn.isHidden())
        self.assertFalse(widget.workascii_signup_btn.isHidden())

    def test_hidden_workascii_settings_round_trip(self):
        widget = self._create_widget()

        widget.set_settings(
            {
                "provider": "workascii_chatgpt",
                "workascii_workspace_name": "workspace-a",
                "workascii_workspace_index": 4,
                "workascii_timeout_sec": 900,
                "workascii_headless": True,
                "workascii_profile_template_dir": r"C:\profiles\template",
                "workascii_refresh_every_requests": 12,
            }
        )

        result = widget.get_settings()

        self.assertEqual(result["workascii_workspace_name"], "workspace-a")
        self.assertEqual(result["workascii_workspace_index"], 4)
        self.assertEqual(result["workascii_timeout_sec"], 900)
        self.assertTrue(result["workascii_headless"])
        self.assertEqual(result["workascii_profile_template_dir"], r"C:\profiles\template")
        self.assertEqual(result["workascii_refresh_every_requests"], 12)

    def test_local_provider_shows_refresh_button(self):
        widget = self._create_widget()

        widget.set_available_models("local")
        self.assertFalse(widget.refresh_models_btn.isHidden())

        widget.set_available_models("gemini")
        self.assertTrue(widget.refresh_models_btn.isHidden())

    def test_custom_model_dialog_builds_model_config(self):
        dialog = CustomModelDialog(
            "Demo Provider",
            defaults={
                "rpm": 12,
                "max_concurrent_requests": 3,
                "context_length": 64000,
                "max_output_tokens": 4096,
            },
        )
        self.addCleanup(dialog.close)

        dialog.model_id_edit.setText("demo/model")
        dialog.display_name_edit.setText("Demo Model")

        display_name, model_config = dialog.get_model_entry()

        self.assertEqual(display_name, "Demo Model")
        self.assertEqual(model_config["id"], "demo/model")
        self.assertEqual(model_config["rpm"], 12)
        self.assertEqual(model_config["max_concurrent_requests"], 3)
        self.assertEqual(model_config["context_length"], 64000)
        self.assertEqual(model_config["max_output_tokens"], 4096)
        self.assertTrue(model_config["user_defined"])

    def test_save_custom_model_entry_uses_settings_manager(self):
        widget = self._create_widget()

        saved = widget._save_custom_model_entry(
            "demo",
            "Demo Model",
            {"id": "demo/model", "rpm": 10},
        )

        self.assertTrue(saved)
        self.assertEqual(
            widget.settings_manager.custom_provider_models["demo"]["Demo Model"]["id"],
            "demo/model",
        )

    def test_temperature_uses_model_default_until_override_enabled(self):
        widget = self._create_widget()
        provider_config = {
            "local": {
                "needs_warmup": False,
                "models": {
                    "Local Model": {
                        "id": "local-model",
                        "provider": "local",
                        "rpm": 1000,
                        "max_concurrent_requests": 1,
                        "default_temperature": 0.4,
                    }
                },
            }
        }
        all_models = {
            "Local Model": {
                "id": "local-model",
                "provider": "local",
                "rpm": 1000,
                "max_concurrent_requests": 1,
                "default_temperature": 0.4,
            }
        }

        with patch.object(api_config, "ensure_dynamic_provider_models"), \
             patch.object(api_config, "api_providers", return_value=provider_config), \
             patch.object(api_config, "all_models", return_value=all_models):
            widget.set_available_models("local")

            self.assertFalse(widget.temperature_override_checkbox.isChecked())
            self.assertFalse(widget.temperature_spin.isEnabled())
            self.assertAlmostEqual(widget.temperature_spin.value(), 0.4)

            settings = widget.get_settings()
            self.assertFalse(settings["temperature_override_enabled"])
            self.assertAlmostEqual(settings["temperature"], 0.4)

            widget.temperature_override_checkbox.setChecked(True)
            self.assertTrue(widget.temperature_spin.isEnabled())

    def test_temperature_override_round_trip(self):
        widget = self._create_widget()

        widget.set_settings({"temperature": 0.8, "temperature_override_enabled": True})

        self.assertTrue(widget.temperature_override_checkbox.isChecked())
        self.assertTrue(widget.temperature_spin.isEnabled())
        self.assertAlmostEqual(widget.get_settings()["temperature"], 0.8)
        self.assertTrue(widget.get_settings()["temperature_override_enabled"])

    def test_hidden_parent_does_not_clear_warmup_or_system_instruction(self):
        widget = self._create_widget()

        widget.warmup_checkbox.setVisible(True)
        widget.warmup_checkbox.setChecked(True)
        widget.system_instruction_editor_dialog.set_prompt("system prompt")
        widget.system_instruction_checkbox.setChecked(True)

        settings = widget.get_settings()

        self.assertTrue(settings["use_warmup"])
        self.assertTrue(settings["use_system_instruction"])
        self.assertEqual(settings["system_instruction"], "system prompt")

    def test_hidden_parent_does_not_convert_thinking_level_to_budget(self):
        widget = self._create_widget()

        widget.thinking_level_combo.addItems(["LOW", "HIGH"])
        widget.thinking_level_combo.setVisible(True)
        widget.thinking_level_combo.setCurrentText("HIGH")
        widget.thinking_checkbox.setChecked(True)

        settings = widget.get_settings()

        self.assertEqual(settings["thinking_level"], "HIGH")
        self.assertIsNone(settings["thinking_budget"])

    def test_set_settings_restores_thinking_level_after_model_rebuild(self):
        widget = self._create_widget()
        level_model = {
            "id": "level-model",
            "provider": "level_provider",
            "rpm": 10,
            "max_concurrent_requests": 1,
            "thinkingLevel": ["low", "high"],
        }
        provider_config = {
            "level_provider": {
                "needs_warmup": True,
                "models": {"Level Model": level_model},
            }
        }

        with patch.object(api_config, "ensure_dynamic_provider_models"), \
             patch.object(api_config, "api_providers", return_value=provider_config), \
             patch.object(api_config, "all_models", return_value={"Level Model": level_model}):
            widget.set_available_models("level_provider")
            widget.thinking_level_combo.setVisible(False)
            widget.set_settings(
                {
                    "provider": "level_provider",
                    "model": "Level Model",
                    "thinking_enabled": True,
                    "thinking_level": "HIGH",
                    "use_warmup": True,
                }
            )

            settings = widget.get_settings()

        self.assertEqual(widget.thinking_level_combo.currentText(), "HIGH")
        self.assertEqual(settings["thinking_level"], "HIGH")
        self.assertTrue(settings["use_warmup"])

    def test_chatgpt_auth_buttons_launch_saved_profile_browser(self):
        widget = self._create_widget()

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_root = Path(temp_dir)
            node_path = runtime_root / "playwright_runtime" / "node.exe"
            package_root = runtime_root / "playwright_runtime" / "package"
            browsers_path = runtime_root / "playwright_runtime" / "ms-playwright"
            launcher_script = runtime_root / "gemini_translator" / "scripts" / "chatgpt_profile_launcher.cjs"
            profile_dir = runtime_root / "chatgpt-profile-run"

            node_path.parent.mkdir(parents=True, exist_ok=True)
            package_root.mkdir(parents=True, exist_ok=True)
            browsers_path.mkdir(parents=True, exist_ok=True)
            launcher_script.parent.mkdir(parents=True, exist_ok=True)
            profile_dir.mkdir(parents=True, exist_ok=True)

            node_path.write_text("", encoding="utf-8")
            (package_root / "package.json").write_text("{}", encoding="utf-8")
            launcher_script.write_text("", encoding="utf-8")

            with patch.object(api_config, "default_workascii_runtime_root", return_value=runtime_root), \
                 patch.object(api_config, "default_workascii_profile_dir", return_value=profile_dir), \
                 patch.object(api_config, "find_node_executable", return_value=node_path), \
                 patch.object(api_config, "find_playwright_package_root", return_value=package_root), \
                 patch.object(api_config, "find_playwright_browsers_path", return_value=browsers_path), \
                 patch.object(api_config, "get_resource_path", return_value=launcher_script), \
                 patch("gemini_translator.ui.widgets.model_settings_widget.subprocess.Popen") as popen_mock:
                widget._open_chatgpt_login()
                widget._open_chatgpt_signup()

            self.assertEqual(popen_mock.call_count, 2)

            login_command = popen_mock.call_args_list[0].args[0]
            signup_command = popen_mock.call_args_list[1].args[0]

            self.assertEqual(
                login_command,
                [
                    str(node_path),
                    str(launcher_script),
                    os.path.normpath(str(profile_dir)),
                    CHATGPT_LOGIN_URL,
                    os.path.normpath(str(package_root)),
                    os.path.normpath(str(browsers_path)),
                ],
            )
            self.assertEqual(
                signup_command,
                [
                    str(node_path),
                    str(launcher_script),
                    os.path.normpath(str(profile_dir)),
                    CHATGPT_SIGNUP_URL,
                    os.path.normpath(str(package_root)),
                    os.path.normpath(str(browsers_path)),
                ],
            )
            self.assertEqual(popen_mock.call_args_list[0].kwargs["cwd"], str(runtime_root))


if __name__ == "__main__":
    unittest.main()
