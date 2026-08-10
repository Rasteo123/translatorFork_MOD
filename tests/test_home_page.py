import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.ui.pages.home_page import HomePage
from gemini_translator.ui.shell import ShellPage


class HomePageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _home(self):
        home = HomePage()
        self.addCleanup(home.close)
        return home

    def test_is_shell_page(self):
        self.assertIsInstance(self._home(), ShellPage)

    def test_exposes_all_expected_tool_ids(self):
        home = self._home()
        expected = {
            "translator", "validator", "glossary", "rulate_export",
            "chapter_splitter", "gemini_reader", "ranobelib_uploader",
            "qidian_rulate_creator", "prompt_benchmark",
        }
        self.assertEqual(set(home.tool_buttons.keys()), expected)

    def test_clicking_button_emits_tool_selected(self):
        home = self._home()
        received = []
        home.tool_selected.connect(received.append)
        home.tool_buttons["validator"].click()
        home.tool_buttons["translator"].click()
        self.assertEqual(received, ["validator", "translator"])

    def test_home_page_title_is_empty(self):
        # Home intentionally shows no nav-bar title and no Back button.
        self.assertEqual(self._home().get_page_title(), "")

    def test_exposes_proxy_settings_controls(self):
        home = self._home()
        self.assertEqual(home.proxy_button.text(), "Прокси")
        self.assertEqual(home.proxy_status_label.text(), "Прокси: выключен")

    def test_proxy_status_displays_enabled_connection_without_password(self):
        home = self._home()
        home._update_proxy_display(
            {
                "enabled": True,
                "type": "SOCKS5",
                "host": "127.0.0.1",
                "port": 1080,
                "user": "alice",
                "pass": "secret",
            }
        )

        self.assertEqual(home.proxy_status_label.text(), "Прокси: SOCKS5://127.0.0.1:1080")
        self.assertIn("Пользователь: alice", home.proxy_status_label.toolTip())
        self.assertNotIn("secret", home.proxy_status_label.toolTip())

    def test_proxy_button_opens_settings_dialog(self):
        settings_manager = Mock()
        settings_manager.load_proxy_settings.return_value = {"enabled": False}

        with (
            patch.object(HomePage, "_settings_manager", return_value=settings_manager),
            patch("gemini_translator.ui.dialogs.proxy.ProxySettingsDialog") as dialog_class,
        ):
            home = self._home()
            home.proxy_button.click()

        dialog_class.assert_called_once_with(home, settings_manager)
        # Вне шелла present_dialog показывает обычный window-modal open().
        dialog_class.return_value.open.assert_called_once_with()
