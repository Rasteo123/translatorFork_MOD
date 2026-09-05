# -*- coding: utf-8 -*-
"""Регресс-тест на дефект ui-dialogs-validation/runtime/3-fixer-ai-session-clears-shared
и на замечание рецензента (major #2) к его первому исправлению.

AITranslationPage.task_manager — это тот же ChapterQueueManager, что у
главного окна (app.task_manager == engine.task_manager). Нажатие "Начать
перевод" в AI-фиксере недоперевода вызывало task_manager.clear_all_queues()
перед добавлением своих задач raw_text_translation, что безвозвратно стирало
общую очередь (pending/failed главы, оставленные там намеренно после
остановки обычной сессии перевода). Тест проверяет, что задачи, лежавшие в
очереди ДО старта AI-сессии фиксера, возвращаются в неё после завершения
этой сессии.

Рецензент указал, что первое исправление снимало снимок через
get_all_tasks_for_rebuild(), который отдаёт ВСЕ задачи независимо от
статуса — включая уже переведённые главы со статусом 'completed'. При
восстановлении add_pending_tasks кладёт их обратно как 'pending', то есть
уже оплаченный перевод переводится заново. Правильно снимать снимок через
get_all_pending_tasks() (только статусы 'pending'/'held') — тогда
'completed' (и, как честный побочный компромисс, 'failed') главы не
воскресают вовсе, а не превращаются в 'pending'.
"""
import os
import sqlite3
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets

from gemini_translator.api import config as api_config
from gemini_translator.core.task_manager import ChapterQueueManager
from gemini_translator.ui.dialogs.validation_dialogs.untranslated_fixer_dialog import AITranslationPage


class _DummyBus(QtCore.QObject):
    event_posted = QtCore.pyqtSignal(dict)


class _Widget:
    """Заглушка любого дочернего виджета: любой атрибут — no-op вызов."""

    def __init__(self, **kw):
        self.__dict__.update(kw)

    def __getattr__(self, name):
        return lambda *a, **k: None


class _FixerHarness:
    """Харнесс на боевых методах AITranslationPage, без построения GUI-страницы."""

    _on_start_stop_clicked = AITranslationPage._on_start_stop_clicked
    _on_global_event = AITranslationPage._on_global_event
    _is_owned_session_lifecycle_event = AITranslationPage._is_owned_session_lifecycle_event
    _is_owned_session_event = AITranslationPage._is_owned_session_event
    _restore_preserved_queue = AITranslationPage._restore_preserved_queue

    def __init__(self, task_manager, tasks_payloads):
        self.task_manager = task_manager
        self.engine = None
        self.is_session_active = False
        self.suppress_popups = True
        self._session_run_id = None
        self._owned_session_id = None
        self._preserved_queue_snapshot = None
        self.translated_results = []
        self.finish_reason = ""
        self.tasks_payloads = tasks_payloads
        self.posted = []

        self.settings_manager = _Widget()
        self.key_widget = _Widget()
        self.key_widget.can_start_ai_session = lambda: True
        self.prompt_widget = _Widget()
        self.prompt_widget.get_prompt = lambda: "промпт"
        self.start_stop_btn = _Widget()

    def _set_ui_active(self, active):
        self.is_session_active = active

    def get_settings(self):
        return {"background_session": True}

    def _reset_token_usage(self):
        pass

    def _update_apply_button(self):
        pass

    def _finish_auto_session(self, ok):
        pass

    def _disconnect_global_events(self):
        pass

    def _post_event(self, name, data=None):
        self.posted.append(name)


class SharedQueuePreservedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app.event_bus = _DummyBus()
        cls.app.main_db_connection = sqlite3.connect(
            api_config.SHARED_DB_URI,
            uri=True,
            check_same_thread=False,
        )
        cls.app.main_db_connection.row_factory = sqlite3.Row

    def _make_manager(self):
        manager = ChapterQueueManager(event_bus=self.app.event_bus)
        self.addCleanup(manager.clear_all_queues)
        manager.clear_all_queues()
        return manager

    def test_start_and_finish_restores_preexisting_main_window_queue(self):
        tm = self._make_manager()

        # Состояние общей очереди главного окна после остановленной сессии:
        # ch_1 уже переведена ('completed'), ch_2 упала с ошибкой ('failed',
        # CONTENT_FILTER), ch_3/ch_4 ещё ждут перевода ('pending').
        tm.add_pending_tasks([
            ("epub_chapter", "book.epub", f"Text/ch_{i}.xhtml", "<p>src</p>", "", "")
            for i in range(1, 5)
        ])
        all_tasks_before = tm.get_all_tasks_for_rebuild()
        completed_id = all_tasks_before[0][0]
        failed_id = all_tasks_before[1][0]
        tm.update_task(task_id=completed_id, new_status='completed')
        tm.update_task(task_id=failed_id, new_status='failed')
        tm.record_failure((failed_id,), 'CONTENT_FILTER')

        harness = _FixerHarness(tm, ["<html><body><p data-id='0'>текст</p></body></html>"])

        # --- Нажатие "Начать перевод" в фиксере ---
        harness._on_start_stop_clicked()

        with tm._get_read_only_conn() as conn:
            payloads_during = [r["payload"] for r in conn.execute("SELECT payload FROM tasks")]
        self.assertEqual(
            len(payloads_during), 1,
            "во время AI-сессии фиксера в очереди должна быть только его собственная задача",
        )

        # --- Сессия фиксера успешно завершилась ---
        harness._owned_session_id = "fixer-session"
        harness._on_global_event({
            "event": "session_finished",
            "session_id": "fixer-session",
            "data": {
                "background_session": True,
                "background_role": "untranslated_fixer",
                "background_run_id": harness._session_run_id,
                "reason": "Сессия успешно завершена",
            },
        })

        with tm._get_read_only_conn() as conn:
            tasks_after = conn.execute("SELECT payload, status FROM tasks").fetchall()

        paths_after = [row["payload"] for row in tasks_after]
        self.assertEqual(
            len(tasks_after), 2,
            "воскресать как pending должны только исходно pending/held главы "
            "(ch_3, ch_4) — не completed (ch_1) и не failed (ch_2)",
        )
        self.assertTrue(all(row["status"] == 'pending' for row in tasks_after))
        self.assertTrue(any("ch_3.xhtml" in p for p in paths_after))
        self.assertTrue(any("ch_4.xhtml" in p for p in paths_after))
        self.assertFalse(
            any("ch_1.xhtml" in p for p in paths_after),
            "уже переведённая глава (была 'completed') не должна воскресать "
            "как 'pending' и переводиться заново",
        )
        self.assertFalse(
            any("ch_2.xhtml" in p for p in paths_after),
            "глава с ошибкой ('failed') — сознательный компромисс: не "
            "воскресает вовсе, а не превращается в 'pending' без истории ошибок",
        )


if __name__ == "__main__":
    unittest.main()
