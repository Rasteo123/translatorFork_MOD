import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtGui, QtWidgets

from gemini_translator.api import config as api_config
from gemini_translator.ui.widgets.key_management_widget import (
    AdaptiveControlsWidget,
    KeyManagementWidget,
)


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


class _KeySettingsStub:
    config_dir = ""

    def load_key_statuses(self):
        return []

    def is_key_limit_active(self, key_info, model_id):
        return False

    def get_request_count(self, key_info, model_id):
        return 0


class _KeyStatusSettingsStub(_KeySettingsStub):
    def __init__(self, provider_id):
        self.provider_id = provider_id

    def load_key_statuses(self):
        return [
            {"provider": self.provider_id, "key": "active-a"},
            {"provider": self.provider_id, "key": "active-b"},
            {"provider": self.provider_id, "key": "exhausted-c", "limited": True},
        ]

    def is_key_limit_active(self, key_info, model_id):
        return bool(key_info.get("limited"))

    def _get_status_for_model(self, key_info, model_id):
        return {"exhausted_level": 2 if key_info.get("limited") else 0}

    def get_key_reset_time_str(self, key_info, model_id):
        return "Сброс позже"


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

        # Trigger resize directly to avoid host.show() and event loop quirks on Windows
        widget.resize(320, 300)
        dummy_event = QtGui.QResizeEvent(QtCore.QSize(320, 300), QtCore.QSize(320, 300))
        widget.resizeEvent(dummy_event)

        initial_calls = sum(button.style_calls for button in arrow_buttons + action_buttons)
        self.assertGreater(initial_calls, 0)

        widget.resize(320, 305)
        dummy_event = QtGui.QResizeEvent(QtCore.QSize(320, 305), QtCore.QSize(320, 300))
        widget.resizeEvent(dummy_event)

        repeated_calls = sum(button.style_calls for button in arrow_buttons + action_buttons)
        self.assertEqual(repeated_calls, initial_calls)

        widget.resize(320, 420)
        dummy_event = QtGui.QResizeEvent(QtCore.QSize(320, 420), QtCore.QSize(320, 305))
        widget.resizeEvent(dummy_event)

        changed_calls = sum(button.style_calls for button in arrow_buttons + action_buttons)
        self.assertGreater(changed_calls, repeated_calls)


class KeyManagementWidgetProviderModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        api_config.initialize_configs()

    def test_subscribes_only_to_relevant_topics(self):
        bus = _RecordingBus()
        old_bus = getattr(self.app, "event_bus", None)
        self.app.event_bus = bus
        try:
            widget = KeyManagementWidget(_KeySettingsStub())
            self.addCleanup(widget.close)

            for topic in (
                "key_statuses_updated",
                "model_changed",
                "session_started",
                "request_count_updated",
                "fatal_error",
            ):
                self.assertIn(topic, bus.subscriptions)
            # Энергоэффективность: не будимся на самое частое широковещательное событие.
            self.assertNotIn("log_message", bus.subscriptions)

            # Функционал сохраняется: целевое событие по-прежнему обрабатывается.
            bus.emit_topic(
                "session_started",
                {"settings": {"model_config": {"id": "m-42"}}},
            )
            self.assertEqual(widget.current_model_id, "m-42")
        finally:
            if old_bus is None:
                if hasattr(self.app, "event_bus"):
                    delattr(self.app, "event_bus")
            else:
                self.app.event_bus = old_bus

    def test_local_provider_uses_virtual_session_without_api_key(self):
        widget = KeyManagementWidget(_KeySettingsStub())
        self.addCleanup(widget.close)

        widget.set_active_keys_for_provider("local", [])

        self.assertFalse(api_config.provider_requires_api_key("local"))
        self.assertEqual(widget.get_active_keys(), ["__local_model_session__"])
        self.assertEqual(widget.active_keys_list.count(), 1)
        self.assertIn("Локальная сессия", widget.active_keys_list.item(0).text())
        self.assertFalse(widget.available_keys_list.isEnabled())

    def test_key_status_is_rendered_as_provider_card(self):
        first_provider_id = next(
            provider_id
            for provider_id, provider in api_config.api_providers().items()
            if provider.get("visible", True)
            and api_config.provider_requires_api_key(provider_id)
        )
        widget = KeyManagementWidget(_KeyStatusSettingsStub(first_provider_id))
        self.addCleanup(widget.close)

        card = widget.findChild(QtWidgets.QFrame, "keyStatusCard")
        self.assertIsNotNone(card)
        self.assertEqual(widget.key_total_value_label.text(), "3")
        self.assertEqual(widget.key_available_value_label.text(), "2")
        self.assertEqual(widget.key_exhausted_value_label.text(), "1")
        self.assertFalse(widget.findChildren(QtWidgets.QLabel, "keyStatusDetail"))


if __name__ == "__main__":
    unittest.main()
