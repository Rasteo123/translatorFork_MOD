"""cluster-45: HomePage._update_proxy_display vs InitialSetupDialog._update_proxy_display.

Обе копии рисовали один и тот же текст/tooltip статуса прокси, но разошлись:
только версия InitialSetupDialog красит лейбл через theme_manager (зелёный —
включён, приглушённый — выключен). Каноническая реализация вынесена в
``gemini_translator.ui.widgets.proxy_status.render_proxy_status`` (раскраска
сохранена — она покрыта test_translator_only_proxy_controls.py).

(а) Характеризационные тесты самой канонической функции — крайние случаи,
    которые различали копии (раскраска, host/port пустые при enabled=True).
(б) Тесты-маршрутизация: HomePage и InitialSetupDialog обязаны звать
    render_proxy_status, а не иметь свою копию логики. До рефакторинга это
    падает (имя render_proxy_status ещё не импортировано в модуль).
"""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.ui import theme_manager
from gemini_translator.ui.widgets.proxy_status import render_proxy_status


class _LabelStub:
    def __init__(self):
        self.text = ""
        self.tooltip = ""
        self.stylesheet = ""

    def setText(self, value):
        self.text = value

    def setToolTip(self, value):
        self.tooltip = value

    def setStyleSheet(self, value):
        self.stylesheet = value


class RenderProxyStatusCharacterizationTests(unittest.TestCase):
    """(а) Поведение канонической render_proxy_status."""

    def test_enabled_with_host_and_port_paints_success_color(self):
        label = _LabelStub()

        render_proxy_status(
            label,
            {
                "enabled": True,
                "type": "SOCKS5",
                "host": "127.0.0.1",
                "port": 1080,
                "user": "alice",
                "pass": "secret",
            },
        )

        self.assertEqual(label.text, "Прокси: SOCKS5://127.0.0.1:1080")
        self.assertIn("Тип: SOCKS5", label.tooltip)
        self.assertIn("Пользователь: alice", label.tooltip)
        self.assertNotIn("secret", label.tooltip)
        self.assertEqual(label.stylesheet, f"color: {theme_manager.color('success')};")

    def test_disabled_paints_muted_color(self):
        label = _LabelStub()

        render_proxy_status(label, {"enabled": False})

        self.assertEqual(label.text, "Прокси: выключен")
        self.assertIn("без прокси", label.tooltip)
        self.assertEqual(label.stylesheet, f"color: {theme_manager.color('text_muted')};")

    def test_enabled_without_host_is_treated_as_disabled(self):
        """enabled=True но host пуст — не «включённый» статус (было по-разному в копиях)."""
        label = _LabelStub()

        render_proxy_status(label, {"enabled": True, "type": "HTTP", "host": "", "port": 8080})

        self.assertEqual(label.text, "Прокси: выключен")
        self.assertEqual(label.stylesheet, f"color: {theme_manager.color('text_muted')};")

    def test_no_user_omits_user_line_from_tooltip(self):
        label = _LabelStub()

        render_proxy_status(
            label, {"enabled": True, "type": "HTTP", "host": "proxy.example", "port": 8080}
        )

        self.assertNotIn("Пользователь", label.tooltip)


class HomePageRoutesThroughCanonicalRendererTests(unittest.TestCase):
    """(б) HomePage._update_proxy_display обязан звать render_proxy_status."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_update_proxy_display_delegates_to_render_proxy_status(self):
        from gemini_translator.ui.pages import home_page

        home = home_page.HomePage()
        self.addCleanup(home.close)

        settings = {"enabled": True, "type": "SOCKS5", "host": "10.0.0.1", "port": 9050}
        with patch.object(home_page, "render_proxy_status") as mock_render:
            home._update_proxy_display(settings)

        mock_render.assert_called_once_with(home.proxy_status_label, settings)


class InitialSetupDialogRoutesThroughCanonicalRendererTests(unittest.TestCase):
    """(б) InitialSetupDialog._update_proxy_display обязан звать render_proxy_status."""

    def test_update_proxy_display_delegates_to_render_proxy_status(self):
        from gemini_translator.ui.dialogs import setup as setup_module

        class _Harness:
            _update_proxy_display = setup_module.InitialSetupDialog._update_proxy_display

            def __init__(self):
                self.proxy_status_label = _LabelStub()

        harness = _Harness()
        settings = {"enabled": True, "type": "HTTP", "host": "proxy.example", "port": 8080}

        with patch.object(setup_module, "render_proxy_status") as mock_render:
            harness._update_proxy_display(settings)

        mock_render.assert_called_once_with(harness.proxy_status_label, settings)


if __name__ == "__main__":
    unittest.main()
