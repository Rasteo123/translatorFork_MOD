"""Свойства починки слипшихся русских слов.

`repair_glued_russian_words` режет строку и склеивает обратно по границам,
найденным морфологией. Ошибка в границах — это потерянная или задвоенная
буква прямо в готовом переводе, и увидит её только читатель.

Две особенности, выясненные генератором, а не чтением кода:

* починка вставляет не только пробел, но и дефис: «изподверь» превращается в
  «из-под верь», и это правильный русский. Поэтому сравнивать надо буквы без
  пробелов И без дефисов;
* слово, слипшееся из трёх частей, разбиралось за один проход не полностью:
  «дверьдверьИван» → «дверьдверь Иван», и вторая склейка доживала до
  следующего запуска проверки. Теперь проходы повторяются внутри функции до
  покоя; свойство сходимости ниже сторожит, что предел проходов достаточен.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from gemini_translator.utils.glued_words import (
    find_glued_russian_words,
    repair_glued_russian_words,
)

_WORDS = [
    "дверь", "открыл", "коридор", "шагнул", "тёмный", "пошёл", "дальше", "она",
    "сказала", "что", "это", "было", "очень", "странно", "город", "улица",
    "человек", "Иван", "Москва", "не", "и", "на", "по", "из", "под", "за",
    "мама", "мыла", "раму", "стол", "над",
]


@st.composite
def russian_prose(draw):
    """Текст, в котором часть слов намеренно слиплась."""
    words = []
    for _ in range(draw(st.integers(min_value=1, max_value=20))):
        word = draw(st.sampled_from(_WORDS))
        kind = draw(st.integers(min_value=0, max_value=5))
        if kind == 0:
            words.append(word + draw(st.sampled_from(_WORDS)))
        elif kind == 1:
            words.append(word + draw(st.sampled_from(_WORDS)) + draw(st.sampled_from(_WORDS)))
        elif kind == 2:
            words.append(word.capitalize())
        else:
            words.append(word)
    separator = draw(st.sampled_from([" ", "  ", " — ", ", ", ". ", "\n"]))
    return separator.join(words)


FAST_SETTINGS = settings(
    max_examples=80,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


def _letters(value: str) -> str:
    """Буквы без пробелов и дефисов: и то и другое починка вправе вставлять."""
    return "".join(value.split()).replace("-", "")


@FAST_SETTINGS
@given(text=russian_prose())
def test_repair_only_ever_inserts_separators(text):
    """Ни одна буква не должна пропасть или задвоиться."""
    repaired, _candidates = repair_glued_russian_words(text)
    assert _letters(repaired) == _letters(text), f"вход={text!r} выход={repaired!r}"


@FAST_SETTINGS
@given(text=russian_prose())
def test_repair_converges(text):
    """Повторная починка обязана прийти к покою, пусть и не с первого раза."""
    current = text
    for _ in range(5):
        following, _candidates = repair_glued_russian_words(current)
        if following == current:
            return
        current = following
    raise AssertionError(f"не сошлось за 5 проходов: {text!r} -> {current!r}")


@FAST_SETTINGS
@given(text=russian_prose())
def test_confident_candidates_are_bounded_and_disjoint(text):
    """Правки применяются срезами подряд — перекрытие испортило бы текст.

    `repair_glued_russian_words` идёт по уверенным кандидатам с конца и режет
    `text[:start] + replacement + text[end:]`. Если два таких кандидата
    пересекутся, второй срез затрёт результат первого.
    """
    candidates = find_glued_russian_words(text)
    for candidate in candidates:
        assert 0 <= candidate.start < candidate.end <= len(text), (
            f"границы вне текста: {candidate.start}..{candidate.end} при len={len(text)}"
        )

    confident = sorted(
        (candidate for candidate in candidates if candidate.confident),
        key=lambda candidate: candidate.start,
    )
    for earlier, later in zip(confident, confident[1:]):
        assert earlier.end <= later.start, (
            f"уверенные кандидаты перекрываются: "
            f"{earlier.start}..{earlier.end} и {later.start}..{later.end}"
        )


def test_a_triple_glue_is_fully_separated_in_one_call():
    """Регрессия: раньше за один вызов отваливалась только последняя часть.

    `validation.py` зовёт починку один раз за проверку, поэтому недоделанная
    склейка доживала до следующего запуска. Кандидаты при этом по-прежнему
    описывают НАЙДЕННОЕ, а не оставшееся, — на этом контракте держится отчёт.
    """
    repaired, candidates = repair_glued_russian_words("дверьдверьИван")

    assert repaired == "дверь дверь Иван"
    assert [candidate.original for candidate in candidates] == ["дверьдверьИван"]
