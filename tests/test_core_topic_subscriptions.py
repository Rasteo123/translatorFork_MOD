import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.core.translation_engine import TranslationEngine


class _TopicOnlyBus:
    def __init__(self):
        self.subscriptions = {}

    def subscribe(self, event_name, callback):
        self.subscriptions.setdefault(event_name, []).append(callback)

    def unsubscribe(self, event_name, callback):
        callbacks = self.subscriptions.get(event_name, [])
        if callback in callbacks:
            callbacks.remove(callback)


class _DummyContext:
    chinese_processor = None


class _DummySettings:
    pass


class _DummyTaskManager:
    pass


class _FakeChunkAssembler:
    def __init__(self):
        self.cleaned_up = False
        self.delete_later_called = False

    def cleanup(self):
        self.cleaned_up = True

    def deleteLater(self):
        self.delete_later_called = True


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
        self.assertTrue(assembler.delete_later_called)
        self.assertIsNone(engine.chunk_assembler)
