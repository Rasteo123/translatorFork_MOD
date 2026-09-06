"""dups-gt_ui_dialogs_glossary_dialogs_conflict_reso-06, находка
ui-dialogs-glossary-b/design/3-conflict-resolvers-duplicated-:

Пошаговый режим «Визард» (start_wizard_mode/end_wizard_mode/wizard_go_next/
wizard_go_prev/_show_wizard_step) был продублирован почти дословно между
ComplexOverlapResolverPage (список left_list, элементы хранят id термина в
Qt.ItemDataRole.UserRole, поле self.wizard_terms) и ReverseConflictResolverPage
(список translations_list, элементы БЕЗ UserRole — идентификатором служит сам
текст элемента, поле self.wizard_items).

Этот файл:
  1) характеризует поведение канонической машины состояний WizardStepMixin на
     обоих реальных подклассах (полный цикл start -> next* -> завершение,
     wizard_go_prev, ветка «все термины уже проверены»,
     UserRole-vs-text ключ элемента в _show_wizard_step);
  2) содержит routing-тест: обе страницы обязаны использовать БУКВАЛЬНО один и
     тот же код метода (не переопределять его) — тест обязан падать до
     рефакторинга (это были independent-копии) и проходить после.
"""
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore
from PyQt6.QtWidgets import QApplication

from gemini_translator.ui.widgets.glossary_widget import GlossaryWidget  # noqa: F401

from gemini_translator.ui.dialogs.glossary_dialogs.conflict_resolvers import (
    ComplexOverlapResolverPage,
    ReverseConflictResolverPage,
)


def _make_complex_page():
    overlap_groups = {"A": ["AB", "AC"], "B": ["BC"]}
    inverted_groups = {"AB": ["A"], "AC": ["A"], "BC": ["B"]}
    original_glossary = {
        "A": {"rus": "Терм А", "note": ""},
        "B": {"rus": "Терм Б", "note": ""},
        "AB": {"rus": "Терм АБ", "note": ""},
        "AC": {"rus": "Терм АЦ", "note": ""},
        "BC": {"rus": "Терм БЦ", "note": ""},
    }
    page = ComplexOverlapResolverPage(overlap_groups, inverted_groups, original_glossary, False)
    return page


def _make_reverse_page():
    reverse_issues = {
        "Общий перевод 1": {"complete": [], "orphans": []},
        "Общий перевод 2": {"complete": [], "orphans": []},
    }
    original_glossary = []
    page = ReverseConflictResolverPage(reverse_issues, original_glossary)
    return page


def _wizard_list_widget(page):
    if isinstance(page, ComplexOverlapResolverPage):
        return page.left_list
    return page.translations_list


def _wizard_checked_set(page):
    if isinstance(page, ComplexOverlapResolverPage):
        return page.checked_terms
    return page.checked_items


def _wizard_queue(page):
    # Оба поля-накопителя (self.wizard_terms / self.wizard_items) — до
    # рефакторинга разные имена; после рефакторинга должны совпасть в одно
    # имя, читаемое общим геттером на самом хосте.
    for candidate in ("wizard_queue", "wizard_terms", "wizard_items"):
        if hasattr(page, candidate):
            return getattr(page, candidate)
    raise AssertionError("wizard queue attribute not found")


class WizardStepCharacterizationTests(unittest.TestCase):
    """Характеризует канонический цикл визарда на обеих реальных страницах."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _run_full_cycle(self, page):
        list_widget = _wizard_list_widget(page)
        self.assertEqual(list_widget.count(), 2)

        with mock.patch(
            "gemini_translator.ui.dialogs.glossary_dialogs.conflict_resolvers.QMessageBox"
        ) as mock_box:
            page.start_wizard_mode()
            mock_box.information.assert_not_called()

        self.assertTrue(page.wizard_mode_active)
        self.assertEqual(page.wizard_current_index, 0)
        self.assertFalse(list_widget.isEnabled())
        self.assertEqual(page.top_controls_stack.currentWidget(), page.wizard_mode_widget)
        self.assertEqual(page.wizard_progress_label.text(), "Шаг 1 из 2")
        self.assertFalse(page.wizard_prev_button.isEnabled())
        self.assertEqual(page.wizard_next_button.text(), "Далее >")

        # На первом шаге должен быть выбран первый элемент списка.
        self.assertIs(list_widget.currentItem(), list_widget.item(0))

        # wizard_go_prev на первом шаге ничего не делает.
        page.wizard_go_prev()
        self.assertEqual(page.wizard_current_index, 0)

        # Переход вперёд отмечает текущий термин проверенным и идёт на шаг 2.
        page.checked_checkbox.setChecked(False)
        page.wizard_go_next()
        self.assertEqual(page.wizard_current_index, 1)
        self.assertEqual(page.wizard_progress_label.text(), "Шаг 2 из 2")
        self.assertTrue(page.wizard_prev_button.isEnabled())
        self.assertEqual(page.wizard_next_button.text(), "Завершить")
        self.assertIs(list_widget.currentItem(), list_widget.item(1))

        # wizard_go_prev должен вернуть на первый шаг.
        page.wizard_go_prev()
        self.assertEqual(page.wizard_current_index, 0)
        self.assertIs(list_widget.currentItem(), list_widget.item(0))
        page.wizard_go_next()
        self.assertEqual(page.wizard_current_index, 1)

        # Последний "Далее" (он же "Завершить") показывает сообщение и
        # выключает режим визарда.
        with mock.patch(
            "gemini_translator.ui.dialogs.glossary_dialogs.conflict_resolvers.QMessageBox"
        ) as mock_box:
            page.wizard_go_next()
            mock_box.information.assert_called_once()

        self.assertFalse(page.wizard_mode_active)
        self.assertTrue(list_widget.isEnabled())
        self.assertEqual(page.top_controls_stack.currentWidget(), page.normal_mode_widget)

    def test_complex_overlap_resolver_wizard_cycle(self):
        page = _make_complex_page()
        self.addCleanup(page.close)
        self._run_full_cycle(page)

    def test_reverse_conflict_resolver_wizard_cycle(self):
        page = _make_reverse_page()
        self.addCleanup(page.close)
        self._run_full_cycle(page)

    def test_complex_overlap_all_checked_shows_info_and_stays_inactive(self):
        page = _make_complex_page()
        self.addCleanup(page.close)
        page.checked_terms.update({"A", "B"})

        with mock.patch(
            "gemini_translator.ui.dialogs.glossary_dialogs.conflict_resolvers.QMessageBox"
        ) as mock_box:
            page.start_wizard_mode()
            mock_box.information.assert_called_once()

        self.assertFalse(page.wizard_mode_active)
        self.assertTrue(page.left_list.isEnabled())

    def test_reverse_conflict_all_checked_shows_info_and_stays_inactive(self):
        page = _make_reverse_page()
        self.addCleanup(page.close)
        page.checked_items.update({"Общий перевод 1", "Общий перевод 2"})

        with mock.patch(
            "gemini_translator.ui.dialogs.glossary_dialogs.conflict_resolvers.QMessageBox"
        ) as mock_box:
            page.start_wizard_mode()
            mock_box.information.assert_called_once()

        self.assertFalse(page.wizard_mode_active)
        self.assertTrue(page.translations_list.isEnabled())

    def test_wizard_step_selection_works_with_and_without_user_role_key(self):
        """ComplexOverlapResolverPage хранит id термина в UserRole элемента
        списка; ReverseConflictResolverPage — нет, там ключом служит сам
        текст элемента. Общая реализация _show_wizard_step обязана уметь и
        то, и другое без флагов поведения (см. _wizard_item_key)."""
        complex_page = _make_complex_page()
        self.addCleanup(complex_page.close)
        self.assertIsNotNone(
            complex_page.left_list.item(0).data(QtCore.Qt.ItemDataRole.UserRole)
        )

        reverse_page = _make_reverse_page()
        self.addCleanup(reverse_page.close)
        self.assertIsNone(
            reverse_page.translations_list.item(0).data(QtCore.Qt.ItemDataRole.UserRole)
        )

        with mock.patch(
            "gemini_translator.ui.dialogs.glossary_dialogs.conflict_resolvers.QMessageBox"
        ):
            complex_page.start_wizard_mode()
            reverse_page.start_wizard_mode()

        self.assertIs(
            complex_page.left_list.currentItem(), complex_page.left_list.item(0)
        )
        self.assertIs(
            reverse_page.translations_list.currentItem(),
            reverse_page.translations_list.item(0),
        )


class WizardStepRoutingTests(unittest.TestCase):
    """Обе страницы обязаны идти через ОДИН И ТОТ ЖЕ код (общий миксин), а не
    через параллельные копии. До рефакторинга это FALSE (разные функции с
    разными self.wizard_terms/self.wizard_items внутри), после — True."""

    def test_start_wizard_mode_is_the_same_function_object(self):
        self.assertIs(
            ComplexOverlapResolverPage.start_wizard_mode,
            ReverseConflictResolverPage.start_wizard_mode,
        )

    def test_end_wizard_mode_is_the_same_function_object(self):
        self.assertIs(
            ComplexOverlapResolverPage.end_wizard_mode,
            ReverseConflictResolverPage.end_wizard_mode,
        )

    def test_wizard_go_next_is_the_same_function_object(self):
        self.assertIs(
            ComplexOverlapResolverPage.wizard_go_next,
            ReverseConflictResolverPage.wizard_go_next,
        )

    def test_wizard_go_prev_is_the_same_function_object(self):
        self.assertIs(
            ComplexOverlapResolverPage.wizard_go_prev,
            ReverseConflictResolverPage.wizard_go_prev,
        )

    def test_show_wizard_step_is_the_same_function_object(self):
        self.assertIs(
            ComplexOverlapResolverPage._show_wizard_step,
            ReverseConflictResolverPage._show_wizard_step,
        )


if __name__ == "__main__":
    unittest.main()
