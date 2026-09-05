"""Регресс на находку ui-dialogs-epub-consistency/bugs/3-consistency-can-leave-ignores-.

can_leave() у ConsistencyValidatorPage должен спрашивать пользователя перед
уходом со страницы, если фоновый AI-поток (analysis_thread/fix_thread/
single_fix_thread) ещё работает, а on_leave() должен реально дожидаться
завершения потока, а не выходить по короткому фиксированному таймауту, пока
поток жив — иначе NavigationController.pop() поставит страницу на удаление
раньше, чем воркер закончит слать сигналы на уже удалённые виджеты.
"""

import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMessageBox, QStackedWidget

from gemini_translator.ui.dialogs.consistency_checker import ConsistencyValidatorDialog
from gemini_translator.ui.shell import NavigationController, ShellPage


class _StubThread(QObject):
    """Имитирует QThread: isRunning()/wait() без реального фонового потока,
    но с настоящим сигналом finished, который можно дождаться через QEventLoop."""

    finished = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._running = True

    def isRunning(self):
        return self._running

    def isFinished(self):
        return not self._running

    def wait(self, timeout_ms):
        # Как настоящий QThread.wait(): если поток уже не работает — True сразу,
        # иначе просто сообщает, что не дождался (сам по себе не блокирует и не
        # эмулирует реальное время ожидания — ровно как без настоящей нагрузки).
        return not self._running

    def finish_after(self, delay_ms):
        def _finish():
            self._running = False
            self.finished.emit()
        QTimer.singleShot(delay_ms, _finish)


class _CanLeaveHarness:
    can_leave = ConsistencyValidatorDialog.can_leave

    def __init__(self, *, running_attr=None):
        self.pending_fixes = []
        self._running_attr = running_attr

    def _is_thread_running(self, thread_attr):
        return thread_attr == self._running_attr


class _WaitForThreadHarness:
    _wait_for_thread = ConsistencyValidatorDialog._wait_for_thread
    _remember_thread_until_deleted = ConsistencyValidatorDialog._remember_thread_until_deleted
    _forget_pending_thread = ConsistencyValidatorDialog._forget_pending_thread


class _HangingStubThread(QObject):
    """Имитирует поток, который никогда не завершается (завис сетевой запрос
    без собственного таймаута) — finished никогда не эмитится."""

    finished = pyqtSignal()

    def isRunning(self):
        return True

    def isFinished(self):
        return False

    def wait(self, timeout_ms):
        return False


class _AlreadyFinishedRaceThread(QObject):
    """Имитирует узкое окно QThreadPrivate::finish(): isRunning() ещё не
    сброшен, хотя isFinished() уже True (finish() внутри уже отработал).
    finished при этом никогда не эмитится — если код опирается на сигнал
    или на isRunning(), он застрянет в ожидании."""

    finished = pyqtSignal()

    def isRunning(self):
        return True

    def isFinished(self):
        return True

    def wait(self, timeout_ms):
        return False


class _SleepingThread(QThread):
    """Настоящий QThread, который «работает» заданное время — нужен, чтобы
    воспроизвести реентерабельный клик по «Назад» ровно во вложенном цикле
    ожидания _wait_for_thread, а не через мок."""

    def __init__(self, seconds):
        super().__init__()
        self._seconds = seconds

    def run(self):
        time.sleep(self._seconds)


class _NavPage(ShellPage):
    """Минимальная страница шелла, использующая боевые can_leave/on_leave/
    _wait_for_thread ConsistencyValidatorPage поверх настоящего
    NavigationController — воспроизводит гонку из репорта рецензента без
    моков стека навигации."""

    can_leave = ConsistencyValidatorDialog.can_leave
    on_leave = ConsistencyValidatorDialog.on_leave
    _wait_for_thread = ConsistencyValidatorDialog._wait_for_thread
    _is_thread_running = ConsistencyValidatorDialog._is_thread_running
    _remember_thread_until_deleted = ConsistencyValidatorDialog._remember_thread_until_deleted
    _forget_pending_thread = ConsistencyValidatorDialog._forget_pending_thread

    def __init__(self, thread):
        super().__init__()
        self.pending_fixes = []
        self.analysis_thread = thread
        self.fix_thread = None
        self.single_fix_thread = None
        self.engine = SimpleNamespace(cancel=lambda: None)

    def _release_power_inhibitor(self):
        pass


class ConsistencyCanLeaveRunningThreadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_can_leave_asks_before_leaving_with_running_analysis_thread(self):
        harness = _CanLeaveHarness(running_attr="analysis_thread")

        with patch(
            "gemini_translator.ui.dialogs.consistency_checker.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ) as question:
            result = harness.can_leave()

        question.assert_called_once()
        self.assertFalse(result)

    def test_can_leave_allows_leaving_when_user_confirms_running_thread(self):
        harness = _CanLeaveHarness(running_attr="fix_thread")

        with patch(
            "gemini_translator.ui.dialogs.consistency_checker.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            result = harness.can_leave()

        self.assertTrue(result)

    def test_wait_for_thread_blocks_until_thread_actually_finishes(self):
        thread = _StubThread()
        thread.finish_after(200)
        harness = _WaitForThreadHarness()
        harness.worker_thread = thread

        started = time.monotonic()
        harness._wait_for_thread("worker_thread", 50)
        elapsed = time.monotonic() - started

        self.assertFalse(thread.isRunning())
        self.assertGreaterEqual(elapsed, 0.15)

    def test_can_leave_does_not_prompt_when_nothing_is_running(self):
        # Негативный случай из замечания рецензента (minor #4): обычный уход
        # без несохранённых правок и без работающих потоков не должен
        # показывать новый диалог вообще.
        harness = _CanLeaveHarness(running_attr=None)

        with patch(
            "gemini_translator.ui.dialogs.consistency_checker.QMessageBox.question"
        ) as question:
            result = harness.can_leave()

        question.assert_not_called()
        self.assertTrue(result)

    def test_wait_for_thread_gives_up_after_hard_cap_and_blocks_signals(self):
        # Major #2 рецензента: поток, который никогда не завершается
        # (сетевой запрос без таймаута), не должен вешать ожидание навечно —
        # по истечении hard_cap_ms ожидание должно прекратиться, а сигналы
        # потока — быть заглушены, чтобы поздний finished/result_ready не
        # долетел до слотов уже удалённой страницы.
        thread = _HangingStubThread()
        harness = _WaitForThreadHarness()
        harness.worker_thread = thread

        started = time.monotonic()
        harness._wait_for_thread("worker_thread", timeout_ms=10, hard_cap_ms=80)
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 2.0, "ожидание не должно быть безграничным")
        self.assertGreaterEqual(elapsed, 0.07)
        self.assertTrue(thread.isRunning())
        self.assertTrue(thread.signalsBlocked())
        self.assertIn(thread, getattr(harness, "_threads_pending_delete", []))

    def test_wait_for_thread_skips_loop_when_isfinished_races_ahead_of_isrunning(self):
        # Minor #3 рецензента: узкое окно QThreadPrivate::finish(), где
        # isRunning() ещё True, а isFinished() уже True. Сигнал finished в
        # этом стабе никогда не эмитится, поэтому если код полагается на
        # isRunning()/сигнал (а не на isFinished()), он провисит до полного
        # hard_cap_ms. Проверяем, что ожидание короткое, а не равно cap'у.
        thread = _AlreadyFinishedRaceThread()
        harness = _WaitForThreadHarness()
        harness.worker_thread = thread

        started = time.monotonic()
        harness._wait_for_thread("worker_thread", timeout_ms=10, hard_cap_ms=2000)
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.5, "isFinished()=True должен закрывать гонку сразу")

    def test_on_leave_reentrant_pop_is_rejected_while_waiting_for_thread(self):
        # Major #1 рецензента: пока on_leave() ждёт поток во вложенном
        # QEventLoop, повторный вызов NavigationController.pop() (второй
        # клик по «← Назад») не должен реентерабельно входить в pop() —
        # иначе первый pop() догоняет уже удалённую страницу и падает с
        # RuntimeError на shell.py:192, как показано в репорте рецензента.
        stack = QStackedWidget()
        nav = NavigationController(stack)
        home = ShellPage()
        nav.set_home(home)

        # Дольше жёстко зашитого быстрого timeout_ms=1000 в on_leave(), иначе
        # thread.wait(1000) успеет дождаться потока и вложенный QEventLoop
        # (единственное место, где реентерабельный вызов вообще может
        # произойти) просто не будет запущен.
        thread = _SleepingThread(1.2)
        page = _NavPage(thread)
        nav.push(page)
        thread.start()

        reentrant_results = []

        def _attempt_reentrant_pop():
            reentrant_results.append(nav.pop())

        QTimer.singleShot(0, _attempt_reentrant_pop)

        with patch(
            "gemini_translator.ui.dialogs.consistency_checker.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            first_result = nav.pop()

        thread.wait(5000)

        self.assertTrue(first_result)
        self.assertEqual(
            reentrant_results,
            [False],
            "реентерабельный pop() должен быть отклонён, а не выполниться параллельно",
        )
        self.assertEqual(nav.depth, 1)


if __name__ == "__main__":
    unittest.main()
