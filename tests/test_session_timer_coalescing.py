import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets

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


class SessionTimerCoalescingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _make_engine(self):
        engine = TranslationEngine(
            context_manager=_DummyContext(),
            settings_manager=_DummySettings(),
            task_manager=_DummyTaskManager(),
            event_bus=_TopicOnlyBus(),
        )
        self.addCleanup(engine.cleanup)
        return engine

    def test_session_monitor_timer_uses_very_coarse_type(self):
        engine = self._make_engine()
        engine._start_timers()
        self.addCleanup(engine._stop_timers)
        # Health-check раз в ~5.5с не требует точности — позволяем macOS
        # объединять его пробуждения с другими таймерами для экономии энергии.
        self.assertEqual(
            engine.session_monitor_timer.timerType(),
            QtCore.Qt.TimerType.VeryCoarseTimer,
        )


if __name__ == "__main__":
    unittest.main()
