import json
import os
import sqlite3
import tempfile
import unittest
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets

from gemini_translator.api import config as api_config
from gemini_translator.core.chunk_assembler import ChunkAssembler
from gemini_translator.core.task_manager import ChapterQueueManager, tuple_serializer


class _DummyBus(QtCore.QObject):
    event_posted = QtCore.pyqtSignal(dict)


class _TopicBus:
    def __init__(self):
        self.subscriptions = {}

    def subscribe(self, event_name, callback):
        self.subscriptions.setdefault(event_name, []).append(callback)

    def unsubscribe(self, event_name, callback):
        callbacks = self.subscriptions.get(event_name, [])
        if callback in callbacks:
            callbacks.remove(callback)

    def subscriber_count(self):
        return sum(len(callbacks) for callbacks in self.subscriptions.values())


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

    def _task_error_count(self, task_id, error_type):
        with self.task_manager._get_read_only_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM task_errors WHERE task_id = ? AND error_type = ?",
                (task_id, error_type),
            ).fetchone()
        return row[0]

    def _virtual_path_for_real_file(self, real_path):
        normalized_path = os.path.abspath(real_path).replace(":", "_drive").replace("\\", "/")
        if normalized_path.startswith("/"):
            normalized_path = normalized_path[1:]
        return "mem://" + normalized_path

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
                "00000000-0000-0000-0000-000000000001",
                ("epub_chunk", "mem://missing.epub", chapter_path, "<p>source 1</p>", 0, 2, prefix, suffix),
                "<body><p>translated 1</p></body>",
            )
            self._insert_completed_chunk(
                "00000000-0000-0000-0000-000000000002",
                ("epub_chunk", "mem://missing.epub", chapter_path, "<p>source 2</p>", 1, 2, prefix, suffix),
                "<body><p>translated 2</p></body>",
            )

            assembler._assemble_chapter_from_db(["00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"], chapter_path)

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

    def test_assembles_legacy_chunk_payload_from_real_path_when_mem_epub_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_folder = os.path.join(temp_dir, "out")
            os.makedirs(output_folder)
            epub_path = os.path.join(temp_dir, "book.epub")
            chapter_path = "Text/ch.xhtml"
            with zipfile.ZipFile(epub_path, "w") as epub_zip:
                epub_zip.writestr(
                    chapter_path,
                    '<html><body class="chapter"><p>source 1</p><p>source 2</p></body></html>',
                )

            project_manager = _ProjectManagerStub(output_folder)
            assembler = ChunkAssembler(
                output_folder,
                project_manager,
                settings={"use_prettify": False},
            )
            virtual_epub_path = self._virtual_path_for_real_file(epub_path)

            self._insert_completed_chunk(
                "00000000-0000-0000-0000-000000000001",
                ("epub_chunk", virtual_epub_path, chapter_path, "<p>source 1</p>", 0, 2),
                "<body><p>translated 1</p></body>",
            )
            self._insert_completed_chunk(
                "00000000-0000-0000-0000-000000000002",
                ("epub_chunk", virtual_epub_path, chapter_path, "<p>source 2</p>", 1, 2),
                "<body><p>translated 2</p></body>",
            )

            assembler._assemble_chapter_from_db(["00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"], chapter_path)

            output_path = os.path.join(output_folder, "Text", "ch_translated.html")
            self.assertTrue(os.path.exists(output_path))
            with open(output_path, "r", encoding="utf-8") as handle:
                assembled_html = handle.read()
            self.assertEqual(
                assembled_html,
                '<html><body class="chapter"><p>translated 1</p><p>translated 2</p></body></html>',
            )
            self.assertEqual(self._chunk_result_count(), 0)

    def test_failed_assembly_keeps_chunk_results_for_retry(self):
        with tempfile.TemporaryDirectory() as output_folder:
            assembler = ChunkAssembler(
                output_folder,
                _ProjectManagerStub(output_folder),
                settings={"use_prettify": False},
            )
            chapter_path = "Text/ch.xhtml"
            self._insert_completed_chunk(
                "00000000-0000-0000-0000-000000000001",
                ("epub_chunk", "mem://missing.epub", chapter_path, "<p>source</p>", 0, 1),
                "<body><p>translated</p></body>",
            )

            assembler._assemble_chapter_from_db(["00000000-0000-0000-0000-000000000001"], chapter_path)

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
                    ("00000000-0000-0000-0000-000000000001", json.dumps(payload, default=tuple_serializer)),
                )

            assembler._assemble_chapter_from_db(["00000000-0000-0000-0000-000000000001"], chapter_path)

            self.assertEqual(self._task_status("00000000-0000-0000-0000-000000000001"), "pending")

    def test_set_pending_tasks_clears_stale_chunk_results(self):
        chapter_path = "Text/ch.xhtml"
        self._insert_completed_chunk(
            "00000000-0000-0000-0000-000000000001",
            ("epub_chunk", "book.epub", chapter_path, "<p>source</p>", 0, 1, "<body>", "</body>"),
            "<body><p>translated</p></body>",
        )

        self.task_manager.set_pending_tasks([("epub", "book.epub", "Text/next.xhtml")])

        self.assertEqual(self._chunk_result_count(), 0)

    def test_invalid_full_assembly_requeues_chunks_without_writing_output(self):
        with tempfile.TemporaryDirectory() as output_folder:
            assembler = ChunkAssembler(
                output_folder,
                _ProjectManagerStub(output_folder),
                settings={"use_prettify": False},
            )
            chapter_path = "Text/ch.xhtml"
            prefix = "<body>"
            suffix = "</body>"
            source_a = "".join(
                f"<p>Source paragraph {index}. " + ("source text " * 12) + "</p>"
                for index in range(20)
            )
            source_b = "".join(
                f"<p>Source paragraph {index}. " + ("source text " * 12) + "</p>"
                for index in range(20, 40)
            )

            self._insert_completed_chunk(
                "00000000-0000-0000-0000-000000000001",
                ("epub_chunk", "mem://missing.epub", chapter_path, source_a, 0, 2, prefix, suffix),
                "<body><p>\u041f\u0435\u0440\u0435\u0432\u043e\u0434.</p></body>",
            )
            self._insert_completed_chunk(
                "00000000-0000-0000-0000-000000000002",
                ("epub_chunk", "mem://missing.epub", chapter_path, source_b, 1, 2, prefix, suffix),
                "<body><p>\u0424\u0438\u043d\u0430\u043b.</p></body>",
            )

            assembler._assemble_chapter_from_db(
                [
                    "00000000-0000-0000-0000-000000000001",
                    "00000000-0000-0000-0000-000000000002",
                ],
                chapter_path,
            )

            output_path = os.path.join(output_folder, "Text", "ch_translated.html")
            self.assertFalse(os.path.exists(output_path))
            self.assertEqual(self._task_status("00000000-0000-0000-0000-000000000001"), "pending")
            self.assertEqual(self._task_status("00000000-0000-0000-0000-000000000002"), "pending")
            self.assertEqual(
                self._task_error_count(
                    "00000000-0000-0000-0000-000000000001",
                    "ASSEMBLY_VALIDATION",
                ),
                1,
            )

            with self.task_manager._get_write_conn() as conn:
                conn.execute(
                    "UPDATE tasks SET status = 'completed' WHERE task_id IN (?, ?)",
                    (
                        "00000000-0000-0000-0000-000000000001",
                        "00000000-0000-0000-0000-000000000002",
                    ),
                )

            assembler._assemble_chapter_from_db(
                [
                    "00000000-0000-0000-0000-000000000001",
                    "00000000-0000-0000-0000-000000000002",
                ],
                chapter_path,
            )

            self.assertEqual(self._task_status("00000000-0000-0000-0000-000000000001"), "pending")
            self.assertEqual(self._task_status("00000000-0000-0000-0000-000000000002"), "pending")
            self.assertEqual(
                self._task_error_count(
                    "00000000-0000-0000-0000-000000000001",
                    "ASSEMBLY_VALIDATION",
                ),
                2,
            )

    def test_repeated_scans_do_not_queue_duplicate_assemblies(self):
        with tempfile.TemporaryDirectory() as output_folder:
            assembler = ChunkAssembler(
                output_folder,
                _ProjectManagerStub(output_folder),
                settings={"use_prettify": False},
            )
            chapter_path = "Text/ch.xhtml"
            prefix = '<html><body class="chapter">'
            suffix = "</body></html>"
            self._insert_completed_chunk(
                "00000000-0000-0000-0000-000000000001",
                ("epub_chunk", "mem://missing.epub", chapter_path, "<p>source 1</p>", 0, 2, prefix, suffix),
                "<body><p>translated 1</p></body>",
            )
            self._insert_completed_chunk(
                "00000000-0000-0000-0000-000000000002",
                ("epub_chunk", "mem://missing.epub", chapter_path, "<p>source 2</p>", 1, 2, prefix, suffix),
                "<body><p>translated 2</p></body>",
            )

            queued_assemblies = []
            assembler._assemble_chapter_from_db = (
                lambda task_ids, original_path: queued_assemblies.append((tuple(task_ids), original_path))
            )

            assembler.run_final_assembly_check()
            assembler.run_final_assembly_check()
            self.app.processEvents()

            self.assertEqual(
                queued_assemblies,
                [(("00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"), chapter_path)],
            )

    def test_cleanup_unsubscribes_topic_bus_callbacks(self):
        original_bus = self.app.event_bus
        topic_bus = _TopicBus()
        self.app.event_bus = topic_bus
        self.addCleanup(setattr, self.app, "event_bus", original_bus)

        with tempfile.TemporaryDirectory() as output_folder:
            assembler = ChunkAssembler(
                output_folder,
                _ProjectManagerStub(output_folder),
                settings={"use_prettify": False},
            )

            self.assertEqual(topic_bus.subscriber_count(), len(assembler._event_topics))

            assembler.cleanup()
            assembler.cleanup()

            self.assertEqual(topic_bus.subscriber_count(), 0)


if __name__ == "__main__":
    unittest.main()
