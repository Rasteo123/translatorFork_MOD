"""RanobeLib: сбой до начала загрузки не должен выглядеть как успешная загрузка.

Регрессия аудита ranobelib/bugs/1: run() ловил любое исключение вокруг всего
процесса, включая авторизацию до цикла по главам, и завершался со статистикой
(0, 0, 0). _on_upload_finished принимал это за полный успех: стирал resume-состояние
и показывал «Загрузка завершена — OK: 0, Ошибки: 0».
"""
import os
import sys
import unittest
from datetime import datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

TESTS_DIR = os.path.dirname(__file__)
PROJECT_ROOT = os.path.dirname(TESTS_DIR)
RANOBELIB_DIR = os.path.join(PROJECT_ROOT, "ranobelib")
if RANOBELIB_DIR not in sys.path:
    sys.path.insert(0, RANOBELIB_DIR)

from PyQt6.QtWidgets import QApplication  # noqa: E402

import api_upload  # noqa: E402
import workers  # noqa: E402
from api_upload import ApiUploadWorker  # noqa: E402
from main_window import RanobeUploaderApp  # noqa: E402
from models import ChapterData  # noqa: E402
from workers import UploadWorker  # noqa: E402


_APP = QApplication.instance() or QApplication([])


def _chapters(count: int) -> list[ChapterData]:
    return [ChapterData("1", float(i + 1), f"Глава {i + 1}", f"<p>Текст {i + 1}</p>") for i in range(count)]


def _worker(cls, chapters):
    return cls(
        "https://ranobelib.me/ru/book/1--test-book/add-chapter",
        chapters,
        schedule_enabled=False,
        start_time=datetime(2026, 1, 1, 12, 0),
        interval_minutes=10,
        paid_enabled=False,
        price=0,
        force_num=True,
    )


class _FailingAuth:
    def __call__(self, slug):
        raise RuntimeError("Не найдена сохранённая сессия RanobeLib")


class ApiUploadWorkerFatalErrorTests(unittest.TestCase):
    def test_auth_failure_before_the_loop_is_reported_as_fatal(self):
        worker = _worker(ApiUploadWorker, _chapters(3))
        original = api_upload.resolve_api_auth
        api_upload.resolve_api_auth = _FailingAuth()
        try:
            worker.run()
        finally:
            api_upload.resolve_api_auth = original
        self.assertIn("Не найдена сохранённая сессия", worker.fatal_error)
        self.assertEqual((worker._ok, worker._errors, worker._skipped), (0, 0, 0))


class BrowserUploadWorkerFatalErrorTests(unittest.TestCase):
    def test_browser_launch_failure_is_reported_as_fatal(self):
        worker = _worker(UploadWorker, _chapters(2))
        original = workers.sync_playwright

        def broken_playwright():
            raise RuntimeError("Chromium не установлен")

        workers.sync_playwright = broken_playwright
        try:
            worker.run()
        finally:
            workers.sync_playwright = original
        self.assertIn("Chromium не установлен", worker.fatal_error)


class _Settings:
    def __init__(self, values):
        self.values = dict(values)

    def remove(self, key):
        self.values.pop(key, None)


class _Widget:
    def setEnabled(self, value):
        pass

    def setText(self, value):
        pass


class _FinishHarness:
    _on_upload_finished = RanobeUploaderApp._on_upload_finished
    _clear_resume_state = RanobeUploaderApp._clear_resume_state

    def __init__(self, worker):
        self.worker = worker
        self.settings = _Settings({"resume_file": "/books/book.epub", "resume_index": 49, "resume_url": "https://x"})
        self.btn_start = self.btn_stop = self.btn_file = self.lbl_eta = _Widget()
        self._upload_ok = 0
        self._upload_errors = 0
        self.logs = []
        self.notifications = []
        self.finished_dialogs = []

    def _append_log(self, level, message):
        self.logs.append((level, message))

    def _show_notification(self, title, message):
        self.notifications.append(message)

    def _finish_process_dialog(self, kind):
        self.finished_dialogs.append(kind)


class _FakeWorker:
    def __init__(self, fatal_error):
        self.fatal_error = fatal_error
        self.is_running = True
        self._skipped = 0


class FinishHandlerTests(unittest.TestCase):
    def test_fatal_error_keeps_resume_state_and_reports_failure(self):
        harness = _FinishHarness(_FakeWorker("Не найдена сохранённая сессия RanobeLib"))
        harness._on_upload_finished()
        self.assertEqual(harness.settings.values["resume_index"], 49)
        self.assertTrue(any("прервана" in message for message in harness.notifications), harness.notifications)
        self.assertFalse(any(level == "SUCCESS" for level, _ in harness.logs), harness.logs)

    def test_clean_run_still_clears_resume_state(self):
        harness = _FinishHarness(_FakeWorker(""))
        harness._on_upload_finished()
        self.assertNotIn("resume_index", harness.settings.values)
        self.assertTrue(any("завершена" in message for message in harness.notifications))
