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


class BackgroundFetchTests(unittest.TestCase):
    def _make_stub_with_db(self):
        """Build a minimal stub with a real in-memory SQLite. Bind the methods
        under test via types.MethodType (per test-env-deps memory)."""
        import sqlite3
        import json
        from gemini_translator.core.task_manager import ChapterQueueManager

        tm = types.SimpleNamespace(
            _ui_state_list_cache=[],
            _sort_keys={},
        )

        # In-memory SQLite with the schema and seed rows.
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE tasks (task_id TEXT PRIMARY KEY, payload TEXT, status TEXT,
                                priority INTEGER, sequence INTEGER);
            CREATE TABLE task_errors (task_id TEXT, error_type TEXT, timestamp REAL);
        """)
        # 3 tasks across status groups, mixed priorities.
        conn.execute("INSERT INTO tasks VALUES (?, ?, ?, ?, ?)",
                     ("00000000-0000-0000-0000-000000000001",
                      json.dumps(["epub", "/tmp/a.epub", "/tmp/a.html"]),
                      "in_progress", 10, 1))
        conn.execute("INSERT INTO tasks VALUES (?, ?, ?, ?, ?)",
                     ("00000000-0000-0000-0000-000000000002",
                      json.dumps(["epub", "/tmp/b.epub", "/tmp/b.html"]),
                      "completed", 5, 2))
        conn.execute("INSERT INTO tasks VALUES (?, ?, ?, ?, ?)",
                     ("00000000-0000-0000-0000-000000000003",
                      json.dumps(["epub", "/tmp/c.epub", "/tmp/c.html"]),
                      "failed", 5, 3))
        conn.commit()

        # Stub _get_read_only_conn to return a context manager wrapping our conn.
        class _ConnCtx:
            def __enter__(self_inner): return conn
            def __exit__(self_inner, *a): return False
        tm._get_read_only_conn = lambda: _ConnCtx()
        tm._payload_for_ui = lambda p: p  # identity (no-op for tests)

        # Bind the methods under test
        tm._get_ui_state_list_background = types.MethodType(
            ChapterQueueManager._get_ui_state_list_background, tm
        )
        tm._fetch_full_ui_state = types.MethodType(
            ChapterQueueManager._fetch_full_ui_state, tm
        )
        tm._fetch_error_histories = types.MethodType(
            ChapterQueueManager._fetch_error_histories, tm
        )
        tm._build_ui_entry = types.MethodType(
            ChapterQueueManager._build_ui_entry, tm
        )
        tm._fetch_partial_ui_state = types.MethodType(
            ChapterQueueManager._fetch_partial_ui_state, tm
        )
        return tm

    def test_full_path_returns_list_with_ui_aliased_statuses(self):
        tm = self._make_stub_with_db()
        snapshot = {"ids": (), "structural": True}
        result = tm._get_ui_state_list_background(snapshot)
        self.assertIn("entries", result)
        self.assertEqual(result["mode"], "full")
        entries = result["entries"]
        # 3 tasks, ordered: in_progress (1), success (4 from completed), error (5 from failed)
        self.assertEqual(len(entries), 3)
        statuses = [entry[1] for entry in entries]
        self.assertEqual(statuses, ["in_progress", "success", "error"])

    def test_full_path_populates_sort_keys(self):
        tm = self._make_stub_with_db()
        snapshot = {"ids": (), "structural": True}
        result = tm._get_ui_state_list_background(snapshot)
        self.assertIn("sort_keys", result)
        sk = result["sort_keys"]
        self.assertEqual(len(sk), 3)
        self.assertEqual(sk["00000000-0000-0000-0000-000000000001"], (10, 1))
        self.assertEqual(sk["00000000-0000-0000-0000-000000000002"], (5, 2))
        self.assertEqual(sk["00000000-0000-0000-0000-000000000003"], (5, 3))

    def test_partial_path_refetches_only_requested_ids(self):
        tm = self._make_stub_with_db()
        # Seed cache from a prior full fetch.
        full = tm._get_ui_state_list_background({"ids": (), "structural": True})
        tm._ui_state_list_cache = full["entries"]
        tm._sort_keys = full["sort_keys"]
        # Mutate the DB: change task 2 from completed -> in_progress.
        with tm._get_read_only_conn() as conn:
            conn.execute("UPDATE tasks SET status = 'in_progress' WHERE task_id = ?",
                         ("00000000-0000-0000-0000-000000000002",))
            conn.commit()

        snapshot = {"ids": ("00000000-0000-0000-0000-000000000002",), "structural": False}
        result = tm._get_ui_state_list_background(snapshot)

        self.assertEqual(result["mode"], "partial")
        # Task 2 in the merged list should now have ui_status 'in_progress'.
        entries_by_id = {str(e[0][0]): e for e in result["entries"]}
        self.assertEqual(entries_by_id["00000000-0000-0000-0000-000000000002"][1], "in_progress")
        # Group 1 (in_progress) entries come before group 5 (error).
        statuses = [(str(e[0][0]), e[1]) for e in result["entries"]]
        in_progress_ids = [tid for tid, s in statuses if s == "in_progress"]
        # task 1 priority 10, task 2 priority 5 → task 1 first.
        self.assertEqual(in_progress_ids,
                         ["00000000-0000-0000-0000-000000000001",
                          "00000000-0000-0000-0000-000000000002"])

    def test_partial_path_returns_structural_retry_when_id_missing(self):
        tm = self._make_stub_with_db()
        full = tm._get_ui_state_list_background({"ids": (), "structural": True})
        tm._ui_state_list_cache = full["entries"]
        tm._sort_keys = full["sort_keys"]
        # Delete a row outside the dirty-tracking path.
        with tm._get_read_only_conn() as conn:
            conn.execute("DELETE FROM tasks WHERE task_id = ?",
                         ("00000000-0000-0000-0000-000000000002",))
            conn.commit()

        snapshot = {"ids": ("00000000-0000-0000-0000-000000000002",), "structural": False}
        result = tm._get_ui_state_list_background(snapshot)

        self.assertEqual(result["mode"], "structural_retry")

    def test_partial_path_resort_matches_full_path_for_aliased_statuses(self):
        """Cover both completed/success and failed/error aliases."""
        tm = self._make_stub_with_db()
        full = tm._get_ui_state_list_background({"ids": (), "structural": True})
        tm._ui_state_list_cache = full["entries"]
        tm._sort_keys = full["sort_keys"]
        # Toggle task 3 (failed/error) -> completed/success.
        with tm._get_read_only_conn() as conn:
            conn.execute("UPDATE tasks SET status = 'completed' WHERE task_id = ?",
                         ("00000000-0000-0000-0000-000000000003",))
            conn.commit()

        partial = tm._get_ui_state_list_background({
            "ids": ("00000000-0000-0000-0000-000000000003",),
            "structural": False,
        })
        # Fresh full fetch from the same db for comparison.
        full2 = tm._get_ui_state_list_background({"ids": (), "structural": True})
        partial_order = [str(e[0][0]) for e in partial["entries"]]
        full_order = [str(e[0][0]) for e in full2["entries"]]
        self.assertEqual(partial_order, full_order,
                         "Partial merge + Python sort must produce the same order as full SQL ORDER BY")


class OnCacheUpdatedTests(unittest.TestCase):
    def _make_stub(self):
        tm = types.SimpleNamespace(
            _ui_state_list_cache=[],
            _sort_keys={},
            _dirty_state_lock=Lock(),
            _dirty_task_ids=set(),
            _structural_dirty=False,
            _is_updating_cache=True,
            _cache_update_worker=None,
            _in_flight_snapshot={"ids": ("a", "b"), "structural": False},
            _update_timer=_TimerStub(),
            _posted_events=[],
        )
        tm._post_event = lambda name, data: tm._posted_events.append((name, data))
        from gemini_translator.core.task_manager import ChapterQueueManager
        tm._on_cache_updated = types.MethodType(ChapterQueueManager._on_cache_updated, tm)
        tm._recover_failed_worker = types.MethodType(ChapterQueueManager._recover_failed_worker, tm)
        tm._restart_timer_if_dirty = types.MethodType(ChapterQueueManager._restart_timer_if_dirty, tm)
        return tm

    def _make_worker(self, result):
        return types.SimpleNamespace(result=result)

    def test_full_success_replaces_cache_and_emits_none_changed_ids(self):
        tm = self._make_stub()
        tm._ui_state_list_cache = [("old",)]
        tm._sort_keys = {"old": (0, 0)}
        new_entries = [(("uuid-x", ("epub",)), "in_progress", {})]
        new_sort_keys = {"x": (10, 1)}
        worker = self._make_worker({"mode": "full", "entries": new_entries, "sort_keys": new_sort_keys})

        tm._on_cache_updated(worker)

        self.assertEqual(tm._ui_state_list_cache, new_entries)
        self.assertEqual(tm._sort_keys, new_sort_keys)
        self.assertEqual(len(tm._posted_events), 1)
        name, data = tm._posted_events[0]
        self.assertEqual(name, "task_state_changed")
        self.assertIsNone(data["changed_ids"])
        self.assertEqual(data["full_state"], new_entries)
        self.assertFalse(tm._is_updating_cache)
        self.assertIsNone(tm._in_flight_snapshot)

    def test_partial_success_emits_changed_ids_excluding_unchanged(self):
        tm = self._make_stub()
        import uuid
        a, b = uuid.UUID("00000000-0000-0000-0000-00000000000a"), uuid.UUID("00000000-0000-0000-0000-00000000000b")
        # Old cache: a=pending, b=in_progress.
        old_entries = [((a, ("epub",)), "pending", {}), ((b, ("epub",)), "in_progress", {})]
        tm._ui_state_list_cache = old_entries
        tm._sort_keys = {str(a): (0, 1), str(b): (0, 2)}
        tm._in_flight_snapshot = {"ids": (str(a), str(b)), "structural": False}

        # New entries from partial fetch: a unchanged, b → success.
        new_entries = [((a, ("epub",)), "pending", {}), ((b, ("epub",)), "success", {})]
        sort_keys_delta = {str(a): (0, 1), str(b): (0, 2)}
        worker = self._make_worker({"mode": "partial", "entries": new_entries,
                                    "sort_keys_delta": sort_keys_delta})

        tm._on_cache_updated(worker)

        self.assertEqual(tm._ui_state_list_cache, new_entries)
        name, data = tm._posted_events[0]
        self.assertEqual(set(data["changed_ids"]), {str(b)},
                         "Only b actually changed; a's entry equals the old one and is excluded")
        self.assertIsInstance(data["changed_ids"], list,
                              "Payload must carry list[str], not set")

    def test_structural_retry_restores_snapshot_and_sets_structural(self):
        tm = self._make_stub()
        tm._in_flight_snapshot = {"ids": ("a", "b"), "structural": False}
        worker = self._make_worker({"mode": "structural_retry"})

        tm._on_cache_updated(worker)

        # Snapshot ids back in the dirty set
        self.assertEqual(tm._dirty_task_ids, {"a", "b"})
        self.assertTrue(tm._structural_dirty)
        # Timer was kicked
        self.assertGreaterEqual(tm._update_timer.start_calls, 1)
        # No event emitted (the data was not refreshed)
        self.assertEqual(tm._posted_events, [])
        # Snapshot cleared
        self.assertIsNone(tm._in_flight_snapshot)
        self.assertFalse(tm._is_updating_cache)

    def test_worker_returns_none_recovers(self):
        tm = self._make_stub()
        tm._in_flight_snapshot = {"ids": ("a",), "structural": False}
        worker = self._make_worker(None)
        tm._on_cache_updated(worker)
        self.assertEqual(tm._dirty_task_ids, {"a"})
        self.assertTrue(tm._structural_dirty)
        self.assertGreaterEqual(tm._update_timer.start_calls, 1)

    def test_worker_returns_error_dict_recovers(self):
        tm = self._make_stub()
        tm._in_flight_snapshot = {"ids": ("a",), "structural": False}
        worker = self._make_worker({"mode": "error", "error": "boom"})
        tm._on_cache_updated(worker)
        self.assertEqual(tm._dirty_task_ids, {"a"})
        self.assertTrue(tm._structural_dirty)

    def test_followup_timer_starts_when_dirty_accumulated_during_worker(self):
        """Silent-drop bug fix: notifications arriving while _is_updating_cache=True
        are buffered in the dirty set. After _on_cache_updated, the timer is
        restarted so the next batch runs."""
        tm = self._make_stub()
        # Simulate: while the worker ran, two more dirty notifications arrived.
        tm._dirty_task_ids = {"new-1", "new-2"}
        tm._in_flight_snapshot = {"ids": ("a",), "structural": False}
        new_entries = [(("uuid-a", ("epub",)), "in_progress", {})]
        worker = self._make_worker({"mode": "partial", "entries": new_entries,
                                    "sort_keys_delta": {"a": (0, 0)}})
        tm._on_cache_updated(worker)
        self.assertGreaterEqual(tm._update_timer.start_calls, 1,
                                "Timer must restart because new dirty ids accumulated during the worker run")


class ThreadHopTests(unittest.TestCase):
    """Verifies QTimer is only started from the main thread."""

    @classmethod
    def setUpClass(cls):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_notify_task_dirty_from_worker_thread_does_not_touch_timer(self):
        import threading
        from PyQt6 import QtCore

        tm = types.SimpleNamespace(
            _dirty_state_lock=Lock(),
            _dirty_task_ids=set(),
            _structural_dirty=False,
        )
        timer = QtCore.QTimer()
        timer.setSingleShot(True)
        tm._update_timer = timer

        emit_recorder = {"calls": 0}
        class _Signal:
            def emit(self_inner):
                emit_recorder["calls"] += 1
        tm._ui_update_requested = _Signal()

        from gemini_translator.core.task_manager import ChapterQueueManager
        tm.notify_task_dirty = types.MethodType(ChapterQueueManager.notify_task_dirty, tm)

        def worker():
            tm.notify_task_dirty("from-worker")

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=2.0)
        self.assertFalse(t.is_alive(), "worker thread should complete quickly")

        # Dirty set was updated under lock — safe across threads.
        self.assertEqual(tm._dirty_task_ids, {"from-worker"})
        # Signal was emitted — main thread will pick this up via queued connection.
        self.assertEqual(emit_recorder["calls"], 1)
        # Critically: QTimer.start() was NOT called from the worker thread.
        self.assertFalse(timer.isActive(),
                         "QTimer must not be started directly from worker thread")


class CallsiteMigrationTests(unittest.TestCase):
    def test_record_failure_marks_only_that_task_dirty(self):
        tm = types.SimpleNamespace(
            _dirty_state_lock=Lock(),
            _dirty_task_ids=set(),
            _structural_dirty=False,
            _ui_update_requested=_SignalStub(),
        )
        from gemini_translator.core.task_manager import ChapterQueueManager
        tm.notify_task_dirty = types.MethodType(ChapterQueueManager.notify_task_dirty, tm)

        tm.notify_task_dirty("00000000-0000-0000-0000-00000000aaaa")

        self.assertEqual(tm._dirty_task_ids, {"00000000-0000-0000-0000-00000000aaaa"})
        self.assertFalse(tm._structural_dirty,
                         "Single-task transitions must NOT set the structural flag")


if __name__ == "__main__":
    unittest.main()
