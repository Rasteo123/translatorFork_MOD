"""RanobeLib: закрытие окна (крестиком или «Вернуться в меню» → self.close()) не должно
молча бросать активный воркер (открытый браузер Playwright, идущую заливку/расписание)
работать в фоне без возможности его остановить.

Регрессия находки ranobelib/bugs/3-return-to-menu-abandons-runnin: closeEvent не
проверял isRunning() ни для одного worker-атрибута и не вызывал stop(). `_return_to_menu`
в реальном приложении в итоге сам зовёт self.close(), поэтому единственная точка,
которая реально перехватывает оба пути (крестик и «В меню»), — closeEvent; фикс сделан
в закреплённом за closeEvent хелпере _confirm_close_running_workers, чтобы его можно
было проверить без полноценной иерархии QWidget.
"""
import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

TESTS_DIR = os.path.dirname(__file__)
PROJECT_ROOT = os.path.dirname(TESTS_DIR)
RANOBELIB_DIR = os.path.join(PROJECT_ROOT, "ranobelib")
if RANOBELIB_DIR not in sys.path:
    sys.path.insert(0, RANOBELIB_DIR)

from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from main_window import RanobeUploaderApp  # noqa: E402

_APP = QApplication.instance() or QApplication([])


class _WorkerStub:
    def __init__(self, running=True):
        self._running = running
        self.stop_calls = 0

    def isRunning(self):
        return self._running

    def stop(self):
        self.stop_calls += 1
        self._running = False


class _RaisingWorkerStub(_WorkerStub):
    """Воркер, у которого stop() кидает исключение — не должен ронять closeEvent."""

    def stop(self):
        self.stop_calls += 1
        raise RuntimeError("boom")


class _NoStopWorkerStub:
    """Воркер без метода stop() — как RanobeLibCatalogMatchWorker/QidianFetchWorker/
    CodexCoverTranslateWorker (ranobelib/workers.py, qidian_rulate/workers.py)."""

    def __init__(self, running=True):
        self._running = running
        self.interrupt_calls = 0

    def isRunning(self):
        return self._running

    def requestInterruption(self):
        self.interrupt_calls += 1


class _CloseWorkersHarness:
    """Реальные _running_workers/_confirm_close_running_workers на лёгком объекте."""

    _running_workers = RanobeUploaderApp._running_workers
    _confirm_close_running_workers = RanobeUploaderApp._confirm_close_running_workers
    _TRACKED_WORKER_ATTRS = RanobeUploaderApp._TRACKED_WORKER_ATTRS

    def __init__(self):
        self.logs = []

    def _append_log(self, level, message):
        self.logs.append((level, message))


class _FakeCloseEvent:
    def __init__(self):
        self.ignored = False
        self.accepted = False

    def ignore(self):
        self.ignored = True

    def accept(self):
        self.accepted = True


class _CloseEventHarness:
    """Реальный closeEvent на лёгком объекте.

    closeEvent содержит `super().closeEvent(event)` — при отказе пользователя от
    закрытия метод возвращается ДО этой строки (event.ignore() + return), поэтому
    ветка отказа проверяется honestly и без проблем. Ветка подтверждения доходит
    до super().closeEvent(event); harness — не экземпляр QWidget, поэтому там
    ожидаемо падает TypeError (super(type, obj): obj must be an instance of
    type) — тест ловит именно это исключение и проверяет, что ДО него closeEvent
    успел остановить воркер и выставить _closing.
    """

    closeEvent = RanobeUploaderApp.closeEvent
    _confirm_close_running_workers = RanobeUploaderApp._confirm_close_running_workers
    _running_workers = RanobeUploaderApp._running_workers
    _TRACKED_WORKER_ATTRS = RanobeUploaderApp._TRACKED_WORKER_ATTRS

    def __init__(self):
        self._closing = False
        self.logs = []

    def _append_log(self, level, message):
        self.logs.append((level, message))


class RanobeLibCloseStopsWorkerTests(unittest.TestCase):
    def test_running_workers_collects_only_actually_running_tracked_attrs(self):
        harness = _CloseWorkersHarness()
        harness.worker = _WorkerStub(running=False)
        harness.login_worker = _WorkerStub(running=True)
        # Атрибуты, инициализированные None (как в __init__ RanobeUploaderApp),
        # не должны падать при проверке.
        harness._media_source_cover_fetch_worker = None

        running = harness._running_workers()

        self.assertEqual(running, [harness.login_worker])

    def test_running_workers_tracks_last_chapter_detector(self):
        """LastChapterDetector (main_window.py:2158, «пропускать залитые») —
        полноценный QThread с сетевым запросом и своим stop() (workers.py:2854),
        но не входил в _TRACKED_WORKER_ATTRS: при закрытии окна во время его
        работы поток оставался жить незамеченным."""
        harness = _CloseWorkersHarness()
        harness._detector = _WorkerStub(running=True)

        running = harness._running_workers()

        self.assertEqual(running, [harness._detector])

    def test_running_workers_empty_when_nothing_tracked(self):
        harness = _CloseWorkersHarness()

        self.assertEqual(harness._running_workers(), [])

    def test_confirm_close_asks_and_stops_running_worker_on_yes(self):
        harness = _CloseWorkersHarness()
        worker = _WorkerStub(running=True)
        harness.worker = worker

        with patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
        ) as mock_q:
            result = harness._confirm_close_running_workers()

        mock_q.assert_called_once()
        self.assertTrue(result)
        self.assertEqual(worker.stop_calls, 1)

    def test_confirm_close_declined_does_not_stop_worker(self):
        harness = _CloseWorkersHarness()
        worker = _WorkerStub(running=True)
        harness.worker = worker

        with patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.No
        ) as mock_q:
            result = harness._confirm_close_running_workers()

        mock_q.assert_called_once()
        self.assertFalse(result)
        self.assertEqual(worker.stop_calls, 0)
        # Воркер должен остаться в работающем состоянии — ничего не остановили.
        self.assertTrue(worker.isRunning())

    def test_confirm_close_without_running_worker_skips_prompt(self):
        harness = _CloseWorkersHarness()
        harness.worker = _WorkerStub(running=False)

        with patch.object(QMessageBox, "question") as mock_q:
            result = harness._confirm_close_running_workers()

        mock_q.assert_not_called()
        self.assertTrue(result)

    def test_confirm_close_survives_worker_stop_raising(self):
        harness = _CloseWorkersHarness()
        harness.worker = _RaisingWorkerStub(running=True)

        with patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
        ):
            result = harness._confirm_close_running_workers()

        self.assertTrue(result)

    def test_confirm_close_logs_warning_and_requests_interruption_when_stop_missing(self):
        """Воркер без stop() (RanobeLibCatalogMatchWorker/QidianFetchWorker/
        CodexCoverTranslateWorker) не должен молча остаться работать: пользователю
        обещали прервать процесс, значит нужно хотя бы попросить Qt-прерывание и
        предупредить в логе, а не просто проглотить AttributeError."""
        harness = _CloseWorkersHarness()
        worker = _NoStopWorkerStub(running=True)
        harness.worker = worker

        with patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
        ):
            result = harness._confirm_close_running_workers()

        self.assertTrue(result)
        self.assertEqual(worker.interrupt_calls, 1)
        self.assertTrue(
            any(level == "WARNING" for level, _ in harness.logs),
            f"ожидалось WARNING в логе, получено: {harness.logs}",
        )

    def test_close_event_ignores_and_keeps_window_when_declined(self):
        """closeEvent обязан реально звать event.ignore() и не выставлять _closing,
        когда пользователь отказался прерывать фоновый воркер — иначе удаление
        всей проверки из closeEvent (регресс находки 3) осталось бы незамеченным."""
        harness = _CloseEventHarness()
        worker = _WorkerStub(running=True)
        harness.worker = worker
        event = _FakeCloseEvent()

        with patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.No
        ):
            harness.closeEvent(event)

        self.assertTrue(event.ignored)
        self.assertFalse(harness._closing)
        self.assertEqual(worker.stop_calls, 0)
        self.assertTrue(worker.isRunning())

    def test_close_event_stops_worker_and_proceeds_when_confirmed(self):
        """closeEvent обязан реально остановить воркер и продолжить закрытие
        (выставить _closing), когда пользователь подтвердил прерывание."""
        harness = _CloseEventHarness()
        worker = _WorkerStub(running=True)
        harness.worker = worker
        harness._save_settings = lambda: None
        harness._process_dialogs = {}
        harness._tray_icon = None
        event = _FakeCloseEvent()

        with patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
        ):
            # closeEvent доходит до super().closeEvent(event); harness — не
            # QWidget, поэтому здесь ожидаемо падает TypeError. Это нормально:
            # к этому моменту stop()/_closing уже отработали, что и проверяем.
            with self.assertRaises(TypeError):
                harness.closeEvent(event)

        self.assertEqual(worker.stop_calls, 1)
        self.assertTrue(harness._closing)


if __name__ == "__main__":
    unittest.main()
