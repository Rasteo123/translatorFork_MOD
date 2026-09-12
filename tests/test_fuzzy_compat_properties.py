"""Свойства fuzzy-скоринга.

`fuzzy_compat` воспроизводит на rapidfuzz побайтовое поведение fuzzywuzzy,
потому что под него откалиброваны пороги Stage-3 глоссария и consistency
checker. Существующий `tests/test_fuzzy_compat.py` закрепляет это корпусом
примеров; здесь те же обязательства требуются от произвольных строк.

Самое нагруженное — эквивалентность `token_set_ratio_preclean` и
`token_set_ratio` на входах, уже прошедших `universal_cleaner`. На ней держится
право звать более дешёвый вариант в горячем пути: если эквивалентность
поедет, пороги начнут срабатывать иначе, и никто этого не заметит.
"""

from __future__ import annotations

import re

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from gemini_translator.utils import fuzzy_compat

# Latin-1 (é, ü, ß) здесь не случайно: force_ascii вырезает коды 128..255,
# и это одна из двух особенностей fuzzywuzzy, которые модуль повторяет.
_ALPHABET = "абвгдеёжзий ABCxyz_-.,!éüß测试0123"

any_text = st.text(alphabet=_ALPHABET, max_size=40)
# Строки, уже прошедшие universal_cleaner: \W+ -> ' ', lower, strip.
precleaned_text = any_text.map(
    lambda value: re.sub(r"\W+", " ", value, flags=re.UNICODE).lower().strip()
)

PROPERTY_SETTINGS = settings(
    max_examples=150,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

_SCORERS = (
    fuzzy_compat.ratio,
    fuzzy_compat.token_set_ratio,
    fuzzy_compat.token_set_ratio_preclean,
)


@PROPERTY_SETTINGS
@given(first=any_text, second=any_text)
def test_every_score_stays_within_the_calibrated_range(first, second):
    """Пороги сравнивают с числом 0..100 — выход за диапазон сломает их молча."""
    for scorer in _SCORERS:
        score = scorer(first, second)
        assert 0 <= score <= 100, f"{scorer.__name__}({first!r}, {second!r}) = {score}"


@PROPERTY_SETTINGS
@given(first=any_text, second=any_text)
def test_scores_do_not_depend_on_argument_order(first, second):
    assert fuzzy_compat.ratio(first, second) == fuzzy_compat.ratio(second, first)
    assert fuzzy_compat.token_set_ratio(first, second) == fuzzy_compat.token_set_ratio(
        second, first
    )


@PROPERTY_SETTINGS
@given(value=any_text)
def test_a_string_matches_itself_completely(value):
    if value:
        assert fuzzy_compat.ratio(value, value) == 100


@PROPERTY_SETTINGS
@given(value=any_text)
def test_preprocessing_is_idempotent(value):
    """Иначе эквивалентность ниже держалась бы на удаче."""
    once = fuzzy_compat._full_process(value)
    assert fuzzy_compat._full_process(once) == once


@PROPERTY_SETTINGS
@given(first=precleaned_text, second=precleaned_text)
def test_precleaned_shortcut_matches_the_full_scorer(first, second):
    """Дешёвый вариант обязан давать ровно тот же балл на очищенных входах."""
    assert fuzzy_compat.token_set_ratio_preclean(first, second) == (
        fuzzy_compat.token_set_ratio(first, second)
    )
