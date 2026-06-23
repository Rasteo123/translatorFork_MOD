# tests/test_apply_saved_app_theme.py
import unittest
from PyQt6 import QtWidgets
import main as app_main
from gemini_translator.ui import themes


class FakeSettings:
    def __init__(self, data):
        self.data = dict(data)
    def load_settings(self):
        return dict(self.data)
    def save_settings(self, d):
        self.data = dict(d); return True
    def load_full_session_settings(self):
        return dict(self.data)


class ApplySavedThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_legacy_profile_renders_dark(self):
        app_main.apply_saved_app_theme(
            self.app, FakeSettings({"api_keys_with_status": [{"key": "x"}]})
        )
        self.assertIn(themes.DARK_DEFAULT_THEME_COLORS["window_bg"], self.app.styleSheet())

    def test_explicit_light_mode_renders_light(self):
        app_main.apply_saved_app_theme(self.app, FakeSettings({"ui_theme_mode": "light"}))
        self.assertIn(themes.LIGHT_DEFAULT_THEME_COLORS["window_bg"], self.app.styleSheet())
