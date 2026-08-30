"""The translation queue must respect unresolved QA risk before dispatching."""

import os
import sqlite3
import unittest
import uuid

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.api import config as api_config
from gemini_translator.core.task_manager import (
    QA_GATE_SCHEMA_VERSION,
    ChapterQueueManager,
    QaQueueOutcome,
)


class _DummyBus:
    def __init__(self):
        self.subscriptions = {}
        self.events = []

    def subscribe(self, name, callback):
        self.subscriptions.setdefault(name, []).append(callback)

    def unsubscribe(self, name, callback):
        callbacks = self.subscriptions.get(name, [])
        if callback in callbacks:
            callbacks.remove(callback)

    def post(self, name, data=None):
        self.events.append((name, data))

    def emit_event(self, event):
        self.events.append((event.get("event"), event.get("data")))

    def get_data(self, key):
        return None


class QaGateQueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app.event_bus = _DummyBus()
        cls.app.main_db_connection = sqlite3.connect(
            api_config.SHARED_DB_URI, uri=True, check_same_thread=False
        )
        cls.app.main_db_connection.row_factory = sqlite3.Row

    def setUp(self):
        self.manager = ChapterQueueManager(event_bus=self.app.event_bus)
        self.app.task_manager = self.manager
        with self.manager._get_write_conn() as conn:
            conn.execute("DELETE FROM tasks")
            conn.execute("DELETE FROM qa_gates")

    def _add_task(self, sequence, *, chain_id=None, chain_index=None, status="pending"):
        task_id = uuid.uuid4()
        with self.manager._get_write_conn() as conn:
            conn.execute(
                "INSERT INTO tasks (task_id, payload, status, sequence, priority,"
                " chain_id, chain_index) VALUES (?, ?, ?, ?, 0, ?, ?)",
                (
                    str(task_id),
                    f'["epub_chapter", "chapter-{sequence}.html"]',
                    status,
                    sequence,
                    chain_id,
                    chain_index,
                ),
            )
        return task_id

    def _status(self, task_id):
        rows = self.manager._execute_light_read(
            "SELECT status FROM tasks WHERE task_id = ?", (str(task_id),)
        )
        return rows[0]["status"] if rows else None

    def test_existing_database_is_migrated_with_qa_gates(self):
        """An older project database must gain the gate table without a rebuild."""
        self.assertTrue(self.manager.schema_has_table("qa_gates"))
        self.assertGreaterEqual(self.manager.schema_version, QA_GATE_SCHEMA_VERSION)

    def test_qa_pending_predecessor_blocks_the_next_chained_task(self):
        """A chapter still in QA must not release its chained successor."""
        first = self._add_task(1, chain_id=7, chain_index=0)
        second = self._add_task(2, chain_id=7, chain_index=1)
        self.manager.update_task(first, new_status="in_progress")

        self.manager.mark_task_qa_pending(first, ["chapter-1"])
        self.assertEqual(self._status(first), "qa_pending")
        self.assertIsNone(self.manager.get_next_task("worker-2"))

        self.manager.resolve_task_qa(first, QaQueueOutcome.completed(["chapter-1"]))
        self.assertEqual(self._status(first), "completed")
        claimed = self.manager.get_next_task("worker-2")
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed[0], second)

    def test_open_high_gate_blocks_every_new_dispatch_in_the_project(self):
        """Unresolved high risk must stop the whole queue, not just one chain."""
        first = self._add_task(1)
        self._add_task(2)
        self.manager.update_task(first, new_status="in_progress")
        self.manager.mark_task_qa_pending(first, ["chapter-1"])

        self.manager.resolve_task_qa(
            first,
            QaQueueOutcome.high_unresolved(["chapter-1"], reason="confirmed omission"),
        )

        self.assertEqual(self._status(first), "qa_blocked")
        self.assertIsNone(self.manager.get_next_task("worker-2"))
        gates = self.manager.get_open_qa_gates()
        self.assertEqual([gate.chapter_id for gate in gates], ["chapter-1"])
        self.assertEqual(gates[0].risk_level, "high")
        self.assertEqual(gates[0].reason, "confirmed omission")

    def test_closing_the_gate_releases_the_queue_again(self):
        """A resolved gate must let translation continue without a restart."""
        first = self._add_task(1)
        second = self._add_task(2)
        self.manager.update_task(first, new_status="in_progress")
        self.manager.mark_task_qa_pending(first, ["chapter-1"])
        self.manager.resolve_task_qa(
            first, QaQueueOutcome.high_unresolved(["chapter-1"], reason="gap")
        )

        self.manager.close_qa_gate(first, "chapter-1", resolution="repaired")

        self.assertEqual(self.manager.get_open_qa_gates(), [])
        claimed = self.manager.get_next_task("worker-1")
        self.assertIsNotNone(claimed)
        self.assertIn(claimed[0], {first, second})

    def test_in_progress_work_is_never_cancelled_by_a_gate(self):
        """An open gate stops new dispatch only; running workers keep their task."""
        running = self._add_task(1)
        blocked = self._add_task(2)
        self.manager.update_task(running, new_status="in_progress")
        self.manager.update_task(blocked, new_status="in_progress")
        self.manager.mark_task_qa_pending(blocked, ["chapter-2"])
        self.manager.resolve_task_qa(
            blocked, QaQueueOutcome.high_unresolved(["chapter-2"], reason="gap")
        )

        self.assertEqual(self._status(running), "in_progress")

    def test_weak_and_deferred_outcomes_never_open_a_gate(self):
        """A weak statistical signal or a QA outage must not stop translating."""
        first = self._add_task(1)
        self._add_task(2)
        self.manager.update_task(first, new_status="in_progress")
        self.manager.mark_task_qa_pending(first, ["chapter-1"])

        self.manager.resolve_task_qa(
            first, QaQueueOutcome.deferred(["chapter-1"], reason="embeddings offline")
        )

        self.assertEqual(self._status(first), "completed")
        self.assertEqual(self.manager.get_open_qa_gates(), [])
        self.assertIsNotNone(self.manager.get_next_task("worker-2"))

    def test_cancelled_outcome_keeps_the_task_resumable(self):
        """A user cancellation must leave QA to be resumed, not lost."""
        first = self._add_task(1)
        self.manager.update_task(first, new_status="in_progress")
        self.manager.mark_task_qa_pending(first, ["chapter-1"])

        self.manager.resolve_task_qa(first, QaQueueOutcome.cancelled(["chapter-1"]))

        self.assertEqual(self._status(first), "qa_pending")
        self.assertEqual(self.manager.get_open_qa_gates(), [])

    def test_every_gate_operation_is_idempotent(self):
        """Repeating a QA decision after a restart must not change the outcome."""
        first = self._add_task(1)
        self.manager.update_task(first, new_status="in_progress")
        self.manager.mark_task_qa_pending(first, ["chapter-1"])
        self.manager.mark_task_qa_pending(first, ["chapter-1"])
        outcome = QaQueueOutcome.high_unresolved(["chapter-1"], reason="gap")
        self.manager.resolve_task_qa(first, outcome)
        self.manager.resolve_task_qa(first, outcome)

        self.assertEqual(len(self.manager.get_open_qa_gates()), 1)

        self.manager.close_qa_gate(first, "chapter-1", resolution="repaired")
        self.manager.close_qa_gate(first, "chapter-1", resolution="repaired")

        self.assertEqual(self.manager.get_open_qa_gates(), [])

    def test_a_session_with_only_blocked_work_is_finished(self):
        """A gate must not deadlock a session that can no longer dispatch."""
        first = self._add_task(1)
        self._add_task(2)
        self.manager.update_task(first, new_status="in_progress")
        self.manager.mark_task_qa_pending(first, ["chapter-1"])
        self.manager.resolve_task_qa(
            first, QaQueueOutcome.high_unresolved(["chapter-1"], reason="gap")
        )

        self.assertTrue(self.manager.is_finished())

    def test_a_session_waiting_for_qa_is_not_finished(self):
        """The session must stay alive while a chapter is still being checked."""
        first = self._add_task(1)
        self.manager.update_task(first, new_status="in_progress")
        self.manager.mark_task_qa_pending(first, ["chapter-1"])

        self.assertFalse(self.manager.is_finished())

    def test_unknown_outcome_kinds_are_refused(self):
        """An unrecognised outcome must never silently complete a task."""
        with self.assertRaises(ValueError):
            QaQueueOutcome("teleported")


if __name__ == "__main__":
    unittest.main()
