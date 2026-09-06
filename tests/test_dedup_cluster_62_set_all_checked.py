# -*- coding: utf-8 -*-
"""Кластер dedup-62: ``_set_all_checked`` был переизобретён в 4 модулях.

(а) Характеризационные тесты на поведение канонической реализации
    ``gemini_translator.utils.document_importer.set_all_checked`` — прежде
    всего null-safety (одни копии делали ``if item:``, другие — нет; канон
    берёт безопасный вариант).
(б) Тест-маршрутизация: подменяем каноническую функцию в пространстве имён
    каждого вызывающего модуля и проверяем, что метод ``_set_all_checked``
    каждого класса реально идёт через неё. До рефакторинга у каждого места
    была своя копия цикла — эти тесты обязаны падать (mock не вызывается).
"""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("GT_DISABLE_LOCAL_MODEL_DISCOVERY", "1")

from PyQt6 import QtCore, QtWidgets  # noqa: E402
from PyQt6.QtCore import Qt  # noqa: E402

from gemini_translator.utils import document_importer as document_importer_module  # noqa: E402
from gemini_translator.utils.document_importer import set_all_checked  # noqa: E402
from gemini_translator.ui.dialogs import validation as validation_module  # noqa: E402
from gemini_translator.ui.pages import benchmark_page as benchmark_page_module  # noqa: E402
from gemini_translator.ui.widgets import glossary_widget as glossary_widget_module  # noqa: E402


class SetAllCheckedCharacterizationTests(unittest.TestCase):
    """Поведение канонической ``set_all_checked`` на QTableWidget/QListWidget."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_table_checks_all_present_items(self):
        table = QtWidgets.QTableWidget(3, 1)
        for row in range(3):
            item = QtWidgets.QTableWidgetItem()
            item.setCheckState(Qt.CheckState.Unchecked)
            table.setItem(row, 0, item)

        set_all_checked(table, True)

        for row in range(3):
            self.assertEqual(table.item(row, 0).checkState(), Qt.CheckState.Checked)

    def test_table_unchecks_all_present_items(self):
        table = QtWidgets.QTableWidget(2, 1)
        for row in range(2):
            item = QtWidgets.QTableWidgetItem()
            item.setCheckState(Qt.CheckState.Checked)
            table.setItem(row, 0, item)

        set_all_checked(table, False)

        for row in range(2):
            self.assertEqual(table.item(row, 0).checkState(), Qt.CheckState.Unchecked)

    def test_table_is_null_safe_for_missing_row_item(self):
        """Строка без item в колонке 0 не должна ронять функцию (AttributeError).

        benchmark_page.py и document_importer.py раньше делали
        ``self.table.item(row, 0).setCheckState(...)`` без проверки — канон
        обязан быть безопасным для обеих бывших копий.
        """
        table = QtWidgets.QTableWidget(3, 1)
        item0 = QtWidgets.QTableWidgetItem()
        table.setItem(0, 0, item0)
        # row 1 намеренно оставлена без item(row, 0)
        item2 = QtWidgets.QTableWidgetItem()
        table.setItem(2, 0, item2)

        set_all_checked(table, True)  # не должно бросить исключение

        self.assertEqual(item0.checkState(), Qt.CheckState.Checked)
        self.assertEqual(item2.checkState(), Qt.CheckState.Checked)

    def test_table_respects_column_argument(self):
        table = QtWidgets.QTableWidget(2, 2)
        col0_items = []
        col1_items = []
        for row in range(2):
            a = QtWidgets.QTableWidgetItem()
            a.setCheckState(Qt.CheckState.Unchecked)
            table.setItem(row, 0, a)
            col0_items.append(a)
            b = QtWidgets.QTableWidgetItem()
            b.setCheckState(Qt.CheckState.Unchecked)
            table.setItem(row, 1, b)
            col1_items.append(b)

        set_all_checked(table, True, column=1)

        for item in col0_items:
            self.assertEqual(item.checkState(), Qt.CheckState.Unchecked)
        for item in col1_items:
            self.assertEqual(item.checkState(), Qt.CheckState.Checked)

    def test_list_widget_checks_and_unchecks_all_items(self):
        list_widget = QtWidgets.QListWidget()
        for label in ("a", "b", "c"):
            item = QtWidgets.QListWidgetItem(label)
            item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            list_widget.addItem(item)

        set_all_checked(list_widget, True)
        for index in range(list_widget.count()):
            self.assertEqual(list_widget.item(index).checkState(), Qt.CheckState.Checked)

        set_all_checked(list_widget, False)
        for index in range(list_widget.count()):
            self.assertEqual(list_widget.item(index).checkState(), Qt.CheckState.Unchecked)

    def test_list_widget_is_null_safe_for_empty_list(self):
        list_widget = QtWidgets.QListWidget()
        set_all_checked(list_widget, True)  # не должно бросить исключение
        self.assertEqual(list_widget.count(), 0)


class SetAllCheckedRoutingTests(unittest.TestCase):
    """Каждое бывшее место вызова обязано идти через каноническую функцию."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_document_importer_dialog_routes_through_canonical(self):
        class _Stub:
            table = QtWidgets.QTableWidget(1, 1)

        stub = _Stub()
        stub.table.setItem(0, 0, QtWidgets.QTableWidgetItem())
        stub._set_all_checked = document_importer_module.DocumentImportDialog._set_all_checked.__get__(
            stub, _Stub
        )

        with patch.object(document_importer_module, "set_all_checked", create=True) as mocked:
            stub._set_all_checked(True)

        mocked.assert_called_once_with(stub.table, True)

    def test_validation_page_routes_through_canonical(self):
        class _Stub:
            _populating_table = False

            def _update_selection_summary(self):
                self._summary_calls = getattr(self, "_summary_calls", 0) + 1

        stub = _Stub()
        stub.table = QtWidgets.QTableWidget(1, 1)
        stub.table.setItem(0, 0, QtWidgets.QTableWidgetItem())
        stub._set_all_checked = validation_module.AIRepairReviewPage._set_all_checked.__get__(stub, _Stub)

        with patch.object(validation_module, "set_all_checked", create=True) as mocked:
            stub._set_all_checked(False)

        mocked.assert_called_once_with(stub.table, False)
        self.assertEqual(getattr(stub, "_summary_calls", 0), 1)
        self.assertFalse(stub._populating_table)

    def test_glossary_review_dialog_routes_through_canonical(self):
        class _Stub:
            pass

        stub = _Stub()
        stub.table = QtWidgets.QTableWidget(1, 1)
        stub.table.setItem(0, 0, QtWidgets.QTableWidgetItem())
        stub._set_all_checked = glossary_widget_module.GeneratedTermsReviewDialog._set_all_checked.__get__(
            stub, _Stub
        )

        with patch.object(glossary_widget_module, "set_all_checked", create=True) as mocked:
            stub._set_all_checked(True)

        mocked.assert_called_once_with(stub.table, True)

    def test_benchmark_page_routes_through_canonical_and_updates_estimate(self):
        class _Stub:
            def __init__(self):
                self.estimate_calls = 0

            def _update_run_estimate(self):
                self.estimate_calls += 1

        stub = _Stub()
        list_widget = QtWidgets.QListWidget()
        stub._set_all_checked = benchmark_page_module.PromptBenchmarkPage._set_all_checked.__get__(
            stub, _Stub
        )

        with patch.object(benchmark_page_module, "set_all_checked", create=True) as mocked:
            stub._set_all_checked(list_widget, False)

        mocked.assert_called_once_with(list_widget, False)
        self.assertEqual(stub.estimate_calls, 1)


if __name__ == "__main__":
    unittest.main()
