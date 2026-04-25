import os
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets

from gemini_translator.api import config as api_config
from gemini_translator.core.task_manager import ChapterQueueManager
from gemini_translator.core.worker_helpers.prompt_builder import PromptBuilder
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
