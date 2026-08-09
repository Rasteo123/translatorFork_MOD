"""Список «Общие паттерны»: элементы рисует делегат, без per-item виджетов.

Раньше на каждый паттерн создавался QWidget+QVBoxLayout+QLabel'ы + adjustSize:
6000 паттернов = ~1.5с фриза при открытии анализатора.
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets

import gemini_translator.ui.dialogs.validation  # порядок импорта
from gemini_translator.ui.dialogs.glossary_dialogs.core_term_dialog import (
    PATTERN_COUNT_ROLE,
    PATTERN_TEXT_ROLE,
    PATTERN_TRANSLATION_ROLE,
    CoreTermAnalyzerPage,
    PatternListDelegate,
)


class PatternListDelegateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _make_page(self, candidates):
        glossary = [{"original": t, "rus": f"tr {t}", "note": ""} for members in candidates.values() for t in members]
        page = CoreTermAnalyzerPage(glossary, None, candidates, False)
        self.addCleanup(page.deleteLater)
        page._async_prepare_data_and_populate()  # строит UI и заполняет список
        return page

    def test_populate_sets_roles_without_item_widgets(self):
        page = self._make_page({
            "алая пилюля": {"алая пилюля возрождения", "алая пилюля небес"},
            "меч дракона": {"меч дракона востока"},
        })

        self.assertEqual(page.left_list.count(), 2)
        for i in range(page.left_list.count()):
            item = page.left_list.item(i)
            self.assertIsNone(page.left_list.itemWidget(item))
            self.assertTrue(item.data(PATTERN_TEXT_ROLE))
            self.assertGreaterEqual(item.data(PATTERN_COUNT_ROLE), 1)
        # Сортировка: больший счётчик первым
        self.assertEqual(page.left_list.item(0).data(PATTERN_COUNT_ROLE), 2)

    def test_translation_role_set_when_pattern_is_term(self):
        candidates = {"алая пилюля": {"алая пилюля возрождения"}}
        glossary = [
            {"original": "алая пилюля", "rus": "scarlet pill", "note": ""},
            {"original": "алая пилюля возрождения", "rus": "x", "note": ""},
        ]
        page = CoreTermAnalyzerPage(glossary, None, candidates, False)
        self.addCleanup(page.deleteLater)
        page._async_prepare_data_and_populate()

        item = page.left_list.item(0)
        self.assertEqual(item.data(PATTERN_TRANSLATION_ROLE), "→ scarlet pill")

    def test_size_hint_two_lines_taller_and_respects_minimum(self):
        page = self._make_page({"алая пилюля": {"алая пилюля возрождения"}})
        delegate = page.left_list.itemDelegate()
        self.assertIsInstance(delegate, PatternListDelegate)

        option = QtWidgets.QStyleOptionViewItem()
        option.rect = QtCore.QRect(0, 0, 200, 0)
        option.font = page.left_list.font()
        option.fontMetrics = page.left_list.fontMetrics()

        index_one = page.left_list.model().index(0, 0)
        h_one = delegate.sizeHint(option, index_one).height()
        self.assertGreaterEqual(h_one, PatternListDelegate.MIN_HEIGHT)

        page.left_list.item(0).setData(PATTERN_TRANSLATION_ROLE, "→ перевод")
        h_two = delegate.sizeHint(option, index_one).height()
        self.assertGreaterEqual(h_two, h_one)

    def test_count_update_via_roles(self):
        page = self._make_page({"алая пилюля": {"а", "б", "в"}})
        key = page.left_list.item(0).data(QtCore.Qt.ItemDataRole.UserRole)
        page.analysis_data[key]['members'].pop()
        page._update_left_list_item_by_tuple(key)
        self.assertEqual(page.left_list.item(0).data(PATTERN_COUNT_ROLE), 2)


if __name__ == "__main__":
    unittest.main()
