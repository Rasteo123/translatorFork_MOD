"""Хвост дедупа dups-gt_core_task_manager-21 (core-a/design/1-managed-session-scan-4x).

Волна объединила 2 из 4 ручных сканов bus._data_store по префиксу
'managed_session_active_' (task_manager.py); две копии в
TranslationEngine (is_managed_mode и _end_session) остались, потому что файл
был вне периметра волны. Здесь тест-маршрутизация: is_managed_mode обязан
спрашивать канонический EventBus.has_managed_session_active(), а не
сканировать словарь сам.
"""

from __future__ import annotations

import unittest

from gemini_translator.core.translation_engine import TranslationEngine


class _SpyBus:
    """Шина, у которой канонический предикат отвечает True, а ручной скан
    _data_store — нет (словарь пуст). Различает маршрут вызова."""

    def __init__(self, answer: bool):
        self._data_store = {}
        self.answer = answer
        self.calls = 0

    def has_managed_session_active(self, prefix: str = "managed_session_active_") -> bool:
        self.calls += 1
        return self.answer

    def get_data(self, key, default=None):
        return self._data_store.get(key, default)


class _EngineStub:
    def __init__(self, bus):
        self.bus = bus


class IsManagedModeRoutesThroughEventBusTests(unittest.TestCase):
    def test_true_comes_from_canonical_predicate_not_manual_scan(self):
        bus = _SpyBus(answer=True)
        self.assertTrue(
            TranslationEngine.is_managed_mode(_EngineStub(bus)),
            "is_managed_mode() обязан вызывать bus.has_managed_session_active(), "
            "а не сканировать bus._data_store сам",
        )
        self.assertEqual(bus.calls, 1)

    def test_false_comes_from_canonical_predicate(self):
        bus = _SpyBus(answer=False)
        self.assertFalse(TranslationEngine.is_managed_mode(_EngineStub(bus)))
        self.assertEqual(bus.calls, 1)

    def test_no_bus_is_not_managed(self):
        self.assertFalse(TranslationEngine.is_managed_mode(_EngineStub(None)))


if __name__ == "__main__":
    unittest.main()


class _ClearSpyBus:
    """Шина с ключом флага в _data_store и парным мутатором: различает, снял
    ли _end_session флаги через канонический clear_managed_session_flags или
    руками через pop_data."""

    def __init__(self):
        self._data_store = {"managed_session_active_abc": True, "other_key": 1}
        self.clear_calls = 0
        self.pop_calls = []

    def clear_managed_session_flags(self, prefix: str = "managed_session_active_") -> int:
        self.clear_calls += 1
        for key in [k for k in self._data_store if k.startswith(prefix)]:
            del self._data_store[key]
        return 1

    def pop_data(self, key, default=None):
        self.pop_calls.append(key)
        return self._data_store.pop(key, default)


class _EndSessionStub:
    def __init__(self, bus):
        self.bus = bus
        self.session_id = None          # -> _end_session выходит сразу после снятия флагов
        self.is_session_finishing = False


class EndSessionRoutesThroughEventBusMutatorTests(unittest.TestCase):
    def test_flags_are_cleared_via_canonical_mutator_not_manual_scan(self):
        bus = _ClearSpyBus()
        TranslationEngine._end_session(_EndSessionStub(bus), "тест")
        self.assertEqual(bus.clear_calls, 1, "_end_session обязан звать bus.clear_managed_session_flags()")
        self.assertEqual(bus.pop_calls, [], "ручной скан _data_store + pop_data должен исчезнуть")
        self.assertNotIn("managed_session_active_abc", bus._data_store)
        self.assertIn("other_key", bus._data_store)


class EventBusClearManagedSessionFlagsTests(unittest.TestCase):
    """Характеризация парного мутатора канонического предиката."""

    def test_removes_only_prefixed_keys_and_reports_count(self):
        from main import EventBus

        bus = EventBus()
        bus.set_data("managed_session_active_1", True)
        bus.set_data("managed_session_active_2", False)
        bus.set_data("cli_session_active", True)
        try:
            self.assertTrue(bus.has_managed_session_active())
            self.assertEqual(bus.clear_managed_session_flags(), 2)
            self.assertFalse(bus.has_managed_session_active())
            self.assertTrue(bus.get_data("cli_session_active"))
            self.assertEqual(bus.clear_managed_session_flags(), 0)
        finally:
            bus.pop_data("cli_session_active", None)


class _StoreOnlyBus:
    """Заглушка шины без канонического предиката, но с флагом в _data_store:
    ловит «запасной» ручной скан, который был копией предиката."""

    def __init__(self):
        self._data_store = {"managed_session_active_x": True}

    def get_data(self, key, default=None):
        return self._data_store.get(key, default)


class TaskManagerHasNoFallbackScanTests(unittest.TestCase):
    def test_bus_without_canonical_predicate_is_not_managed(self):
        from gemini_translator.core.task_manager import ChapterQueueManager

        stub = type("_QM", (), {"bus": _StoreOnlyBus()})()
        self.assertFalse(
            ChapterQueueManager._has_managed_session_active(stub),
            "без EventBus.has_managed_session_active ручного скана _data_store быть не должно",
        )
