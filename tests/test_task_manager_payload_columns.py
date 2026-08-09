"""Индексируемые колонки task_type/chapter_path/chunk_index, производные от payload.

Колонки заменяют запросы вида `payload LIKE '%...%'` (поиск предыдущего чанка,
финальная сборка глав). Они generated (VIRTUAL, json_extract), поэтому обязаны
оставаться синхронными с payload при любом INSERT и при замене payload через
update_task — это и проверяется здесь, включая ALTER-миграцию уже созданной
таблицы старой схемы в рамках того же процесса.
"""

import os
import sqlite3
import unittest
import uuid

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets

from gemini_translator.api import config as api_config
from gemini_translator.core.task_manager import ChapterQueueManager


class _DummyBus(QtCore.QObject):
    event_posted = QtCore.pyqtSignal(dict)


class PayloadColumnsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app.event_bus = _DummyBus()
        cls.app.main_db_connection = sqlite3.connect(
            api_config.SHARED_DB_URI,
            uri=True,
            check_same_thread=False,
        )
        cls.app.main_db_connection.row_factory = sqlite3.Row

    def _make_manager(self):
        manager = ChapterQueueManager(event_bus=self.app.event_bus)
        self.addCleanup(manager.clear_all_queues)
        manager.clear_all_queues()
        return manager

    def test_epub_chunk_payload_derives_all_columns(self):
        manager = self._make_manager()
        manager.add_priority_tasks([
            ("epub_chunk", "book.epub", "Text/глава_1.xhtml", "<p>x</p>", 1, 2, "", ""),
        ])

        with manager._get_read_only_conn() as conn:
            row = conn.execute(
                "SELECT task_type, chapter_path, chunk_index FROM tasks"
            ).fetchone()

        self.assertEqual(row["task_type"], "epub_chunk")
        self.assertEqual(row["chapter_path"], "Text/глава_1.xhtml")
        self.assertEqual(row["chunk_index"], 1)

    def test_non_chunk_payloads_restrict_columns(self):
        manager = self._make_manager()
        manager.add_pending_tasks([
            ("epub", "book.epub", "Text/ch2.xhtml"),
            ("hello_task",),
        ])

        with manager._get_read_only_conn() as conn:
            rows = {
                row["task_type"]: row
                for row in conn.execute(
                    "SELECT task_type, chapter_path, chunk_index FROM tasks"
                ).fetchall()
            }

        self.assertEqual(rows["epub"]["chapter_path"], "Text/ch2.xhtml")
        self.assertIsNone(rows["epub"]["chunk_index"])
        self.assertIsNone(rows["hello_task"]["chapter_path"])
        self.assertIsNone(rows["hello_task"]["chunk_index"])

    def test_columns_follow_payload_replacement(self):
        manager = self._make_manager()
        manager.add_priority_tasks([
            ("epub_chunk", "book.epub", "Text/a.xhtml", "<p>x</p>", 0, 2, "", ""),
        ])
        with manager._get_read_only_conn() as conn:
            task_id = conn.execute("SELECT task_id FROM tasks").fetchone()["task_id"]

        manager.update_task(
            uuid.UUID(task_id),
            new_payload=("epub_chunk", "book.epub", "Text/b.xhtml", "<p>y</p>", 5, 6, "", ""),
        )

        with manager._get_read_only_conn() as conn:
            row = conn.execute(
                "SELECT chapter_path, chunk_index FROM tasks"
            ).fetchone()

        self.assertEqual(row["chapter_path"], "Text/b.xhtml")
        self.assertEqual(row["chunk_index"], 5)

    def test_schema_migration_adds_columns_to_existing_table(self):
        uri = "file:payload_cols_migration?mode=memory&cache=shared"
        seed_conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        seed_conn.row_factory = sqlite3.Row
        self.addCleanup(seed_conn.close)
        with seed_conn:
            seed_conn.execute("""
                CREATE TABLE tasks (
                    task_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL,
                    worker_id TEXT,
                    sequence INTEGER,
                    priority INTEGER DEFAULT 0 NOT NULL,
                    chain_id INTEGER,
                    chain_index INTEGER
                )
            """)
            seed_conn.execute(
                "INSERT INTO tasks (task_id, payload, status, sequence) VALUES (?, ?, ?, ?)",
                (
                    "pre-existing",
                    '["epub_chunk", "book.epub", "Text/old.xhtml", "<p>x</p>", 3, 4, "", ""]',
                    "completed",
                    0,
                ),
            )

        ChapterQueueManager(
            event_bus=self.app.event_bus, db_uri=uri, main_connection=seed_conn
        )

        row = seed_conn.execute(
            "SELECT task_type, chapter_path, chunk_index FROM tasks WHERE task_id = 'pre-existing'"
        ).fetchone()
        self.assertEqual(row["task_type"], "epub_chunk")
        self.assertEqual(row["chapter_path"], "Text/old.xhtml")
        self.assertEqual(row["chunk_index"], 3)

    def test_malformed_payload_row_does_not_break_inserts_or_queries(self):
        """Восстановление сессии вставляет payload из файла на диске: битая
        строка не должна валить INSERT (колонки в индексе!) или SELECT."""
        manager = self._make_manager()
        with manager._get_write_conn() as conn:
            conn.execute(
                "INSERT INTO tasks (task_id, payload, status, sequence) VALUES (?, ?, ?, ?)",
                ("broken", "not-json{", "completed", 0),
            )

        with manager._get_read_only_conn() as conn:
            row = conn.execute(
                "SELECT task_type, chapter_path, chunk_index FROM tasks WHERE task_id = 'broken'"
            ).fetchone()

        self.assertIsNone(row["task_type"])
        self.assertIsNone(row["chapter_path"])
        self.assertIsNone(row["chunk_index"])


if __name__ == "__main__":
    unittest.main()
