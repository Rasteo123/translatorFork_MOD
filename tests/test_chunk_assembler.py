import json
import os
import sqlite3
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets

from gemini_translator.api import config as api_config
from gemini_translator.core.chunk_assembler import ChunkAssembler
from gemini_translator.core.task_manager import ChapterQueueManager, tuple_serializer


class _DummyBus(QtCore.QObject):
    event_posted = QtCore.pyqtSignal(dict)


class _ProjectManagerStub:
    def __init__(self, project_folder):
        self.project_folder = project_folder
        self.registrations = []

    def register_translation(self, original_internal_path, version_suffix, translated_relative_path):
        self.registrations.append(
            (original_internal_path, version_suffix, translated_relative_path)
        )


class ChunkAssemblerTests(unittest.TestCase):
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

    def setUp(self):
        self.task_manager = ChapterQueueManager(event_bus=self.app.event_bus)
        self.app.task_manager = self.task_manager
        with self.task_manager._get_write_conn() as conn:
            conn.execute("DELETE FROM chunk_results")
            conn.execute("DELETE FROM tasks")

    def _insert_completed_chunk(self, task_id, payload, translated_content):
        with self.task_manager._get_write_conn() as conn:
            conn.execute(
                "INSERT INTO tasks (task_id, payload, status, sequence) VALUES (?, ?, 'completed', ?)",
                (task_id, json.dumps(payload, default=tuple_serializer), int(payload[4])),
            )
            conn.execute(
                "INSERT INTO chunk_results (task_id, translated_content, provider_id) VALUES (?, ?, 'test_provider')",
                (task_id, translated_content),
            )

    def _chunk_result_count(self):
        with self.task_manager._get_read_only_conn() as conn:
            row = conn.execute("SELECT COUNT(*) FROM chunk_results").fetchone()
        return row[0]

    def _task_status(self, task_id):
        with self.task_manager._get_read_only_conn() as conn:
            row = conn.execute("SELECT status FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return row["status"] if row else None

    def test_assembles_from_chunk_payload_wrapper_when_mem_epub_is_missing(self):
        with tempfile.TemporaryDirectory() as output_folder:
            project_manager = _ProjectManagerStub(output_folder)
            assembler = ChunkAssembler(
                output_folder,
                project_manager,
                settings={"use_prettify": False},
            )
            chapter_path = "Text/ch.xhtml"
            prefix = '<html><body class="chapter">'
            suffix = "</body></html>"

            self._insert_completed_chunk(
                "chunk-1",
                ("epub_chunk", "mem://missing.epub", chapter_path, "<p>source 1</p>", 0, 2, prefix, suffix),
                "<body><p>translated 1</p></body>",
            )
            self._insert_completed_chunk(
                "chunk-2",
                ("epub_chunk", "mem://missing.epub", chapter_path, "<p>source 2</p>", 1, 2, prefix, suffix),
                "<body><p>translated 2</p></body>",
            )

            assembler._assemble_chapter_from_db(["chunk-1", "chunk-2"], chapter_path)

            output_path = os.path.join(output_folder, "Text", "ch_translated.html")
            self.assertTrue(os.path.exists(output_path))
            with open(output_path, "r", encoding="utf-8") as handle:
                assembled_html = handle.read()
            self.assertEqual(
                assembled_html,
                '<html><body class="chapter"><p>translated 1</p><p>translated 2</p></body></html>',
            )
            self.assertEqual(self._chunk_result_count(), 0)
            self.assertEqual(
                project_manager.registrations,
                [(chapter_path, "_translated.html", os.path.join("Text", "ch_translated.html"))],
            )

    def test_failed_assembly_keeps_chunk_results_for_retry(self):
        with tempfile.TemporaryDirectory() as output_folder:
            assembler = ChunkAssembler(
                output_folder,
                _ProjectManagerStub(output_folder),
                settings={"use_prettify": False},
            )
            chapter_path = "Text/ch.xhtml"
            self._insert_completed_chunk(
                "chunk-1",
                ("epub_chunk", "mem://missing.epub", chapter_path, "<p>source</p>", 0, 1),
                "<body><p>translated</p></body>",
            )

            assembler._assemble_chapter_from_db(["chunk-1"], chapter_path)

            self.assertEqual(self._chunk_result_count(), 1)
            output_path = os.path.join(output_folder, "Text", "ch_translated.html")
            self.assertFalse(os.path.exists(output_path))

    def test_completed_chunk_without_result_is_requeued(self):
        with tempfile.TemporaryDirectory() as output_folder:
            assembler = ChunkAssembler(
                output_folder,
                _ProjectManagerStub(output_folder),
                settings={"use_prettify": False},
            )
            chapter_path = "Text/ch.xhtml"
            payload = ("epub_chunk", "mem://missing.epub", chapter_path, "<p>source</p>", 0, 1)
            with self.task_manager._get_write_conn() as conn:
                conn.execute(
                    "INSERT INTO tasks (task_id, payload, status, sequence) VALUES (?, ?, 'completed', 0)",
                    ("chunk-1", json.dumps(payload, default=tuple_serializer)),
                )

            assembler._assemble_chapter_from_db(["chunk-1"], chapter_path)

            self.assertEqual(self._task_status("chunk-1"), "pending")


if __name__ == "__main__":
    unittest.main()
