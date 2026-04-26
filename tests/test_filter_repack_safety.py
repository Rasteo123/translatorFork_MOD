import os
import sqlite3
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets

from gemini_translator.api import config as api_config
from gemini_translator.core.task_manager import ChapterQueueManager
from gemini_translator.core.worker_helpers.taskers.epub_batch_processor import EpubBatchProcessor
from gemini_translator.scripts.package_filter_tasks import FilterPackagingDialog


class _DummyBus(QtCore.QObject):
    event_posted = QtCore.pyqtSignal(dict)


class _ProjectManagerStub:
    def __init__(self):
        self.registrations = []

    def register_multiple_translations(self, entries_to_add):
        self.registrations.extend(entries_to_add)


class _WorkerStub:
    def __init__(self, output_folder):
        self.output_folder = str(output_folder)
        self.provider_config = {"file_suffix": "_translated.html"}
        self.project_manager = _ProjectManagerStub()
        self.use_prettify = False
        self.events = []

    def _post_event(self, event, payload):
        self.events.append((event, payload))


class FilterRepackSafetyTests(unittest.TestCase):
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

    def test_dilute_payload_marks_green_chapters_as_context_only(self):
        dialog = FilterPackagingDialog(
            filtered_chapters=["Text/ch2.xhtml"],
            successful_chapters=["Text/ch1.xhtml"],
            recommended_size=10_000,
            epub_path="book.epub",
            real_chapter_sizes={
                "Text/ch1.xhtml": 100,
                "Text/ch2.xhtml": 100,
            },
        )
        dialog.chapters_per_batch_spin.setValue(2)

        result = dialog._calculate_new_chapter_list()

        self.assertEqual(result["type"], "payloads")
        payload = result["data"][0]
        self.assertEqual(payload[:3], ("epub_batch", "book.epub", ("Text/ch2.xhtml", "Text/ch1.xhtml")))
        self.assertEqual(payload[3]["save_chapters"], ["Text/ch2.xhtml"])
        self.assertEqual(payload[3]["context_chapters"], ["Text/ch1.xhtml"])

    def test_non_dilute_payloads_still_save_only_filtered_chapters(self):
        dialog = FilterPackagingDialog(
            filtered_chapters=["Text/ch2.xhtml"],
            successful_chapters=["Text/ch1.xhtml", "Text/ch3.xhtml"],
            recommended_size=10_000,
            epub_path="book.epub",
            real_chapter_sizes={
                "Text/ch1.xhtml": 100,
                "Text/ch2.xhtml": 100,
                "Text/ch3.xhtml": 100,
            },
        )
        dialog.dilute_checkbox.setChecked(False)

        result = dialog._calculate_new_chapter_list()

        self.assertEqual(result["type"], "payloads")
        payload = result["data"][0]
        self.assertEqual(payload[:3], (
            "epub_batch",
            "book.epub",
            ("Text/ch1.xhtml", "Text/ch2.xhtml", "Text/ch3.xhtml"),
        ))
        self.assertEqual(payload[3]["save_chapters"], ["Text/ch2.xhtml"])
        self.assertEqual(payload[3]["context_chapters"], ["Text/ch1.xhtml", "Text/ch3.xhtml"])

    def test_batch_processor_does_not_overwrite_context_chapters(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_folder = Path(tmp_dir)
            worker = _WorkerStub(output_folder)
            processor = EpubBatchProcessor(worker)

            successful_paths, save_failed_paths = processor._save_successful_chapters(
                [
                    {
                        "original_path": "Text/ch1.xhtml",
                        "final_html": "<html><body>new green</body></html>",
                    },
                    {
                        "original_path": "Text/ch2.xhtml",
                        "final_html": "<html><body>new filtered</body></html>",
                    },
                ],
                "_translated.html",
                "TEST",
                save_chapter_set={"Text/ch2.xhtml"},
            )

            self.assertEqual(successful_paths, ["Text/ch2.xhtml"])
            self.assertEqual(save_failed_paths, [])
            self.assertFalse((output_folder / "Text" / "ch1_translated.html").exists())
            self.assertEqual(
                (output_folder / "Text" / "ch2_translated.html").read_text(encoding="utf-8"),
                "<html><body>new filtered</body></html>",
            )
            self.assertEqual(len(worker.project_manager.registrations), 1)
            original, suffix, rel_path = worker.project_manager.registrations[0]
            self.assertEqual((original, suffix), ("Text/ch2.xhtml", "_translated.html"))
            self.assertEqual(rel_path.replace("\\", "/"), "Text/ch2_translated.html")

    def test_content_filter_split_keeps_only_save_targets(self):
        manager = ChapterQueueManager(event_bus=self.app.event_bus)
        self.addCleanup(manager.clear_all_queues)
        manager.clear_all_queues()
        manager.set_pending_tasks([
            (
                "epub_batch",
                "book.epub",
                ("Text/ch2.xhtml", "Text/ch1.xhtml"),
                {"save_chapters": ["Text/ch2.xhtml"], "context_chapters": ["Text/ch1.xhtml"]},
            ),
            ("epub", "book.epub", "Text/ch3.xhtml"),
        ])

        task_info = manager.get_next_task("worker-1")
        self.assertTrue(manager.split_in_progress_batch_into_chapters(task_info, worker_id="worker-1"))

        payloads = [tuple(payload) for _task_id, payload in manager.get_all_pending_tasks()]
        self.assertEqual(payloads[0], ("epub", "book.epub", "Text/ch2.xhtml"))
        self.assertNotIn(("epub", "book.epub", "Text/ch1.xhtml"), payloads)
        self.assertIn(("epub", "book.epub", "Text/ch3.xhtml"), payloads)


if __name__ == "__main__":
    unittest.main()
