"""ChapterQueueManager.shutdown(): штатное выключение перед закрытием БД.

В тестах и в параллельных redirect-прогонах (setup.py) менеджер уничтожается
через deleteLater, а его in-memory БД закрывается сразу — пока фоновый
TaskDBWorker ещё может читать её. Итог: «no such table» в логе, три холостых
ретрая и (на Windows CI) аварийный выход процесса. shutdown() останавливает
таймер, дожидается воркера и запрещает новые обновления кэша.
"""
import os
import sqlite3
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets  # noqa: E402

from gemini_translator.core.task_manager import ChapterQueueManager, TaskDBWorker  # noqa: E402


class _Bus:
    def subscribe(self, *_a, **_k):
        pass

    def emit_event(self, *_a, **_k):
        pass


def _make_manager(name):
    uri = f"file:shutdown_{name}?mode=memory&cache=shared"
    anchor = sqlite3.connect(uri, uri=True, check_same_thread=False)
    anchor.row_factory = sqlite3.Row
    manager = ChapterQueueManager(event_bus=_Bus(), db_uri=uri, main_connection=anchor)
    manager._update_timer.setInterval(0)
    return manager, anchor


class ShutdownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _start_slow_worker(self, manager, delay=0.2):
        real = manager._get_ui_state_list_background

        def slow(snapshot, _real=real):
            time.sleep(delay)
            return _real(snapshot)

        manager._get_ui_state_list_background = slow
        manager.notify_structural_change()
        deadline = time.monotonic() + 2
        while manager._cache_update_worker is None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.002)
        self.assertIsNotNone(manager._cache_update_worker, "воркер кэша не стартовал")
        return manager._cache_update_worker

    def test_shutdown_waits_for_the_inflight_worker_before_returning(self):
        manager, anchor = _make_manager("wait")
        self.addCleanup(anchor.close)
        worker = self._start_slow_worker(manager)

        manager.shutdown()

        self.assertTrue(worker.isFinished(), "shutdown() обязан дождаться работающего воркера")
        self.assertNotIn(worker, TaskDBWorker.inflight())

    def test_after_shutdown_notifications_do_not_start_workers(self):
        manager, anchor = _make_manager("quiet")
        self.addCleanup(anchor.close)
        manager.shutdown()
        anchor.close()

        manager.notify_structural_change()
        manager.notify_task_dirty("t-1")
        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)

        self.assertFalse(manager._update_timer.isActive())
        self.assertIsNone(manager._cache_update_worker, "после shutdown() новые воркеры запускаться не должны")

    def test_shutdown_waits_for_the_session_cleanup_worker_too(self):
        """Финиш redirect-прогона закрывает БД сразу после session_finished, а
        _handle_session_finished_background в этот момент ещё крутится в своём
        TaskDBWorker — на Windows CI это давало «no such table» и порчу памяти
        sqlite. shutdown() обязан дождаться ВСЕХ воркеров менеджера."""
        manager, anchor = _make_manager("cleanup")
        self.addCleanup(anchor.close)
        real = manager._handle_session_finished_background

        def slow(*args, **kwargs):
            time.sleep(0.2)
            return real(*args, **kwargs)

        manager._handle_session_finished_background = slow
        manager.on_event({"event": "session_finished", "session_id": None, "data": {"reason": "ok"}})
        deadline = time.monotonic() + 2
        while getattr(manager, "_cleanup_worker", None) is None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.002)
        worker = manager._cleanup_worker
        self.assertIsNotNone(worker, "воркер очистки сессии не стартовал")
        self.assertTrue(worker.isRunning())

        manager.shutdown()

        self.assertTrue(worker.isFinished(), "shutdown() обязан дождаться воркера очистки сессии")

    def test_shutdown_is_idempotent_and_safe_without_worker(self):
        manager, anchor = _make_manager("idempotent")
        self.addCleanup(anchor.close)
        manager.shutdown()
        manager.shutdown()
        self.assertTrue(manager.is_shut_down)


if __name__ == "__main__":
    unittest.main()
