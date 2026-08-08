import os
import sqlite3
import tempfile
import unittest
import zipfile
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets

from gemini_translator.api import config as api_config
from gemini_translator.core.task_manager import ChapterQueueManager
from gemini_translator.core.worker_helpers.taskers.epub_chunk_processor import EpubChunkProcessor
from gemini_translator.core.translation_engine import normalize_sequential_parallel_settings
from gemini_translator.core.worker_helpers.prompt_builder import PromptBuilder
from gemini_translator.utils.epub_tools import TASK_SIZE_UNIT_CHARS
from gemini_translator.utils.glossary_tools import TaskPreparer


class _DummyBus(QtCore.QObject):
    event_posted = QtCore.pyqtSignal(dict)


class _FakeProjectManager:
    def __init__(self, project_folder):
        self.project_folder = project_folder

    def get_versions_for_original(self, original_internal_path):
        if original_internal_path == "Text/ch1.xhtml":
            return {"_translated.html": "Text/ch1_translated.html"}
        return {}


class SequentialTranslationTests(unittest.TestCase):
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

    def test_task_preparer_preserves_batching_in_sequential_mode(self):
        settings = {
            "file_path": "book.epub",
            "use_batching": True,
            "chunking": False,
            "sequential_translation": True,
            "task_size_limit": 1000,
        }
        preparer = TaskPreparer(settings, {"Text/ch1.xhtml": 300, "Text/ch2.xhtml": 300})

        tasks = preparer.prepare_tasks(["Text/ch1.xhtml", "Text/ch2.xhtml"])

        self.assertEqual(tasks, [("epub_batch", "book.epub", ("Text/ch1.xhtml", "Text/ch2.xhtml"))])

    def test_task_preparer_caps_chapters_per_batch(self):
        settings = {
            "file_path": "book.epub",
            "use_batching": True,
            "chunking": False,
            "sequential_translation": False,
            "task_size_limit": 1000,
            "max_chapters_per_batch": 2,
        }
        sizes = {
            "Text/ch1.xhtml": 100,
            "Text/ch2.xhtml": 100,
            "Text/ch3.xhtml": 100,
        }
        preparer = TaskPreparer(settings, sizes)

        tasks = preparer.prepare_tasks(["Text/ch1.xhtml", "Text/ch2.xhtml", "Text/ch3.xhtml"])

        self.assertEqual(tasks, [
            ("epub_batch", "book.epub", ("Text/ch1.xhtml", "Text/ch2.xhtml")),
            ("epub", "book.epub", "Text/ch3.xhtml"),
        ])

    def test_task_preparer_caps_chapters_per_sequential_batch(self):
        settings = {
            "file_path": "book.epub",
            "use_batching": True,
            "chunking": False,
            "sequential_translation": True,
            "task_size_limit": 1000,
            "max_chapters_per_batch": 2,
        }
        sizes = {
            "Text/ch1.xhtml": 100,
            "Text/ch2.xhtml": 100,
            "Text/ch3.xhtml": 100,
        }
        preparer = TaskPreparer(settings, sizes)

        tasks = preparer.prepare_tasks(["Text/ch1.xhtml", "Text/ch2.xhtml", "Text/ch3.xhtml"])

        self.assertEqual(tasks, [
            ("epub_batch", "book.epub", ("Text/ch1.xhtml", "Text/ch2.xhtml")),
            ("epub", "book.epub", "Text/ch3.xhtml"),
        ])

    def test_task_preparer_does_not_open_epub_when_chunking_disabled(self):
        settings = {
            "file_path": "/tmp/does-not-need-to-exist.epub",
            "use_batching": False,
            "chunking": False,
            "sequential_translation": False,
            "task_size_limit": 1000,
        }
        preparer = TaskPreparer(settings, {"Text/ch1.xhtml": 300, "Text/ch2.xhtml": 500})

        tasks = preparer.prepare_tasks(["Text/ch1.xhtml", "Text/ch2.xhtml"])

        self.assertEqual(tasks, [
            ("epub", "/tmp/does-not-need-to-exist.epub", "Text/ch1.xhtml"),
            ("epub", "/tmp/does-not-need-to-exist.epub", "Text/ch2.xhtml"),
        ])

    def test_task_preparer_does_not_open_epub_when_no_chapters_need_chunking(self):
        settings = {
            "file_path": "/tmp/does-not-need-to-exist.epub",
            "use_batching": False,
            "chunking": True,
            "sequential_translation": False,
            "task_size_limit": 1000,
        }
        preparer = TaskPreparer(settings, {"Text/ch1.xhtml": 300, "Text/ch2.xhtml": 500})

        tasks = preparer.prepare_tasks(["Text/ch1.xhtml", "Text/ch2.xhtml"])

        self.assertEqual(tasks, [
            ("epub", "/tmp/does-not-need-to-exist.epub", "Text/ch1.xhtml"),
            ("epub", "/tmp/does-not-need-to-exist.epub", "Text/ch2.xhtml"),
        ])

    def test_task_preparer_does_not_create_single_chunk_in_individual_mode(self):
        with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as epub_file:
            epub_path = epub_file.name
        self.addCleanup(lambda: os.path.exists(epub_path) and os.remove(epub_path))
        body = "<html><body><p>" + ("word " * 50) + "</p></body></html>"
        with zipfile.ZipFile(epub_path, "w") as epub_zip:
            epub_zip.writestr("Text/ch1.xhtml", body)

        settings = {
            "file_path": epub_path,
            "use_batching": False,
            "chunking": True,
            "sequential_translation": False,
            "task_size_limit": 10,
        }
        preparer = TaskPreparer(settings, {"Text/ch1.xhtml": 999})

        with patch.object(TaskPreparer, "_chunk_target_chars_for_token_limit", return_value=len(body) * 2):
            tasks = preparer.prepare_tasks(["Text/ch1.xhtml"])

        self.assertEqual(tasks, [("epub", epub_path, "Text/ch1.xhtml")])

    def test_task_preparer_does_not_create_single_chunk_in_batch_mode(self):
        with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as epub_file:
            epub_path = epub_file.name
        self.addCleanup(lambda: os.path.exists(epub_path) and os.remove(epub_path))
        body = "<html><body><p>" + ("word " * 50) + "</p></body></html>"
        with zipfile.ZipFile(epub_path, "w") as epub_zip:
            epub_zip.writestr("Text/ch1.xhtml", body)

        settings = {
            "file_path": epub_path,
            "use_batching": True,
            "chunking": True,
            "sequential_translation": False,
            "task_size_limit": 10,
        }
        preparer = TaskPreparer(settings, {"Text/ch1.xhtml": 999})

        with patch.object(TaskPreparer, "_chunk_target_chars_for_token_limit", return_value=len(body) * 2):
            tasks = preparer.prepare_tasks(["Text/ch1.xhtml"])

        self.assertEqual(tasks, [("epub", epub_path, "Text/ch1.xhtml")])

    def test_task_preparer_character_unit_uses_limit_as_chunk_chars(self):
        settings = {
            "file_path": "book.epub",
            "use_batching": False,
            "chunking": True,
            "sequential_translation": False,
            "task_size_limit": 10_000,
            "task_size_unit": TASK_SIZE_UNIT_CHARS,
        }
        preparer = TaskPreparer(settings, {})

        self.assertEqual(preparer._chunk_target_chars_for_token_limit("a" * 4000), 10_000)

    def test_task_preparer_batches_small_chapters_across_large_chapter(self):
        settings = {
            "file_path": "book.epub",
            "use_batching": True,
            "chunking": False,
            "sequential_translation": False,
            "task_size_limit": 1000,
        }
        sizes = {
            "Text/ch1.xhtml": 800,
            "Text/ch2.xhtml": 300,
            "Text/ch3.xhtml": 1500,
            "Text/ch4.xhtml": 300,
            "Text/ch10.xhtml": 300,
        }
        preparer = TaskPreparer(settings, sizes)

        tasks = preparer.prepare_tasks([
            "Text/ch1.xhtml",
            "Text/ch2.xhtml",
            "Text/ch3.xhtml",
            "Text/ch4.xhtml",
            "Text/ch10.xhtml",
        ])

        self.assertEqual(tasks, [
            ("epub", "book.epub", "Text/ch1.xhtml"),
            ("epub_batch", "book.epub", ("Text/ch2.xhtml", "Text/ch4.xhtml", "Text/ch10.xhtml")),
            ("epub", "book.epub", "Text/ch3.xhtml"),
        ])

    def test_task_preparer_chunks_large_chapter_in_batch_mode_when_enabled(self):
        with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as epub_file:
            epub_path = epub_file.name
        self.addCleanup(lambda: os.path.exists(epub_path) and os.remove(epub_path))
        long_body = "<html><body>" + ("one two three. " * 800) + "</body></html>"
        with zipfile.ZipFile(epub_path, "w") as epub_zip:
            epub_zip.writestr("Text/ch2.xhtml", "<html><body><p>Two</p></body></html>")
            epub_zip.writestr("Text/ch3.xhtml", long_body)
            epub_zip.writestr("Text/ch4.xhtml", "<html><body><p>Four</p></body></html>")

        settings = {
            "file_path": epub_path,
            "use_batching": True,
            "chunking": True,
            "sequential_translation": False,
            "task_size_limit": 800,
        }
        preparer = TaskPreparer(settings, {
            "Text/ch2.xhtml": 300,
            "Text/ch3.xhtml": len(long_body),
            "Text/ch4.xhtml": 300,
        })

        tasks = preparer.prepare_tasks(["Text/ch2.xhtml", "Text/ch3.xhtml", "Text/ch4.xhtml"])

        self.assertEqual(tasks[0], ("epub_batch", epub_path, ("Text/ch2.xhtml", "Text/ch4.xhtml")))
        self.assertGreater(len(tasks), 2)
        self.assertTrue(all(task[0] == "epub_chunk" for task in tasks[1:]))
        self.assertTrue(all(task[2] == "Text/ch3.xhtml" for task in tasks[1:]))

    def test_task_preparer_preserves_chunking_in_sequential_mode(self):
        with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as epub_file:
            epub_path = epub_file.name
        self.addCleanup(lambda: os.path.exists(epub_path) and os.remove(epub_path))
        long_body = "<html><body>" + ("one two three. " * 800) + "</body></html>"
        with zipfile.ZipFile(epub_path, "w") as epub_zip:
            epub_zip.writestr("Text/ch1.xhtml", long_body)

        settings = {
            "file_path": epub_path,
            "use_batching": False,
            "chunking": True,
            "sequential_translation": True,
            "task_size_limit": 800,
        }
        preparer = TaskPreparer(settings, {"Text/ch1.xhtml": len(long_body)})

        tasks = preparer.prepare_tasks(["Text/ch1.xhtml"])

        self.assertGreater(len(tasks), 1)
        self.assertTrue(all(task[0] == "epub_chunk" for task in tasks))

    def test_prompt_builder_uses_sequential_prompt_and_previous_translation(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            translated_path = Path(tmp_dir) / "Text" / "ch1_translated.html"
            translated_path.parent.mkdir(parents=True)
            translated_path.write_text("<p>Готовая первая глава.</p>", encoding="utf-8")

            builder = PromptBuilder(
                custom_prompt="CUSTOM {text}",
                context_manager=None,
                use_system_instruction=False,
                sequential_mode=True,
                project_manager=_FakeProjectManager(tmp_dir),
                provider_file_suffix="_translated.html",
                sequential_chapter_order=["Text/ch1.xhtml", "Text/ch2.xhtml"],
            )

            with patch.object(
                api_config,
                "default_sequential_prompt",
                return_value="REF={previous_chapter_reference}\nTEXT={text}",
            ), patch.object(api_config, "internal_prompts", return_value={"translation_output_examples": {}}):
                user_prompt, _, _ = builder._build_with_placeholders(
                    "<p>Current</p>",
                    "",
                    "",
                    previous_chapter_reference=builder._build_previous_chapter_reference(["Text/ch2.xhtml"]),
                )

        self.assertNotIn("CUSTOM", user_prompt)
        self.assertIn("Previous translated chapter: Text/ch1.xhtml", user_prompt)
        self.assertIn("Готовая первая глава.", user_prompt)
        self.assertIn("<p>Current</p>", user_prompt)

    def test_prompt_builder_treats_chain_start_as_no_previous_reference(self):
        builder = PromptBuilder(
            custom_prompt="CUSTOM {text}",
            context_manager=None,
            use_system_instruction=False,
            sequential_mode=True,
            project_manager=None,
            provider_file_suffix="_translated.html",
            sequential_chapter_order=["Text/ch1.xhtml", "Text/ch2.xhtml"],
            sequential_chain_starts=["Text/ch2.xhtml"],
        )

        self.assertEqual(
            builder._build_previous_chapter_reference(["Text/ch2.xhtml"]),
            "NO PREVIOUS TRANSLATED CHAPTER AVAILABLE.",
        )

    def test_task_manager_returns_previous_completed_chunk_translation(self):
        manager = ChapterQueueManager(event_bus=self.app.event_bus)
        self.addCleanup(manager.clear_all_queues)
        manager.clear_all_queues()
        with manager._get_write_conn() as conn:
            conn.execute("DELETE FROM chunk_results")

        chain = [
            ("epub_chunk", "book.epub", "Text/ch1.xhtml", "<p>one</p>", 0, 2, "", ""),
            ("epub_chunk", "book.epub", "Text/ch1.xhtml", "<p>two</p>", 1, 2, "", ""),
        ]
        manager.set_pending_task_chains([chain])

        first_task = manager.get_next_task("worker-1")
        manager.task_done_with_content(
            "worker-1",
            first_task,
            "<body><p>translated chunk one</p></body>",
            "gemini",
        )
        second_task = manager.get_next_task("worker-1")

        self.assertEqual(
            manager.get_completed_previous_chunk_translation(
                second_task[0],
                "Text/ch1.xhtml",
                1,
            ),
            "<body><p>translated chunk one</p></body>",
        )

    def test_epub_chunk_processor_uses_previous_chunk_reference_after_first_chunk(self):
        manager = ChapterQueueManager(event_bus=self.app.event_bus)
        self.addCleanup(manager.clear_all_queues)
        manager.clear_all_queues()
        with manager._get_write_conn() as conn:
            conn.execute("DELETE FROM chunk_results")

        chain = [
            ("epub_chunk", "book.epub", "Text/ch2.xhtml", "<p>one</p>", 0, 2, "", ""),
            ("epub_chunk", "book.epub", "Text/ch2.xhtml", "<p>two</p>", 1, 2, "", ""),
        ]
        manager.set_pending_task_chains([chain])
        first_task = manager.get_next_task("worker-1")
        manager.task_done_with_content(
            "worker-1",
            first_task,
            "<body><p>готовый первый чанк</p></body>",
            "gemini",
        )
        second_task = manager.get_next_task("worker-1")

        prompt_builder = PromptBuilder(
            custom_prompt="CUSTOM {text}",
            context_manager=None,
            use_system_instruction=False,
            sequential_mode=True,
            project_manager=None,
            provider_file_suffix="_translated.html",
            sequential_chapter_order=["Text/ch1.xhtml", "Text/ch2.xhtml"],
        )
        processor = EpubChunkProcessor(SimpleNamespace(
            prompt_builder=prompt_builder,
            task_manager=manager,
        ))

        reference = processor._build_sequential_reference(
            task_info=second_task,
            chapter_path="Text/ch2.xhtml",
            chunk_index=1,
            total_chunks=2,
        )

        self.assertIn("Previous translated chunk: Text/ch2.xhtml [1/2]", reference)
        self.assertIn("готовый первый чанк", reference)
        self.assertNotIn("Previous translated chapter: Text/ch1.xhtml", reference)

    def test_emerger_chunks_inherit_chain_from_parent_task(self):
        """Чанки, созданные emerger'ом взамен главы, наследуют её chain_id и
        занимают последовательные chain_index; хвост цепочки сдвигается."""
        manager = ChapterQueueManager(event_bus=self.app.event_bus)
        self.addCleanup(manager.clear_all_queues)
        manager.clear_all_queues()
        with manager._get_write_conn() as conn:
            conn.execute("DELETE FROM chunk_results")

        manager.set_pending_task_chains([
            [
                ("epub", "book.epub", "Text/ch1.xhtml"),
                ("epub", "book.epub", "Text/ch2.xhtml"),
                ("epub", "book.epub", "Text/ch3.xhtml"),
            ]
        ])

        parent_task = manager.get_next_task("worker-1")
        self.assertEqual(tuple(parent_task[1]), ("epub", "book.epub", "Text/ch1.xhtml"))

        chunks = [
            ("epub_chunk", "book.epub", "Text/ch1.xhtml", "<p>one</p>", 0, 2, "", ""),
            ("epub_chunk", "book.epub", "Text/ch1.xhtml", "<p>two</p>", 1, 2, "", ""),
        ]
        manager.add_priority_tasks(chunks, parent_task_id=parent_task[0])
        manager.task_failed_permanently("worker-1", parent_task)

        with manager._get_read_only_conn() as conn:
            rows = conn.execute(
                "SELECT payload, status, chain_id, chain_index FROM tasks "
                "WHERE status = 'pending' ORDER BY chain_index"
            ).fetchall()
        chain_ids = {row["chain_id"] for row in rows}
        self.assertEqual(len(chain_ids), 1, "Все задачи должны остаться в одной цепочке")
        self.assertIsNotNone(chain_ids.pop(), "Чанки не унаследовали chain_id родителя")
        self.assertEqual(
            [row["chain_index"] for row in rows],
            [0, 1, 2, 3],
            "Чанки должны занять позиции родителя, хвост цепочки — сдвинуться",
        )

        first_chunk = manager.get_next_task("worker-1")
        self.assertEqual(tuple(first_chunk[1]), chunks[0])
        manager.task_done_with_content(
            "worker-1",
            first_chunk,
            "<body><p>перевод один</p></body>",
            "gemini",
        )
        second_chunk = manager.get_next_task("worker-1")
        self.assertEqual(tuple(second_chunk[1]), chunks[1])

        self.assertEqual(
            manager.get_completed_previous_chunk_translation(
                second_chunk[0],
                "Text/ch1.xhtml",
                1,
            ),
            "<body><p>перевод один</p></body>",
        )

    def test_previous_chunk_fallback_without_chain_handles_non_ascii_paths(self):
        """Fallback-поиск предыдущего чанка (без chain_id) должен находить
        главы с не-ASCII путями: в payload они хранятся в \\uXXXX-виде."""
        manager = ChapterQueueManager(event_bus=self.app.event_bus)
        self.addCleanup(manager.clear_all_queues)
        manager.clear_all_queues()
        with manager._get_write_conn() as conn:
            conn.execute("DELETE FROM chunk_results")

        chunks = [
            ("epub_chunk", "book.epub", "Text/глава_1.xhtml", "<p>один</p>", 0, 2, "", ""),
            ("epub_chunk", "book.epub", "Text/глава_1.xhtml", "<p>два</p>", 1, 2, "", ""),
        ]
        manager.add_priority_tasks(chunks)

        first_chunk = manager.get_next_task("worker-1")
        manager.task_done_with_content(
            "worker-1",
            first_chunk,
            "<body><p>перевод один</p></body>",
            "gemini",
        )
        second_chunk = manager.get_next_task("worker-1")

        self.assertEqual(
            manager.get_completed_previous_chunk_translation(
                second_chunk[0],
                "Text/глава_1.xhtml",
                1,
            ),
            "<body><p>перевод один</p></body>",
        )

    def test_task_manager_runs_first_task_of_each_chain_in_parallel_only(self):
        manager = ChapterQueueManager(event_bus=self.app.event_bus)
        self.addCleanup(manager.clear_all_queues)
        manager.clear_all_queues()
        manager.set_pending_task_chains([
            [
                ("epub", "book.epub", "Text/ch1.xhtml"),
                ("epub", "book.epub", "Text/ch2.xhtml"),
            ],
            [
                ("epub", "book.epub", "Text/ch6.xhtml"),
                ("epub", "book.epub", "Text/ch7.xhtml"),
            ],
        ])

        first = manager.get_next_task("worker-1")
        second = manager.get_next_task("worker-2")
        blocked = manager.get_next_task("worker-3")

        self.assertEqual(tuple(first[1]), ("epub", "book.epub", "Text/ch1.xhtml"))
        self.assertEqual(tuple(second[1]), ("epub", "book.epub", "Text/ch6.xhtml"))
        self.assertIsNone(blocked)

        manager.task_done("worker-1", first)
        next_first_chain = manager.get_next_task("worker-3")

        self.assertEqual(tuple(next_first_chain[1]), ("epub", "book.epub", "Text/ch2.xhtml"))

    def test_workascii_sequential_mode_uses_parallel_pages_in_one_worker(self):
        settings = {
            "provider": "workascii_chatgpt",
            "sequential_translation": True,
            "sequential_translation_splits": 3,
            "num_instances": 3,
            "max_concurrent_requests": 1,
        }
        logs = []

        normalize_sequential_parallel_settings(settings, logs.append)

        self.assertEqual(settings["num_instances"], 1)
        self.assertEqual(settings["max_concurrent_requests"], 3)
        self.assertTrue(any("parallel page" in message for message in logs))

    def test_non_workascii_sequential_mode_keeps_one_request_per_worker(self):
        settings = {
            "provider": "gemini",
            "sequential_translation": True,
            "sequential_translation_splits": 3,
            "num_instances": 1,
            "max_concurrent_requests": 4,
        }

        normalize_sequential_parallel_settings(settings)

        self.assertEqual(settings["num_instances"], 3)
        self.assertEqual(settings["max_concurrent_requests"], 1)

    def test_in_progress_filtered_batch_can_be_split_into_chapters(self):
        manager = ChapterQueueManager(event_bus=self.app.event_bus)
        self.addCleanup(manager.clear_all_queues)
        manager.clear_all_queues()
        manager.set_pending_tasks([
            ("epub_batch", "book.epub", ("Text/ch1.xhtml", "Text/ch2.xhtml")),
            ("epub", "book.epub", "Text/ch3.xhtml"),
        ])

        task_info = manager.get_next_task("worker-1")
        self.assertTrue(manager.split_in_progress_batch_into_chapters(task_info, worker_id="worker-1"))

        payloads = [payload for _task_id, payload in manager.get_all_pending_tasks()]
        self.assertEqual(tuple(payloads[0]), ("epub", "book.epub", "Text/ch1.xhtml"))
        self.assertEqual(tuple(payloads[1]), ("epub", "book.epub", "Text/ch2.xhtml"))
        self.assertIn(("epub", "book.epub", "Text/ch3.xhtml"), [tuple(payload) for payload in payloads])


if __name__ == "__main__":
    unittest.main()
