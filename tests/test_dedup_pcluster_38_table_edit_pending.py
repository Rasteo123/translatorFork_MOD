"""pcluster-38: обработчик «правка ячейки таблицы -> pending_changes» был
буквально продублирован между ComplexOverlapResolverPage._on_sub_table_item_changed
(conflict_resolvers.py) и CoreTermAnalyzerPage._on_sub_table_item_changed
(core_term_dialog.py). Третий метод кластера, ReverseConflictResolverPage.
_on_table_item_changed, использует другую модель данных (единый dict вместо
кортежа (term, data)) и намеренно не объединяется (см. notes в отчёте).

Этот файл:
  1) характеризует поведение канонической функции
     conflict_resolvers.apply_sub_table_edit_to_pending;
  2) проверяет, что оба места вызова реально идут через неё (routing test,
     обязан падать до рефакторинга и проходить после);
  3) проверяет, что core_term_dialog.py остаётся импортируемым САМ ПО СЕБЕ,
     в чистом интерпретаторе, без циклического импорта через
     conflict_resolvers -> ui.widgets -> glossary_widget -> glossary.py
     (замечание ревьюера pcluster-38: батч-прогоны и обычный `import
     unittest`-запуск маскируют регресс, потому что gemini_translator.ui.widgets
     успевает импортироваться раньше другим тестом/файлом в том же процессе).
"""
import os
import subprocess
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QTableWidget, QTableWidgetItem
from PyQt6.QtCore import Qt

# Импорт glossary_widget первым избегает частичной инициализации модуля
# conflict_resolvers из-за цикла glossary.py <-> glossary_dialogs (см.
# tests/test_fix_g05_conflict_resolvers.py, тот же приём).
from gemini_translator.ui.widgets.glossary_widget import GlossaryWidget  # noqa: F401

from gemini_translator.ui.dialogs.glossary_dialogs import conflict_resolvers
from gemini_translator.ui.dialogs.glossary_dialogs.conflict_resolvers import (
    ComplexOverlapResolverPage,
    apply_sub_table_edit_to_pending,
)
from gemini_translator.ui.dialogs.glossary_dialogs.core_term_dialog import (
    CoreTermAnalyzerPage,
)


class TestModuleImportIsolation(unittest.TestCase):
    """core_term_dialog.py обязан импортироваться сам по себе, в свежем
    процессе, без прогрева ui.widgets другим модулем (иначе цикл
    glossary_dialogs <-> ui.widgets.glossary_widget <-> dialogs.glossary
    молча маскируется порядком сборки тестов).

    conflict_resolvers.py сюда сознательно не включён: он не импортируется
    в изоляции ни до, ни после этого рефакторинга (тянет
    ui.widgets.common_widgets -> ui.widgets.__init__ -> тот же цикл) —
    это pre-existing поведение вне охвата pcluster-38, а не регресс.
    """

    def test_core_term_dialog_importable_standalone(self):
        env = dict(os.environ)
        env["QT_QPA_PLATFORM"] = "offscreen"
        result = subprocess.run(
            [
                sys.executable, "-c",
                "import gemini_translator.ui.dialogs.glossary_dialogs.core_term_dialog"
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(
            result.returncode, 0,
            msg=f"stdout={result.stdout}\nstderr={result.stderr}"
        )


def _make_table_with_id_row(row_id, row=0):
    """QTableWidget с одной строкой; столбец 0 несёт идентификатор в UserRole."""
    table = QTableWidget(1, 3)
    id_item = QTableWidgetItem("")
    id_item.setData(Qt.ItemDataRole.UserRole, row_id)
    table.setItem(row, 0, id_item)
    return table


def _edited_item(table, row, col, text):
    """Имитирует правку ячейки пользователем: меняет ТЕКСТ существующего
    QTableWidgetItem (как это делает Qt при редактировании), а не подменяет
    объект целиком — иначе правка столбца 0 стёрла бы UserRole-идентификатор,
    который в реальном коде хранится в том же item, что и видимый текст.
    """
    item = table.item(row, col)
    if item is None:
        item = QTableWidgetItem()
        table.setItem(row, col, item)
    item.setText(text)
    return item


class TestApplySubTableEditToPendingCharacterization(unittest.TestCase):
    """Характеризационные тесты канонической реализации."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_ignores_columns_outside_0_1_2(self):
        table = _make_table_with_id_row("term1")
        item = _edited_item(table, 0, 3, "junk")
        pending_changes = {}
        baseline_lookup = mock.Mock(return_value={})

        apply_sub_table_edit_to_pending(table, item, pending_changes, baseline_lookup)

        self.assertEqual(pending_changes, {})
        baseline_lookup.assert_not_called()

    def test_missing_id_item_is_noop(self):
        table = QTableWidget(1, 3)  # столбец 0 пуст -> id_item is None
        item = _edited_item(table, 0, 1, "rus text")
        pending_changes = {}
        baseline_lookup = mock.Mock(return_value={})

        apply_sub_table_edit_to_pending(table, item, pending_changes, baseline_lookup)

        self.assertEqual(pending_changes, {})
        baseline_lookup.assert_not_called()

    def test_new_term_uses_baseline_lookup_and_copies_it(self):
        table = _make_table_with_id_row("term1")
        baseline = {"rus": "old_rus", "note": "old_note"}
        item = _edited_item(table, 0, 1, "new_rus")
        pending_changes = {}
        baseline_lookup = mock.Mock(return_value=baseline)

        apply_sub_table_edit_to_pending(table, item, pending_changes, baseline_lookup)

        baseline_lookup.assert_called_once_with("term1")
        self.assertEqual(
            pending_changes["term1"], ("term1", {"rus": "new_rus", "note": "old_note"})
        )
        # baseline-словарь не должен быть замутирован напрямую (используется .copy()).
        self.assertEqual(baseline, {"rus": "old_rus", "note": "old_note"})

    def test_editing_column_0_renames_term_keeps_data(self):
        table = _make_table_with_id_row("term1")
        item = _edited_item(table, 0, 0, "renamed_term")
        pending_changes = {}
        baseline_lookup = mock.Mock(return_value={"rus": "r", "note": "n"})

        apply_sub_table_edit_to_pending(table, item, pending_changes, baseline_lookup)

        self.assertEqual(
            pending_changes["term1"], ("renamed_term", {"rus": "r", "note": "n"})
        )

    def test_second_edit_reuses_pending_tuple_value(self):
        """dict.get(key, default) вычисляет default-выражение всегда (таково
        поведение обеих оригинальных копий), но раз ключ уже есть в
        pending_changes, вычисленное baseline-значение отбрасывается и в
        результат идёт именно накопленная запись, а не свежий baseline.
        """
        table = _make_table_with_id_row("term1")
        pending_changes = {"term1": ("term1", {"rus": "r1", "note": "n1"})}
        baseline_lookup = mock.Mock(return_value={"rus": "STALE", "note": "STALE"})

        item = _edited_item(table, 0, 2, "n2")
        apply_sub_table_edit_to_pending(table, item, pending_changes, baseline_lookup)

        self.assertEqual(pending_changes["term1"], ("term1", {"rus": "r1", "note": "n2"}))


class TestRoutingThroughCanonicalHelper(unittest.TestCase):
    """Оба места вызова обязаны идти через apply_sub_table_edit_to_pending."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_complex_overlap_resolver_page_routes_through_canonical(self):
        page = ComplexOverlapResolverPage.__new__(ComplexOverlapResolverPage)
        page.original_glossary = {"term1": {"rus": "r", "note": "n"}}
        page.pending_changes = {}
        page.sub_terms_table = _make_table_with_id_row("term1")
        item = _edited_item(page.sub_terms_table, 0, 1, "new_rus")

        with mock.patch.object(
            conflict_resolvers, "apply_sub_table_edit_to_pending"
        ) as mocked:
            page._on_sub_table_item_changed(item)

        mocked.assert_called_once()
        called_table, called_item, called_pending, called_lookup = mocked.call_args[0]
        self.assertIs(called_table, page.sub_terms_table)
        self.assertIs(called_item, item)
        self.assertIs(called_pending, page.pending_changes)
        # lookup должен обращаться именно к self.original_glossary (dict.get).
        self.assertEqual(called_lookup("term1"), {"rus": "r", "note": "n"})
        self.assertEqual(called_lookup("missing"), {})

    def test_core_term_analyzer_page_routes_through_canonical(self):
        # CoreTermAnalyzerPage._on_sub_table_item_changed импортирует
        # apply_sub_table_edit_to_pending лениво, внутри тела метода (см.
        # комментарий в core_term_dialog.py — иначе модульный импорт цикличен
        # через ui.widgets.__init__ -> glossary_widget -> dialogs.glossary).
        # Поэтому имя, которое реально резолвится в момент вызова, живёт в
        # неймспейсе conflict_resolvers, а не core_term_dialog — патчим там.
        page = CoreTermAnalyzerPage.__new__(CoreTermAnalyzerPage)
        page.original_glossary_list = [
            {"original": "term1", "rus": "r", "note": "n"},
            {"original": "term2", "rus": "r2", "note": "n2"},
        ]
        page.pending_changes = {}
        page.members_table = _make_table_with_id_row("term1")
        item = _edited_item(page.members_table, 0, 1, "new_rus")

        with mock.patch.object(
            conflict_resolvers, "apply_sub_table_edit_to_pending"
        ) as mocked:
            page._on_sub_table_item_changed(item)

        mocked.assert_called_once()
        called_table, called_item, called_pending, called_lookup = mocked.call_args[0]
        self.assertIs(called_table, page.members_table)
        self.assertIs(called_item, item)
        self.assertIs(called_pending, page.pending_changes)
        # lookup должен обращаться именно к original_glossary_list (линейный поиск).
        self.assertEqual(called_lookup("term1"), {"original": "term1", "rus": "r", "note": "n"})
        self.assertEqual(called_lookup("missing"), {})


if __name__ == "__main__":
    unittest.main()
