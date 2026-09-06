# -*- coding: utf-8 -*-
"""dups-gt_ui_dialogs_validation-01, finding
ui-dialogs-validation/design/26-status-map-and-no-problem-dial (часть 1/2:
словарь подписей статусов).

`reapply_filters` и `add_result` в TranslationValidatorPage объявляли
дословно одинаковый локальный словарь ``status_map`` для перевода
внутреннего статуса строки ('problem'/'neutral'/'ok'/'delete'/'retry'/
'edited') в отображаемый текст колонки 3. Канонический источник — класс-
атрибут ``TranslationValidatorPage.STATUS_LABELS``.

(a) Характеризационный тест фиксирует значение канонического словаря.
(b) Тесты-маршрутизаторы патчат ``STATUS_LABELS`` и проверяют, что ОБА
    метода (reapply_filters, add_result) реально берут текст оттуда, а не
    из своей локальной копии. До рефакторинга (локальные словари-литералы)
    эти тесты ПАДАЮТ, после — проходят.
"""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gemini_translator.ui.dialogs.validation import (
    TranslationValidatorDialog,
    TranslationValidatorPage,
)


class _CheckStub:
    def __init__(self, checked=False):
        self._checked = checked

    def isChecked(self):
        return self._checked


class _StubItem:
    def __init__(self):
        self.text = None

    def setText(self, value):
        self.text = value


class _StubTable:
    def __init__(self, row_count):
        self._row_count = row_count
        self._items = {}
        self._hidden = {}

    def blockSignals(self, value):
        pass

    def setSortingEnabled(self, value):
        pass

    def setUpdatesEnabled(self, value):
        pass

    def rowCount(self):
        return self._row_count

    def item(self, row, column):
        return self._items.setdefault((row, column), _StubItem())

    def setItem(self, row, column, item):
        self._items[(row, column)] = item

    def setRowHidden(self, row, hidden):
        self._hidden[row] = hidden

    def isRowHidden(self, row):
        return self._hidden.get(row, False)


class _ReapplyFiltersHarness:
    reapply_filters = TranslationValidatorDialog.reapply_filters
    STATUS_LABELS = dict(TranslationValidatorPage.STATUS_LABELS)

    def __init__(self):
        self.table_results = _StubTable(1)
        self.results_data = {0: {"status": None}}
        self.check_show_all = _CheckStub(True)
        self.lbl_status = _StubItem()

    def _get_current_ratio_bounds(self):
        return (0.9, 1.2)

    def _calculate_status_for_data(self, data, override_bounds=None):
        return [], "ok"

    def _set_problem_cell(self, row, data, current_reasons):
        pass

    def update_row_color(self, row, status):
        pass

    def _update_analyze_button_state(self):
        pass


class _AddResultHarness:
    add_result = TranslationValidatorDialog.add_result
    STATUS_LABELS = dict(TranslationValidatorPage.STATUS_LABELS)

    def __init__(self):
        self.path_row_map = {"Text/ch1.xhtml": 0}
        self.results_data = {}
        self.dirty_files = set()
        self.untranslated_found_count = 0
        self.check_show_all = _CheckStub(True)
        self.table_results = _StubTable(1)
        self.table_results.setItem(0, 2, _StubItem())

    def _calculate_status_for_data(self, data, override_bounds=None):
        return [], "ok"

    def _update_previous_problem_path_for_data(self, data):
        pass

    def _set_problem_cell(self, row, data, current_reasons):
        pass

    def update_row_color(self, row, status):
        pass


class CanonicalStatusLabelsValueTests(unittest.TestCase):
    """(a) Характеризация канонической реализации."""

    def test_canonical_status_labels_cover_all_known_statuses(self):
        self.assertEqual(
            TranslationValidatorPage.STATUS_LABELS,
            {
                "problem": "Проблема",
                "neutral": "Проблем нет",
                "ok": "Готов",
                "delete": "На удаление",
                "retry": "К переотправке",
                "edited": "Редакт.",
            },
        )


class ReapplyFiltersRoutingTests(unittest.TestCase):
    """(b) Маршрутизация для reapply_filters (было: локальный status_map)."""

    def test_reapply_filters_uses_canonical_status_labels(self):
        harness = _ReapplyFiltersHarness()
        custom_labels = {"ok": "__CUSTOM_OK_LABEL__"}
        with patch.object(_ReapplyFiltersHarness, "STATUS_LABELS", custom_labels):
            harness.reapply_filters()

        self.assertEqual(harness.table_results.item(0, 3).text, "__CUSTOM_OK_LABEL__")


class AddResultRoutingTests(unittest.TestCase):
    """(b) Маршрутизация для add_result (было: локальный status_map)."""

    def test_add_result_uses_canonical_status_labels(self):
        harness = _AddResultHarness()
        custom_labels = {"ok": "__CUSTOM_OK_LABEL_2__"}
        result = {
            "internal_html_path": "Text/ch1.xhtml",
            "len_orig": 10,
            "len_trans": 12,
            "translated_html": "<p>x</p>",
        }
        with patch.object(_AddResultHarness, "STATUS_LABELS", custom_labels):
            harness.add_result(result)

        self.assertEqual(harness.table_results.item(0, 3).text, "__CUSTOM_OK_LABEL_2__")


if __name__ == "__main__":
    unittest.main()
