"""Дедуп cluster-11: _truncate_details_text (log_widget.py) и
_truncate_log_details (task_manager.py) были дословно идентичными копиями
одной и той же функции обрезки текста деталей лога (константа 16000).

Канонический источник истины: gemini_translator.utils.text.truncate_log_details.

Тесты:
1. TruncateLogDetailsCharacterizationTests — поведение самой канонической
   функции на граничных случаях, которые различали бы копии, если бы они
   разошлись.
2. Routing-тесты (по одному на бывшее место вызова) — подменяют каноническую
   функцию в пространстве имён модуля-вызывающего и проверяют, что вызов
   реально идёт через неё. Эти тесты обязаны падать до рефакторинга (у
   вызывающего кода была своя локальная копия) и проходить после.
"""

import os
import types
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gemini_translator.utils.text import truncate_log_details


class TruncateLogDetailsCharacterizationTests(unittest.TestCase):
    def test_short_text_returned_unchanged_after_stripping(self):
        self.assertEqual(truncate_log_details("  hello world  "), "hello world")

    def test_text_exactly_at_limit_is_not_truncated(self):
        text = "a" * 16000
        result = truncate_log_details(text)
        self.assertEqual(result, text)
        self.assertNotIn("[details truncated:", result)

    def test_text_over_limit_is_truncated_with_omitted_count(self):
        text = "a" * 16005
        result = truncate_log_details(text)
        expected_omitted = 16005 - 16000
        self.assertTrue(result.startswith("a" * 16000))
        self.assertIn(f"[details truncated: {expected_omitted} chars omitted]", result)
        # Обрезанный хвост перед суффиксом не должен содержать сырых пробелов
        # (rstrip перед добавлением суффикса), а суффикс отделён пустой строкой.
        self.assertIn("\n\n[details truncated:", result)

    def test_custom_limit_parameter_is_respected(self):
        text = "b" * 100
        result = truncate_log_details(text, limit=10)
        self.assertTrue(result.startswith("b" * 10))
        self.assertIn("[details truncated: 90 chars omitted]", result)

    def test_default_limit_matches_historical_constant(self):
        text = "c" * 16001
        result = truncate_log_details(text)
        self.assertIn("[details truncated: 1 chars omitted]", result)

    def test_trailing_whitespace_before_truncation_point_is_stripped(self):
        # 16000 значащих символов + пробелы ровно на границе среза не должны
        # оставлять "хвостовой мусор" перед суффиксом.
        text = ("d" * 15995) + "     " + ("e" * 10)
        result = truncate_log_details(text, limit=16000)
        omitted = len(text) - 16000
        self.assertIn(f"[details truncated: {omitted} chars omitted]", result)
        # Часть до суффикса не должна заканчиваться пробелом.
        head = result.split("\n\n[details truncated:")[0]
        self.assertEqual(head, head.rstrip())


class LogWidgetRoutesThroughCanonicalTruncateTests(unittest.TestCase):
    """Место вызова: gemini_translator/ui/widgets/log_widget.py, LogWidget._build_log_html."""

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_build_log_html_routes_details_text_through_canonical_function(self):
        from gemini_translator.ui.widgets import log_widget as log_widget_module

        sentinel = "SENTINEL-FROM-CANONICAL-TRUNCATE"
        calls = []

        def fake_truncate(text, limit=16000):
            calls.append((text, limit))
            return sentinel

        widget = log_widget_module.LogWidget(event_bus=None)
        try:
            with patch.object(
                log_widget_module, "truncate_log_details", fake_truncate, create=True
            ):
                widget._details_map.clear()
                widget._build_log_html({"message": "hi", "details_text": "x" * 20000})

            self.assertTrue(calls, "truncate_log_details не был вызван из _build_log_html")
            details = list(widget._details_map.values())
            self.assertEqual(len(details), 1)
            self.assertEqual(details[0]["text"], sentinel)
        finally:
            widget.close()


class ChapterQueueManagerRoutesThroughCanonicalTruncateTests(unittest.TestCase):
    """Место вызова: gemini_translator/core/task_manager.py, ChapterQueueManager._log."""

    class _BusStub:
        def __init__(self):
            self.events = []

        def emit_event(self, event):
            self.events.append(event)

    def test_log_routes_details_text_through_canonical_function(self):
        from gemini_translator.core import task_manager as task_manager_module

        sentinel = "SENTINEL-FROM-CANONICAL-TRUNCATE"
        calls = []

        def fake_truncate(text, limit=16000):
            calls.append((text, limit))
            return sentinel

        bus = self._BusStub()
        manager_stub = types.SimpleNamespace(bus=bus, session_id="test-session")
        manager_stub._log = types.MethodType(task_manager_module.ChapterQueueManager._log, manager_stub)
        manager_stub._post_event = types.MethodType(
            task_manager_module.ChapterQueueManager._post_event, manager_stub
        )

        with patch.object(task_manager_module, "truncate_log_details", fake_truncate, create=True):
            manager_stub._log({"message": "hi", "details_text": "y" * 20000})

        self.assertTrue(calls, "truncate_log_details не был вызван из ChapterQueueManager._log")
        self.assertEqual(bus.events[-1]["data"]["details_text"], sentinel)


if __name__ == "__main__":
    unittest.main()
