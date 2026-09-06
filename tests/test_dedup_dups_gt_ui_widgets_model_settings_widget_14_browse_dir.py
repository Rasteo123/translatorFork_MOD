"""Тесты для dups-gt_ui_widgets_model_settings_widget-14.

_browse_repo_dir и _browse_workascii_directory в model_settings_widget.py
дублировали общую цепочку фолбэков на settings_manager перед
QFileDialog.getExistingDirectory (get_project_start_folder -> get_last_project_folder).
Общая часть выносится в модульную функцию _resolve_initial_browse_dir; финальная
валидация (домашняя папка / os.path.dirname) у каждого метода своя и остаётся на месте.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("GT_DISABLE_LOCAL_MODEL_DISCOVERY", "1")

from PyQt6 import QtCore, QtWidgets

from gemini_translator.ui.widgets import model_settings_widget
from gemini_translator.ui.widgets.model_settings_widget import (
    FreeDeepseekApiDialog,
    ModelSettingsWidget,
)


class _DummyBus(QtCore.QObject):
    event_posted = QtCore.pyqtSignal(dict)


class _SettingsStubWithStartFolder:
    """Есть get_project_start_folder — он должен иметь приоритет."""

    def __init__(self, start_folder="", last_folder=""):
        self._start_folder = start_folder
        self._last_folder = last_folder

    def get_project_start_folder(self):
        return self._start_folder

    def get_last_project_folder(self):
        return self._last_folder


class _SettingsStubWithoutStartFolder:
    """Нет get_project_start_folder — используется только get_last_project_folder."""

    def __init__(self, last_folder=""):
        self._last_folder = last_folder

    def get_last_project_folder(self):
        return self._last_folder


class _WidgetSettingsStub(_SettingsStubWithoutStartFolder):
    """Полноценный стаб settings_manager, достаточный для создания ModelSettingsWidget."""

    def __init__(self, config_dir):
        super().__init__(last_folder=config_dir)
        self.config_dir = config_dir
        self._system_prompts = {}
        self._last_system_prompt_text = ""
        self._last_system_prompt_preset_name = ""
        self._last_settings = {}
        self._full_session_settings = {}
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

    def load_full_session_settings(self):
        return dict(self._full_session_settings)

    def save_full_session_settings(self, settings):
        self._full_session_settings = dict(settings or {})
        return True

    def get_last_settings(self):
        return dict(self._last_settings)

    def add_custom_provider_model(self, provider_id, display_name, model_config):
        self.custom_provider_models.setdefault(provider_id, {})[display_name] = dict(model_config)
        return True


class _SettingsStubRaising:
    def get_project_start_folder(self):
        raise RuntimeError("boom")


# ---------------------------------------------------------------------------
# (а) Характеризационные тесты канонической функции _resolve_initial_browse_dir
# ---------------------------------------------------------------------------
class ResolveInitialBrowseDirTests(unittest.TestCase):
    def test_returns_current_path_when_it_is_a_valid_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = _SettingsStubWithStartFolder(start_folder="/should/not/be/used")
            result = model_settings_widget._resolve_initial_browse_dir(settings, tmp)
            self.assertEqual(result, tmp)

    def test_falls_back_to_project_start_folder_when_current_path_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = _SettingsStubWithStartFolder(start_folder=tmp, last_folder="/unused")
            result = model_settings_widget._resolve_initial_browse_dir(settings, "")
            self.assertEqual(result, tmp)

    def test_falls_back_to_project_start_folder_when_current_path_not_a_directory(self):
        settings = _SettingsStubWithStartFolder(start_folder="/some/start", last_folder="/unused")
        result = model_settings_widget._resolve_initial_browse_dir(
            settings, "/definitely/not/a/real/path/xyz"
        )
        self.assertEqual(result, "/some/start")

    def test_uses_last_project_folder_when_start_folder_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = _SettingsStubWithoutStartFolder(last_folder=tmp)
            result = model_settings_widget._resolve_initial_browse_dir(settings, "")
            self.assertEqual(result, tmp)

    def test_returns_empty_string_when_settings_manager_raises(self):
        settings = _SettingsStubRaising()
        result = model_settings_widget._resolve_initial_browse_dir(settings, "")
        self.assertEqual(result, "")

    def test_returns_empty_string_when_no_fallback_available(self):
        settings = _SettingsStubWithStartFolder(start_folder="", last_folder="")
        result = model_settings_widget._resolve_initial_browse_dir(settings, "")
        self.assertEqual(result, "")


# ---------------------------------------------------------------------------
# (б) Тест-маршрутизация: оба места вызова должны идти через общую функцию
# ---------------------------------------------------------------------------
class BrowseDirRoutingTests(unittest.TestCase):
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

    def test_browse_repo_dir_routes_through_shared_resolver(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        settings = _WidgetSettingsStub(temp_dir.name)
        dialog = FreeDeepseekApiDialog(settings)
        self.addCleanup(dialog.close)
        sentinel = tempfile.mkdtemp()
        self.addCleanup(lambda: os.rmdir(sentinel))

        with patch.object(
            model_settings_widget, "_resolve_initial_browse_dir", return_value=sentinel
        ) as mock_resolve, patch.object(
            QtWidgets.QFileDialog, "getExistingDirectory", return_value=""
        ) as mock_dialog:
            dialog._browse_repo_dir()

        self.assertTrue(mock_resolve.called, "_browse_repo_dir должен вызывать общий резолвер")
        self.assertEqual(mock_dialog.call_args[0][2], sentinel)

    def test_browse_workascii_directory_routes_through_shared_resolver(self):
        widget = self._create_widget()
        sentinel = tempfile.mkdtemp()
        self.addCleanup(lambda: os.rmdir(sentinel))
        target_edit = QtWidgets.QLineEdit()

        with patch.object(
            model_settings_widget, "_resolve_initial_browse_dir", return_value=sentinel
        ) as mock_resolve, patch.object(
            QtWidgets.QFileDialog, "getExistingDirectory", return_value=""
        ) as mock_dialog:
            widget._browse_workascii_directory(target_edit, "Тестовая папка")

        self.assertTrue(
            mock_resolve.called,
            "_browse_workascii_directory должен вызывать общий резолвер",
        )
        self.assertEqual(mock_dialog.call_args[0][2], sentinel)


if __name__ == "__main__":
    unittest.main()
