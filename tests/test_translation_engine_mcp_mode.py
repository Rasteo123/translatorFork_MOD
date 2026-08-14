import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.api.errors import RateLimitExceededError
from gemini_translator.api.managers import ApiKeyManager
from gemini_translator.core.translation_engine import TranslationEngine


class _RecordingBus:
    def __init__(self):
        self.events = []
        self.subscriptions = {}
        self.event_posted = self._Emitter(self)

    class _Emitter:
        def __init__(self, owner):
            self.owner = owner

        def emit(self, event):
            self.owner.emit_event(event)

    def subscribe(self, topic, callback):
        self.subscriptions.setdefault(topic, []).append(callback)

    def unsubscribe(self, topic, callback):
        callbacks = self.subscriptions.get(topic, [])
        if callback in callbacks:
            callbacks.remove(callback)

    def emit_event(self, event):
        self.events.append(event)


class _ContextManager:
    chinese_processor = None

    def __init__(self):
        self.updated_settings = None

    def update_settings(self, settings):
        self.updated_settings = dict(settings)


class _SettingsManager:
    def load_proxy_settings(self):
        return {}


class _TaskManager:
    session_id = None

    def has_pending_tasks(self):
        return False

    def is_finished(self):
        return False


class TranslationEngineMcpModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_mcp_mode_uses_virtual_key_and_regular_session_start_path(self):
        bus = _RecordingBus()
        context_manager = _ContextManager()
        engine = TranslationEngine(
            context_manager=context_manager,
            settings_manager=_SettingsManager(),
            task_manager=_TaskManager(),
            event_bus=bus,
        )
        self.addCleanup(engine.cleanup)

        engine.apply_and_start_session(
            {
                "provider": "__mcp_server__",
                "mcp_mode": True,
                "api_keys": ["__mcp_client_session__"],
                "num_instances": 1,
                "model_config": {"id": "mcp-client", "provider": "__mcp_server__"},
            }
        )

        self.assertIsNone(engine.api_key_manager)
        self.assertFalse(engine.is_starting)
        self.assertEqual(context_manager.updated_settings["provider"], "__mcp_server__")
        messages = [
            event.get("data", {}).get("message", "")
            for event in bus.events
            if event.get("event") == "log_message"
        ]
        self.assertFalse(any("[MCP] Внутренний движок" in message for message in messages))
        finished = [event for event in bus.events if event.get("event") == "session_finished"]
        self.assertEqual(len(finished), 1)
        self.assertIn("Нет задач", finished[0].get("data", {}).get("reason", ""))
        self.assertNotIn("Нет доступных API ключей", finished[0].get("data", {}).get("reason", ""))

    def test_mcp_quota_exhaustion_finishes_session_with_reset_hint(self):
        bus = _RecordingBus()
        engine = TranslationEngine(
            context_manager=_ContextManager(),
            settings_manager=_SettingsManager(),
            task_manager=_TaskManager(),
            event_bus=bus,
        )
        self.addCleanup(engine.cleanup)
        engine.session_id = "mcp-session"
        engine.session_settings = {"provider": "__mcp_server__"}
        engine.api_key_manager = ApiKeyManager(["__mcp_client_session__"])
        engine.keys_map = {"worker-1": "__mcp_client_session__"}
        engine.active_workers_map = {"worker-1": object()}

        error = RateLimitExceededError(
            "MCP AI-клиент упёрся в 5-часовое окно лимита. "
            "Сброс примерно 2026-07-02 18:30:00 +05."
        )
        error.mcp_reset_hint = "Сброс примерно 2026-07-02 18:30:00 +05"
        error.mcp_limit_window = "5-часовое окно лимита"

        engine._handle_fatal_error(
            "worker-1",
            {"type": "quota_exceeded", "exception": error},
            worker_session=engine.session_id,
        )
        engine.active_workers_map.clear()
        engine._check_if_session_finished()

        finished = [event for event in bus.events if event.get("event") == "session_finished"]
        self.assertEqual(len(finished), 1)
        reason = finished[0].get("data", {}).get("reason", "")
        self.assertIn("MCP AI-клиент", reason)
        self.assertIn("Сброс примерно 2026-07-02 18:30:00 +05", reason)
        self.assertNotIn("Все API ключи исчерпаны", reason)

    def test_worker_initialization_failure_ends_only_the_session(self):
        bus = _RecordingBus()
        engine = TranslationEngine(
            context_manager=_ContextManager(),
            settings_manager=_SettingsManager(),
            task_manager=_TaskManager(),
            event_bus=bus,
        )
        self.addCleanup(engine.cleanup)
        engine.session_id = "glossary-session"
        engine.session_settings = {
            "provider": "gemini",
            "api_keys": ["test-key"],
        }
        engine.api_key_manager = ApiKeyManager(["test-key"])

        with (
            patch(
                "gemini_translator.core.translation_engine.UniversalWorker",
                side_effect=ModuleNotFoundError("missing Windows bundle module"),
            ),
            patch.object(engine, "_end_session") as end_session,
        ):
            engine._launch_worker("test-key")

        end_session.assert_called_once()
        reason = end_session.call_args.args[0]
        self.assertIn("ModuleNotFoundError", reason)
        self.assertIn("missing Windows bundle module", reason)
        self.assertEqual(engine.keys_map, {})
        self.assertEqual(engine.active_workers_map, {})
        engine.session_id = None


if __name__ == "__main__":
    unittest.main()
