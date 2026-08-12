import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.core.translation_engine import TranslationEngine


class _TopicOnlyBus:
    def __init__(self):
        self.subscriptions = {}
        self.events = []

    def subscribe(self, event_name, callback):
        self.subscriptions.setdefault(event_name, []).append(callback)

    def unsubscribe(self, event_name, callback):
        callbacks = self.subscriptions.get(event_name, [])
        if callback in callbacks:
            callbacks.remove(callback)

    def emit_event(self, event):
        self.events.append(event)


class _DummyContext:
    chinese_processor = None


class _DummySettings:
    pass


class _DummyTaskManager:
    pass


class _FakeChunkAssembler:
    def __init__(self):
        self.cleaned_up = False

    def cleanup(self):
        self.cleaned_up = True


class _FakePowerInhibitor:
    def __init__(self):
        self.prevent_calls = 0
        self.allow_calls = 0

    def prevent_sleep(self):
        self.prevent_calls += 1
        return True

    def allow_sleep(self):
        self.allow_calls += 1


class CoreTopicSubscriptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_translation_engine_subscribes_only_to_relevant_topics(self):
        bus = _TopicOnlyBus()

        engine = TranslationEngine(
            context_manager=_DummyContext(),
            settings_manager=_DummySettings(),
            task_manager=_DummyTaskManager(),
            event_bus=bus,
        )

        self.addCleanup(engine.cleanup)
        self.assertIn("start_session_requested", bus.subscriptions)
        self.assertIn("temporary_limit_warning_received", bus.subscriptions)
        self.assertIn("task_finished", bus.subscriptions)
        self.assertNotIn("log_message", bus.subscriptions)

    def test_translation_engine_cleans_chunk_assembler(self):
        bus = _TopicOnlyBus()

        engine = TranslationEngine(
            context_manager=_DummyContext(),
            settings_manager=_DummySettings(),
            task_manager=_DummyTaskManager(),
            event_bus=bus,
        )
        self.addCleanup(engine.cleanup)

        assembler = _FakeChunkAssembler()
        engine.chunk_assembler = assembler

        engine._cleanup_chunk_assembler()

        self.assertTrue(assembler.cleaned_up)
        self.assertIsNone(engine.chunk_assembler)

    def test_translation_engine_controls_power_inhibitor_for_opted_in_session(self):
        engine = TranslationEngine(
            context_manager=_DummyContext(),
            settings_manager=_DummySettings(),
            task_manager=_DummyTaskManager(),
            event_bus=_TopicOnlyBus(),
        )
        self.addCleanup(engine.cleanup)
        inhibitor = _FakePowerInhibitor()
        engine.power_inhibitor = inhibitor
        engine.session_settings = {"prevent_sleep_during_translation": True}
        engine.session_id = "session-1"
        engine.summary_shown_for_session = True

        engine._activate_power_inhibitor_for_session()
        engine._end_session("Сессия успешно завершена")

        self.assertEqual(inhibitor.prevent_calls, 1)
        self.assertEqual(inhibitor.allow_calls, 1)

    def test_translation_engine_releases_power_inhibitor_when_worker_shutdown_fails(self):
        engine = TranslationEngine(
            context_manager=_DummyContext(),
            settings_manager=_DummySettings(),
            task_manager=_DummyTaskManager(),
            event_bus=_TopicOnlyBus(),
        )
        self.addCleanup(engine.cleanup)
        inhibitor = _FakePowerInhibitor()
        engine.power_inhibitor = inhibitor
        engine.session_settings = {"prevent_sleep_during_translation": True}
        engine.session_id = "session-1"
        original_terminate_all_workers = engine._terminate_all_workers

        def fail_worker_shutdown():
            raise RuntimeError("worker shutdown failed")

        engine._terminate_all_workers = fail_worker_shutdown
        engine._activate_power_inhibitor_for_session()
        try:
            with self.assertRaisesRegex(RuntimeError, "worker shutdown failed"):
                engine._end_session("Сессия успешно завершена")
        finally:
            engine._terminate_all_workers = original_terminate_all_workers

        self.assertEqual(inhibitor.prevent_calls, 1)
        self.assertEqual(inhibitor.allow_calls, 1)

    def test_translation_engine_leaves_power_inhibitor_idle_when_setting_is_disabled(self):
        engine = TranslationEngine(
            context_manager=_DummyContext(),
            settings_manager=_DummySettings(),
            task_manager=_DummyTaskManager(),
            event_bus=_TopicOnlyBus(),
        )
        self.addCleanup(engine.cleanup)
        inhibitor = _FakePowerInhibitor()
        engine.power_inhibitor = inhibitor
        engine.session_settings = {"prevent_sleep_during_translation": False}

        engine._activate_power_inhibitor_for_session()

        self.assertEqual(inhibitor.prevent_calls, 0)
