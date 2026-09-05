"""Регресс на g26/ui-pages-shell/bugs/4-qidian-page-missing-can-leave-.

QidianCreatorPage не переопределяла can_leave(), поэтому уход со страницы
(NavigationController.pop()) не блокировался, пока работал один из её
QThread-воркеров (парсинг Qidian, подготовка AI-описания, генерация обложки,
Rulate-логин/заполнение) — воркер оставался осиротевшим и неотменяемым.
"""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gemini_translator.ui.pages.qidian_creator_page import QidianCreatorPage
from gemini_translator.ui.shell import ShellPage


class _FakeWorker:
    def __init__(self, running: bool):
        self._running = running

    def isRunning(self):
        return self._running


class _CanLeaveHarness:
    """Минимальный харнесс: реальный can_leave, без тяжёлого _build_ui()."""

    can_leave = QidianCreatorPage.can_leave

    def __init__(self, workers):
        self._workers = workers


class QidianCreatorPageCanLeaveTests(unittest.TestCase):
    def test_can_leave_overridden_not_base_shell_page(self):
        # Базовая находка: страница вообще не переопределяла can_leave.
        self.assertIn("can_leave", QidianCreatorPage.__dict__)
        self.assertIsNot(QidianCreatorPage.can_leave, ShellPage.can_leave)

    def test_can_leave_true_when_no_workers_running(self):
        page = _CanLeaveHarness([])
        self.assertTrue(page.can_leave())

    def test_can_leave_true_when_workers_finished(self):
        page = _CanLeaveHarness([_FakeWorker(False), _FakeWorker(False)])
        self.assertTrue(page.can_leave())

    def test_can_leave_false_while_a_worker_is_running(self):
        page = _CanLeaveHarness([_FakeWorker(False), _FakeWorker(True)])

        with patch(
            "gemini_translator.ui.pages.qidian_creator_page.QMessageBox.warning"
        ) as warning:
            self.assertFalse(page.can_leave())

        warning.assert_called_once()


if __name__ == "__main__":
    unittest.main()
