import pytest

from gemini_translator.utils.text import prettify_html


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "<p>─ Это всё проделки этой твари! ─ прорычал один из них.</p>",
            "<p>— Это всё проделки этой твари! — прорычал один из них.</p>",
        ),
        (
            "<p>─ Но что? ─ с тревогой спросил Чэнь Муу.</p>",
            "<p>— Но что? — с тревогой спросил Чэнь Муу.</p>",
        ),
        (
            "<p>─ Доктор Чэнь, что за шутки! ─ подхватил Эддингтон.</p>",
            "<p>— Доктор Чэнь, что за шутки! — подхватил Эддингтон.</p>",
        ),
    ],
)
def test_prettify_keeps_author_words_lowercase_after_question_or_exclamation(
    source,
    expected,
):
    assert prettify_html(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "<p>Что случилось? никто не ответил.</p>",
            "<p>Что случилось? Никто не ответил.</p>",
        ),
        (
            "<p>Первое предложение. ─ второе предложение.</p>",
            "<p>Первое предложение. — Второе предложение.</p>",
        ),
    ],
)
def test_prettify_still_capitalizes_other_sentence_boundaries(source, expected):
    assert prettify_html(source) == expected
