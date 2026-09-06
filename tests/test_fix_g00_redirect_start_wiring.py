"""
Регресс для находок ревью по ui-dialogs-setup/logic/2-redirect-queue-rescue-
crosstalk (major #2 и #3 из отчёта ревью).

Прежние тесты (test_fix_g00_redirect_queue_rescue_crosstalk.py) проверяли три
новых хелпера (_make_redirect_task_manager_session_finished_filter,
_install_main_task_manager_redirect_guard,
_maybe_uninstall_main_task_manager_redirect_guard) В ИЗОЛЯЦИИ, вызывая их
напрямую из теста — а не через боевую проводку в
_start_parallel_filter_redirect (setup.py). Если удалить блок кода, который
эти хелперы подключает внутри _start_parallel_filter_redirect, те тесты
остаются зелёными. Здесь гоняется САМ _start_parallel_filter_redirect
(замокан только тяжёлые внешние зависимости: TranslationEngine,
TranslationProjectManager, _build_filter_redirect_payloads) — на настоящих
main.EventBus + gemini_translator.core.task_manager.ChapterQueueManager.

Второй класс тестов — откат при сбое ПОСЛЕ того, как redirect-фильтр уже
подписан на шину и гейт основного task_manager уже установлен (major #2):
до фикса except-ветка только логировала и возвращала False, не отписывая
фильтр и не снимая гейт — утечка подписчика на шине + гейт, который никто
не снимет, потому что run_id так и не попал в
_auto_filter_parallel_redirect_runs.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sqlite3
import unittest
from unittest.mock import patch

from PyQt6 import QtCore, QtWidgets

from main import EventBus
from gemini_translator.core.task_manager import ChapterQueueManager
from gemini_translator.ui.dialogs.setup import InitialSetupDialog


def _make_main_queue(bus):
    db_uri = f"file:main_wiring_{id(bus)}?mode=memory&cache=shared"
    anchor = sqlite3.connect(db_uri, uri=True, check_same_thread=False)
    anchor.row_factory = sqlite3.Row
    task_manager = ChapterQueueManager(event_bus=bus, db_uri=db_uri, main_connection=anchor)
    return task_manager, anchor


def _wait_for_cleanup_worker(app, task_manager, timeout=5.0):
    """_handle_session_finished_background работает в отдельном QThread —
    дожидаемся его завершения, обрабатывая события (как это делает GUI).
    На медленном Windows-раннере поток стартует позже, чем главный поток
    доходит до assert — без ожидания статусы читаются до «спасения»."""
    import time

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


class _StubRedirectEngine(QtCore.QObject):
    """Замена TranslationEngine: реальный QObject (нужен для
    engine.moveToThread(...)/engine_thread.finished.connect(engine.deleteLater)
    в боевом коде), без побочных эффектов запуска перевода."""
    _counter = 0

    def __init__(self, context_manager=None, settings_manager=None, task_manager=None, event_bus=None):
        super().__init__()
        _StubRedirectEngine._counter += 1
        self.engine_id = f"stub-engine-{_StubRedirectEngine._counter}"
        self.task_manager = task_manager


class _BoomingRedirectEngine:
    """Имитирует сбой конструктора TranslationEngine (или любого шага между
    подпиской фильтра/установкой гейта и добавлением run_id в runs)."""

    def __init__(self, *args, **kwargs):
        raise RuntimeError("не удалось поднять TranslationEngine")


class _ParallelRedirectWiringHarness(QtCore.QObject):
    """Гоняет БОЕВОЙ _start_parallel_filter_redirect (и всё, что он реально
    подключает на шине), а не только хелперы вокруг него."""

    _start_parallel_filter_redirect = InitialSetupDialog._start_parallel_filter_redirect
    _make_redirect_task_manager_session_finished_filter = (
        InitialSetupDialog._make_redirect_task_manager_session_finished_filter
    )
    _install_main_task_manager_redirect_guard = (
        InitialSetupDialog._install_main_task_manager_redirect_guard
    )
    _maybe_uninstall_main_task_manager_redirect_guard = (
        InitialSetupDialog._maybe_uninstall_main_task_manager_redirect_guard
    )
    _shutdown_parallel_filter_redirect_runs = InitialSetupDialog._shutdown_parallel_filter_redirect_runs
    _finish_parallel_filter_redirect_run = InitialSetupDialog._finish_parallel_filter_redirect_run
    _normalize_auto_chapters = InitialSetupDialog._normalize_auto_chapters
    _make_auto_chapter_signature = InitialSetupDialog._make_auto_chapter_signature
    _compose_auto_details = InitialSetupDialog._compose_auto_details
    _extract_chapters_from_payload = InitialSetupDialog._extract_chapters_from_payload

    def __init__(self, bus, main_task_manager):
        super().__init__()
        self.bus = bus
        self.engine = _EngineStub(main_task_manager)
        self.selected_file = "C:/book.epub"
        self.output_folder = "/tmp/does-not-need-to-exist"
        self.context_manager = object()
        self.settings_manager = object()
        self._auto_filter_parallel_redirect_runs = {}
        self._auto_filter_parallel_redirect_signatures = set()
        self.logs = []

    def get_settings(self):
        return {}

    def _get_filter_retry_translation_options(self):
        return {}

    def _build_filter_redirect_payloads(self, chapters, settings):
        return [("epub", "book.epub", chapter) for chapter in chapters]

    def _auto_log(self, message, force=False, **kwargs):
        self.logs.append(message)


class RealStartParallelFilterRedirectWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.bus = EventBus()
        self.main_tm, self.main_anchor = _make_main_queue(self.bus)
        self.addCleanup(self.main_anchor.close)
        self.addCleanup(self.main_tm.deleteLater)
        # Cleanup-и идут в обратном порядке: сначала гасим фоновый кэш, потом БД.
        self.addCleanup(self.main_tm.shutdown)
        self.harness = _ParallelRedirectWiringHarness(self.bus, self.main_tm)

    def _start(self, chapters, run_label):
        with patch(
            "gemini_translator.ui.dialogs.setup.TranslationEngine",
            _StubRedirectEngine,
        ), patch(
            "gemini_translator.ui.dialogs.setup.TranslationProjectManager",
            lambda *args, **kwargs: object(),
        ):
            return self.harness._start_parallel_filter_redirect(
                chapters,
                {"enabled": True},
                {"provider": "deepseek", "api_keys": ["k1"], "model": "m1", "model_config": {}},
            )

    def test_start_unsubscribes_main_on_event_and_installs_guard_once(self):
        started_1 = self._start(["Text/ch1.xhtml"], "run-a")
        self.assertTrue(started_1)

        subscribers = self.bus._topic_subscribers.get('session_finished', [])
        self.assertNotIn(
            self.main_tm.on_event, subscribers,
            "Боевой _start_parallel_filter_redirect должен снимать "
            "оригинальный main_task_manager.on_event с шины и заменять его "
            "гейтом — если удалить проводку гейта из метода, оригинал "
            "остался бы подписан напрямую",
        )
        guard = getattr(self.harness, '_main_task_manager_redirect_guard', None)
        self.assertIsNotNone(guard, "Гейт должен быть установлен после первого запуска")

        started_2 = self._start(["Text/ch2.xhtml"], "run-b")
        self.assertTrue(started_2)
        self.assertIs(
            getattr(self.harness, '_main_task_manager_redirect_guard'), guard,
            "Второй параллельный прогон не должен переустанавливать гейт "
            "(условие 'if not self._auto_filter_parallel_redirect_runs')",
        )
        self.assertEqual(len(self.harness._auto_filter_parallel_redirect_runs), 2)

        # Оба redirect-прогона реально сбрасывают СВОИ in_progress-задачи по
        # своему run_id, не задевая друг друга и основную очередь.
        for run_id, runner in list(self.harness._auto_filter_parallel_redirect_runs.items()):
            self.harness._finish_parallel_filter_redirect_run(run_id, 'ok')

        self.assertEqual(
            self.harness._auto_filter_parallel_redirect_runs, {},
            "Оба прогона должны завершиться и уйти из runs",
        )
        subscribers_after = self.bus._topic_subscribers.get('session_finished', [])
        self.assertIn(
            self.main_tm.on_event, subscribers_after,
            "После завершения ВСЕХ параллельных прогонов оригинальный "
            "main_task_manager.on_event должен вернуться на шину",
        )
        self.assertIsNone(
            getattr(self.harness, '_main_task_manager_redirect_guard', None),
            "Гейт должен быть снят, когда прогонов не осталось",
        )
        # Обёртки-фильтры redirect-очередей отписаны — на шине для
        # 'session_finished' не осталось ничего, кроме исходного
        # main_task_manager.on_event.
        self.assertEqual(subscribers_after, [self.main_tm.on_event])

    def test_finished_run_rescues_only_its_own_stuck_tasks_end_to_end(self):
        """Полный сквозной сценарий боевой проводки: параллельный
        redirect-прогон реально держит in_progress-задачу, финиш ЧУЖОЙ
        (основной) сессии её не трогает, а его собственный финиш —
        спасает."""
        started = self._start(["Text/ch9.xhtml"], "run-c")
        self.assertTrue(started)
        run_id, runner = next(iter(self.harness._auto_filter_parallel_redirect_runs.items()))
        redirect_tm = runner['task_manager']
        self.assertIsNotNone(redirect_tm.get_next_task("redirect-worker"))
        self.assertEqual(_statuses(redirect_tm), ["in_progress"])

        self.main_tm.set_pending_tasks([("epub", "book.epub", "Text/ch1.xhtml")])
        self.assertIsNotNone(self.main_tm.get_next_task("main-worker"))
        self.assertEqual(_statuses(self.main_tm), ["in_progress"])

        # Финиш ОСНОВНОЙ сессии не должен сбрасывать in_progress redirect-очереди.
        self.bus.emit_event({
            'event': 'session_finished',
            'source': 'TranslationEngine',
            'session_id': 'main',
            'data': {'reason': 'ok'},
        })
        # Спасение зависших задач ОСНОВНОЙ очереди идёт в её собственном
        # фоновом воркере — ждём именно его, а не воркер redirect-очереди
        # (у той на чужой финиш воркер вообще не стартует).
        _wait_for_cleanup_worker(self.app, self.main_tm)

        self.assertEqual(
            _statuses(redirect_tm), ["in_progress"],
            "Финиш основной сессии не должен сбрасывать задачу параллельного "
            "redirect-прогона",
        )
        self.assertEqual(
            _statuses(self.main_tm), ["pending"],
            "Финиш основной сессии по-прежнему спасает её собственные "
            "зависшие задачи",
        )

        # А финиш СВОЕГО прогона (настоящее событие на шине, с совпадающим
        # background_run_id) — спасает его собственную задачу через
        # _filtered_on_event, подписанный боевым _start_parallel_filter_redirect.
        self.bus.emit_event({
            'event': 'session_finished',
            'source': 'TranslationEngine',
            'session_id': 'redirect',
            'data': {
                'reason': 'ok',
                'background_session': True,
                'background_role': 'auto_filter_redirect',
                'background_run_id': run_id,
            },
        })
        _wait_for_cleanup_worker(self.app, redirect_tm)
        self.assertEqual(
            _statuses(redirect_tm), ["pending"],
            "Финиш СВОЕГО фонового прогона (через боевую подписку "
            "_filtered_on_event) должен спасать его зависшую задачу",
        )

        # _finish_parallel_filter_redirect_run — это отдельная бухгалтерия
        # страницы (лог/подсчёт успехов, снятие подписок и гейта), а не сам
        # механизм "спасения" статусов. Она закрывает db_anchor — единственную
        # связь с in-memory БД redirect-очереди, поэтому статусы после неё уже
        # не читаем. Проверяем только то, что она не падает и корректно
        # убирает бухгалтерию.
        self.harness._finish_parallel_filter_redirect_run(run_id, 'ok')
        self.assertEqual(self.harness._auto_filter_parallel_redirect_runs, {})
        subscribers_after = self.bus._topic_subscribers.get('session_finished', [])
        self.assertEqual(subscribers_after, [self.main_tm.on_event])


class StartParallelFilterRedirectRollbackTests(unittest.TestCase):
    """Major #2: сбой ПОСЛЕ подписки redirect-фильтра и установки гейта не
    должен оставлять их висеть навсегда."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.bus = EventBus()
        self.main_tm, self.main_anchor = _make_main_queue(self.bus)
        self.addCleanup(self.main_anchor.close)
        self.addCleanup(self.main_tm.deleteLater)
        # Cleanup-и идут в обратном порядке: сначала гасим фоновый кэш, потом БД.
        self.addCleanup(self.main_tm.shutdown)
        self.harness = _ParallelRedirectWiringHarness(self.bus, self.main_tm)

    def test_failure_after_guard_install_rolls_back_subscriptions_and_guard(self):
        with patch(
            "gemini_translator.ui.dialogs.setup.TranslationEngine",
            _BoomingRedirectEngine,
        ), patch(
            "gemini_translator.ui.dialogs.setup.TranslationProjectManager",
            lambda *args, **kwargs: object(),
        ):
            started = self.harness._start_parallel_filter_redirect(
                ["Text/ch1.xhtml"],
                {"enabled": True},
                {"provider": "deepseek", "api_keys": ["k1"], "model": "m1", "model_config": {}},
            )

        self.assertFalse(started)
        self.assertEqual(
            self.harness._auto_filter_parallel_redirect_runs, {},
            "Неудачный запуск не должен добавлять run_id в runs",
        )

        subscribers = self.bus._topic_subscribers.get('session_finished', [])
        self.assertEqual(
            subscribers, [self.main_tm.on_event],
            "После отката на шине не должно остаться ни обёртки-фильтра "
            "redirect-очереди (утечка подписчика, держащего живой "
            "ChapterQueueManager), ни гейта основного task_manager — до "
            "фикса except-ветка ничего не отписывала",
        )
        self.assertIsNone(
            getattr(self.harness, '_main_task_manager_redirect_guard', None),
            "Гейт, установленный ЭТИМ неудачным запуском, должен быть снят "
            "в except-ветке, а не висеть до конца жизни страницы",
        )

    def test_second_run_failure_keeps_first_runs_guard_and_gating(self):
        """Если гейт уже стоит из-за ДРУГОГО, реально работающего прогона,
        сбой второго запуска не должен его снимать."""
        with patch(
            "gemini_translator.ui.dialogs.setup.TranslationEngine",
            _StubRedirectEngine,
        ), patch(
            "gemini_translator.ui.dialogs.setup.TranslationProjectManager",
            lambda *args, **kwargs: object(),
        ):
            started_first = self.harness._start_parallel_filter_redirect(
                ["Text/ch1.xhtml"],
                {"enabled": True},
                {"provider": "deepseek", "api_keys": ["k1"], "model": "m1", "model_config": {}},
            )
        self.assertTrue(started_first)
        guard = getattr(self.harness, '_main_task_manager_redirect_guard', None)
        self.assertIsNotNone(guard)

        with patch(
            "gemini_translator.ui.dialogs.setup.TranslationEngine",
            _BoomingRedirectEngine,
        ), patch(
            "gemini_translator.ui.dialogs.setup.TranslationProjectManager",
            lambda *args, **kwargs: object(),
        ):
            started_second = self.harness._start_parallel_filter_redirect(
                ["Text/ch2.xhtml"],
                {"enabled": True},
                {"provider": "deepseek", "api_keys": ["k1"], "model": "m1", "model_config": {}},
            )
        self.assertFalse(started_second)

        self.assertEqual(len(self.harness._auto_filter_parallel_redirect_runs), 1)
        self.assertIs(
            getattr(self.harness, '_main_task_manager_redirect_guard', None), guard,
            "Гейт первого (успешного и всё ещё активного) прогона не должен "
            "сниматься из-за отката ВТОРОГО, неудачного запуска",
        )
        subscribers = self.bus._topic_subscribers.get('session_finished', [])
        self.assertNotIn(self.main_tm.on_event, subscribers)

        # Уборка за собой — гасим оставшийся успешный прогон.
        self.harness._shutdown_parallel_filter_redirect_runs()
        subscribers_after = self.bus._topic_subscribers.get('session_finished', [])
        self.assertEqual(subscribers_after, [self.main_tm.on_event])


if __name__ == "__main__":
    unittest.main()
