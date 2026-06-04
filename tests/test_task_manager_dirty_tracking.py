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


if __name__ == "__main__":
    unittest.main()
