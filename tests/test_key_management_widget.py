import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.api import config as api_config
from gemini_translator.ui.widgets.key_management_widget import (
    AdaptiveControlsWidget,
    KeyManagementWidget,
)


class _KeySettingsStub:
    config_dir = ""

    def load_key_statuses(self):
        return []

    def is_key_limit_active(self, key_info, model_id):
        return False

    def get_request_count(self, key_info, model_id):
        return 0


class _CountingButton(QtWidgets.QPushButton):
    def __init__(self, text=""):
        super().__init__(text)
        self.style_calls = 0

    def setStyleSheet(self, style):
        self.style_calls += 1
        super().setStyleSheet(style)


class AdaptiveControlsWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_resize_with_same_style_bucket_does_not_reapply_button_styles(self):
        arrow_buttons = [_CountingButton(text) for text in (">", "<", ">>", "<<")]
        action_buttons = [_CountingButton(text) for text in ("Add", "Reset", "Force", "Delete")]
        separator = QtWidgets.QFrame()
        separator.setFrameShape(QtWidgets.QFrame.Shape.HLine)

        widget = AdaptiveControlsWidget(arrow_buttons, action_buttons, separator)
        host = QtWidgets.QWidget()
        self.addCleanup(host.close)
        layout = QtWidgets.QVBoxLayout(host)
        layout.addWidget(widget)

        host.resize(320, 300)
        host.show()
        self.app.processEvents()

        initial_calls = sum(button.style_calls for button in arrow_buttons + action_buttons)
        self.assertGreater(initial_calls, 0)

        host.resize(320, 305)
        self.app.processEvents()

        repeated_calls = sum(button.style_calls for button in arrow_buttons + action_buttons)
        self.assertEqual(repeated_calls, initial_calls)

        host.resize(320, 420)
        self.app.processEvents()

        changed_calls = sum(button.style_calls for button in arrow_buttons + action_buttons)
        self.assertGreater(changed_calls, repeated_calls)


class KeyManagementWidgetProviderModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        api_config.initialize_configs()

    def test_local_provider_uses_virtual_session_without_api_key(self):
        widget = KeyManagementWidget(_KeySettingsStub())
        self.addCleanup(widget.close)

        widget.set_active_keys_for_provider("local", [])
        self.app.processEvents()

        self.assertFalse(api_config.provider_requires_api_key("local"))
        self.assertEqual(widget.get_active_keys(), ["__local_model_session__"])
        self.assertEqual(widget.active_keys_list.count(), 1)
        self.assertIn("Локальная сессия", widget.active_keys_list.item(0).text())
        self.assertFalse(widget.available_keys_list.isEnabled())


if __name__ == "__main__":
    unittest.main()
