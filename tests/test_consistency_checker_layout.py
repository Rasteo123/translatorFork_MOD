import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("GT_DISABLE_LOCAL_MODEL_DISCOVERY", "1")

from PyQt6 import QtWidgets

from main import EventBus
from gemini_translator.ui.dialogs.consistency_checker import (
    DEEP_CONSISTENCY_MODE,
    ConsistencyValidatorDialog,
    ConsistencyValidatorPage,
)
from gemini_translator.utils.power_inhibitor import PREVENT_SLEEP_SETTING_KEY
from gemini_translator.utils.settings import SettingsManager


class _CheckBoxStub:
    def __init__(self, checked=False):
        self._checked = bool(checked)

    def isChecked(self):
        return self._checked

    def setChecked(self, value):
        self._checked = bool(value)


class _SettingsManagerStub:
    def __init__(self, full_session=None):
        self.full_session = dict(full_session or {})
        self.saved_full_session = None

    def load_full_session_settings(self):
        return dict(self.full_session)

    def save_full_session_settings(self, settings):
        self.saved_full_session = dict(settings)
        self.full_session = dict(settings)
        return True


class _ModelSettingsStub:
    def get_settings(self):
        return {"model": "test-model"}


class _KeySettingsStub:
    def get_selected_provider(self):
        return "gemini"


class _SpinBoxStub:
    def value(self):
        return 3


class _ConsistencySettingsHarness:
    _get_current_config = ConsistencyValidatorDialog._get_current_config
    _save_shared_sleep_prevention_setting = ConsistencyValidatorDialog._save_shared_sleep_prevention_setting

    def __init__(self, full_session=None):
        self.settings_manager = _SettingsManagerStub(full_session)
        self.prevent_sleep_checkbox = _CheckBoxStub()
        self.model_settings_widget = _ModelSettingsStub()
        self.key_management_widget = _KeySettingsStub()
        self.chunk_size_spin = _SpinBoxStub()
        self.consistency_mode_combo = None


class ConsistencyCheckerLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.app.event_bus = EventBus()
        self.settings_file = tempfile.NamedTemporaryFile(
            suffix=".json",
            delete=False,
        )
        self.settings_file.close()
        self.settings = SettingsManager(
            event_bus=self.app.event_bus,
            config_file=self.settings_file.name,
        )
        self.app.settings_manager = self.settings
        self.app.get_settings_manager = lambda: self.settings

    def tearDown(self):
        self.settings.flush()
        try:
            os.unlink(self.settings_file.name)
        except FileNotFoundError:
            pass

    @patch("gemini_translator.ui.widgets.key_management_widget.KeyManagementWidget.minimumSizeHint")
    def test_settings_tab_keeps_key_management_widget_at_usable_width(self, mock_min_size):
        from PyQt6.QtCore import QSize
        mock_min_size.return_value = QSize(800, 600)
        with patch.object(ConsistencyValidatorPage, "_check_for_previous_session", lambda _page: None):
            page = ConsistencyValidatorPage(
                [{"name": "Chapter 1", "content": "text", "path": "chapter.xhtml"}],
                self.settings,
            )
        self.addCleanup(page.close)

        page.resize(2500, 1000)
        page.main_tabs.setCurrentIndex(1)

        settings_tab = page.main_tabs.widget(1)
        settings_left_tabs = next(
            tab_widget
            for tab_widget in settings_tab.findChildren(QtWidgets.QTabWidget)
            if tab_widget.indexOf(page.key_management_widget) != -1
        )

        self.assertGreaterEqual(
            settings_left_tabs.minimumWidth(),
            page.key_management_widget.minimumSizeHint().width(),
        )

    def test_consistency_saves_shared_sleep_prevention_checkbox(self):
        harness = _ConsistencySettingsHarness({"model": "kept-model"})
        harness.prevent_sleep_checkbox.setChecked(True)

        harness._save_shared_sleep_prevention_setting(True)

        self.assertEqual(harness.settings_manager.saved_full_session["model"], "kept-model")
        self.assertTrue(
            harness.settings_manager.saved_full_session[PREVENT_SLEEP_SETTING_KEY]
        )

    def test_consistency_config_includes_shared_sleep_prevention_setting(self):
        harness = _ConsistencySettingsHarness()
        harness.prevent_sleep_checkbox.setChecked(True)

        config = harness._get_current_config()

        self.assertEqual(config["consistency_mode"], DEEP_CONSISTENCY_MODE)
        self.assertTrue(config[PREVENT_SLEEP_SETTING_KEY])


if __name__ == "__main__":
    unittest.main()
