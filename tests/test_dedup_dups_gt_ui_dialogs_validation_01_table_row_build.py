# -*- coding: utf-8 -*-
"""dups-gt_ui_dialogs_validation-01, finding
ui-dialogs-validation/design/12-table-row-build-loop-copy.

``_populate_initial_table`` и ``_smart_reload_table_preserving_data``
дословно повторяли один и тот же блок построения строки таблицы
результатов (insertRow, SortableChapterItem, длины, placeholder статуса,
results_data/path_row_map, dirty_files, скрытие готовых строк). Разница —
только текст placeholder-а ("Ожидание..." против "...") и (сознательно
НЕ унифицируемая здесь) отзывчивость интерфейса первичной загрузки
(processEvents/setUpdatesEnabled), которая в этом рефакторинге не
переносится на "умную" перезагрузку, чтобы не менять её поведение сверх
устранения самого дублирования.

(a) Характеризационные тесты фиксируют поведение канонического
    ``_append_result_row`` (обычная строка, скрытие готового файла,
    учёт needs_analysis).
(b) Тесты-маршрутизаторы патчат ``_append_result_row`` и проверяют, что
    ОБА метода реально вызывают его на каждую главу, вместо своей
    встроенной копии. До рефакторинга (собственный inline-блок) эти
    тесты ПАДАЮТ, после — проходят.
"""

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.ui.dialogs.validation import (
    TranslationValidatorDialog,
    TranslationValidatorPage,
)


class _CheckStub:
    def __init__(self, checked=False):
        self._checked = checked

    def isChecked(self):
        return self._checked


class _RowStubTable:
    def __init__(self):
        self.inserted_rows = []
        self._items = {}
        self._hidden = {}

    def insertRow(self, row_pos):
        self.inserted_rows.append(row_pos)

    def setItem(self, row, column, item):
        self._items[(row, column)] = item

    def item(self, row, column):
        return self._items.get((row, column))

    def setRowHidden(self, row, hidden):
        self._hidden[row] = hidden

    def isRowHidden(self, row):
        return self._hidden.get(row, False)


class _AppendResultRowHarness:
    _append_result_row = TranslationValidatorDialog._append_result_row

    def __init__(self, check_revalidate_ok=False):
        self.table_results = _RowStubTable()
        self.results_data = {}
        self.path_row_map = {}
        self.dirty_files = set()
        self.check_revalidate_ok = _CheckStub(check_revalidate_ok)

    def _calculate_status_for_data(self, data):
        return [], "neutral"

    def _set_problem_cell(self, row, data, current_reasons):
        pass


class AppendResultRowCharacterizationTests(unittest.TestCase):
    """(a) Характеризация канонического построителя строки."""

    def test_builds_row_with_arrow_label_and_lengths(self):
        harness = _AppendResultRowHarness()
        data = {"len_orig": 5, "len_trans": 7, "has_cached_analysis": True}

        harness._append_result_row(
            0, "Text/ch1.xhtml", "ch1_translated.html", False, data, True,
            placeholder_text="Ожидание...",
        )

        self.assertEqual(harness.table_results.inserted_rows, [0])
        self.assertEqual(harness.table_results.item(0, 0).text(), "ch1.xhtml -> ch1_translated.html")
        self.assertEqual(harness.table_results.item(0, 2).text(), "5 | 7")
        self.assertEqual(harness.table_results.item(0, 3).text(), "Ожидание...")
        self.assertIs(harness.results_data[0], data)
        self.assertEqual(harness.path_row_map["Text/ch1.xhtml"], 0)
        self.assertIn("Text/ch1.xhtml", harness.dirty_files)
        self.assertFalse(harness.table_results.isRowHidden(0))

    def test_uses_given_placeholder_text(self):
        harness = _AppendResultRowHarness()
        harness._append_result_row(
            0, "Text/ch1.xhtml", "ch1_translated.html", False, {}, False,
            placeholder_text="...",
        )
        self.assertEqual(harness.table_results.item(0, 3).text(), "...")

    def test_marks_validated_label_without_needing_analysis(self):
        harness = _AppendResultRowHarness()
        harness._append_result_row(
            0, "Text/ch1.xhtml", "ch1_validated.html", True, {}, False,
            placeholder_text="Ожидание...",
        )
        self.assertEqual(harness.table_results.item(0, 0).text(), "ch1.xhtml [Готов]")
        self.assertNotIn("Text/ch1.xhtml", harness.dirty_files)

    def test_hides_ready_row_when_revalidate_checkbox_unchecked(self):
        harness = _AppendResultRowHarness(check_revalidate_ok=False)
        harness._append_result_row(
            0, "Text/ch1.xhtml", "ch1_validated.html", True, {}, False,
            placeholder_text="Ожидание...",
        )
        self.assertTrue(harness.table_results.isRowHidden(0))

    def test_does_not_hide_ready_row_when_revalidate_checkbox_checked(self):
        harness = _AppendResultRowHarness(check_revalidate_ok=True)
        harness._append_result_row(
            0, "Text/ch1.xhtml", "ch1_validated.html", True, {}, False,
            placeholder_text="Ожидание...",
        )
        self.assertFalse(harness.table_results.isRowHidden(0))

    def test_missing_cached_analysis_shows_dash_lengths(self):
        harness = _AppendResultRowHarness()
        harness._append_result_row(
            0, "Text/ch1.xhtml", "ch1_translated.html", False, {}, True,
            placeholder_text="Ожидание...",
        )
        self.assertEqual(harness.table_results.item(0, 2).text(), "- | -")


class _ProjectManagerStub:
    def __init__(self, versions_by_path):
        self._versions_by_path = versions_by_path

    def get_versions_for_original(self, internal_path):
        return self._versions_by_path.get(internal_path)


class _RoutingTestsBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app.global_version = ""

    def _make_page(self, versions_by_path):
        project_manager = _ProjectManagerStub(versions_by_path)
        with patch.object(TranslationValidatorPage, "_perform_initial_cjk_scan"):
            page = TranslationValidatorPage(
                "/tmp/nonexistent-translations",
                "/tmp/nonexistent-book.epub",
                project_manager=project_manager,
            )
        self.addCleanup(page.deleteLater)
        page._populate_initial_table_timer.stop()
        return page


class PopulateInitialTableRoutingTests(_RoutingTestsBase):
    def test_populate_initial_table_delegates_row_building_per_chapter(self):
        page = self._make_page(
            {
                "Text/ch1.xhtml": {"_translated.html": "ch1_translated.html"},
                "Text/ch2.xhtml": {"_translated.html": "ch2_translated.html"},
            }
        )

        with (
            patch.object(TranslationValidatorPage, "_load_validation_snapshot_state"),
            patch(
                "gemini_translator.ui.dialogs.validation.get_epub_chapter_order",
                return_value=(["Text/ch1.xhtml", "Text/ch2.xhtml"], "spine"),
            ),
            patch.object(
                TranslationValidatorPage,
                "_build_row_data_for_file",
                return_value=({"len_orig": 1, "len_trans": 1}, True),
            ),
            patch.object(TranslationValidatorPage, "_append_result_row", MagicMock()) as mock_append,
        ):
            page._populate_initial_table()

        self.assertEqual(mock_append.call_count, 2)
        for call in mock_append.call_args_list:
            self.assertEqual(call.kwargs.get("placeholder_text"), "Ожидание...")
        called_paths = [call.args[1] for call in mock_append.call_args_list]
        self.assertEqual(called_paths, ["Text/ch1.xhtml", "Text/ch2.xhtml"])


class SmartReloadTableRoutingTests(_RoutingTestsBase):
    def test_smart_reload_delegates_row_building_per_chapter(self):
        page = self._make_page(
            {
                "Text/ch1.xhtml": {"_translated.html": "ch1_translated.html"},
            }
        )

        with (
            patch.object(TranslationValidatorPage, "_load_validation_snapshot_state"),
            patch(
                "gemini_translator.ui.dialogs.validation.get_epub_chapter_order",
                return_value=(["Text/ch1.xhtml"], "spine"),
            ),
            patch.object(
                TranslationValidatorPage,
                "_build_row_data_for_file",
                return_value=({"len_orig": 1, "len_trans": 1}, True),
            ),
            patch.object(TranslationValidatorPage, "_append_result_row", MagicMock()) as mock_append,
        ):
            page._smart_reload_table_preserving_data()

        self.assertEqual(mock_append.call_count, 1)
        self.assertEqual(mock_append.call_args.kwargs.get("placeholder_text"), "...")
        self.assertEqual(mock_append.call_args.args[1], "Text/ch1.xhtml")


if __name__ == "__main__":
    unittest.main()
