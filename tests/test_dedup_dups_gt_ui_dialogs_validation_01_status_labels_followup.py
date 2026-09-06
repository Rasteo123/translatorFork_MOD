# -*- coding: utf-8 -*-
"""dups-gt_ui_dialogs_validation-01, ревью-фикс к finding
ui-dialogs-validation/design/26-status-map-and-no-problem-dial и
design/9-word-exceptions-builder-copy.

Рецензент указал, что первая волна закрыла только два места (reapply_filters,
add_result), а подписи статусов колонки 3 по-прежнему пишутся литералами в
обход ``TranslationValidatorPage.STATUS_LABELS`` ещё в пяти местах:
``_mark_row_changed_by_ai_repair``, ``mark_chapters_for_retry``,
``on_text_edited``, ``_apply_untranslated_fixer_changes`` (буквенный литерал
"Редакт." / "К переотправке") и ``mark_selected_rows`` (собственный
status_map с продублированными подписями).

Failure_scenario находки: переименование статуса "Редакт." в STATUS_LABELS
должно поменять подпись ВЕЗДЕ. Каждый тест ниже патчит STATUS_LABELS
сентинельным значением и проверяет, что соответствующий метод действительно
взял подпись оттуда, а не из своей копии. До правки (литерал в коде) тест
падает, потому что в ячейке окажется исходный русский текст, а не сентинел.

Также покрывает finding 9 (word-exceptions-builder-copy): после review-фикса
``_build_current_untranslated_exceptions`` должен быть тонким вызовом
``_get_effective_word_exceptions`` (одна и та же логика разбора текста
исключений не должна жить в двух местах), и finding 15 (закрытие окна должно
перезапускать цикл через канонический ``menu_utils.return_to_main_menu``,
а не литерал ``QApplication.exit(2000)``).
"""

import os
import types
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gemini_translator.ui.dialogs import validation as validation_module
from gemini_translator.ui.dialogs.validation import (
    TranslationValidatorDialog,
    TranslationValidatorPage,
)


class _StubItem:
    def __init__(self):
        self.text = None

    def setText(self, value):
        self.text = value


class _StubTable:
    def __init__(self):
        self._items = {}
        self._selected = []

    def item(self, row, column):
        return self._items.setdefault((row, column), _StubItem())

    def setItem(self, row, column, item):
        self._items[(row, column)] = item

    def selectedItems(self):
        return self._selected

    def removeCellWidget(self, row, column):
        pass


class _RowRef:
    """Мимикрирует QTableWidgetItem.row() для selectedItems()."""

    def __init__(self, row):
        self._row = row

    def row(self):
        return self._row


SENTINEL_EDITED = "__SENTINEL_EDITED__"
SENTINEL_RETRY = "__SENTINEL_RETRY__"


class MarkRowChangedByAiRepairRoutingTests(unittest.TestCase):
    """_mark_row_changed_by_ai_repair должен брать подпись из STATUS_LABELS."""

    def test_uses_canonical_status_labels_for_edited(self):
        page = types.SimpleNamespace()
        page._mark_row_changed_by_ai_repair = types.MethodType(
            TranslationValidatorPage._mark_row_changed_by_ai_repair, page
        )
        page.results_data = {0: {"status": "problem"}}
        page.dirty_files = set()
        page.table_results = _StubTable()
        page._invalidate_analysis_for_data = MagicMock()
        page.update_row_color = MagicMock()
        page._fixer_stale_rows = set()
        page.STATUS_LABELS = {**TranslationValidatorPage.STATUS_LABELS, "edited": SENTINEL_EDITED}

        page._mark_row_changed_by_ai_repair(0)

        self.assertEqual(page.table_results.item(0, 3).text, SENTINEL_EDITED)


class MarkChaptersForRetryRoutingTests(unittest.TestCase):
    """mark_chapters_for_retry должен брать подпись из STATUS_LABELS."""

    def test_uses_canonical_status_labels_for_retry(self):
        page = types.SimpleNamespace()
        page.mark_chapters_for_retry = types.MethodType(
            TranslationValidatorPage.mark_chapters_for_retry, page
        )
        page.results_data = {0: {"status": "problem"}}
        page.table_results = _StubTable()
        page._find_result_row_by_internal_path = MagicMock(return_value=0)
        page.update_row_color = MagicMock()
        page.lbl_status = _StubItem()
        page.STATUS_LABELS = {**TranslationValidatorPage.STATUS_LABELS, "retry": SENTINEL_RETRY}

        page.mark_chapters_for_retry(["Text/ch1.xhtml"])

        self.assertEqual(page.table_results.item(0, 3).text, SENTINEL_RETRY)


class OnTextEditedRoutingTests(unittest.TestCase):
    """on_text_edited должен брать подпись из STATUS_LABELS."""

    def test_uses_canonical_status_labels_for_edited(self):
        page = types.SimpleNamespace()
        page.on_text_edited = types.MethodType(TranslationValidatorPage.on_text_edited, page)
        page.is_code_view = True
        page.table_results = _StubTable()
        page.table_results._selected = [_RowRef(0)]
        page.view_translated = MagicMock()
        page.view_translated.toPlainText.return_value = "<p>x</p>"
        page.results_data = {0: {"is_edited": False}}
        page.update_row_color = MagicMock()
        page.btn_save_changes = MagicMock()
        page.STATUS_LABELS = {**TranslationValidatorPage.STATUS_LABELS, "edited": SENTINEL_EDITED}

        page.on_text_edited()

        self.assertEqual(page.table_results.item(0, 3).text, SENTINEL_EDITED)


class ApplyUntranslatedFixerChangesRoutingTests(unittest.TestCase):
    """_apply_untranslated_fixer_changes должен брать подпись из STATUS_LABELS."""

    def test_uses_canonical_status_labels_for_edited(self):
        page = types.SimpleNamespace()
        page._apply_untranslated_fixer_changes = types.MethodType(
            TranslationValidatorPage._apply_untranslated_fixer_changes, page
        )
        page.results_data = {
            0: {
                "translated_html": "<p>OLD text</p>",
                "internal_html_path": "Text/ch1.xhtml",
                "is_edited": False,
            }
        }
        page.table_results = _StubTable()
        page._ensure_row_translated_html_loaded = MagicMock(return_value="<p>OLD text</p>")
        page.update_row_color = MagicMock()
        page._recalculate_untranslated_words_for_rows = MagicMock()
        page.reapply_filters = MagicMock()
        page._recalc_untranslated_stats_ui = MagicMock()
        page.btn_save_changes = MagicMock()
        page.update_comparison_view = MagicMock()
        page.STATUS_LABELS = {**TranslationValidatorPage.STATUS_LABELS, "edited": SENTINEL_EDITED}

        changes = [
            {
                "source_type": "user",
                "new_context": "NEW text",
                "occurrences": [
                    {"replace_mode": "literal_html", "row_index": 0, "literal_html": "OLD text"}
                ],
            }
        ]

        page._apply_untranslated_fixer_changes(
            changes, soup_cache={}, save_immediately=False, show_feedback=False
        )

        self.assertEqual(page.table_results.item(0, 3).text, SENTINEL_EDITED)


class MarkSelectedRowsRoutingTests(unittest.TestCase):
    """mark_selected_rows не должен хранить собственную копию подписей."""

    def test_uses_canonical_status_labels_for_retry(self):
        page = types.SimpleNamespace()
        page.mark_selected_rows = types.MethodType(TranslationValidatorPage.mark_selected_rows, page)
        page.table_results = _StubTable()
        page.table_results._selected = [_RowRef(0)]
        page.results_data = {0: {"status": "problem"}}
        page.update_row_color = MagicMock()
        page.STATUS_LABELS = {**TranslationValidatorPage.STATUS_LABELS, "retry": SENTINEL_RETRY}

        page.mark_selected_rows("retry")

        self.assertEqual(page.table_results.item(0, 3).text, SENTINEL_RETRY)
        self.assertEqual(page.results_data[0]["status"], "retry")

    def test_mark_ok_and_delete_still_work(self):
        page = types.SimpleNamespace()
        page.mark_selected_rows = types.MethodType(TranslationValidatorPage.mark_selected_rows, page)
        page.table_results = _StubTable()
        page.table_results._selected = [_RowRef(0)]
        page.results_data = {0: {"status": "problem"}}
        page.update_row_color = MagicMock()
        page.STATUS_LABELS = dict(TranslationValidatorPage.STATUS_LABELS)

        page.mark_selected_rows("mark_ok")

        self.assertEqual(page.table_results.item(0, 3).text, TranslationValidatorPage.STATUS_LABELS["ok"])
        self.assertEqual(page.results_data[0]["status"], "ok")


class BuildCurrentUntranslatedExceptionsDelegationTests(unittest.TestCase):
    """finding 9: _build_current_untranslated_exceptions должен делегировать
    в _get_effective_word_exceptions, а не повторять её тело."""

    def test_delegates_to_get_effective_word_exceptions(self):
        page = types.SimpleNamespace()
        page._build_current_untranslated_exceptions = types.MethodType(
            TranslationValidatorPage._build_current_untranslated_exceptions, page
        )
        sentinel_result = {"__sentinel_exception_word__"}
        page._get_effective_word_exceptions = MagicMock(return_value=sentinel_result)

        result = page._build_current_untranslated_exceptions()

        page._get_effective_word_exceptions.assert_called_once()
        self.assertEqual(result, sentinel_result)


class CloseEventReturnToMainMenuTests(unittest.TestCase):
    """finding 15: перезапуск цикла должен идти через канонический
    return_to_main_menu(), а не литерал QApplication.exit(2000)."""

    def test_menu_action_calls_canonical_return_to_main_menu(self):
        dialog = types.SimpleNamespace()
        dialog.closeEvent = types.MethodType(TranslationValidatorDialog.closeEvent, dialog)
        dialog.page = types.SimpleNamespace(
            _awaiting_analysis_thread_stop=False,
            analysis_thread=None,
            retry_is_available=False,
        )
        event = MagicMock()

        with patch.object(validation_module, "prompt_return_to_menu", return_value="menu"), \
             patch.object(validation_module, "return_to_main_menu") as mock_return_to_menu, \
             patch.object(validation_module.QApplication, "exit") as mock_qapp_exit:
            dialog.closeEvent(event)

        mock_return_to_menu.assert_called_once_with()
        mock_qapp_exit.assert_not_called()
        event.accept.assert_called_once()


if __name__ == "__main__":
    unittest.main()
