# tests/test_appearance_theme_mode.py
import unittest
from unittest.mock import MagicMock
from PyQt6 import QtWidgets
from gemini_translator.ui import theme_manager as tm


class ThemeModeHandlerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_handler_saves_mode_and_reapplies(self):
        # The live combo + handler live on InitialSetupPage (the class that builds
        # the appearance group); InitialSetupDialog delegates to it via __getattr__.
        from gemini_translator.ui.dialogs.setup import InitialSetupPage
        page = InitialSetupPage.__new__(InitialSetupPage)  # no heavy __init__
        page.settings_manager = MagicMock()
        page.settings_manager.load_settings.return_value = {}
        page._ui_theme_colors = {}
        page._on_theme_mode_changed("light")
        page.settings_manager.save_settings.assert_called()
        saved = page.settings_manager.save_settings.call_args[0][0]
        self.assertEqual(saved[tm.THEME_MODE_KEY], "light")
        self.assertIn("#f4f4f6", self.app.styleSheet())

    def test_mode_switch_changes_colors_without_custom_override(self):
        # Regression for "light/dark does nothing": with no explicit colour
        # override, switching mode must change the applied base colours.
        from gemini_translator.ui.dialogs.setup import InitialSetupPage
        from gemini_translator.ui import themes
        page = InitialSetupPage.__new__(InitialSetupPage)
        page.settings_manager = MagicMock()
        page.settings_manager.load_settings.return_value = {}
        page._ui_theme_colors = {}
        page._on_theme_mode_changed("light")
        self.assertIn(
            themes.LIGHT_DEFAULT_THEME_COLORS["window_bg"], self.app.styleSheet()
        )
        page._on_theme_mode_changed("dark")
        self.assertIn(
            themes.DARK_DEFAULT_THEME_COLORS["window_bg"], self.app.styleSheet()
        )

    def test_apply_ui_theme_colors_keeps_override_sparse(self):
        # Regression: the override must stay sparse (only the keys the user
        # picked). A filled dict would pin window/panel and defeat mode switching.
        from gemini_translator.ui.dialogs.setup import InitialSetupPage
        page = InitialSetupPage.__new__(InitialSetupPage)
        page.settings_manager = MagicMock()
        page.settings_manager.load_settings.return_value = {}
        page._ui_theme_colors = {}
        page.theme_color_buttons = {}  # makes _refresh_ui_theme_controls a no-op
        page._mark_settings_as_dirty = lambda: None
        page._apply_ui_theme_colors({"accent": "#abcdef"}, mark_dirty=False)
        self.assertEqual(page._ui_theme_colors, {"accent": "#abcdef"})
