"""
Тест для root-entry/bugs/2-excepthook-emit-not-liveness-c.

global_excepthook принимал успешную постановку critical_error_requested в
очередь (QueuedConnection) за доказательство отзывчивости GUI-потока и сразу
делал `return`, из-за чего путь "грациозное завершение + аварийный
просмотрщик" был физически недостижим, пока `QApplication.instance()` не
None — даже если событийный цикл на самом деле завис.

Раунд 2 ревью нашёл два новых дефекта в самой правке (см.
scratchpad/inputs/groups/g01.json):

* blocker — `liveness_event.clear()` шёл ПОСЛЕ `dispatcher.emit()`, поэтому
  при очень быстрой (в тот же тик) доставке QueuedConnection-сигнала
  clear() мог затереть уже полученное подтверждение и watchdog ошибочно
  эскалировал бы (os._exit) на живом приложении.
* major — watchdog запускался без single-flight: серия однотипных
  исключений плодит поток watchdog-потоков, и clear() от исключения B мог
  погасить подтверждение, которого ждёт watchdog исключения A.

Тесты гоняют реальный main.global_excepthook, реальный
main._watch_gui_liveness_and_escalate и реальный
main.ApplicationWithContext._show_critical_error на минимальных объектах,
имитирующих интерфейс ApplicationWithContext (реальный threading.Event).
"""
import os
import threading
import types
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets

import main


def _join_watchdog_threads(timeout=3):
    """Ждёт завершения всех текущих watchdog-потоков, чтобы следующий тест
    не унаследовал захваченный ими single-flight guard."""
    for thread in threading.enumerate():
        if thread.name == "excepthook-liveness-watchdog":
            thread.join(timeout=timeout)


class _SignalStub:
    """Как в tests/test_global_excepthook_thread_handoff.py: emit просто
    запоминает сообщение, без реальной доставки через Qt event loop —
    для тестов, которым нужен ручной контроль момента подтверждения."""

    def __init__(self):
        self.messages = []

    def emit(self, message):
        self.messages.append(message)


class _ImmediateDeliverySignalStub:
    """emit() выставляет liveness-событие СИНХРОННО, в этом же вызове —
    имитация наихудшего с точки зрения гонки случая: Qt доставил
    QueuedConnection в тот же тик, что и постановка в очередь (реальный
    _show_critical_error делает это через QueuedConnection-слот). Если
    clear() в global_excepthook выполняется ПОСЛЕ emit(), он затирает уже
    выставленное здесь подтверждение — так проверяется порядок операций."""

    def __init__(self, liveness_event):
        self._liveness_event = liveness_event
        self.messages = []

    def emit(self, message):
        self.messages.append(message)
        self._liveness_event.set()


class _FakeAppWithLivenessTracking:
    """Минимальный объект с интерфейсом реального ApplicationWithContext,
    достаточным для global_excepthook: dispatcher с .emit() и
    _critical_error_delivered — threading.Event, который выставляется
    в момент, когда сигнал "доставлен" (симулирует _show_critical_error)."""

    def __init__(self, immediate_delivery=False):
        self._critical_error_delivered = threading.Event()
        if immediate_delivery:
            self.critical_error_requested = _ImmediateDeliverySignalStub(
                self._critical_error_delivered
            )
        else:
            self.critical_error_requested = _SignalStub()


class ExcepthookLivenessWatchdogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def tearDown(self):
        # Гигиена между тестами: single-flight guard — модульный синглтон,
        # не даём зависшему потоку одного теста испортить следующий.
        _join_watchdog_threads(timeout=3)

    def _raise_and_hook(self, fake_app):
        with patch.object(QtWidgets.QApplication, "instance", return_value=fake_app):
            try:
                raise RuntimeError("watchdog boom")
            except RuntimeError as exc:
                main.global_excepthook(type(exc), exc, exc.__traceback__)

    def test_show_critical_error_sets_liveness_event(self):
        """Проверяет реальное тело ApplicationWithContext._show_critical_error
        (main.py) — именно оно должно выставлять _critical_error_delivered
        в момент доставки QueuedConnection-сигнала; без этого watchdog
        никогда бы не увидел подтверждение и эскалировал бы на живом GUI."""
        obj = types.SimpleNamespace(_critical_error_delivered=threading.Event())

        with patch.object(QtCore.QTimer, "singleShot"):
            main.ApplicationWithContext._show_critical_error(obj, "боевое сообщение")

        self.assertTrue(
            obj._critical_error_delivered.is_set(),
            "_show_critical_error обязан выставлять _critical_error_delivered "
            "сразу при вызове — это и есть доказательство, что событийный "
            "цикл провернулся",
        )

    def test_confirmed_delivery_within_same_tick_does_not_escalate(self):
        """Regression для blocker: если доставка происходит в тот же тик, что
        и emit() (наихудший случай гонки), clear() ДОЛЖЕН был выполниться
        раньше emit() — иначе он затирает уже полученное подтверждение и
        watchdog ошибочно эскалирует на живом приложении. Тест детерминирован
        (без сна и подгонки таймингов): событие уже выставлено ДО того, как
        стартует watchdog-поток."""
        fake_app = _FakeAppWithLivenessTracking(immediate_delivery=True)

        with patch.object(main, "EXCEPTHOOK_LIVENESS_TIMEOUT_SEC", 0.3), \
                patch.object(main, "_escalate_to_emergency_shutdown") as escalate:
            self._raise_and_hook(fake_app)
            _join_watchdog_threads(timeout=3)

        self.assertEqual(len(fake_app.critical_error_requested.messages), 1)
        escalate.assert_not_called()

    def test_unconfirmed_delivery_escalates_after_timeout(self):
        """Если GUI завис и не подтвердил доставку — должна сработать эскалация."""
        fake_app = _FakeAppWithLivenessTracking()

        with patch.object(main, "EXCEPTHOOK_LIVENESS_TIMEOUT_SEC", 0.2), \
                patch.object(main, "_escalate_to_emergency_shutdown") as escalate:
            self._raise_and_hook(fake_app)

            # Событие никогда не выставляется — GUI "завис".
            _join_watchdog_threads(timeout=3)

        self.assertEqual(len(fake_app.critical_error_requested.messages), 1)
        escalate.assert_called_once()
        called_app, called_message = escalate.call_args[0]
        self.assertIs(called_app, fake_app)
        self.assertIn("watchdog boom", called_message)

    def test_single_flight_guard_prevents_concurrent_watchdogs(self):
        """Regression для major: второе исключение, пока watchdog первого ещё
        не решил судьбу GUI, не должно порождать второй watchdog-поток (иначе
        clear() второго исключения гасит подтверждение, которого ждёт
        watchdog первого)."""
        fake_app = _FakeAppWithLivenessTracking()

        with patch.object(main, "EXCEPTHOOK_LIVENESS_TIMEOUT_SEC", 1.0), \
                patch.object(main, "_escalate_to_emergency_shutdown") as escalate:
            with patch.object(QtWidgets.QApplication, "instance", return_value=fake_app):
                try:
                    raise RuntimeError("first boom")
                except RuntimeError as exc:
                    main.global_excepthook(type(exc), exc, exc.__traceback__)

                watchdogs_after_first = [
                    t for t in threading.enumerate()
                    if t.name == "excepthook-liveness-watchdog"
                ]
                self.assertEqual(
                    len(watchdogs_after_first), 1,
                    "Первое исключение должно запустить ровно один watchdog",
                )

                try:
                    raise RuntimeError("second boom")
                except RuntimeError as exc:
                    main.global_excepthook(type(exc), exc, exc.__traceback__)

                watchdogs_after_second = [
                    t for t in threading.enumerate()
                    if t.name == "excepthook-liveness-watchdog"
                ]
                self.assertEqual(
                    len(watchdogs_after_second), 1,
                    "Второе исключение не должно порождать второй "
                    "watchdog-поток, пока первый ещё активен (single-flight)",
                )

                # Подтверждаем доставку — как реально делает
                # _show_critical_error. Это должно закрыть единственный
                # активный watchdog без эскалации.
                fake_app._critical_error_delivered.set()
                _join_watchdog_threads(timeout=3)

        self.assertEqual(len(fake_app.critical_error_requested.messages), 2)
        escalate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
