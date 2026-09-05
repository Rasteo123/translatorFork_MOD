# -*- coding: utf-8 -*-
"""Регресс-тест на дефект ui-dialogs-validation/runtime/2-ai-translation-page-dead-rejec
и на замечания рецензента к его первому исправлению.

В AITranslationPage метод reject() был определён дважды: второе (безусловное)
определение затирало первое (защищённое, вызывающее _check_can_close). Из-за
этого кнопка «Прервать» во время активной AI-сессии сразу закрывала страницу
(request_back), не останавливая движок и не спрашивая про несохранённые
результаты. Кроме того, can_leave() не был переопределён, поэтому
NavigationController.pop() тоже не вспоминал про активную сессию.

Рецензент указал на новый дефект первого исправления: can_leave()/
_check_can_close() смотрели только на флаг is_session_active, который
выставляется оптимистично в _on_start_stop_clicked() ДО того, как движок
реально подтвердит старт событием session_started. Если движок молча
проигнорировал start_session_requested (translation_engine.py: сессия уже
занята/запускается), это событие никогда не придёт — флаг остаётся True
навсегда, и страница становится невыходимой (ни "Назад", ни "Прервать",
ни закрытие окна). Правильная проверка — на РЕАЛЬНУЮ сессию движка
(engine.session_id), а не только на локальный флаг; при обнаружении такого
"зависшего" запуска состояние должно сбрасываться самостоятельно.
"""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets
from PyQt6.QtCore import pyqtSignal

from gemini_translator.ui.dialogs.validation_dialogs.untranslated_fixer_dialog import AITranslationPage


class _EngineStub:
    """Заглушка app.engine: важен только session_id."""

    def __init__(self, session_id=None):
        self.session_id = session_id


class _RejectHarness(QtWidgets.QWidget):
    """Минимальный харнесс: боевые reject/accept/_check_can_close/can_leave
    привязаны к простому QWidget вместо полноценной AITranslationPage
    (которая требует живой app.event_bus/app.engine и десяток виджетов)."""

    result_ready = pyqtSignal(list)
    finished = pyqtSignal(int)
    request_back = pyqtSignal()

    reject = AITranslationPage.reject
    accept = AITranslationPage.accept
    _check_can_close = AITranslationPage._check_can_close
    _abort_stuck_session_start = AITranslationPage._abort_stuck_session_start
    can_leave = AITranslationPage.can_leave
    on_leave = AITranslationPage.on_leave
    _disconnect_global_events = AITranslationPage._disconnect_global_events
    get_translated_results = AITranslationPage.get_translated_results
    closeEvent = AITranslationPage.closeEvent

    def __init__(self):
        super().__init__()
        self.is_session_active = False
        self.translated_results = []
        self.stop_requested = 0
        self.bus = None
        # По умолчанию — реальной сессии в движке нет (как до первого клика
        # "Начать перевод"). Тесты на "активную" сессию выставляют session_id
        # явно, чтобы отличать её от "зависшего" is_session_active без
        # подтверждения движка.
        self.engine = _EngineStub(session_id=None)
        self.suppress_popups = False
        self._session_run_id = None
        self._owned_session_id = None
        self.restore_calls = 0

    def _on_start_stop_clicked(self):
        # В реальном классе это шлёт manual_stop_requested и переводит
        # кнопку в состояние "Остановка…". Для теста reject()/can_leave()
        # важен только сам факт вызова.
        self.stop_requested += 1

    def _set_ui_active(self, active):
        self.is_session_active = active

    def _restore_preserved_queue(self):
        self.restore_calls += 1


class _FakeCloseEvent:
    def __init__(self):
        self.ignored = False

    def ignore(self):
        self.ignored = True


class DeadRejectRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_reject_during_active_session_stops_engine_instead_of_closing(self):
        h = _RejectHarness()
        h.is_session_active = True
        h.engine.session_id = "real-session-1"
        h.translated_results = ["partial-translation"]
        emitted = []
        h.request_back.connect(lambda: emitted.append('request_back'))
        h.result_ready.connect(lambda r: emitted.append(('result_ready', r)))
        h.finished.connect(lambda code: emitted.append(('finished', code)))

        h.reject()

        self.assertEqual(
            h.stop_requested, 1,
            "reject() во время активной сессии обязан запросить остановку движка",
        )
        self.assertEqual(
            emitted, [],
            "reject() во время активной сессии не должен закрывать страницу "
            "(result_ready/finished/request_back)",
        )

    def test_reject_when_idle_without_results_closes_and_emits_signals(self):
        h = _RejectHarness()

        emitted = []
        h.request_back.connect(lambda: emitted.append('request_back'))
        h.result_ready.connect(lambda r: emitted.append(('result_ready', r)))
        h.finished.connect(lambda code: emitted.append(('finished', code)))

        h.reject()

        self.assertEqual(h.stop_requested, 0)
        self.assertIn('request_back', emitted)
        self.assertIn(('result_ready', []), emitted)
        self.assertIn(('finished', 0), emitted)

    def test_can_leave_vetoes_only_while_session_active(self):
        h = _RejectHarness()
        self.assertTrue(h.can_leave())

        h.is_session_active = True
        h.engine.session_id = "real-session-1"
        self.assertFalse(h.can_leave())

    def test_close_event_ignored_while_session_active(self):
        h = _RejectHarness()
        h.is_session_active = True
        h.engine.session_id = "real-session-1"
        event = _FakeCloseEvent()

        h.closeEvent(event)

        self.assertTrue(event.ignored, "closeEvent обязан отклонить закрытие активной сессии")
        self.assertEqual(h.stop_requested, 1)

    def test_can_leave_does_not_deadlock_when_engine_never_confirmed_session(self):
        """Замечание рецензента (major #1): is_session_active выставляется
        оптимистично ДО ответа движка. Если движок проигнорировал
        start_session_requested (сессия уже занята кем-то другим и потом
        была остановлена), engine.session_id пуст, а session_started для
        нашего _session_run_id не придёт никогда. can_leave() не должен
        вечно вето в этом случае — иначе кнопка "Назад" перестаёт работать
        навсегда."""
        h = _RejectHarness()
        h.is_session_active = True
        h.engine.session_id = None  # движок так и не подтвердил старт

        self.assertTrue(
            h.can_leave(),
            "can_leave() не должен блокировать уход, если у движка нет "
            "реальной активной сессии (engine.session_id пуст), даже если "
            "is_session_active застрял в True",
        )

    def test_check_can_close_recovers_from_stuck_session_start(self):
        """Тот же дефект, путь «Прервать»/closeEvent: _on_start_stop_clicked
        в этой ситуации не делает ВООБЩЕ ничего (is_session_active=True, но
        engine.session_id пуст — ветка отправки manual_stop_requested не
        срабатывает) и _check_can_close вернул бы False навсегда. Правильное
        поведение — самостоятельно сбросить состояние и разрешить уход."""
        h = _RejectHarness()
        h.is_session_active = True
        h.engine.session_id = None
        h._session_run_id = "stuck-run-id"
        h._owned_session_id = "stuck-session-id"

        result = h._check_can_close()

        self.assertTrue(result, "_check_can_close обязан разблокировать страницу")
        self.assertEqual(
            h.stop_requested, 0,
            "не должен пытаться остановить несуществующую сессию движка",
        )
        self.assertFalse(h.is_session_active)
        self.assertIsNone(h._session_run_id)
        self.assertIsNone(h._owned_session_id)
        self.assertEqual(
            h.restore_calls, 1,
            "чужая очередь, снятая перед стартом, должна быть возвращена",
        )

    def test_check_can_close_skips_confirmation_popup_in_suppressed_mode(self):
        """Замечание рецензента (minor #5): в авто-режиме (suppress_popups)
        модальный QMessageBox.question про несохранённые результаты не
        должен всплывать — это повесит скрытый авто-пайплайн."""
        h = _RejectHarness()
        h.translated_results = ["partial"]
        h.suppress_popups = True

        with patch(
            "gemini_translator.ui.dialogs.validation_dialogs.untranslated_fixer_dialog.QMessageBox.question"
        ) as mock_question:
            result = h._check_can_close()

        mock_question.assert_not_called()
        self.assertTrue(result, "в авто-режиме выход при непринятых результатах не должен блокироваться")


if __name__ == "__main__":
    unittest.main()
