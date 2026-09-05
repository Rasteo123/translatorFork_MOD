"""
Регресс для ui-dialogs-setup/logic/1-redirect-finish-typeerror.

_finish_parallel_filter_redirect_run звал
task_manager._get_ui_state_list_background() без обязательного параметра
snapshot и распаковывал результат как список кортежей (task_info, status,
details), хотя метод либо падает TypeError, либо (при наличии snapshot)
возвращает dict {'mode', 'entries', 'sort_keys'}. try/finally без except
пропускал исключение наружу, и завершение параллельного filter redirect
никогда не отрабатывало (mark_tasks_completed не вызывался, сигнатура не
снималась).

Тест использует настоящий ChapterQueueManager на in-memory SQLite — ровно
так же, как его собирает _start_parallel_filter_redirect в setup.py.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sqlite3
import unittest
import uuid

from PyQt6 import QtWidgets

from gemini_translator.core.task_manager import ChapterQueueManager
from gemini_translator.ui.dialogs.setup import InitialSetupDialog


class _FakeBus:
    """Минимальная шина: subscribe()/emit_event(), как ожидает ChapterQueueManager
    (реальный лог/уведомления нам не нужны — только чтобы конструктор и
    служебные вызовы _log() не падали)."""

    def subscribe(self, event_name, callback):
        pass

    def emit_event(self, event):
        pass


class _FinishRedirectHarness:
    _extract_chapters_from_payload = InitialSetupDialog._extract_chapters_from_payload
    _normalize_auto_chapters = InitialSetupDialog._normalize_auto_chapters
    _compose_auto_details = InitialSetupDialog._compose_auto_details
    _finish_parallel_filter_redirect_run = InitialSetupDialog._finish_parallel_filter_redirect_run
    _maybe_uninstall_main_task_manager_redirect_guard = (
        InitialSetupDialog._maybe_uninstall_main_task_manager_redirect_guard
    )

    def __init__(self, run_id, runner):
        self._auto_filter_parallel_redirect_runs = {run_id: runner}
        self._auto_filter_parallel_redirect_signatures = {runner.get('signature')}
        self.engine = None
        self.auto_log_calls = []

    def _auto_log(self, message, **kwargs):
        self.auto_log_calls.append((message, kwargs))


class FinishParallelFilterRedirectRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _make_task_manager(self):
        db_uri = f"file:test_finish_redirect_{uuid.uuid4().hex}?mode=memory&cache=shared"
        db_anchor = sqlite3.connect(db_uri, uri=True, check_same_thread=False)
        db_anchor.row_factory = sqlite3.Row
        self.addCleanup(db_anchor.close)
        task_manager = ChapterQueueManager(
            event_bus=_FakeBus(), db_uri=db_uri, main_connection=db_anchor
        )
        self.addCleanup(task_manager.deleteLater)
        return task_manager

    def test_finish_reports_success_without_typeerror(self):
        task_manager = self._make_task_manager()
        chapter = "Text/ch1.xhtml"
        task_manager.set_pending_tasks([("epub", "book.epub", chapter)])
        task_info = task_manager.get_next_task("worker-0")
        self.assertIsNotNone(task_info)
        task_manager.task_done("worker-0", task_info)

        signature = (chapter,)
        runner = {
            'signature': signature,
            'chapters': [chapter],
            'source_task_ids': set(),
            'task_manager': task_manager,
            'thread': None,
            'db_anchor': None,
        }
        harness = _FinishRedirectHarness('run-1', runner)

        # До фикса здесь падал TypeError: _get_ui_state_list_background()
        # missing 1 required positional argument: 'snapshot'.
        harness._finish_parallel_filter_redirect_run('run-1')

        self.assertEqual(harness._auto_filter_parallel_redirect_runs, {})
        self.assertTrue(harness.auto_log_calls, "Ожидался хотя бы один вызов _auto_log")
        message, _kwargs = harness.auto_log_calls[-1]
        self.assertIn("завершён", message)
        # На успехе сигнатуру никто не трогает — её снимает
        # _reset_auto_workflow_state в конце авто-раунда.
        self.assertIn(signature, harness._auto_filter_parallel_redirect_signatures)

    def test_finish_handles_backend_errors_without_leaking_signature(self):
        """Если чтение статусов задач падает — сигнатура должна сниматься,
        а не оставаться навечно (иначе _try_auto_filter_recovery/
        _try_auto_filter_redirect_followup будут молча возвращать False)."""
        task_manager = self._make_task_manager()
        chapter = "Text/ch2.xhtml"
        signature = (chapter,)
        runner = {
            'signature': signature,
            'chapters': [chapter],
            'source_task_ids': set(),
            'task_manager': task_manager,
            'thread': None,
            'db_anchor': None,
        }
        harness = _FinishRedirectHarness('run-2', runner)

        # Форсируем сбой на уровне чтения БД (боевой метод класса, не сам
        # проверяемый метод setup.py), чтобы убедиться, что except-ветка
        # снимает сигнатуру и не роняет вызывающий код.
        def _boom():
            raise RuntimeError("БД недоступна")

        # _finish_parallel_filter_redirect_run читает через узкий
        # _light_read_conn() (см. находку про _get_read_only_conn — полный
        # backup БД под приоритетным замком без явного закрытия
        # соединения — тут не нужен), а не через _get_read_only_conn().
        task_manager._light_read_conn = _boom

        harness._finish_parallel_filter_redirect_run('run-2')

        self.assertEqual(harness._auto_filter_parallel_redirect_runs, {})
        self.assertNotIn(signature, harness._auto_filter_parallel_redirect_signatures)


if __name__ == "__main__":
    unittest.main()
