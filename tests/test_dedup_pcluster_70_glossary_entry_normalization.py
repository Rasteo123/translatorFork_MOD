"""pcluster-70 (правка по замечаниям рецензента): нормализация записи
глоссария реинвентировала каноническую glossary_tools.glossary_entry_key /
glossary_widget.normalize_imported_glossary_entry в нескольких местах.

(а) major: sorted_glossary_entries.sort_key (gemini_translator/ui/widgets/
    glossary_widget.py) считал `str(entry.get('original','') or '').strip()`
    + `.casefold()` инлайново — посимвольно то же выражение, что и
    glossary_tools.glossary_entry_key, только без isinstance-guard. Тест ниже
    обязан падать на дореформенном коде: sort_key не проходит через
    glossary_entry_key, поэтому mock.patch.object(..., wraps=...) не
    фиксирует ни одного вызова.

(б) minor: ProjectGlossaryController.find_entries/upsert_entry/delete_term
    (gemini_translator/ui/dialogs/validation_dialogs/
    untranslated_fixer_dialog.py) считали ключ ПОИСКОВОГО термина инлайново
    (`(term or '').strip().casefold()`), а не через каноническую
    glossary_entry_key({'original': term}) — расхождение не косметическое:
    канон оборачивает значение в str(...), инлайн падает на нестроковом term.

(в) minor: общая преамбула "dict глоссария -> список записей" дублировалась
    в glossary_snapshot и GlossaryWidget.set_glossary (обе в
    glossary_widget.py) — вынесена в glossary_entries_as_list. Общие четыре
    вызова normalize_glossary_field дублировались в
    normalize_imported_glossary_entry и set_glossary — вынесены в
    apply_glossary_field_normalization.
"""
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.ui.dialogs.validation_dialogs import untranslated_fixer_dialog as ufd
from gemini_translator.ui.widgets import glossary_widget
from gemini_translator.ui.widgets.glossary_widget import (
    GlossaryWidget,
    sorted_glossary_entries,
    glossary_snapshot,
)

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class _Node:
    """Обычный (не-Qt) объект с .parent() — этого достаточно для
    ProjectGlossaryController._discover_context."""

    def __init__(self, parent=None, **attrs):
        self._parent = parent
        for key, value in attrs.items():
            setattr(self, key, value)

    def parent(self):
        return self._parent


def _controller():
    owner = _Node(parent=_Node())
    return ufd.ProjectGlossaryController(owner)


# --- (а) sorted_glossary_entries обязан маршрутизироваться через glossary_entry_key ---

class SortedGlossaryEntriesKeyRoutingTests(unittest.TestCase):
    def test_sort_key_routes_through_canonical_glossary_entry_key(self):
        entries = [{"original": "Zeta"}, {"original": "Alpha"}]
        with mock.patch.object(
            glossary_widget, "glossary_entry_key", wraps=glossary_widget.glossary_entry_key
        ) as canon:
            result = sorted_glossary_entries(entries)
        canon.assert_any_call(entries[0])
        canon.assert_any_call(entries[1])
        self.assertEqual([e["original"] for e in result], ["Alpha", "Zeta"])

    def test_non_dict_entry_is_sorted_last_instead_of_crashing(self):
        # Побочный эффект маршрутизации через glossary_entry_key: она
        # безопасна на не-dict элементах (возвращает ""), в отличие от
        # старого инлайна entry.get(...), который падал с AttributeError.
        entries = ["not-a-dict", {"original": "Alpha"}]
        result = sorted_glossary_entries(entries)
        self.assertEqual(result[0], {"original": "Alpha"})
        self.assertEqual(result[1], "not-a-dict")

    def test_ordering_and_blank_last_preserved(self):
        entries = [
            {"original": "zeta", "rus": "zeta"},
            {"original": "", "rus": "blank"},
            {"original": "Alpha", "rus": "alpha"},
            {"original": "beta", "rus": "beta"},
        ]
        result = sorted_glossary_entries(entries)
        self.assertEqual(
            [entry["original"] for entry in result],
            ["Alpha", "beta", "zeta", ""],
        )


# --- (б) ProjectGlossaryController: ключ ПОИСКОВОГО термина через glossary_entry_key ---

class ProjectGlossaryControllerTermKeyRoutingTests(unittest.TestCase):
    def test_find_entries_term_key_routes_through_canonical_key(self):
        controller = _controller()
        entries = [{"original": "Foo", "rus": "a"}]
        with mock.patch.object(ufd, "glossary_entry_key", wraps=ufd.glossary_entry_key) as canon:
            controller.find_entries(entries, "Foo")
        # Канон обязан быть вызван и для самого термина (обёрнутого в dict),
        # и для записи глоссария.
        canon.assert_any_call({"original": "Foo"})
        canon.assert_any_call(entries[0])

    def test_find_entries_non_string_term_does_not_crash(self):
        controller = _controller()
        entries = [{"original": "123", "rus": "a"}]
        result = controller.find_entries(entries, 123)
        self.assertEqual(len(result), 1)

    def test_upsert_entry_term_key_routes_through_canonical_key(self):
        controller = _controller()
        entries = [{"original": "Foo", "rus": "a", "note": "", "timestamp": 1.0}]
        with mock.patch.object(ufd, "glossary_entry_key", wraps=ufd.glossary_entry_key) as canon:
            with mock.patch.object(controller, "save", side_effect=lambda e: e):
                controller.upsert_entry(entries, "Foo", "b", "")
        canon.assert_any_call({"original": "Foo"})

    def test_delete_term_term_key_routes_through_canonical_key(self):
        controller = _controller()
        entries = [{"original": "Foo", "rus": "a"}]
        with mock.patch.object(ufd, "glossary_entry_key", wraps=ufd.glossary_entry_key) as canon:
            with mock.patch.object(controller, "save", side_effect=lambda e: e):
                controller.delete_term(entries, "Foo")
        canon.assert_any_call({"original": "Foo"})

    def test_delete_term_non_string_term_does_not_crash(self):
        controller = _controller()
        entries = [{"original": "123", "rus": "a"}]
        with mock.patch.object(controller, "save", side_effect=lambda e: e):
            remaining, removed_count = controller.delete_term(entries, 123)
        self.assertEqual(removed_count, 1)
        self.assertEqual(remaining, [])


# --- (в) общая преамбула dict->list и нормализация полей вынесены в хелперы ---

class GlossaryEntriesAsListHelperTests(unittest.TestCase):
    def test_helper_exists_and_handles_dict_and_list(self):
        as_list = glossary_widget.glossary_entries_as_list
        self.assertEqual(
            as_list({"Foo": "bar"}),
            [{"original": "Foo", "rus": "bar"}],
        )
        self.assertEqual(
            as_list({"Foo": {"rus": "bar", "note": "n"}}),
            [{"original": "Foo", "rus": "bar", "note": "n"}],
        )
        entries = [{"original": "Foo"}]
        self.assertEqual(as_list(entries), entries)
        self.assertEqual(as_list("garbage"), [])

    def test_glossary_snapshot_routes_through_helper(self):
        with mock.patch.object(
            glossary_widget, "glossary_entries_as_list", wraps=glossary_widget.glossary_entries_as_list
        ) as helper:
            glossary_snapshot({"Foo": "bar"})
        helper.assert_called_once_with({"Foo": "bar"})

    def test_set_glossary_routes_through_helper(self):
        widget = GlossaryWidget()
        self.addCleanup(widget.close)
        with mock.patch.object(
            glossary_widget, "glossary_entries_as_list", wraps=glossary_widget.glossary_entries_as_list
        ) as helper:
            widget.set_glossary({"Foo": "bar"})
        helper.assert_called_once_with({"Foo": "bar"})
        self.assertEqual(widget.get_glossary()[0]["original"], "Foo")


class ApplyGlossaryFieldNormalizationHelperTests(unittest.TestCase):
    def test_helper_normalizes_the_four_fields_in_place(self):
        apply_norm = glossary_widget.apply_glossary_field_normalization
        entry = {"original": None, "rus": None, "note": None, "translation": None}
        result = apply_norm(entry)
        self.assertIs(result, entry)
        self.assertEqual(entry, {"original": "", "rus": "", "note": "", "translation": ""})

    def test_helper_skips_translation_field_when_absent(self):
        entry = {"original": "x", "rus": "y", "note": "z"}
        result = glossary_widget.apply_glossary_field_normalization(entry)
        self.assertNotIn("translation", result)

    def test_normalize_imported_glossary_entry_routes_through_helper(self):
        with mock.patch.object(
            glossary_widget,
            "apply_glossary_field_normalization",
            wraps=glossary_widget.apply_glossary_field_normalization,
        ) as helper:
            glossary_widget.normalize_imported_glossary_entry({"original": "Foo"})
        helper.assert_called_once()

    def test_set_glossary_routes_through_field_normalization_helper(self):
        widget = GlossaryWidget()
        self.addCleanup(widget.close)
        with mock.patch.object(
            glossary_widget,
            "apply_glossary_field_normalization",
            wraps=glossary_widget.apply_glossary_field_normalization,
        ) as helper:
            widget.set_glossary([{"original": "Foo", "rus": "bar"}])
        helper.assert_called_once()


if __name__ == "__main__":
    unittest.main()
