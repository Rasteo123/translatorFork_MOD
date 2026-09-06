# -*- coding: utf-8 -*-
"""Характеризационные тесты + тест-маршрутизация для pcluster-20 (_split_csv/_split_csv_text).

Кластер: gemini_translator/core/worker_helpers/provider_orchestrator.py:_split_csv,
ranobelib/main_window.py:_split_csv_text, gemini_translator/ui/dialogs/qidian_rulate_creator.py:_split_csv
— три реинкарнации одного и того же хелпера "разбить CSV-строку по запятым/переносам,
обрезать пробелы", разошедшиеся в деталях (';' как разделитель, дедупликация).

Каноническая реализация: gemini_translator.utils.text.split_csv(text, *, delimiters, dedupe).
"""
import os
import sys
import unittest
from unittest.mock import patch

from gemini_translator.utils.text import split_csv

_TESTS_DIR = os.path.dirname(__file__)
_PROJECT_ROOT = os.path.dirname(_TESTS_DIR)
_RANOBELIB_DIR = os.path.join(_PROJECT_ROOT, "ranobelib")
if _RANOBELIB_DIR not in sys.path:
    sys.path.insert(0, _RANOBELIB_DIR)


class TestSplitCsvCharacterization(unittest.TestCase):
    """(a) Характеризация поведения канонической split_csv на граничных случаях,
    которые различали три копии."""

    def test_default_splits_on_comma_semicolon_and_newline(self):
        # Поведение provider_orchestrator._split_csv и ranobelib._split_csv_text:
        # ';' - тоже разделитель, дедупликации нет.
        self.assertEqual(
            split_csv("a, b ;a\nc"),
            ["a", "b", "a", "c"],
        )

    def test_default_no_dedupe_keeps_duplicates(self):
        self.assertEqual(split_csv("a,a,a"), ["a", "a", "a"])

    def test_default_empty_and_none_return_empty_list(self):
        self.assertEqual(split_csv(""), [])
        self.assertEqual(split_csv(None), [])

    def test_default_strips_whitespace_and_drops_empty_items(self):
        self.assertEqual(split_csv(" a ,, b \n\n c "), ["a", "b", "c"])

    def test_dedupe_true_deduplicates_preserving_first_occurrence_order(self):
        # Поведение qidian_rulate_creator._split_csv.
        self.assertEqual(split_csv("a, b ,a\nc", dedupe=True), ["a", "b", "c"])

    def test_dedupe_true_empty_returns_empty_list(self):
        self.assertEqual(split_csv("", dedupe=True), [])

    def test_qidian_delimiters_do_not_split_on_semicolon(self):
        # qidian_rulate_creator._split_csv никогда не резала по ';' - только
        # запятая и перевод строки. Явный delimiters сохраняет это при миграции.
        self.assertEqual(
            split_csv("a;b,c", delimiters=r"[,\n]+", dedupe=True),
            ["a;b", "c"],
        )

    def test_custom_delimiters_are_honored(self):
        self.assertEqual(split_csv("a|b|c", delimiters=r"[|]+"), ["a", "b", "c"])


class TestSplitCsvRouting(unittest.TestCase):
    """(b) Тест-маршрутизация: каждый бывший вызывающий обязан идти через
    каноническую gemini_translator.utils.text.split_csv, а не через свою копию.

    До рефакторинга у каждого модуля была собственная функция-копия, и
    monkeypatch канонической split_csv не влиял на результат вызова -
    поэтому эти тесты обязаны падать (RED) до миграции и проходить (GREEN)
    после.
    """

    def test_provider_orchestrator_normalize_provider_specs_routes_through_canonical(self):
        from gemini_translator.core.worker_helpers import provider_orchestrator as orch

        sentinel = ["ROUTED_A", "ROUTED_B"]
        worker = type("W", (), {"parallel_provider_list": "irrelevant,csv", "parallel_providers": None})()
        with patch.object(orch, "split_csv", return_value=sentinel) as mocked:
            specs = orch._normalize_provider_specs(worker)
        mocked.assert_called_once()
        self.assertEqual(
            [spec["provider"] for spec in specs],
            ["ROUTED_A", "ROUTED_B"],
        )

    def test_provider_orchestrator_normalize_pass_specs_temperatures_route_through_canonical(self):
        from gemini_translator.core.worker_helpers import provider_orchestrator as orch

        worker = type(
            "W",
            (),
            {
                "multi_pass_variants": None,
                "multi_pass_count": 2,
                "multi_pass_chapter_count": 2,
                "temperature": 1.0,
                "multi_pass_temperatures": "0.1,0.9",
            },
        )()
        with patch.object(
            orch, "split_csv", return_value=["0.1", "0.9"]
        ) as mocked:
            orch._normalize_pass_specs(worker)
        mocked.assert_called_once_with("0.1,0.9")

    def test_qidian_rulate_creator_split_csv_routes_through_canonical_with_dedupe(self):
        from gemini_translator.ui.dialogs import qidian_rulate_creator as mod

        with patch.object(
            mod, "split_csv", return_value=["ROUTED"]
        ) as mocked:
            result = mod._split_csv("a,b,a")
        mocked.assert_called_once()
        _, kwargs = mocked.call_args
        self.assertTrue(kwargs.get("dedupe"))
        self.assertEqual(result, ["ROUTED"])

    def test_ranobelib_main_window_uses_canonical_split_csv(self):
        # ranobelib/main_window.py больше не содержит собственную _split_csv_text;
        # модуль импортирует и вызывает каноническую split_csv напрямую.
        import main_window as ranobelib_main_window

        self.assertFalse(
            hasattr(ranobelib_main_window, "_split_csv_text"),
            "ranobelib.main_window должен использовать каноническую split_csv, "
            "а не собственную _split_csv_text",
        )
        self.assertIs(ranobelib_main_window.split_csv, split_csv)


if __name__ == "__main__":
    unittest.main()
