"""Находка core-a/design/1-managed-session-scan-4x.

Предикат «есть ли активная управляемая сессия» (скан bus._data_store.keys()
на префикс 'managed_session_active_' со значением True) был вручную
скопирован в ChapterQueueManager.is_finished и
ChapterQueueManager.has_pending_tasks (task_manager.py). Каноническая версия
теперь — EventBus.has_managed_session_active (main.py); оба места в
task_manager.py должны маршрутизироваться через неё.

(TranslationEngine.is_managed_mode/_end_session в translation_engine.py
содержат ещё 2 копии того же предиката — этот файл вне области правки
данной волны, поэтому здесь не тестируется.)
"""
import os
import sqlite3
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.api import config as api_config
from gemini_translator.core.task_manager import ChapterQueueManager
from main import EventBus

_MISSING = object()


class EventBusHasManagedSessionActiveTests(unittest.TestCase):
    """Характеризационные тесты канонического EventBus.has_managed_session_active."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_no_keys_returns_false(self):
        bus = EventBus()
        self.assertFalse(bus.has_managed_session_active())

    def test_matching_key_with_true_value_returns_true(self):
        bus = EventBus()
        bus.set_data("managed_session_active_123", True)
        try:
            self.assertTrue(bus.has_managed_session_active())
        finally:
            bus.pop_data("managed_session_active_123")

    def test_matching_key_with_false_value_returns_false(self):
        bus = EventBus()
        bus.set_data("managed_session_active_123", False)
        try:
            self.assertFalse(bus.has_managed_session_active())
        finally:
            bus.pop_data("managed_session_active_123")

    def test_unrelated_key_does_not_match_prefix(self):
        bus = EventBus()
        bus.set_data("cli_session_active", True)
        try:
            self.assertFalse(bus.has_managed_session_active())
        finally:
            bus.pop_data("cli_session_active")

    def test_custom_prefix_is_honored(self):
        bus = EventBus()
        bus.set_data("other_prefix_1", True)
        try:
            self.assertTrue(bus.has_managed_session_active(prefix="other_prefix_"))
            self.assertFalse(bus.has_managed_session_active())
        finally:
            bus.pop_data("other_prefix_1")


class ManagedSessionRoutingTests(unittest.TestCase):
    """Routing-тест: ChapterQueueManager должен спрашивать EventBus, а не
    сканировать bus._data_store сам. ДО рефакторинга оба теста ниже падают
    (is_finished/has_pending_tasks игнорируют подмену метода на bus и
    сканируют _data_store напрямую), ПОСЛЕ — проходят."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls._previous_app_attrs = {
            name: getattr(cls.app, name, _MISSING)
            for name in ("event_bus", "task_manager", "main_db_connection")
        }
        cls.app.event_bus = EventBus()
        cls.app.main_db_connection = sqlite3.connect(
            api_config.SHARED_DB_URI, uri=True, check_same_thread=False
        )
        cls.app.main_db_connection.row_factory = sqlite3.Row

    @classmethod
    def tearDownClass(cls):
        try:
            cls.app.main_db_connection.close()
        except Exception:
            pass
        for name, value in cls._previous_app_attrs.items():
            if value is _MISSING:
                if hasattr(cls.app, name):
                    delattr(cls.app, name)
            else:
                setattr(cls.app, name, value)

    def setUp(self):
        self.manager = ChapterQueueManager(event_bus=self.app.event_bus)
        self.app.task_manager = self.manager
        with self.manager._get_write_conn() as conn:
            conn.execute("DELETE FROM tasks")

    def _patch_bus_predicate(self, return_value):
        calls = []

        def spy(prefix="managed_session_active_"):
            calls.append(prefix)
            return return_value

        self.app.event_bus.has_managed_session_active = spy
        return calls

    def tearDown(self):
        try:
            delattr(self.app.event_bus, "has_managed_session_active")
        except AttributeError:
            pass

    def test_is_finished_routes_through_event_bus_predicate(self):
        # Пустая БД: без подмены is_finished() был бы True. Подменяем
        # EventBus-предикат так, чтобы он утверждал обратное — если
        # is_finished() реально спрашивает шину, результат должен стать False.
        calls = self._patch_bus_predicate(True)
        self.assertFalse(
            self.manager.is_finished(),
            "is_finished() должен считать сессию незавершённой, когда "
            "EventBus.has_managed_session_active() вернул True",
        )
        self.assertTrue(
            calls,
            "is_finished() обязан вызывать bus.has_managed_session_active(), "
            "а не сканировать _data_store вручную",
        )

    def test_has_pending_tasks_routes_through_event_bus_predicate(self):
        # В БД нет задач: без подмены has_pending_tasks() был бы False.
        calls = self._patch_bus_predicate(True)
        self.assertTrue(
            self.manager.has_pending_tasks(),
            "has_pending_tasks() должен вернуть True, когда "
            "EventBus.has_managed_session_active() вернул True",
        )
        self.assertTrue(
            calls,
            "has_pending_tasks() обязан вызывать bus.has_managed_session_active(), "
            "а не сканировать _data_store вручную",
        )

    def test_is_finished_false_predicate_falls_back_to_db_check(self):
        calls = self._patch_bus_predicate(False)
        # Пустая БД и предикат вернул False -> сессия завершена.
        self.assertTrue(self.manager.is_finished())
        self.assertTrue(calls)


if __name__ == "__main__":
    unittest.main()
