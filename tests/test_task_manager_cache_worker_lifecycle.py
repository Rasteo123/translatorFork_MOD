"""ChapterQueueManager: фоновый воркер кэша не должен ни зацикливаться на
сломанной БД, ни бить по удалённому менеджеру, ни умирать вместе с ним.

Плавающий сегфолт полного набора (faulthandler показывал лямбду
`worker.finished.connect(lambda: self._on_cache_updated(worker))` во время
processEvents pytest-qt): менеджеры, созданные тестами, после закрытия
in-memory БД бесконечно перезапускали таймер из _recover_failed_worker
(каждые 100 мс новый TaskDBWorker с «no such table»), а когда сборщик мусора
наконец уничтожал такой менеджер, вместе с ним удалялся ещё работающий QThread.
"""
import gc
import os
import sqlite3
import time
import types
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets, sip  # noqa: E402

from gemini_translator.core.task_manager import ChapterQueueManager, TaskDBWorker  # noqa: E402


class _TimerStub:
    def __init__(self):
        self.starts = []

    def start(self, interval=None):
        self.starts.append(interval)


class _Bus:
    def subscribe(self, *_a, **_k):
        pass

    def emit_event(self, *_a, **_k):
        pass


def _make_manager(name):
    uri = f"file:cache_worker_lifecycle_{name}?mode=memory&cache=shared"
    anchor = sqlite3.connect(uri, uri=True, check_same_thread=False)
    anchor.row_factory = sqlite3.Row
    manager = ChapterQueueManager(event_bus=_Bus(), db_uri=uri, main_connection=anchor)
    manager._update_timer.setInterval(0)
    return manager, anchor


def _pump(app, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)


class FailedWorkerRetryBudgetTests(unittest.TestCase):
    """Сломанная БД не должна крутить таймер → воркер → ошибка → таймер вечно."""

    def _stub(self):
        tm = types.SimpleNamespace(
            _dirty_state_lock=__import__("threading").Lock(),
            _dirty_task_ids=set(),
            _structural_dirty=False,
            _is_updating_cache=True,
            _cache_update_worker=None,
            _in_flight_snapshot={"ids": ("a",), "structural": False},
            _update_timer=_TimerStub(),
            _session_active=False,
            _last_cache_update_ns=0,
            _SESSION_UPDATE_INTERVAL_MS=2000,
            _SESSION_RESTART_COOLDOWN_NS=1_500_000_000,
            _ui_state_list_cache=[],
            _sort_keys={},
        )
        tm._post_event = lambda name, data: None
        for method in ("_on_cache_updated", "_recover_failed_worker", "_restart_timer_if_dirty"):
            setattr(tm, method, types.MethodType(getattr(ChapterQueueManager, method), tm))
        return tm

    def test_retries_stop_after_consecutive_failures_and_dirty_state_is_kept(self):
        tm = self._stub()
        for _ in range(6):
            tm._on_cache_updated(types.SimpleNamespace(result=None))
            tm._is_updating_cache = True
        self.assertLess(len(tm._update_timer.starts), 6, "таймер перезапускается после каждой ошибки без предела")
        self.assertTrue(tm._structural_dirty, "после остановки ретраев dirty-флаг должен остаться — его подхватит следующий notify_*")

    def test_success_resets_the_failure_streak(self):
        tm = self._stub()
        for _ in range(3):
            tm._on_cache_updated(types.SimpleNamespace(result=None))
            tm._is_updating_cache = True
        exhausted = len(tm._update_timer.starts)
        tm._on_cache_updated(types.SimpleNamespace(result=None))
        tm._is_updating_cache = True
        self.assertEqual(len(tm._update_timer.starts), exhausted, "бюджет ретраев исчерпан — таймер не перезапускается")
        # Успех (сам по себе может перезапустить таймер из-за накопленного dirty-состояния)…
        tm._on_cache_updated(types.SimpleNamespace(result={"mode": "full", "entries": [], "sort_keys": {}}))
        tm._is_updating_cache = True
        before = len(tm._update_timer.starts)
        # …после чего первая же ошибка снова получает ретрай.
        tm._on_cache_updated(types.SimpleNamespace(result=None))
        self.assertEqual(len(tm._update_timer.starts), before + 1, "после успеха счётчик ошибок должен обнуляться")


class WorkerAndManagerLifetimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _start_slow_worker(self, manager, delay=0.15):
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

    def test_finished_slot_is_not_delivered_to_a_destroyed_manager(self):
        manager, anchor = _make_manager("destroyed")
        self.addCleanup(anchor.close)
        calls = []
        manager._on_cache_updated = lambda worker: calls.append(worker)
        worker = self._start_slow_worker(manager)

        manager.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        self.assertTrue(sip.isdeleted(manager))

        self.assertTrue(worker.wait(3000))
        _pump(self.app, 0.3)
        self.assertEqual(calls, [], "finished удалённого менеджера не должен вызывать _on_cache_updated")

    def test_inflight_worker_outlives_garbage_collected_manager(self):
        manager, anchor = _make_manager("collected")
        self.addCleanup(anchor.close)
        worker = self._start_slow_worker(manager)
        self.assertIn(worker, TaskDBWorker.inflight(), "работающий воркер должен удерживаться реестром, а не только менеджером")

        del manager
        gc.collect()
        self.assertTrue(worker.isRunning() or worker.isFinished())
        self.assertTrue(worker.wait(3000))
        _pump(self.app, 0.3)
        self.assertNotIn(worker, TaskDBWorker.inflight(), "после завершения воркер должен покидать реестр")


if __name__ == "__main__":
    unittest.main()
