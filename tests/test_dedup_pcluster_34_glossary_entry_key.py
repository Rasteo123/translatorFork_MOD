"""pcluster-34: нормализация ключа записи глоссария (`original`) была
реализована повторно в ProjectGlossaryController.find_entries/upsert_entry/
delete_term (gemini_translator/ui/dialogs/validation_dialogs/
untranslated_fixer_dialog.py) через `.strip().casefold()` вместо канонической
glossary_tools.glossary_entry_key.

Волна ремонта (repair): ProjectGlossaryController._normalize_entries
(untranslated_fixer_dialog.py) и ConsistencyValidatorPage.
_normalize_shared_project_glossary_entries (consistency_checker.py:~1008) были
почти дословными близнецами — одинаковая распаковка dict/list, одинаковая
цепочка фолбэков перевода, одинаковая дедупликация по сигнатуре
(original.casefold(), rus, note). Реальных различий было два: фолбэк
примечания (note vs note/notes/definition) и дефолт timestamp
(entry.get('timestamp') or now vs entry.get('timestamp')). Обе копии сведены
к общей glossary_tools.normalize_glossary_entries(glossary_data, *,
note_fallbacks=(...), stamp_missing_timestamp=...), параметризованной именно
этими двумя различиями — поведение каждой копии сохранено побайтово (см.
NormalizeGlossaryEntriesCharacterizationTests и тесты маршрутизации ниже).

Также устранена ещё одна инлайновая копия ключа термина: setup.py
InitialSetupPage._merge_base_glossary_into_project_glossary считала
existing_keys/key через .lower().strip() вместо glossary_entry_key
(.strip().casefold()) — см. SetupMergeBaseGlossaryKeyRoutingTests. Это
осознанный сдвиг .lower() -> .casefold(), аналогичный прошлой волне.

Волна ремонта №2 (по замечаниям повторного ревью):

(major) validation.py: TranslationValidatorPage._get_effective_word_exceptions
(~2298) и _build_current_untranslated_exceptions (~5701) — близнецы: обе читают
project_glossary.json, разворачивают dict-или-list, извлекают перевод через
entry.get('rus') or entry.get('translation') or entry.get('target') or '' и
собирают латинские "остатки" (после вычитания кириллицы) как латинские слова
длины >= 2. Общий блок вынесен в _glossary_latin_residue_exceptions(exceptions_set,
warn_context=...) — см. TranslationValidatorPageWordExceptionsSharedHelperTests.
Единственное реальное различие (текст warn-сообщения в исключении) сохранено
через параметр.

(minor) setup.py: три копии построения карты {original: {'rus', 'note'}} для
GlossaryReplacer — _copy_original_chapters, _calibrate_cpu, get_settings.
_calibrate_cpu молча терял фолбэк 'rus' -> 'translation' (латентный баг того же
рода, ради которого кластер заведён). Все три сведены к
glossary_tools.glossary_list_to_replacer_map — см.
GlossaryListToReplacerMapCharacterizationTests, SetupGetSettingsGlossaryMapRoutingTests,
SetupCalibrateCpuGlossaryMapRoutingTests (последний характеризует исправление
бага: 'translation' теперь тоже фолбэк). _copy_original_chapters требует живого
QMessageBox.exec() модального цикла для тестового покрытия конца в конец, что
непропорционально дорого для minor-правки; проверен статическим разбором
исходника (SetupCopyOriginalChaptersSourceRoutingTest) — метод больше не
содержит инлайновый цикл построения карты, а вызывает канонический хелпер с той
же переменной glossary_list.

(а) Характеризационные тесты канонической glossary_entry_key.
(б) Тест-маршрутизация: find_entries/upsert_entry/delete_term обязаны
    вычислять ключ термина через glossary_entry_key. Эти тесты обязаны
    падать на дореформенном коде (AttributeError: модуль ufd ещё не
    импортирует glossary_entry_key), т.к. ключ вычислялся инлайново.
(в) GlossaryAggregator.merge (glossary_tools.py) считал тот же ключ ещё три
    раза инлайново через `.lower().strip()` (initial_map, key в supplement,
    db_map в update) вместо glossary_entry_key. Тесты ниже характеризуют два
    реальных дефекта старого инлайна и фиксируют осознанный сдвиг поведения
    .lower() -> .casefold():
      - AttributeError на нестроковом, но истинном 'original' в supplement
        (glossary_entry_key безопасен: str(...) перед strip/casefold);
      - .lower() не схлопывает ключи, различающиеся только на ß/подобных
        символах, а .casefold() (как в канонической функции) — схлопывает;
        это осознанный сдвиг, а не побайтовая эквивалентность.
"""
import inspect
import json
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.utils import glossary_tools
from gemini_translator.utils.glossary_tools import (
    GlossaryAggregator,
    glossary_entry_key,
    glossary_list_to_replacer_map,
    normalize_glossary_entries,
)
from gemini_translator.ui.dialogs.validation_dialogs import untranslated_fixer_dialog as ufd
from gemini_translator.ui.dialogs import consistency_checker
from gemini_translator.ui.dialogs import setup as setup_dialog
from gemini_translator.ui.dialogs import validation as validation_dialog
from gemini_translator.ui.widgets import glossary_widget

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


# --- (а) характеризационные тесты канонической glossary_entry_key ---

class GlossaryEntryKeyCharacterizationTests(unittest.TestCase):
    def test_strips_and_casefolds_original(self):
        self.assertEqual(glossary_entry_key({"original": "  Foo Bar  "}), "foo bar")

    def test_non_dict_entry_returns_empty_string(self):
        self.assertEqual(glossary_entry_key("not-a-dict"), "")
        self.assertEqual(glossary_entry_key(None), "")
        self.assertEqual(glossary_entry_key(["original", "x"]), "")

    def test_missing_original_returns_empty_string(self):
        self.assertEqual(glossary_entry_key({"rus": "x"}), "")

    def test_explicit_none_original_returns_empty_string_not_literal_none(self):
        # Граничный случай, который расходился со старым инлайновым
        # `str(entry.get('original', '')).strip().casefold()`: если ключ
        # 'original' присутствует со значением None, инлайновая версия
        # возвращала "none" (str(None)), а не пустую строку.
        self.assertEqual(glossary_entry_key({"original": None}), "")

    def test_glossary_widget_reexports_canonical_function(self):
        # glossary_widget.py больше не держит собственную копию — только
        # реэкспортирует каноническую из utils/glossary_tools.py.
        self.assertIs(glossary_widget.glossary_entry_key, glossary_entry_key)


# --- (б) маршрутизация ProjectGlossaryController на glossary_entry_key ---

class ProjectGlossaryControllerKeyRoutingTests(unittest.TestCase):
    def test_find_entries_routes_through_canonical_key(self):
        controller = _controller()
        entries = [{"original": "Foo", "rus": "a"}, {"original": "Bar", "rus": "b"}]
        with mock.patch.object(ufd, "glossary_entry_key", wraps=ufd.glossary_entry_key) as canon:
            result = controller.find_entries(entries, "foo")
        canon.assert_any_call(entries[0])
        canon.assert_any_call(entries[1])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["original"], "Foo")

    def test_upsert_entry_routes_through_canonical_key(self):
        controller = _controller()
        entries = [{"original": "Foo", "rus": "a", "note": "", "timestamp": 1.0}]
        with mock.patch.object(ufd, "glossary_entry_key", wraps=ufd.glossary_entry_key) as canon:
            with mock.patch.object(controller, "save", side_effect=lambda e: e):
                saved, info = controller.upsert_entry(entries, "Foo", "b", "")
        canon.assert_any_call(entries[0])
        self.assertEqual(info["status"], "updated")
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["rus"], "b")

    def test_delete_term_routes_through_canonical_key(self):
        controller = _controller()
        entries = [{"original": "Foo", "rus": "a"}, {"original": "Bar", "rus": "b"}]
        with mock.patch.object(ufd, "glossary_entry_key", wraps=ufd.glossary_entry_key) as canon:
            with mock.patch.object(controller, "save", side_effect=lambda e: e):
                remaining, removed_count = controller.delete_term(entries, "foo")
        canon.assert_any_call(entries[0])
        canon.assert_any_call(entries[1])
        self.assertEqual(removed_count, 1)
        self.assertEqual([e["original"] for e in remaining], ["Bar"])



# --- (в) GlossaryAggregator.merge должен считать ключ через glossary_entry_key,
# а не инлайново `.lower().strip()` (initial_map / key / db_map) ---

class GlossaryAggregatorMergeKeyRoutingTests(unittest.TestCase):
    def test_supplement_mode_routes_through_canonical_key(self):
        initial = [{"original": "Foo", "rus": "a"}]
        db_terms = [{"original": "Bar", "rus": "b"}]
        with mock.patch.object(
            glossary_tools, "glossary_entry_key", wraps=glossary_tools.glossary_entry_key
        ) as canon:
            result = GlossaryAggregator(initial, merge_mode="supplement").merge(db_terms)
        canon.assert_any_call(initial[0])
        canon.assert_any_call(db_terms[0])
        self.assertEqual([e["original"] for e in result], ["Foo", "Bar"])

    def test_update_mode_routes_through_canonical_key(self):
        initial = [{"original": "Foo", "rus": "a"}]
        db_terms = [{"original": "Foo", "rus": "b"}]
        with mock.patch.object(
            glossary_tools, "glossary_entry_key", wraps=glossary_tools.glossary_entry_key
        ) as canon:
            result = GlossaryAggregator(initial, merge_mode="update").merge(db_terms)
        canon.assert_any_call(initial[0])
        canon.assert_any_call(db_terms[0])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["rus"], "b")

    def test_supplement_mode_non_string_original_does_not_crash(self):
        # Старый инлайн `term.get('original', '').lower().strip()` падал с
        # AttributeError на истинном, но нестроковом original (например int) —
        # glossary_entry_key оборачивает значение в str(...) перед strip/casefold.
        initial = [{"original": "Foo", "rus": "a"}]
        db_terms = [{"original": 123, "rus": "b"}]
        result = GlossaryAggregator(initial, merge_mode="supplement").merge(db_terms)
        originals = [e["original"] for e in result]
        self.assertIn("Foo", originals)
        self.assertIn(123, originals)

    def test_update_mode_casefold_collapses_where_lower_would_not(self):
        # Осознанный сдвиг .lower() -> .casefold(): "Straße" и "STRASSE"
        # совпадают только под casefold (ß -> "ss"). Каноническая
        # glossary_entry_key использует casefold, поэтому после унификации
        # db-вариант должен победить как обновление ТОЙ ЖЕ записи, а не
        # остаться отдельной второй записью.
        initial = [{"original": "Straße", "rus": "a"}]
        db_terms = [{"original": "STRASSE", "rus": "b"}]
        result = GlossaryAggregator(initial, merge_mode="update").merge(db_terms)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["original"], "STRASSE")
        self.assertEqual(result[0]["rus"], "b")


# --- (г) normalize_glossary_entries: каноническая парсинг+дедупликация,
# параметризованная двумя реальными различиями между копиями ---

class NormalizeGlossaryEntriesCharacterizationTests(unittest.TestCase):
    def test_dict_form_uses_key_as_original(self):
        data = {"Foo": {"rus": "a"}, "ignored": "not-a-dict"}
        result = normalize_glossary_entries(data)
        self.assertEqual(result, [{"original": "Foo", "rus": "a", "note": "", "timestamp": None}])

    def test_list_form_and_translation_fallback_chain(self):
        data = [{"original": "Foo", "translation": "a"}, {"original": "Bar", "target": "b"}]
        result = normalize_glossary_entries(data)
        self.assertEqual([e["rus"] for e in result], ["a", "b"])

    def test_dedup_by_signature_original_rus_note(self):
        data = [
            {"original": "Foo", "rus": "a", "note": "x"},
            {"original": "foo", "rus": "a", "note": "x"},  # тот же casefold-ключ + rus/note -> дубль
            {"original": "Foo", "rus": "a", "note": "y"},  # другое note -> отдельная запись
        ]
        result = normalize_glossary_entries(data)
        self.assertEqual(len(result), 2)

    def test_fully_empty_entry_is_dropped(self):
        data = [{"original": "", "rus": "", "note": ""}]
        self.assertEqual(normalize_glossary_entries(data), [])

    def test_note_fallbacks_first_truthy_field_wins(self):
        entry = {"original": "Foo", "rus": "a", "note": "", "notes": "n2", "definition": "n3"}
        result = normalize_glossary_entries([entry], note_fallbacks=("note", "notes", "definition"))
        self.assertEqual(result[0]["note"], "n2")

    def test_note_fallbacks_restricted_to_note_only(self):
        # Поведение ufd: только поле 'note', без notes/definition.
        entry = {"original": "Foo", "rus": "a", "notes": "n2"}
        result = normalize_glossary_entries([entry], note_fallbacks=("note",))
        self.assertEqual(result[0]["note"], "")

    def test_stamp_missing_timestamp_false_keeps_none(self):
        result = normalize_glossary_entries([{"original": "Foo", "rus": "a"}], stamp_missing_timestamp=False)
        self.assertIsNone(result[0]["timestamp"])

    def test_stamp_missing_timestamp_true_fills_current_time(self):
        result = normalize_glossary_entries([{"original": "Foo", "rus": "a"}], stamp_missing_timestamp=True)
        self.assertIsInstance(result[0]["timestamp"], float)

    def test_stamp_missing_timestamp_true_preserves_existing(self):
        result = normalize_glossary_entries(
            [{"original": "Foo", "rus": "a", "timestamp": 42.0}], stamp_missing_timestamp=True
        )
        self.assertEqual(result[0]["timestamp"], 42.0)


class ProjectGlossaryControllerNormalizeRoutesToCanonicalTests(unittest.TestCase):
    """ProjectGlossaryController._normalize_entries должен делегировать в
    glossary_tools.normalize_glossary_entries(note_fallbacks=('note',),
    stamp_missing_timestamp=True) вместо собственного парсинга/дедупликации,
    сохраняя ПРЕЖНЕЕ наблюдаемое поведение (характеризация ниже — то же самое,
    что было в инлайновой версии до рефакторинга)."""

    def test_routes_through_canonical_normalize_function(self):
        controller = _controller()
        data = [{"original": "Foo", "rus": "a"}]
        with mock.patch.object(
            ufd, "normalize_glossary_entries", wraps=ufd.normalize_glossary_entries
        ) as canon:
            controller._normalize_entries(data)
        canon.assert_called_once_with(data, note_fallbacks=("note",), stamp_missing_timestamp=True)

    def test_dedup_and_note_fallback_behavior_preserved(self):
        controller = _controller()
        data = [
            {"original": "Foo", "rus": "a", "note": "", "notes": "should-be-ignored"},
            {"original": "foo", "rus": "a", "note": ""},  # дубль по сигнатуре
        ]
        result = controller._normalize_entries(data)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["note"], "")  # 'notes' не является фолбэком для ufd

    def test_missing_timestamp_is_stamped_with_current_time(self):
        controller = _controller()
        result = controller._normalize_entries([{"original": "Foo", "rus": "a"}])
        self.assertIsInstance(result[0]["timestamp"], float)


class ConsistencyValidatorPageNormalizeRoutesToCanonicalTests(unittest.TestCase):
    """ConsistencyValidatorPage._normalize_shared_project_glossary_entries должен
    делегировать в ту же каноническую функцию с note_fallbacks=('note', 'notes',
    'definition') и stamp_missing_timestamp=False (запись без timestamp остаётся
    с timestamp=None, как и раньше — эта копия никогда не проставляла время)."""

    def test_routes_through_canonical_normalize_function(self):
        data = [{"original": "Foo", "rus": "a"}]
        with mock.patch.object(
            consistency_checker, "normalize_glossary_entries",
            wraps=consistency_checker.normalize_glossary_entries,
        ) as canon:
            consistency_checker.ConsistencyValidatorPage._normalize_shared_project_glossary_entries(data)
        canon.assert_called_once_with(
            data, note_fallbacks=("note", "notes", "definition"), stamp_missing_timestamp=False
        )

    def test_note_fallback_chain_preserved(self):
        data = [{"original": "Foo", "rus": "a", "notes": "n2"}]
        result = consistency_checker.ConsistencyValidatorPage._normalize_shared_project_glossary_entries(data)
        self.assertEqual(result[0]["note"], "n2")

    def test_missing_timestamp_stays_none_not_stamped(self):
        data = [{"original": "Foo", "rus": "a"}]
        result = consistency_checker.ConsistencyValidatorPage._normalize_shared_project_glossary_entries(data)
        self.assertIsNone(result[0]["timestamp"])


# --- (д) InitialSetupPage._merge_base_glossary_into_project_glossary: ключ
# термина через glossary_entry_key, а не инлайновый .lower().strip() ---

class _FakeGlossaryWidget:
    def __init__(self, entries):
        self._entries = entries
        self.set_glossary_calls = []

    def get_glossary(self):
        return self._entries

    def set_glossary(self, entries):
        self.set_glossary_calls.append(entries)


class SetupMergeBaseGlossaryKeyRoutingTests(unittest.TestCase):
    def _merge(self, current_entries, base_entries):
        fake_self = mock.Mock(spec=["glossary_widget"])
        fake_self.glossary_widget = _FakeGlossaryWidget(current_entries)
        with mock.patch.object(setup_dialog.api_config, "load_base_glossary", return_value=base_entries):
            added = setup_dialog.InitialSetupPage._merge_base_glossary_into_project_glossary(
                fake_self, "some_base_id"
            )
        return added, fake_self.glossary_widget

    def test_routes_through_canonical_key(self):
        current = [{"original": "Foo", "rus": "a"}]
        base = [{"original": "Bar", "rus": "b"}]
        with mock.patch.object(
            setup_dialog, "glossary_entry_key", wraps=setup_dialog.glossary_entry_key
        ) as canon:
            added, widget = self._merge(current, base)
        self.assertTrue(canon.called)
        self.assertEqual(added, 1)
        self.assertEqual(len(widget.set_glossary_calls), 1)

    def test_casefold_collapses_where_lower_would_not(self):
        # Осознанный сдвиг .lower() -> .casefold(): "Straße" (текущий проектный
        # глоссарий) и "STRASSE" (базовый) должны считаться ОДНИМ термином под
        # casefold, поэтому базовая запись не добавляется как дубль.
        current = [{"original": "Straße", "rus": "a"}]
        base = [{"original": "STRASSE", "rus": "b"}]
        added, widget = self._merge(current, base)
        self.assertEqual(added, 0)
        self.assertEqual(widget.set_glossary_calls, [])

    def test_new_term_is_still_added(self):
        current = [{"original": "Foo", "rus": "a"}]
        base = [{"original": "Bar", "rus": "b"}]
        added, widget = self._merge(current, base)
        self.assertEqual(added, 1)
        self.assertEqual(widget.set_glossary_calls[0][-1]["original"], "Bar")


# --- Волна ремонта №2 (major): validation.py TranslationValidatorPage
# ._get_effective_word_exceptions / _build_current_untranslated_exceptions
# должны делегировать общий блок "прочитать project_glossary.json, извлечь
# латинские остатки" в один приватный метод, а не дублировать его дважды ---

class _FakeProjectManager:
    def __init__(self, project_folder):
        self.project_folder = project_folder


class _ValidatorWordExceptionsHarness:
    """Обычный (не-Qt, не-Mock) объект, вооружённый нужными методами
    TranslationValidatorPage напрямую с класса — так `self.<метод>(...)`
    внутри них резолвится через MRO этого класса, и mock.patch.object на
    самом харнессе (а не на тяжёлом Qt-классе) честно перехватывает вызовы."""

    _get_effective_word_exceptions = validation_dialog.TranslationValidatorPage._get_effective_word_exceptions
    _build_current_untranslated_exceptions = (
        validation_dialog.TranslationValidatorPage._build_current_untranslated_exceptions
    )
    _glossary_latin_residue_exceptions = (
        validation_dialog.TranslationValidatorPage._glossary_latin_residue_exceptions
    )

    def __init__(self, project_folder=None):
        self.settings_manager = None
        self.project_manager = _FakeProjectManager(project_folder) if project_folder else None


class TranslationValidatorPageWordExceptionsSharedHelperTests(unittest.TestCase):
    def test_get_effective_word_exceptions_routes_through_shared_helper(self):
        harness = _ValidatorWordExceptionsHarness()
        with mock.patch.object(
            harness,
            "_glossary_latin_residue_exceptions",
            wraps=harness._glossary_latin_residue_exceptions,
        ) as canon:
            result = harness._get_effective_word_exceptions()
        canon.assert_called_once()
        self.assertIsInstance(result, set)

    def test_build_current_untranslated_exceptions_routes_through_shared_helper(self):
        harness = _ValidatorWordExceptionsHarness()
        with mock.patch.object(
            harness,
            "_glossary_latin_residue_exceptions",
            wraps=harness._glossary_latin_residue_exceptions,
        ) as canon:
            result = harness._build_current_untranslated_exceptions()
        canon.assert_called_once()
        self.assertIsInstance(result, set)

    def test_both_methods_extract_identical_latin_residue_from_glossary(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            glossary_path = os.path.join(tmp_dir, "project_glossary.json")
            with open(glossary_path, "w", encoding="utf-8") as f:
                json.dump(
                    [
                        {"original": "Foo", "rus": "Текст Residue123 тест"},
                        {"original": "Bar", "translation": "TransWord еще"},
                        {"original": "Baz", "target": "TargetWord и"},
                        {"original": "Qux", "rus": ""},
                        "not-a-dict",
                    ],
                    f,
                )
            harness = _ValidatorWordExceptionsHarness(tmp_dir)
            result_a = harness._get_effective_word_exceptions()
            result_b = harness._build_current_untranslated_exceptions()

        for word in ("residue", "transword", "targetword"):
            self.assertIn(word, result_a)
            self.assertIn(word, result_b)
        self.assertEqual(result_a, result_b)

    def test_broken_glossary_file_does_not_crash_either_method(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            glossary_path = os.path.join(tmp_dir, "project_glossary.json")
            with open(glossary_path, "w", encoding="utf-8") as f:
                f.write("{not valid json")
            harness = _ValidatorWordExceptionsHarness(tmp_dir)
            result_a = harness._get_effective_word_exceptions()
            result_b = harness._build_current_untranslated_exceptions()
        self.assertIsInstance(result_a, set)
        self.assertIsInstance(result_b, set)


# --- Волна ремонта №2 (minor): setup.py — три копии построения карты
# {original: {'rus', 'note'}} для GlossaryReplacer сведены к
# glossary_tools.glossary_list_to_replacer_map ---

class GlossaryListToReplacerMapCharacterizationTests(unittest.TestCase):
    def test_translation_fallback_used_when_rus_missing(self):
        result = glossary_list_to_replacer_map([{"original": "Foo", "translation": "a"}])
        self.assertEqual(result, {"Foo": {"rus": "a", "note": ""}})

    def test_rus_preferred_over_translation_when_both_present(self):
        result = glossary_list_to_replacer_map([{"original": "Foo", "rus": "a", "translation": "b"}])
        self.assertEqual(result["Foo"]["rus"], "a")

    def test_blank_original_is_skipped(self):
        result = glossary_list_to_replacer_map([{"original": "  ", "rus": "a"}])
        self.assertEqual(result, {})

    def test_note_defaults_to_empty_string(self):
        result = glossary_list_to_replacer_map([{"original": "Foo", "rus": "a"}])
        self.assertEqual(result["Foo"]["note"], "")


class _StopAfterCall(Exception):
    pass


class SetupGetSettingsGlossaryMapRoutingTests(unittest.TestCase):
    def test_get_settings_routes_through_canonical_map_builder(self):
        fake_self = mock.Mock()
        glossary_list = [{"original": "Foo", "rus": "a"}]
        fake_self.glossary_widget.get_glossary.return_value = glossary_list
        with mock.patch.object(
            setup_dialog, "glossary_list_to_replacer_map", side_effect=_StopAfterCall
        ) as canon:
            with self.assertRaises(_StopAfterCall):
                setup_dialog.InitialSetupPage.get_settings(fake_self)
        canon.assert_called_once_with(glossary_list)


class SetupCalibrateCpuGlossaryMapRoutingTests(unittest.TestCase):
    def _fake_page(self, glossary_list):
        fake_self = mock.Mock()
        fake_self.glossary_widget.get_glossary.return_value = glossary_list
        fake_self.html_files = ["chapter1.html"]
        return fake_self

    def test_calibrate_cpu_routes_through_canonical_map_builder(self):
        glossary_list = [{"original": "Foo", "rus": "a"}]
        fake_self = self._fake_page(glossary_list)
        with mock.patch.object(
            setup_dialog, "glossary_list_to_replacer_map", side_effect=_StopAfterCall
        ) as canon:
            with self.assertRaises(_StopAfterCall):
                setup_dialog.InitialSetupPage._calibrate_cpu(fake_self, no_log=True)
        canon.assert_called_once()
        # Аргумент — срез current_glossary_list, не сам список целиком.
        (call_arg,) = canon.call_args.args
        self.assertEqual(list(call_arg), glossary_list[: setup_dialog.BENCHMARK_GLOSSARY_SIZE])

    def test_calibrate_cpu_translation_fallback_bug_is_fixed(self):
        # Раньше _calibrate_cpu терял фолбэк 'rus' -> 'translation' (в отличие
        # от get_settings/_copy_original_chapters) — запись с переводом только
        # в поле 'translation' попадала в выборку калибровки с пустым 'rus'.
        # После маршрутизации через общий glossary_list_to_replacer_map этого
        # расхождения больше нет.
        glossary_list = [{"original": "Foo", "translation": "TransOnly"}]
        fake_self = self._fake_page(glossary_list)
        captured = {}

        def _capture(entries):
            captured["map"] = glossary_list_to_replacer_map(entries)
            raise _StopAfterCall

        with mock.patch.object(setup_dialog, "glossary_list_to_replacer_map", side_effect=_capture):
            with self.assertRaises(_StopAfterCall):
                setup_dialog.InitialSetupPage._calibrate_cpu(fake_self, no_log=True)
        self.assertEqual(captured["map"]["Foo"]["rus"], "TransOnly")


# --- Волна ремонта №2 (minor): glossary_widget.glossary_entries_as_list и
# инлайновая распаковка dict-или-list внутри normalize_glossary_entries были
# двумя параллельными копиями одной и той же преамбулы, различавшимися только
# обработкой не-dict значения внутри dict-формы. Каноническая версия перенесена
# в glossary_tools.py (utils не может импортировать из ui), glossary_widget
# теперь только реэкспортирует её под тем же именем ---

class GlossaryEntriesAsListUnifiedHelperTests(unittest.TestCase):
    def test_canonical_lives_in_glossary_tools_and_is_reexported(self):
        self.assertIs(glossary_widget.glossary_entries_as_list, glossary_tools.glossary_entries_as_list)

    def test_wrap_scalar_as_rus_true_matches_old_glossary_widget_behavior(self):
        result = glossary_tools.glossary_entries_as_list({"Foo": "bar"}, wrap_scalar_as_rus=True)
        self.assertEqual(result, [{"original": "Foo", "rus": "bar"}])

    def test_wrap_scalar_as_rus_false_matches_old_normalize_glossary_entries_behavior(self):
        result = glossary_tools.glossary_entries_as_list({"Foo": "bar", "Baz": {"rus": "x"}}, wrap_scalar_as_rus=False)
        self.assertEqual(result, [{"original": "Baz", "rus": "x"}])

    def test_default_is_wrap_scalar_as_rus_true(self):
        result = glossary_tools.glossary_entries_as_list({"Foo": "bar"})
        self.assertEqual(result, [{"original": "Foo", "rus": "bar"}])

    def test_normalize_glossary_entries_routes_through_shared_helper(self):
        with mock.patch.object(
            glossary_tools, "glossary_entries_as_list", wraps=glossary_tools.glossary_entries_as_list
        ) as canon:
            normalize_glossary_entries({"Foo": "not-a-dict-value"})
        canon.assert_called_once_with({"Foo": "not-a-dict-value"}, wrap_scalar_as_rus=False)


class SetupCopyOriginalChaptersSourceRoutingTest(unittest.TestCase):
    """_copy_original_chapters требует живого модального QMessageBox.exec()
    для сквозного поведенческого теста (см. докстринг модуля) — проверяется
    статическим разбором исходника: метод больше не содержит инлайновый цикл
    построения карты и вызывает канонический хелпер с переменной glossary_list."""

    def test_no_longer_contains_inline_map_building_loop(self):
        source = inspect.getsource(setup_dialog.InitialSetupPage._copy_original_chapters)
        self.assertNotIn("full_glossary_data = {}", source)
        self.assertIn("glossary_list_to_replacer_map(glossary_list)", source)


if __name__ == "__main__":
    unittest.main()
