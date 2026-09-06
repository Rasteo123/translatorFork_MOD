"""pcluster-66: FunctionWorker и TaskDBWorker — общий скелет run(), разные протоколы.

Характеризационные тесты на _CallableThread (общая база) + тесты-маршрутизация,
которые проверяют, что FunctionWorker и TaskDBWorker больше не определяют
собственный run(), а используют унаследованный из _CallableThread. Протоколы
сигналов (done/failed у FunctionWorker vs self.result у TaskDBWorker) сознательно
не объединяются — см. rationale/recommended_canonical в pcluster-66.
"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gemini_translator.utils.qt_worker import _CallableThread
from gemini_translator.utils.updater import FunctionWorker
from gemini_translator.core.task_manager import TaskDBWorker


class _RecordingThread(_CallableThread):
    """Минимальный подкласс для характеризации run() без реального QThread.start()."""

    def __init__(self, fn):
        super().__init__()
        self._fn = fn
        self.success_result = "__unset__"
        self.error_exc = None

    def _call(self):
        return self._fn()

    def _on_success(self, result):
        self.success_result = result

    def _on_error(self, exc):
        self.error_exc = exc


class CallableThreadCharacterizationTests(unittest.TestCase):
    def test_run_calls_on_success_with_call_result(self):
        t = _RecordingThread(lambda: 42)
        t.run()
        self.assertEqual(t.success_result, 42)
        self.assertIsNone(t.error_exc)

    def test_run_calls_on_error_with_exception_on_failure(self):
        boom = ValueError("kaboom")

        def _raise():
            raise boom

        t = _RecordingThread(_raise)
        t.run()
        self.assertEqual(t.success_result, "__unset__")
        self.assertIs(t.error_exc, boom)

    def test_base_hooks_are_not_implemented_directly(self):
        t = _CallableThread()
        with self.assertRaises(NotImplementedError):
            t._call()
        with self.assertRaises(NotImplementedError):
            t._on_success(None)
        with self.assertRaises(NotImplementedError):
            t._on_error(Exception())


class RoutingThroughCallableThreadTests(unittest.TestCase):
    """RED до рефакторинга: обе копии определяли свой run() и не наследовали
    _CallableThread.run. GREEN после: run унаследован от общей базы."""

    def test_function_worker_uses_shared_run(self):
        self.assertIs(
            FunctionWorker.run, _CallableThread.run,
            "FunctionWorker должен наследовать run() от _CallableThread, а не "
            "определять собственный дубль")

    def test_task_db_worker_uses_shared_run(self):
        self.assertIs(
            TaskDBWorker.run, _CallableThread.run,
            "TaskDBWorker должен наследовать run() от _CallableThread, а не "
            "определять собственный дубль")


class FunctionWorkerProtocolPreservedTests(unittest.TestCase):
    """Протокол FunctionWorker (done/failed) не должен измениться рефакторингом."""

    def test_success_emits_done_with_result(self):
        w = FunctionWorker(lambda: "ok")
        seen = []
        w.done.connect(seen.append)
        w.run()
        self.assertEqual(seen, ["ok"])

    def test_failure_emits_failed_with_user_message_when_present(self):
        class _UserErr(Exception):
            user_message = "человекочитаемое сообщение"

        def _raise():
            raise _UserErr("raw")

        w = FunctionWorker(_raise)
        seen = []
        w.failed.connect(seen.append)
        w.run()
        self.assertEqual(seen, ["человекочитаемое сообщение"])

    def test_failure_emits_str_of_exception_without_user_message(self):
        def _raise():
            raise RuntimeError("plain failure")

        w = FunctionWorker(_raise)
        seen = []
        w.failed.connect(seen.append)
        w.run()
        self.assertEqual(seen, ["plain failure"])


class TaskDBWorkerProtocolPreservedTests(unittest.TestCase):
    """Протокол TaskDBWorker (self.result, без сигналов) не должен измениться."""

    def test_success_sets_result_attribute(self):
        w = TaskDBWorker(lambda x, y: x + y, 2, 3)
        w.run()
        self.assertEqual(w.result, 5)

    def test_failure_leaves_result_none(self):
        def _raise():
            raise RuntimeError("db exploded")

        w = TaskDBWorker(_raise)
        w.result = "sentinel-should-be-cleared"
        w.run()
        self.assertIsNone(w.result)

    def test_kwargs_are_forwarded_to_target_func(self):
        w = TaskDBWorker(lambda a, b=None: (a, b), 1, b=9)
        w.run()
        self.assertEqual(w.result, (1, 9))


if __name__ == "__main__":
    unittest.main()
