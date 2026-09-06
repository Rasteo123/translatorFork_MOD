"""
Тесты для cluster-00: ChapterData и auto_fill_missing_chapter_numbers
дублировались целиком в ranobelib/models.py и ranobelib/utils.py.

Каноническая реализация: ranobelib/models.py (все 7 потребителей уже
импортируют её оттуда). Копия в ranobelib/utils.py была мёртвым кодом
(нигде не импортировалась и не вызывалась) и подлежит удалению.

(a) Характеризационные тесты фиксируют поведение канонической реализации
    (ChapterData.__repr__/preview/content_length/num_found и граничные
    случаи auto_fill_missing_chapter_numbers), чтобы рефакторинг ничего
    не сломал.
(b) "Маршрутизационный" тест для этого кластера — это утверждение, что
    модуль utils больше не определяет свою копию ChapterData /
    auto_fill_missing_chapter_numbers: поскольку копия в utils.py была
    мёртвым кодом (ни один вызывающий код её не использовал), классической
    подмены "вызов идёт не туда" здесь нет — до рефакторинга тест падает
    из-за самого факта существования дублирующего определения в utils.py,
    после рефакторинга проходит, когда дубликат удалён.
"""
import os
import sys

TESTS_DIR = os.path.dirname(__file__)
PROJECT_ROOT = os.path.dirname(TESTS_DIR)
RANOBELIB_DIR = os.path.join(PROJECT_ROOT, "ranobelib")

if RANOBELIB_DIR not in sys.path:
    sys.path.insert(0, RANOBELIB_DIR)

from models import ChapterData, auto_fill_missing_chapter_numbers  # noqa: E402


# ─── (a) Характеризационные тесты канонической реализации ────────────────────

def test_init_sets_all_fields():
    ch = ChapterData("2", 5.0, "Название", "<p>текст</p>", _parse_index=3, _num_found=False)
    assert ch.volume == "2"
    assert ch.number == 5.0
    assert ch.title == "Название"
    assert ch.content == "<p>текст</p>"
    assert ch._parse_index == 3
    assert ch.num_found is False


def test_repr_with_title():
    ch = ChapterData("1", 5.0, "Начало", "текст")
    assert repr(ch) == "Т.1 Гл.5: Начало"


def test_repr_without_title():
    ch = ChapterData("1", 5.5, "", "текст")
    assert repr(ch) == "Т.1 Гл.5.5"


def test_content_length():
    ch = ChapterData("1", 1.0, "", "0123456789")
    assert ch.content_length == 10


def test_preview_strips_html_and_truncates():
    long_text = "<p>" + ("а" * 250) + "</p>"
    ch = ChapterData("1", 1.0, "", long_text)
    preview = ch.preview
    assert "<p>" not in preview
    assert len(preview) == 201  # 200 символов + многоточие
    assert preview.endswith("…")


def test_preview_short_content_no_ellipsis():
    ch = ChapterData("1", 1.0, "", "<b>коротко</b>")
    assert ch.preview == "коротко"


def test_auto_fill_empty_list_noop():
    chapters = []
    auto_fill_missing_chapter_numbers(chapters)
    assert chapters == []


def test_auto_fill_no_numbers_sequential():
    chapters = [
        ChapterData("1", 0.0, "Пролог", "т", _num_found=False),
        ChapterData("1", 0.0, "Интро", "т", _num_found=False),
    ]
    auto_fill_missing_chapter_numbers(chapters)
    assert [c.number for c in chapters] == [1.0, 2.0]


def test_auto_fill_forward_from_previous():
    chapters = [
        ChapterData("1", 100.0, "", "т"),
        ChapterData("1", 101.0, "", "т"),
        ChapterData("1", 0.0, "Экстра", "т", _num_found=False),
        ChapterData("1", 0.0, "Экстра 2", "т", _num_found=False),
    ]
    auto_fill_missing_chapter_numbers(chapters)
    assert [c.number for c in chapters] == [100.0, 101.0, 102.0, 103.0]


def test_auto_fill_backward_anchor_when_no_previous():
    chapters = [
        ChapterData("1", 0.0, "Пролог", "т", _num_found=False),
        ChapterData("1", 0.0, "Интро", "т", _num_found=False),
        ChapterData("1", 10.0, "", "т"),
    ]
    auto_fill_missing_chapter_numbers(chapters)
    assert [c.number for c in chapters] == [8.0, 9.0, 10.0]


# ─── (b) Дубликат в utils.py должен быть удалён ───────────────────────────────

def test_utils_no_longer_defines_duplicate_chapterdata():
    import utils as ranobelib_utils

    assert not hasattr(ranobelib_utils, "ChapterData"), (
        "ranobelib/utils.py всё ещё содержит дубликат класса ChapterData "
        "(мёртвый код, канонический источник — ranobelib/models.py)"
    )
    assert not hasattr(ranobelib_utils, "auto_fill_missing_chapter_numbers"), (
        "ranobelib/utils.py всё ещё содержит дубликат "
        "auto_fill_missing_chapter_numbers (канонический источник — models.py)"
    )
