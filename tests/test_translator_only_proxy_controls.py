import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gemini_translator.ui.dialogs.setup import InitialSetupDialog


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


class _EventSignalStub:
    def __init__(self):
        self.events = []

    def emit(self, payload):
        self.events.append(dict(payload))


class _EventBusStub:
    def __init__(self):
        self.event_posted = _EventSignalStub()


class _SettingsManagerStub:
    def __init__(self, proxy_settings=None):
        self._proxy_settings = dict(proxy_settings or {})

    def load_proxy_settings(self):
        return dict(self._proxy_settings)


class _TranslatorOnlyProxyHarness:
    _update_proxy_display = InitialSetupDialog._update_proxy_display
    _activate_proxy_from_settings = InitialSetupDialog._activate_proxy_from_settings

    def __init__(self, proxy_settings=None):
        self.proxy_status_label = _LabelStub()
        self.settings_manager = _SettingsManagerStub(proxy_settings=proxy_settings)
        self.bus = _EventBusStub()


class TranslatorOnlyProxyControlsTests(unittest.TestCase):
    def test_update_proxy_display_renders_enabled_proxy_status(self):
        harness = _TranslatorOnlyProxyHarness()

        harness._update_proxy_display(
            {
                "enabled": True,
                "type": "SOCKS5",
                "host": "127.0.0.1",
                "port": 1080,
                "user": "alice",
            }
        )

        self.assertEqual(harness.proxy_status_label.text, "Прокси: SOCKS5://127.0.0.1:1080")
        self.assertIn("Пользователь: alice", harness.proxy_status_label.tooltip)
        self.assertEqual(harness.proxy_status_label.stylesheet, "color: #4CAF50;")

    def test_update_proxy_display_renders_disabled_proxy_status(self):
        harness = _TranslatorOnlyProxyHarness()

        harness._update_proxy_display({"enabled": False})

        self.assertEqual(harness.proxy_status_label.text, "Прокси: выключен")
        self.assertIn("без прокси", harness.proxy_status_label.tooltip)
        self.assertEqual(harness.proxy_status_label.stylesheet, "color: #9aa4b2;")

    def test_activate_proxy_from_settings_emits_proxy_started_event(self):
        proxy_settings = {
            "enabled": True,
            "type": "HTTP",
            "host": "proxy.example",
            "port": 8080,
        }
        harness = _TranslatorOnlyProxyHarness(proxy_settings=proxy_settings)

        harness._activate_proxy_from_settings()

        self.assertEqual(len(harness.bus.event_posted.events), 1)
        event = harness.bus.event_posted.events[0]
        self.assertEqual(event["event"], "proxy_started")
        self.assertEqual(event["source"], "InitialSetupDialog")
        self.assertEqual(event["data"], proxy_settings)


if __name__ == "__main__":
    unittest.main()
