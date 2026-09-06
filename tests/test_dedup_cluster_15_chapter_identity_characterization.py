"""
Характеризационные тесты для cluster-15: chapter_identity.

_chapter_identity (chapter_selection_dialog.py:39) и _chapter_id
(consistency_checker.py:1086) были дословными копиями (с точностью до
имени/докстроки/аннотации типа) одной и той же функции вычисления
стабильного идентификатора главы. Каноническая реализация вынесена в
gemini_translator.utils.chapter_identity.chapter_identity.

Эти тесты фиксируют поведение канонической реализации на граничных
случаях, которые различали бы копии, если бы они разошлись:
не-словарь, отсутствие обоих ключей, приоритет 'path' над 'name',
обрезка пробелов, нестроковые значения.
"""
from gemini_translator.utils.chapter_identity import chapter_identity


def test_non_dict_returns_empty_string():
    assert chapter_identity("not a dict") == ""
    assert chapter_identity(None) == ""
    assert chapter_identity(["path", "x"]) == ""


def test_missing_path_and_name_returns_empty_string():
    assert chapter_identity({}) == ""
    assert chapter_identity({"other": "value"}) == ""


def test_path_takes_precedence_over_name():
    chapter = {"path": "Text/ch1.xhtml", "name": "Глава 1"}
    assert chapter_identity(chapter) == "Text/ch1.xhtml"


def test_falls_back_to_name_when_path_missing():
    chapter = {"name": "Глава 1"}
    assert chapter_identity(chapter) == "Глава 1"


def test_falls_back_to_name_when_path_is_falsy():
    # path == "" или None считается отсутствующим (or-цепочка)
    assert chapter_identity({"path": "", "name": "Глава 1"}) == "Глава 1"
    assert chapter_identity({"path": None, "name": "Глава 1"}) == "Глава 1"


def test_strips_whitespace():
    chapter = {"path": "  Text/ch1.xhtml  "}
    assert chapter_identity(chapter) == "Text/ch1.xhtml"


def test_non_string_values_are_coerced_and_stripped():
    chapter = {"path": 123}
    assert chapter_identity(chapter) == "123"
