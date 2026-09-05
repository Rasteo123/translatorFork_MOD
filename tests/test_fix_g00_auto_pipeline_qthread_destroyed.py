"""
Регресс для ui-dialogs-setup/runtime/2-auto-pipeline-qthread-destroyed.

Две независимые, но связанные дыры:

1. _prepare_for_close (использует can_leave/pop() NavigationController) не
   проверяет живой _auto_consistency_worker — уход со страницы во время
   AI-проверки согласованности уничтожает работающий QThread(parent=page),
   что в реальном Qt приводит к qFatal('QThread: Destroyed while thread is
   still running') и аварийному завершению процесса.

2. Легаси-обёртка InitialSetupDialog.closeEvent вызывает только
   page._disconnect_event_bus(), но не page.on_leave() — из-за этого
   _shutdown_parallel_filter_redirect_runs() (которая штатно и БЕЗОПАСНО
   останавливает redirect-движки перед их удалением) не отрабатывает при
   закрытии окна через main_translator_only.py.

Тест на (1) использует настоящий QtCore.QThread (реальный запущенный поток,
не мок isRunning()). Тест на (2) вызывает настоящий
InitialSetupDialog.closeEvent на минимальном харнессе с фейковой page,
проверяя фактический порядок вызовов.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time
import unittest
from unittest.mock import patch

from PyQt6 import QtCore

from gemini_translator.ui.dialogs.setup import InitialSetupDialog
from test_setup_settings_persistence import _SetupSettingsHarness, _SettingsManagerStub


class _SleepyThread(QtCore.QThread):
    def run(self):
        time.sleep(0.4)


class PrepareForCloseBlocksOnRunningConsistencyWorkerTests(unittest.TestCase):
    def setUp(self):
        self.harness = _SetupSettingsHarness(settings_manager=_SettingsManagerStub())
        self.harness.is_settings_dirty = False
        self.harness._auto_log = lambda *args, **kwargs: None

    def test_prepare_for_close_blocks_while_worker_is_running(self):
        worker = _SleepyThread()
        self.harness._auto_consistency_worker = worker
        worker.start()
        try:
            self.assertTrue(worker.isRunning())
            self.assertFalse(
                self.harness._prepare_for_close(),
                "Уход со страницы не должен разрешаться, пока AI-consistency "
                "воркер ещё работает — иначе его QThread уничтожится живым",
            )
        finally:
            worker.wait(3000)

        self.assertFalse(worker.isRunning())
        self.assertTrue(
            self.harness._prepare_for_close(),
            "После завершения воркера уход должен снова разрешаться",
        )

    def test_prepare_for_close_allows_leave_without_worker(self):
        self.harness._auto_consistency_worker = None
        self.assertTrue(self.harness._prepare_for_close())


class _FakeCloseEvent:
    def __init__(self):
        self.accepted = False
        self.ignored = False

    def accept(self):
        self.accepted = True

    def ignore(self):
        self.ignored = True


class _FakePage:
    def __init__(self, prepare_result=True):
        self.prepare_result = prepare_result
        self.on_leave_calls = 0
        self.disconnect_calls = 0

    def _prepare_for_close(self):
        return self.prepare_result

    def _disconnect_event_bus(self):
        self.disconnect_calls += 1

    def on_leave(self):
        # Настоящий on_leave делает это же самое плюс
        # _shutdown_parallel_filter_redirect_runs() — здесь достаточно
        # убедиться, что именно ЭТОТ метод вызывается, а не только
        # _disconnect_event_bus() напрямую.
        self.on_leave_calls += 1
        self._disconnect_event_bus()


class _DialogCloseEventHarness:
    closeEvent = InitialSetupDialog.closeEvent

    def __init__(self, page, returning_to_main_menu=False):
        self.page = page
        self._returning_to_main_menu = returning_to_main_menu


class CloseEventCallsOnLeaveTests(unittest.TestCase):
    def test_returning_to_main_menu_branch_calls_on_leave(self):
        page = _FakePage()
        harness = _DialogCloseEventHarness(page, returning_to_main_menu=True)
        event = _FakeCloseEvent()

        with patch("gemini_translator.ui.dialogs.setup.return_to_main_menu") as mock_return:
            harness.closeEvent(event)

        self.assertEqual(
            page.on_leave_calls, 1,
            "closeEvent должен звать page.on_leave() (гасит redirect-движки), "
            "а не только _disconnect_event_bus()",
        )
        self.assertTrue(event.accepted)
        mock_return.assert_called_once()

    def test_prompted_menu_choice_calls_on_leave(self):
        page = _FakePage(prepare_result=True)
        harness = _DialogCloseEventHarness(page, returning_to_main_menu=False)
        event = _FakeCloseEvent()

        with patch("gemini_translator.ui.dialogs.setup.prompt_return_to_menu", return_value="menu"), \
             patch("gemini_translator.ui.dialogs.setup.return_to_main_menu") as mock_return:
            harness.closeEvent(event)

        self.assertEqual(page.on_leave_calls, 1)
        self.assertTrue(event.accepted)
        mock_return.assert_called_once()

    def test_cancel_choice_does_not_call_on_leave(self):
        page = _FakePage()
        harness = _DialogCloseEventHarness(page)
        event = _FakeCloseEvent()

        with patch("gemini_translator.ui.dialogs.setup.prompt_return_to_menu", return_value="cancel"):
            harness.closeEvent(event)

        self.assertEqual(page.on_leave_calls, 0)
        self.assertTrue(event.ignored)

    def test_unsaved_changes_block_close_without_on_leave(self):
        page = _FakePage(prepare_result=False)
        harness = _DialogCloseEventHarness(page)
        event = _FakeCloseEvent()

        with patch("gemini_translator.ui.dialogs.setup.prompt_return_to_menu", return_value="close"):
            harness.closeEvent(event)

        self.assertEqual(page.on_leave_calls, 0)
        self.assertTrue(event.ignored)


if __name__ == "__main__":
    unittest.main()
