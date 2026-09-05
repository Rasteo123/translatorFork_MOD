"""
Тест для root-entry/bugs/5-dispatch-to-topics-swallows-ex.

EventBus._dispatch_to_topics глотал исключения topic-подписчиков без единой
строки лога (main.py: `except Exception: pass`). Тест проверяет реальное
поведение метода: сбойный подписчик не должен ронять доставку остальным,
но след сбоя обязан появиться в stdout.
"""
import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from main import EventBus


class DispatchToTopicsLogsErrorsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_broken_subscriber_error_is_logged_and_does_not_break_delivery(self):
        bus = EventBus()
        received = []

        def broken_subscriber(event):
            raise KeyError("boom")

        def healthy_subscriber(event):
            received.append(event)

        bus.subscribe("task_state_changed", broken_subscriber)
        bus.subscribe("task_state_changed", healthy_subscriber)

        event = {"event": "task_state_changed", "data": {}}

        captured = io.StringIO()
        with redirect_stdout(captured):
            bus._dispatch_to_topics(event)

        # Здоровый подписчик обязан получить событие, несмотря на сбой соседа.
        self.assertEqual(received, [event])

        output = captured.getvalue()
        self.assertTrue(
            output.strip(),
            "Сбой topic-подписчика должен оставлять след в логе/консоли, "
            "а не исчезать бесследно",
        )
        self.assertIn("task_state_changed", output)
        self.assertIn("KeyError", output)

    def test_repeated_failure_of_same_subscriber_is_throttled(self):
        """Review round 2, minor: подписчик, стабильно падающий на частом
        событии (лог/прогресс главы — сотни событий в минуту), не должен
        заваливать GUI-поток десятками полных traceback'ов подряд — это
        само по себе источник тормозов UI. Первый сбой — полный traceback,
        повторные — короткая строка со счётчиком, без traceback."""
        bus = EventBus()

        def broken_subscriber(event):
            raise KeyError("boom")

        bus.subscribe("task_state_changed", broken_subscriber)
        event = {"event": "task_state_changed", "data": {}}

        captured_out = io.StringIO()
        captured_err = io.StringIO()
        # traceback.print_exc() пишет в sys.stderr, а информативная строка
        # (событие + тип исключения) — через print() в sys.stdout, поэтому
        # ловим оба потока.
        with redirect_stdout(captured_out), redirect_stderr(captured_err):
            for _ in range(5):
                bus._dispatch_to_topics(event)

        output = captured_out.getvalue() + captured_err.getvalue()
        traceback_markers = output.count("Traceback (most recent call last)")
        self.assertEqual(
            traceback_markers, 1,
            "Полный traceback обязан печататься только на первый сбой этого "
            "подписчика на этом событии — иначе GUI-поток заваливается "
            "трейсбеками при частом событии с гарантированно падающим "
            "подписчиком",
        )
        # Признак того, что повторные сбои всё равно не исчезают бесследно —
        # где-то в выводе должен быть счётчик повторов.
        self.assertIn("5", output)

    def test_different_subscribers_get_independent_throttle_counters(self):
        """Троттлинг ключуется по (событие, подписчик, тип исключения) —
        сбой одного подписчика не должен 'съедать' полный traceback у
        другого подписчика на том же событии: у каждого свой счётчик."""
        bus = EventBus()

        def broken_subscriber_a(event):
            raise KeyError("boom-a")

        def broken_subscriber_b(event):
            raise KeyError("boom-b")

        bus.subscribe("task_state_changed", broken_subscriber_a)
        event = {"event": "task_state_changed", "data": {}}

        captured_out = io.StringIO()
        captured_err = io.StringIO()
        with redirect_stdout(captured_out), redirect_stderr(captured_err):
            bus._dispatch_to_topics(event)

            bus.unsubscribe("task_state_changed", broken_subscriber_a)
            bus.subscribe("task_state_changed", broken_subscriber_b)

            bus._dispatch_to_topics(event)

        output = captured_out.getvalue() + captured_err.getvalue()
        self.assertEqual(
            output.count("Traceback (most recent call last)"), 2,
            "Разные подписчики (даже с одинаковым типом исключения на одном "
            "и том же событии) должны получать полный traceback каждый на "
            "свой первый сбой",
        )


if __name__ == "__main__":
    unittest.main()
