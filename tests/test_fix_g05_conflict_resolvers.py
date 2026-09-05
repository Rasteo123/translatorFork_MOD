"""
Регрессионные тесты для группы g05 (conflict_resolvers.py):

1) ui-dialogs-glossary-b/bugs/1-conflict-apply-note-wrong-colu —
   "Применить ко всем" в ReverseConflictResolverPage читал колонку 3
   ("Действия", там нет QTableWidgetItem) вместо колонки 2 ("Примечание"),
   поэтому примечание сироты никуда не копировалось, а сама запись-сирота
   всё равно помечалась удалённой.

2) ui-dialogs-glossary-b/bugs/2-conflict-delete-stale-row-inde —
   Кнопка удаления строки в табличном режиме DirectConflictResolverDialog
   захватывала индекс строки на момент построения таблицы. После первого
   ручного удаления индексы всех последующих кнопок оказывались смещены
   относительно фактических строк, и повторное удаление стирало не тот
   термин, на который нажал пользователь.

   Первая версия фикса перестраивала таблицу целиком (_populate_table())
   после каждого удаления — рецензент нашёл в этом две новые регрессии,
   которые ниже покрыты отдельными тестами:
   2a) полная перестройка сбрасывает выбор пользователя (комбобокс,
       «Свой вариант», примечание) во ВСЕХ остальных строках, а не
       только в удаляемой;
   2b) self.conflicts не синхронизировался с wizard_conflicts_list, из-за
       чего переход в пошаговый режим после ручного удаления падал с
       KeyError.
   Заодно рецензент указал на minor-находки: self.conflicts был тем же
   объектом, что и GlossaryManagerPage.direct_conflicts (мутация без
   копии), и счётчик "Найдено N терминов…" не обновлялся при ручном
   удалении.

Важно: перед импортом gemini_translator.ui.widgets.glossary_widget, иначе
прямой импорт conflict_resolvers упирается в циклический импорт
gemini_translator.ui.widgets -> dialogs.glossary -> conflict_resolvers
(тот же приём применён в tests/test_glossary_conflict_identity.py).
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("GT_DISABLE_LOCAL_MODEL_DISCOVERY", "1")

from PyQt6 import QtWidgets  # noqa: E402

from gemini_translator.ui.widgets.glossary_widget import GlossaryWidget  # noqa: F401,E402
from gemini_translator.ui.dialogs.glossary_dialogs.conflict_resolvers import (  # noqa: E402
    DirectConflictResolverDialog,
    ReverseConflictResolverPage,
)


class ReverseConflictApplyNoteToAllTests(unittest.TestCase):
    """Находка 1: применение примечания сироты ко ВСЕМ полным терминам."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _build_resolver(self):
        complete_entries = [
            {"original": "Sword God", "rus": "Бог Меча", "note": "", "timestamp": 1.0},
            {"original": "Blade God", "rus": "Бог Меча", "note": "", "timestamp": 2.0},
        ]
        orphan_entry = {
            "original": "",
            "rus": "Бог Меча",
            "note": "ВАЖНОЕ ПРИМЕЧАНИЕ",
            "timestamp": 3.0,
        }
        reverse_issues = {
            "Бог Меча": {
                "complete": complete_entries,
                "orphans": [orphan_entry],
            }
        }
        original_glossary = complete_entries + [orphan_entry]

        resolver = ReverseConflictResolverPage(reverse_issues, original_glossary)
        self.addCleanup(resolver.close)
        return resolver

    def test_apply_note_to_all_copies_note_into_every_complete_row(self):
        resolver = self._build_resolver()

        self.assertEqual(resolver.complete_table.rowCount(), 2)
        # Сирота лежит в подготовленных данных под собственным _resolver_id.
        orphan_entry = resolver.reverse_issues["Бог Меча"]["orphans"][0]

        resolver._apply_note_to_all(orphan_entry)

        # Примечание должно попасть в колонку 2 ("Примечание") ОБЕИХ строк.
        note_col_0 = resolver.complete_table.item(0, 2).text()
        note_col_1 = resolver.complete_table.item(1, 2).text()
        self.assertEqual(note_col_0, "ВАЖНОЕ ПРИМЕЧАНИЕ")
        self.assertEqual(note_col_1, "ВАЖНОЕ ПРИМЕЧАНИЕ")

        # Патч должен переносить примечание в обе записи, а не просто
        # молча стирать запись-сироту без применения куда-либо.
        patch = resolver.get_patch()
        updates = [p for p in patch if p["after"] is not None]
        deletions = [p for p in patch if p["after"] is None]

        self.assertEqual(len(updates), 2)
        for change in updates:
            self.assertEqual(change["after"].get("note"), "ВАЖНОЕ ПРИМЕЧАНИЕ")

        self.assertEqual(len(deletions), 1)
        self.assertEqual(deletions[0]["before"]["rus"], "Бог Меча")
        self.assertEqual(deletions[0]["before"]["original"], "")


class DirectConflictTableDeleteStaleIndexTests(unittest.TestCase):
    """Находка 2: удаление строки в табличном режиме прямых конфликтов."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _build_dialog(self):
        conflicts = {
            "Alpha": [{"rus": "А1", "note": ""}],
            "Bravo": [{"rus": "Б1", "note": ""}],
            "Charlie": [{"rus": "Ч1", "note": ""}],
            "Delta": [{"rus": "Д1", "note": ""}],
            "Echo": [{"rus": "Э1", "note": ""}],
        }
        dlg = DirectConflictResolverDialog(conflicts, morph=None)
        self.addCleanup(dlg.close)
        return dlg

    @staticmethod
    def _terms(dlg):
        return [dlg.table.item(i, 0).text() for i in range(dlg.table.rowCount())]

    @staticmethod
    def _click_delete_button(dlg, row):
        actions_widget = dlg.table.cellWidget(row, 5)
        layout = actions_widget.layout()
        # Без morph в actions_layout лежит только кнопка удаления.
        delete_btn = layout.itemAt(layout.count() - 1).widget()
        delete_btn.click()

    def test_deleting_a_row_does_not_remove_the_wrong_term_afterwards(self):
        dlg = self._build_dialog()

        self.assertEqual(
            self._terms(dlg), ["Alpha", "Bravo", "Charlie", "Delta", "Echo"]
        )

        # Первое удаление (визуальная строка 2 = Charlie) отрабатывает верно
        # даже в дефектной версии, т.к. индексы ещё не сместились.
        self._click_delete_button(dlg, 2)
        self.assertEqual(self._terms(dlg), ["Alpha", "Bravo", "Delta", "Echo"])

        # Теперь визуальная строка 2 показывает Delta. Пользователь жмёт
        # кнопку удаления именно у Delta — должна остаться Echo.
        self._click_delete_button(dlg, 2)
        self.assertEqual(
            self._terms(dlg),
            ["Alpha", "Bravo", "Echo"],
            "Кнопка удаления должна убирать видимый термин (Delta), "
            "а не термин, оказавшийся под старым индексом (Echo).",
        )


class DirectConflictTableDeleteDoesNotResetOtherRowsTests(unittest.TestCase):
    """Регрессия рецензента 2a: полная перестройка таблицы при удалении
    одной строки сбрасывала выбор пользователя во всех остальных строках."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _build_dialog(self):
        conflicts = {
            "Alpha": [{"rus": "А1", "note": ""}],
            "Bravo": [
                {"rus": "Б1", "note": ""},
                {"rus": "Б2", "note": "заметка Б2"},
            ],
            "Charlie": [{"rus": "Ч1", "note": ""}],
        }
        dlg = DirectConflictResolverDialog(conflicts, morph=None)
        self.addCleanup(dlg.close)
        return dlg

    @staticmethod
    def _terms(dlg):
        return [dlg.table.item(i, 0).text() for i in range(dlg.table.rowCount())]

    @staticmethod
    def _row_of(dlg, term):
        return DirectConflictTableDeleteDoesNotResetOtherRowsTests._terms(dlg).index(term)

    @staticmethod
    def _click_delete_button(dlg, row):
        actions_widget = dlg.table.cellWidget(row, 5)
        layout = actions_widget.layout()
        delete_btn = layout.itemAt(layout.count() - 1).widget()
        delete_btn.click()

    def test_deleting_a_row_preserves_choices_made_in_other_rows(self):
        dlg = self._build_dialog()

        bravo_row = self._row_of(dlg, "Bravo")
        bravo_combo = dlg.table.cellWidget(bravo_row, 2)
        bravo_combo.setCurrentIndex(1)  # выбираем "Б2" — с примечанием
        self.assertEqual(bravo_combo.currentText(), "Б2")
        self.assertEqual(dlg.table.item(bravo_row, 4).text(), "заметка Б2")

        charlie_row = self._row_of(dlg, "Charlie")
        dlg.table.item(charlie_row, 4).setText("правка пользователя")

        # Удаляем Alpha — это не должно затронуть Bravo/Charlie.
        alpha_row = self._row_of(dlg, "Alpha")
        self._click_delete_button(dlg, alpha_row)

        self.assertEqual(self._terms(dlg), ["Bravo", "Charlie"])

        bravo_row = self._row_of(dlg, "Bravo")
        bravo_combo = dlg.table.cellWidget(bravo_row, 2)
        self.assertEqual(
            bravo_combo.currentText(),
            "Б2",
            "Удаление другой строки не должно сбрасывать выбранный "
            "пользователем вариант в комбобоксе Bravo.",
        )
        self.assertEqual(
            dlg.table.item(bravo_row, 4).text(),
            "заметка Б2",
            "Примечание Bravo не должно перезаписываться на значение по "
            "умолчанию при удалении другой строки.",
        )

        charlie_row = self._row_of(dlg, "Charlie")
        self.assertEqual(
            dlg.table.item(charlie_row, 4).text(),
            "правка пользователя",
            "Ручная правка примечания Charlie не должна теряться при "
            "удалении другой строки.",
        )

        dlg._save_table_changes()
        self.assertEqual(
            dlg.resolved_glossary,
            {
                "Bravo": {"rus": "Б2", "note": "заметка Б2"},
                "Charlie": {"rus": "Ч1", "note": "правка пользователя"},
            },
        )

    def test_deleting_a_row_updates_the_conflicts_count_label(self):
        dlg = self._build_dialog()

        top_bar_layout = dlg.table_widget.layout().itemAt(0).layout()
        label = top_bar_layout.itemAt(0).widget()
        self.assertIn("3", label.text())

        alpha_row = self._row_of(dlg, "Alpha")
        self._click_delete_button(dlg, alpha_row)

        self.assertIn(
            "2",
            label.text(),
            "Счётчик «Найдено N терминов…» должен обновляться и при "
            "ручном удалении строки, а не только при авто-схлопывании.",
        )


class DirectConflictTableDeleteKeepsWizardModeUsableTests(unittest.TestCase):
    """Регрессия рецензента 2b: после ручного удаления строки self.conflicts
    и wizard_conflicts_list расходились, и переход в пошаговый режим падал
    с KeyError."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _build_dialog(self):
        conflicts = {
            "Alpha": [{"rus": "А1", "note": ""}],
            "Bravo": [{"rus": "Б1", "note": ""}],
            "Charlie": [{"rus": "Ч1", "note": ""}],
        }
        dlg = DirectConflictResolverDialog(conflicts, morph=None)
        self.addCleanup(dlg.close)
        return dlg

    @staticmethod
    def _terms(dlg):
        return [dlg.table.item(i, 0).text() for i in range(dlg.table.rowCount())]

    @staticmethod
    def _click_delete_button(dlg, row):
        actions_widget = dlg.table.cellWidget(row, 5)
        layout = actions_widget.layout()
        delete_btn = layout.itemAt(layout.count() - 1).widget()
        delete_btn.click()

    def test_deleting_a_row_syncs_wizard_list_and_switching_view_does_not_raise(self):
        dlg = self._build_dialog()

        self._click_delete_button(dlg, 0)  # удаляем Alpha
        self.assertEqual(self._terms(dlg), ["Bravo", "Charlie"])
        self.assertNotIn("Alpha", dlg.conflicts)
        self.assertNotIn(
            "Alpha",
            dlg.wizard_conflicts_list,
            "wizard_conflicts_list должен синхронизироваться с "
            "self.conflicts сразу после ручного удаления строки.",
        )

        # До фикса здесь падало KeyError: 'Alpha', т.к.
        # _display_wizard_step обращался к self.conflicts[term] по
        # устаревшему списку визарда.
        dlg.switch_to_wizard_view()

        self.assertEqual(dlg.stacked_widget.currentWidget(), dlg.wizard_widget)
        self.assertIn(dlg.wizard_term_label.text(), dlg.wizard_conflicts_list)


class DirectConflictDialogDoesNotMutateCallersConflictsDictTests(unittest.TestCase):
    """Минорная находка рецензента: self.conflicts был тем же объектом, что
    и словарь конфликтов вызывающей страницы (GlossaryManagerPage
    .direct_conflicts), поэтому ручное удаление меняло его немедленно, даже
    если пользователь потом нажмёт «Отмена»."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_deleting_a_row_does_not_mutate_the_dict_passed_by_the_caller(self):
        callers_conflicts = {
            "Alpha": [{"rus": "А1", "note": ""}],
            "Bravo": [{"rus": "Б1", "note": ""}],
        }
        dlg = DirectConflictResolverDialog(callers_conflicts, morph=None)
        self.addCleanup(dlg.close)

        actions_widget = dlg.table.cellWidget(0, 5)
        layout = actions_widget.layout()
        delete_btn = layout.itemAt(layout.count() - 1).widget()
        delete_btn.click()

        self.assertNotIn("Alpha", dlg.conflicts)
        self.assertIn(
            "Alpha",
            callers_conflicts,
            "Диалог не должен мутировать словарь конфликтов, переданный "
            "вызывающей стороной — только свою собственную копию.",
        )


if __name__ == "__main__":
    unittest.main()
