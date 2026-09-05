"""
Регресс для ui-dialogs-validation/runtime/1-validator-thread-terminate-the.

can_leave()/closeEvent() при живом ValidationThread делали
`stop(); if not wait(1000): terminate()` и сразу возвращали True, не дожидаясь
реальной остановки потока. QThread.terminate() над потоком, который в этот
момент исполняет CPU-bound Python-код (BeautifulSoup, re, детектор языка),
может убить поток посреди захваченного GIL/внутреннего мьютекса и намертво
подвесить процесс — этот механизм проверен вторым мнением экспериментально
(repro_terminate_only.py / repro_real_terminate.py в scratchpad).

Замена terminate() на вложенный QEventLoop (первая версия фикса) сама внесла
два новых дефекта, найденных ревью:
1) can_leave()/closeEvent() стали реентрантными — пока крутится вложенный
   цикл, Qt обрабатывает события, и повторный клик "Назад"/повторное закрытие
   окна вызывает их ещё раз поверх ещё не завершившегося первого вызова.
2) Пока крутится вложенный цикл, доставляются очередные сигналы потока
   (result_found/progress_update/analysis_finished) слотам уходящей
   страницы — on_analysis_finished успевает выполниться (снапшот, модальный
   диалог, возможный перезапуск анализа) прямо на странице, которую вот-вот
   уберут.

Ниже — тесты на все три момента: не terminate(), не реентерабельно, сигналы
отключены на время ожидания.

Чтобы тест не мог зависнуть сам (даже если фикс окажется неполным), поток в
стенде — НАСТОЯЩИЙ QThread с боевым методом stop(), но с переопределённым
terminate(), который лишь фиксирует факт вызова и НЕ трогает реальный ОС-поток
(вместо просьбы к ОС снять поток, чреватой зависанием самого тестового
процесса). Это позволяет проверить реальное поведение (что код делает при
таймауте wait(1000)), не воспроизводя сам зависший процесс.
"""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest.mock import patch

from PyQt6 import QtCore
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox

from gemini_translator.ui.dialogs.validation import (
    TranslationValidatorDialog,
    TranslationValidatorPage,
)

_APP = QApplication.instance() or QApplication([])


class _BusyThread(QThread):
    """
    Имитирует ValidationThread на "тяжёлой" главе: флаг _is_running (который
    выставляет боевой stop()) проверяется только МЕЖДУ главами, а не внутри
    одной, поэтому одна "глава" длиннее wait(1000) не реагирует на stop()
    мгновенно — именно так добивается таймаут wait(1000) в бою.
    """

    def __init__(self, busy_seconds: float):
        super().__init__()
        self._is_running = True
        self._busy_seconds = busy_seconds
        self.terminate_called = False
        # Доказываем не только что terminate() не вызван, но и что путь
        # таймаута wait(1000) реально был взят — иначе тест зеленеет по
        # случайности (быстрая машина/короткий busy_seconds), а не потому,
        # что код исправления был выполнен (замечание ревью).
        self.wait_timeout_calls = []  # [(msecs, result), ...]

    def run(self):
        # Единая "глава": флаг не проверяется до её конца, как в бою.
        time.sleep(self._busy_seconds)

    def stop(self):
        self._is_running = False

    def terminate(self):
        # Перехватываем: тест не должен реально убивать поток через ОС —
        # это ровно тот механизм, который по данным ревью вешает процесс.
        self.terminate_called = True

    def wait(self, msecs=None):
        if msecs is None:
            return super().wait()
        result = super().wait(msecs)
        self.wait_timeout_calls.append((msecs, result))
        return result


class _BusyThreadWithSignals(QThread):
    """Как _BusyThread, но с настоящими сигналами ValidationThread — нужен,
    чтобы проверить, что can_leave()/closeEvent() отключают их от слотов
    уходящей страницы ДО входа во вложенный цикл ожидания."""

    result_found = pyqtSignal(dict)
    progress_update = pyqtSignal(str, int, int)
    analysis_finished = pyqtSignal(int, int)

    def __init__(self, busy_seconds: float):
        super().__init__()
        self._busy_seconds = busy_seconds
        self.terminate_called = False

    def run(self):
        time.sleep(self._busy_seconds)
        # Как боевой ValidationThread: analysis_finished эмитится в конце
        # run(), непосредственно перед тем, как Qt сам пришлёт finished.
        self.analysis_finished.emit(1, 0)

    def stop(self):
        pass

    def terminate(self):
        self.terminate_called = True


class _PageStub:
    """Минимальная страница с боевыми именами слотов потока."""

    def __init__(self):
        self.analysis_finished_calls = 0
        self.retry_is_available = True

    def add_result(self, *args, **kwargs):
        pass

    def update_status(self, *args, **kwargs):
        pass

    def on_analysis_finished(self, total_scanned, suspicious_found):
        self.analysis_finished_calls += 1


class _EventStub:
    def __init__(self):
        self.ignored = False
        self.accepted = False

    def ignore(self):
        self.ignored = True

    def accept(self):
        self.accepted = True


def _fake_exec_pick_confirm(msg_box_self):
    for btn in msg_box_self.buttons():
        if "прервать" in btn.text().lower():
            msg_box_self._test_clicked = btn
            return 0
    msg_box_self._test_clicked = None
    return 0


def _fake_clicked_button(msg_box_self):
    return getattr(msg_box_self, "_test_clicked", None)


def test_can_leave_does_not_terminate_thread_and_waits_for_real_finish():
    thread = _BusyThread(busy_seconds=3.0)
    thread.start()
    try:
        page = type("PageStub", (), {})()
        page.analysis_thread = thread

        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            result = TranslationValidatorPage.can_leave(page)

        assert result is True
        # Главное: terminate() никогда не вызывается — только штатное
        # завершение потока.
        assert thread.terminate_called is False, (
            "can_leave не должен звать QThread.terminate() — на CPU-bound "
            "потоке это может намертво подвесить процесс"
        )
        # can_leave обязан вернуться только ПОСЛЕ реальной остановки потока,
        # иначе NavigationController.pop() удалит страницу (и единственную
        # ссылку на поток) во время его работы.
        assert thread.isRunning() is False
        # И это должно было произойти именно через ветку таймаута
        # wait(1000) -> False, а не потому что поток случайно успел сам.
        assert thread.wait_timeout_calls == [(1000, False)], (
            "ожидание должно упереться в таймаут wait(1000) — иначе тест не "
            "проверяет ветку исправления"
        )
    finally:
        thread.wait(5000)


def test_dialog_close_event_does_not_terminate_thread_and_waits_for_real_finish():
    thread = _BusyThread(busy_seconds=3.0)
    thread.start()
    try:
        page_stub = type("PageStub", (), {})()
        page_stub.analysis_thread = thread
        page_stub.retry_is_available = True  # пропускаем ветку меню/выхода

        # QMessageBox(self) внутри closeEvent требует, чтобы self был реальным
        # QWidget (используется как parent), поэтому берём настоящий QDialog,
        # а не произвольный объект.
        dialog_stub = QDialog()
        dialog_stub.page = page_stub

        event = _EventStub()

        with patch.object(QMessageBox, "exec", _fake_exec_pick_confirm), \
             patch.object(QMessageBox, "clickedButton", _fake_clicked_button):
            TranslationValidatorDialog.closeEvent(dialog_stub, event)

        assert event.accepted is True
        assert event.ignored is False
        assert thread.terminate_called is False, (
            "closeEvent не должен звать QThread.terminate() — на CPU-bound "
            "потоке это может намертво подвесить процесс"
        )
        assert thread.isRunning() is False
        assert thread.wait_timeout_calls == [(1000, False)], (
            "ожидание должно упереться в таймаут wait(1000) — иначе тест не "
            "проверяет ветку исправления"
        )
    finally:
        thread.wait(5000)


def test_can_leave_ignores_reentrant_call_while_waiting_for_thread():
    """
    Пока can_leave() крутит вложенный QEventLoop в ожидании завершения потока,
    Qt продолжает обрабатывать события. Повторный клик по "Назад" вызовет
    can_leave() ещё раз — а NavigationController.pop() не защищён от
    повторного входа, поэтому такой реентрантный вызов не должен пройти
    дальше немедленного `return False`.
    """
    thread = _BusyThread(busy_seconds=2.0)
    thread.start()
    try:
        page = type("PageStub", (), {})()
        page.analysis_thread = thread

        reentrant_results = []

        def _reentrant_call():
            reentrant_results.append(TranslationValidatorPage.can_leave(page))

        # Стреляем во время вложенного цикла ожидания: первый wait(1000)
        # блокирует главный поток без обработки событий примерно на 1с,
        # поэтому таймер на 1200мс сработает уже ВНУТРИ вложенного
        # QEventLoop (который крутится с ~1с до ~2с).
        QtCore.QTimer.singleShot(1200, _reentrant_call)

        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            result = TranslationValidatorPage.can_leave(page)

        assert result is True
        assert reentrant_results == [False], (
            "повторный can_leave() во время ожидания завершения потока должен "
            "сразу вернуть False — иначе NavigationController.pop() выполнится "
            "дважды и повредит стек навигации"
        )
    finally:
        thread.wait(5000)


def test_close_event_ignores_reentrant_call_while_waiting_for_thread():
    """Тот же реентрантный сценарий, что и выше, но для closeEvent()."""
    thread = _BusyThread(busy_seconds=2.0)
    thread.start()
    try:
        page_stub = type("PageStub", (), {})()
        page_stub.analysis_thread = thread
        page_stub.retry_is_available = True

        dialog_stub = QDialog()
        dialog_stub.page = page_stub

        reentrant_events = []

        def _reentrant_close():
            evt = _EventStub()
            TranslationValidatorDialog.closeEvent(dialog_stub, evt)
            reentrant_events.append(evt)

        QtCore.QTimer.singleShot(1200, _reentrant_close)

        outer_event = _EventStub()
        with patch.object(QMessageBox, "exec", _fake_exec_pick_confirm), \
             patch.object(QMessageBox, "clickedButton", _fake_clicked_button):
            TranslationValidatorDialog.closeEvent(dialog_stub, outer_event)

        assert outer_event.accepted is True
        assert len(reentrant_events) == 1, "реентрантный closeEvent не сработал в окне ожидания"
        assert reentrant_events[0].ignored is True, (
            "повторный closeEvent() во время ожидания завершения потока должен "
            "быть проигнорирован (event.ignore()), а не пройти всю ветку заново"
        )
        assert reentrant_events[0].accepted is False
    finally:
        thread.wait(5000)


def test_can_leave_disconnects_thread_signals_before_waiting():
    """
    Пока can_leave() ждёт завершения потока во вложенном цикле, Qt доставляет
    очередные сигналы потока — analysis_finished, эмитированный в конце run(),
    не должен добраться до on_analysis_finished уходящей страницы (иначе там
    выполнится снапшот/модальный диалог/возможный перезапуск анализа).
    """
    thread = _BusyThreadWithSignals(busy_seconds=1.3)
    page = _PageStub()
    page.analysis_thread = thread
    thread.result_found.connect(page.add_result)
    thread.progress_update.connect(page.update_status)
    thread.analysis_finished.connect(page.on_analysis_finished)

    thread.start()
    try:
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            result = TranslationValidatorPage.can_leave(page)

        assert result is True
        assert page.analysis_finished_calls == 0, (
            "on_analysis_finished не должен выполняться на уходящей странице, "
            "пока can_leave ждёт завершения потока"
        )
    finally:
        thread.wait(5000)


def test_close_event_disconnects_thread_signals_before_waiting():
    """Тот же сценарий отключения сигналов, но для closeEvent()."""
    thread = _BusyThreadWithSignals(busy_seconds=1.3)
    page_stub = _PageStub()
    page_stub.analysis_thread = thread
    thread.result_found.connect(page_stub.add_result)
    thread.progress_update.connect(page_stub.update_status)
    thread.analysis_finished.connect(page_stub.on_analysis_finished)

    dialog_stub = QDialog()
    dialog_stub.page = page_stub

    thread.start()
    try:
        event = _EventStub()
        with patch.object(QMessageBox, "exec", _fake_exec_pick_confirm), \
             patch.object(QMessageBox, "clickedButton", _fake_clicked_button):
            TranslationValidatorDialog.closeEvent(dialog_stub, event)

        assert event.accepted is True
        assert page_stub.analysis_finished_calls == 0, (
            "on_analysis_finished не должен выполняться на уходящей странице, "
            "пока closeEvent ждёт завершения потока"
        )
    finally:
        thread.wait(5000)
