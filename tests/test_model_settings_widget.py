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
    ModelSettingsWidget,
)


class _DummyBus(QtCore.QObject):
    event_posted = QtCore.pyqtSignal(dict)


class _WidgetSettingsStub:
    def __init__(self, config_dir):
        self.config_dir = config_dir
        self._system_prompts = {}
        self._last_system_prompt_text = ""
        self._last_system_prompt_preset_name = ""
        self._last_settings = {}

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
