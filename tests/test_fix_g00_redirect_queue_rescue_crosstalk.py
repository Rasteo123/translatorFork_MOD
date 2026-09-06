"""
Регресс для ui-dialogs-setup/logic/2-redirect-queue-rescue-crosstalk.

_start_parallel_filter_redirect создаёт ВТОРОЙ ChapterQueueManager на ОБЩЕЙ
шине (event_bus=self.bus). ChapterQueueManager.__init__ безусловно
подписывается на топик 'session_finished' этой шины и на любое такое
событие сбрасывает в pending ВСЕ свои in_progress-задачи — без проверки,
чья это сессия завершилась. Поскольку основной task_manager и redirect
task_manager сидят на одной шине, завершение ЛЮБОГО из них сбрасывает
реально работающие задачи ДРУГОГО.

Тест использует настоящие main.EventBus и gemini_translator.core.
ChapterQueueManager (по одному на "основную" и "redirect" очередь, как их
собирает setup.py) и проверяет фикс — три новых метода InitialSetupDialog:
_make_redirect_task_manager_session_finished_filter,
_install_main_task_manager_redirect_guard,
_maybe_uninstall_main_task_manager_redirect_guard.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sqlite3
import time
import unittest
import uuid

from PyQt6 import QtWidgets

from main import EventBus
from gemini_translator.core.task_manager import ChapterQueueManager
from gemini_translator.ui.dialogs.setup import InitialSetupDialog


def _make_queue(bus, tag):
    db_uri = f"file:{tag}_{uuid.uuid4().hex}?mode=memory&cache=shared"
    anchor = sqlite3.connect(db_uri, uri=True, check_same_thread=False)
    anchor.row_factory = sqlite3.Row
    task_manager = ChapterQueueManager(event_bus=bus, db_uri=db_uri, main_connection=anchor)
    return task_manager, anchor


def _wait_for_cleanup_worker(app, task_manager, timeout=5.0):
    """_handle_session_finished_background работает в отдельном QThread —
    дожидаемся его завершения, обрабатывая события (как это делает GUI)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        worker = getattr(task_manager, '_cleanup_worker', None)
        if worker is not None and not worker.isRunning():
            return
        time.sleep(0.02)
    app.processEvents()


def _statuses(task_manager):
    with task_manager._light_read_conn() as conn:
        return [row['status'] for row in conn.execute("SELECT status FROM tasks")]


class _EngineStub:
    def __init__(self, task_manager):
        self.task_manager = task_manager


class _RedirectGuardHarness:
    _make_redirect_task_manager_session_finished_filter = (
        InitialSetupDialog._make_redirect_task_manager_session_finished_filter
    )
    _install_main_task_manager_redirect_guard = (
        InitialSetupDialog._install_main_task_manager_redirect_guard
    )
    _maybe_uninstall_main_task_manager_redirect_guard = (
        InitialSetupDialog._maybe_uninstall_main_task_manager_redirect_guard
    )

    def __init__(self, bus, engine):
        self.bus = bus
        self.engine = engine
        self._auto_filter_parallel_redirect_runs = {}


class RedirectQueueRescueCrosstalkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.bus = EventBus()
        self.main_tm, self.main_anchor = _make_queue(self.bus, "main")
        self.redirect_tm, self.redirect_anchor = _make_queue(self.bus, "redirect")
        self.addCleanup(self.main_anchor.close)
        self.addCleanup(self.redirect_anchor.close)
        self.addCleanup(self.main_tm.deleteLater)
        self.addCleanup(self.redirect_tm.deleteLater)
        # Cleanup-и идут в обратном порядке: сначала штатно гасим фоновый кэш
        # (таймер + работающий TaskDBWorker), и только потом закрываем БД.
        self.addCleanup(self.main_tm.shutdown)
        self.addCleanup(self.redirect_tm.shutdown)

    def test_redirect_finish_does_not_reset_unrelated_main_in_progress_tasks(self):
        """Направление А (главный сценарий бага): финиш redirect-сессии не
        должен сбрасывать реально работающие задачи основной очереди."""
        self.main_tm.set_pending_tasks(
            [("epub", "book.epub", f"Text/ch{i}.xhtml") for i in range(1, 5)]
        )
        for i in range(4):
            self.assertIsNotNone(self.main_tm.get_next_task(f"w{i}"))
        self.assertEqual(_statuses(self.main_tm), ["in_progress"] * 4)

        engine = _EngineStub(self.main_tm)
        harness = _RedirectGuardHarness(self.bus, engine)
        harness._install_main_task_manager_redirect_guard()

        self.bus.emit_event({
            'event': 'session_finished',
            'source': 'TranslationEngine',
            'session_id': 'redirect',
            'data': {
                'reason': 'ok',
                'background_session': True,
                'background_role': 'auto_filter_redirect',
                'background_run_id': 'run-1',
            },
        })
        _wait_for_cleanup_worker(self.app, self.main_tm)

        self.assertEqual(
            _statuses(self.main_tm), ["in_progress"] * 4,
            "Основные in_progress-задачи не должны сбрасываться финишем "
            "фонового redirect-прогона",
        )

        harness._maybe_uninstall_main_task_manager_redirect_guard()

    def test_guard_does_not_block_unrelated_background_session_finish(self):
        """Регресс на находку ревью (blocker): гейт резал ЛЮБОЕ
        'session_finished' с background_session=True, а на той же основной
        очереди (self.engine.task_manager) работает и исправитель
        непереведённого (untranslated_fixer_dialog.py: self.task_manager =
        self.engine.task_manager, background_session=True,
        background_role='untranslated_fixer'). Финиш ЕГО сессии обязан
        по-прежнему спасать реально зависшие in_progress-задачи основной
        очереди — гейт должен резать только background_role ==
        'auto_filter_redirect'."""
        self.main_tm.set_pending_tasks([("epub", "book.epub", "Text/ch1.xhtml")])
        self.assertIsNotNone(self.main_tm.get_next_task("w0"))
        self.assertEqual(_statuses(self.main_tm), ["in_progress"])

        engine = _EngineStub(self.main_tm)
        harness = _RedirectGuardHarness(self.bus, engine)
        harness._install_main_task_manager_redirect_guard()

        self.bus.emit_event({
            'event': 'session_finished',
            'source': 'TranslationEngine',
            'session_id': 'untranslated-fixer',
            'data': {
                'reason': 'ok',
                'background_session': True,
                'background_role': 'untranslated_fixer',
                'background_run_id': 'fixer-run-1',
            },
        })
        _wait_for_cleanup_worker(self.app, self.main_tm)

        self.assertEqual(
            _statuses(self.main_tm), ["pending"],
            "Финиш сессии исправителя непереведённого (background_role != "
            "'auto_filter_redirect') должен по-прежнему спасать зависшие "
            "in_progress-задачи основной очереди — гейт слишком широк, если "
            "это не проходит",
        )

        harness._maybe_uninstall_main_task_manager_redirect_guard()

    def test_main_session_finish_still_rescues_its_own_stuck_tasks(self):
        """Гейт не должен ломать штатное 'спасение' зависших задач при
        настоящем завершении ОСНОВНОЙ сессии."""
        self.main_tm.set_pending_tasks([("epub", "book.epub", "Text/ch1.xhtml")])
        self.assertIsNotNone(self.main_tm.get_next_task("w0"))
        self.assertEqual(_statuses(self.main_tm), ["in_progress"])

        engine = _EngineStub(self.main_tm)
        harness = _RedirectGuardHarness(self.bus, engine)
        harness._install_main_task_manager_redirect_guard()

        self.bus.emit_event({
            'event': 'session_finished',
            'source': 'TranslationEngine',
            'session_id': 'main',
            'data': {'reason': 'ok'},
        })
        _wait_for_cleanup_worker(self.app, self.main_tm)

        self.assertEqual(
            _statuses(self.main_tm), ["pending"],
            "Настоящее завершение основной сессии по-прежнему должно "
            "спасать её собственные зависшие задачи",
        )

        harness._maybe_uninstall_main_task_manager_redirect_guard()

    def test_main_session_finish_does_not_reset_unrelated_redirect_in_progress_task(self):
        """Направление Б: финиш основной сессии не должен сбрасывать задачу,
        которую в этот момент реально переводит параллельный redirect-прогон."""
        self.redirect_tm.set_pending_tasks([("epub", "book.epub", "Text/ch9.xhtml")])
        self.assertIsNotNone(self.redirect_tm.get_next_task("wr0"))
        self.assertEqual(_statuses(self.redirect_tm), ["in_progress"])

        harness = _RedirectGuardHarness(self.bus, _EngineStub(self.main_tm))
        run_filter = harness._make_redirect_task_manager_session_finished_filter(
            self.redirect_tm, "run-1"
        )
        self.bus.unsubscribe('session_finished', self.redirect_tm.on_event)
        self.bus.subscribe('session_finished', run_filter)

        self.bus.emit_event({
            'event': 'session_finished',
            'source': 'TranslationEngine',
            'session_id': 'main',
            'data': {'reason': 'ok'},
        })
        _wait_for_cleanup_worker(self.app, self.redirect_tm)

        self.assertEqual(
            _statuses(self.redirect_tm), ["in_progress"],
            "Завершение основной сессии не должно сбрасывать in_progress "
            "задачу параллельного redirect-прогона",
        )

        # Но финиш СВОЕГО прогона (совпадающий background_run_id) по-прежнему
        # должен доходить до redirect-очереди.
        self.bus.emit_event({
            'event': 'session_finished',
            'source': 'TranslationEngine',
            'session_id': 'redirect',
            'data': {
                'reason': 'ok',
                'background_session': True,
                'background_role': 'auto_filter_redirect',
                'background_run_id': 'run-1',
            },
        })
        _wait_for_cleanup_worker(self.app, self.redirect_tm)

        self.assertEqual(
            _statuses(self.redirect_tm), ["pending"],
            "Финиш СВОЕГО фонового прогона по-прежнему должен спасать его "
            "зависшие задачи",
        )

        self.bus.unsubscribe('session_finished', run_filter)


if __name__ == "__main__":
    unittest.main()
