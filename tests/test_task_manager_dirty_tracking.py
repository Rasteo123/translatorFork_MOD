import types
import unittest
from threading import Lock


class _TimerStub:
    def __init__(self):
        self.start_calls = 0
    def start(self):
        self.start_calls += 1


class _SignalStub:
    def __init__(self):
        self.emit_calls = 0
    def emit(self):
        self.emit_calls += 1


class DirtyTrackingStateTests(unittest.TestCase):
    def test_status_group_order_includes_db_and_ui_aliases(self):
        from gemini_translator.core.task_manager import STATUS_GROUP_ORDER
        self.assertEqual(STATUS_GROUP_ORDER["in_progress"], 1)
        self.assertEqual(STATUS_GROUP_ORDER["pending"], 2)
        self.assertEqual(STATUS_GROUP_ORDER["held"], 3)
        # DB statuses and their UI aliases share the same group:
        self.assertEqual(STATUS_GROUP_ORDER["completed"], STATUS_GROUP_ORDER["success"])
        self.assertEqual(STATUS_GROUP_ORDER["failed"], STATUS_GROUP_ORDER["error"])
        self.assertEqual(STATUS_GROUP_ORDER["completed"], 4)
        self.assertEqual(STATUS_GROUP_ORDER["failed"], 5)


class NotifyApiTests(unittest.TestCase):
    def _make_stub(self):
        # Bind real ChapterQueueManager methods onto a SimpleNamespace stub with just the
        # required attrs (per the test-env-deps memory: __new__ bypass would crash
        # on PyQt6, so we use the MethodType harness idiom).
        tm = types.SimpleNamespace(
            _dirty_state_lock=Lock(),
            _dirty_task_ids=set(),
            _structural_dirty=False,
            _ui_update_requested=_SignalStub(),
        )
        from gemini_translator.core.task_manager import ChapterQueueManager
        tm.notify_task_dirty = types.MethodType(ChapterQueueManager.notify_task_dirty, tm)
        tm.notify_structural_change = types.MethodType(ChapterQueueManager.notify_structural_change, tm)
        return tm

    def test_notify_task_dirty_adds_id_and_emits_signal(self):
        tm = self._make_stub()
        tm.notify_task_dirty("abc-123")
        self.assertEqual(tm._dirty_task_ids, {"abc-123"})
        self.assertFalse(tm._structural_dirty)
        self.assertEqual(tm._ui_update_requested.emit_calls, 1)

    def test_notify_task_dirty_accepts_uuid_and_stringifies(self):
        import uuid
        tm = self._make_stub()
        u = uuid.uuid4()
        tm.notify_task_dirty(u)
        self.assertEqual(tm._dirty_task_ids, {str(u)})

    def test_notify_structural_change_sets_flag_and_emits(self):
        tm = self._make_stub()
        tm.notify_structural_change()
        self.assertTrue(tm._structural_dirty)
        self.assertEqual(tm._dirty_task_ids, set())
        self.assertEqual(tm._ui_update_requested.emit_calls, 1)

    def test_notify_methods_do_not_start_timer_directly(self):
        """Critical: QTimer.start() must NEVER be called from these methods,
        because they may be invoked from worker threads. The thread-hop goes
        through _ui_update_requested -> main-thread slot -> _update_timer.start()."""
        tm = self._make_stub()
        tm._update_timer = _TimerStub()
        tm.notify_task_dirty("x")
        tm.notify_structural_change()
        self.assertEqual(tm._update_timer.start_calls, 0,
                         "QTimer.start() must not be called from notify_* - they may run on worker threads")

    def test_safe_request_ui_update_routes_to_structural(self):
        """Backward compat: all ~25 unmigrated callsites of _safe_request_ui_update
        must now set _structural_dirty, so worst case = today's full-fetch behaviour."""
        tm = self._make_stub()
        from gemini_translator.core.task_manager import ChapterQueueManager
        tm._safe_request_ui_update = types.MethodType(ChapterQueueManager._safe_request_ui_update, tm)
        tm._safe_request_ui_update()
        self.assertTrue(tm._structural_dirty)
        self.assertEqual(tm._ui_update_requested.emit_calls, 1)


class TriggerCacheUpdateTests(unittest.TestCase):
    def _make_stub(self):
        tm = types.SimpleNamespace(
            _dirty_state_lock=Lock(),
            _dirty_task_ids={"a", "b"},
            _structural_dirty=False,
            _is_updating_cache=False,
            _cache_update_worker=None,
            _in_flight_snapshot=None,
            _started_workers=[],
        )

        class _FakeWorker:
            def __init__(self, fn, *args, **kwargs):
                self.fn = fn; self.args = args; self.kwargs = kwargs
                self.finished = types.SimpleNamespace(connect=lambda cb: None)
            def start(self):
                tm._started_workers.append(self)
        tm._FakeWorker = _FakeWorker
        return tm

    def test_trigger_cache_update_snapshots_and_clears_state(self):
        from gemini_translator.core import task_manager as tm_mod
        tm = self._make_stub()
        # Monkeypatch TaskDBWorker in the module to our fake.
        original = tm_mod.TaskDBWorker
        tm_mod.TaskDBWorker = tm._FakeWorker
        try:
            from gemini_translator.core.task_manager import ChapterQueueManager
            tm._get_ui_state_list_background = lambda snapshot: None
            tm._on_cache_updated = lambda worker: None  # stub the worker.finished callback
            tm._trigger_cache_update = types.MethodType(ChapterQueueManager._trigger_cache_update, tm)
            tm._trigger_cache_update()
        finally:
            tm_mod.TaskDBWorker = original

        # State reset
        self.assertEqual(tm._dirty_task_ids, set())
        self.assertFalse(tm._structural_dirty)
        # Snapshot stored for failure recovery
        self.assertIsNotNone(tm._in_flight_snapshot)
        self.assertEqual(set(tm._in_flight_snapshot["ids"]), {"a", "b"})
        self.assertFalse(tm._in_flight_snapshot["structural"])
        # Worker started, with snapshot passed as arg
        self.assertEqual(len(tm._started_workers), 1)
        self.assertTrue(tm._is_updating_cache)

    def test_trigger_cache_update_returns_early_if_worker_already_running(self):
        tm = self._make_stub()
        tm._is_updating_cache = True
        from gemini_translator.core.task_manager import ChapterQueueManager
        tm._trigger_cache_update = types.MethodType(ChapterQueueManager._trigger_cache_update, tm)
        tm._trigger_cache_update()
        # State NOT reset because we did not snapshot
        self.assertEqual(tm._dirty_task_ids, {"a", "b"})
        self.assertIsNone(tm._in_flight_snapshot)
        self.assertEqual(tm._started_workers, [])


if __name__ == "__main__":
    unittest.main()
