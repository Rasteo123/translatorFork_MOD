# -*- coding: utf-8 -*-
"""
cluster-32: CJK-регекс набран вручную минимум в 5 местах вместо канонического
ALL_CJK_PATTERN/is_cjk_text.

Характеризационные тесты канонического модуля
gemini_translator.utils.cjk_ranges: закрепляют границы между узким диапазоном
(CORE_CJK_PATTERN, использовавшимся в 4 местах вручную), средним
(CJK_WITH_EXT_A_PATTERN, ровно то, что уже использовал utils/helpers.py) и
широким (ALL_CJK_PATTERN/UnicodeRanges.ALL_CJK_PATTERN, ровно то, что жило в
untranslated_detector.py) -- это как раз то, что различало копии до дедупа.
"""

import re

from gemini_translator.utils import cjk_ranges


def test_core_cjk_pattern_matches_basic_ranges():
    core = cjk_ranges.CORE_CJK_CHAR_RE
    assert core.search("一")  # CJK Unified Ideographs start (U+4E00)
    assert core.search("鿿")  # CJK Unified Ideographs end (U+9FFF)
    assert core.search("ぁ")  # Hiragana
    assert core.search("ヿ")  # Katakana end
    assert core.search("가")  # Hangul start
    assert core.search("힯")  # Hangul end


def test_core_cjk_pattern_does_not_match_latin_or_cyrillic():
    core = cjk_ranges.CORE_CJK_CHAR_RE
    assert core.search("A") is None
    assert core.search("Я") is None
    assert core.search(" ") is None


def test_core_cjk_pattern_excludes_ext_a_and_punctuation():
    # This is the confirmed divergence between the narrow copies and the wide
    # UnicodeRanges.ALL_CJK_PATTERN: CJK Ext-A / punctuation are NOT part of
    # the narrow "core" range.
    core = cjk_ranges.CORE_CJK_CHAR_RE
    assert core.search("㐀") is None  # CJK Unified Ideographs Ext-A start
    assert core.search("　") is None  # CJK ideographic space (punctuation)


def test_cjk_with_ext_a_pattern_matches_ext_a_but_not_punctuation():
    # Ровно то, что использовал utils/helpers.py._CJK_RUN_PATTERN до дедупа.
    mid = cjk_ranges.CJK_WITH_EXT_A_CHAR_RE
    assert mid.search("㐀")  # Ext-A now included
    assert mid.search("䶿")  # Ext-A end
    assert mid.search("一")
    assert mid.search("가")
    assert mid.search("　") is None  # punctuation still excluded


def test_all_cjk_pattern_includes_ext_a_and_punctuation_and_radicals():
    wide = cjk_ranges.ALL_CJK_CHAR_RE
    assert wide.search("㐀")  # Ext-A
    assert wide.search("一")
    assert wide.search("　")  # CJK symbols/punctuation
    assert wide.search("⼀")  # Kangxi radicals start


def test_all_cjk_pattern_is_unicode_ranges_all_cjk_pattern():
    assert cjk_ranges.ALL_CJK_PATTERN == cjk_ranges.UnicodeRanges.ALL_CJK_PATTERN


def test_unicode_ranges_all_cjk_pattern_locked_exact_string():
    # Замок на точный состав широкого паттерна, перенесённого из
    # untranslated_detector.py, -- рефакторинг не должен тихо расширить или
    # сузить его.
    r = cjk_ranges.UnicodeRanges
    expected = (
        f'[{r.CJK_UNIFIED_IDEOGRAPHS}'
        f'{r.CJK_UNIFIED_IDEOGRAPHS_EXT_A}'
        f'{r.HIRAGANA}'
        f'{r.KATAKANA}'
        f'{r.HANGUL_SYLLABLES}'
        f'{r.BOPOMOFO}'
        f'{r.CJK_COMPATIBILITY_IDEOGRAPHS}'
        f'{r.CJK_SYMBOLS_AND_PUNCTUATION}'
        f'{r.KANGXI_RADICALS}'
        f']'
    )
    assert r.ALL_CJK_PATTERN == expected


def test_core_cjk_pattern_equivalent_to_original_hand_typed_literal():
    # Оригинальный литерал, вручную набранный в auto_workflow_helpers.py,
    # untranslated_fixer_dialog.py (до фикса) и validation.py.
    original_narrow = re.compile(r'[一-鿿぀-ヿ가-힯]')
    probes = ["一", "鿿", "ぁ", "ヿ", "가", "힯", "A", "Я", " ", "㐀", "　"]
    for ch in probes:
        assert bool(cjk_ranges.CORE_CJK_CHAR_RE.search(ch)) == bool(original_narrow.search(ch)), ch


def test_cjk_with_ext_a_pattern_equivalent_to_original_helpers_literal():
    # Оригинальный литерал utils/helpers.py._CJK_RUN_PATTERN.
    original = re.compile(r'[㐀-䶿一-鿿぀-ヿ가-힯]+')
    probes = ["一", "鿿", "ぁ", "ヿ", "가", "힯", "A", "Я", " ", "㐀", "䶿", "　"]
    for ch in probes:
        assert bool(cjk_ranges.CJK_WITH_EXT_A_CHAR_RE.search(ch)) == bool(original.search(ch)), ch


def test_chinese_japanese_korean_sub_patterns_match_language_tools_originals():
    original_chinese = re.compile(r'[一-鿿]+')
    original_japanese = re.compile(r'[぀-ゟ゠-ヿ]+')
    original_korean = re.compile(r'[가-힯]+')
    probes = ["一", "ぁ", "ヿ", "가", "A", "㐀"]
    for ch in probes:
        assert bool(cjk_ranges.CHINESE_CHAR_RE.search(ch)) == bool(original_chinese.search(ch)), ch
        assert bool(cjk_ranges.JAPANESE_CHAR_RE.search(ch)) == bool(original_japanese.search(ch)), ch
        assert bool(cjk_ranges.KOREAN_CHAR_RE.search(ch)) == bool(original_korean.search(ch)), ch


def test_untranslated_detector_reexports_the_same_unicode_ranges_object():
    from gemini_translator.ui.dialogs.validation_dialogs import untranslated_detector

    assert untranslated_detector.UnicodeRanges is cjk_ranges.UnicodeRanges


# -----------------------------------------------------------------------------
# Ревью 2: "+"-квантификатор для run-паттернов (helpers.py._count_chars
# полагается на серии, а не на посимвольные Match-объекты -- см.
# gemini_translator/utils/helpers.py:_count_chars).
# -----------------------------------------------------------------------------

def test_cjk_with_ext_a_run_pattern_has_plus_quantifier():
    # Именно "+" даёт _count_chars в helpers.py эффект серий вместо
    # Match-объекта на каждый CJK-символ (см. ревью cluster-32, helpers.py:114).
    assert cjk_ranges.CJK_WITH_EXT_A_RUN_RE.pattern == cjk_ranges.CJK_WITH_EXT_A_PATTERN + '+'


def test_core_cjk_run_pattern_has_plus_quantifier():
    assert cjk_ranges.CORE_CJK_RUN_RE.pattern == cjk_ranges.CORE_CJK_PATTERN + '+'


def test_cjk_with_ext_a_run_pattern_matches_same_chars_as_char_pattern():
    # "+" не должен менять множество символов, которые он матчит -- только то,
    # как они группируются в Match-объекты.
    probes = ["一", "鿿", "ぁ", "ヿ", "가", "힯", "A", "Я", " ", "㐀", "䶿", "　"]
    for ch in probes:
        assert bool(cjk_ranges.CJK_WITH_EXT_A_RUN_RE.search(ch)) == bool(
            cjk_ranges.CJK_WITH_EXT_A_CHAR_RE.search(ch)
        ), ch


def test_cjk_with_ext_a_run_pattern_counts_runs_not_matches():
    # Ключевое свойство, ради которого нужен "+": одна серия из N CJK-символов
    # подряд -- это ОДИН Match (span длиной N), а не N отдельных Match.
    text = "一" * 5000
    matches = list(cjk_ranges.CJK_WITH_EXT_A_RUN_RE.finditer(text))
    assert len(matches) == 1
    assert matches[0].end() - matches[0].start() == 5000


# -----------------------------------------------------------------------------
# Ревью 2: побайтовая эквивалентность по ВСЕМ символам диапазона, а не по
# точечным пробам -- пробы не ловят ни потерю "+", ни расхождение на границах
# соседних блоков (぀-ゟ ゠-ヿ стык, 䶿/䷀ стык Ext-A).
# -----------------------------------------------------------------------------

def _charset(pattern_or_re, codepoints):
    return {cp for cp in codepoints if pattern_or_re.search(chr(cp))}


# Диапазон с запасом по обе стороны от всех задействованных CJK-блоков.
_FULL_PROBE_RANGE = list(range(0x2E00, 0xD7FF + 1)) + list(range(0xF900, 0xFAFF + 1))


def test_core_cjk_pattern_full_charset_equivalent_to_original_literal():
    original = re.compile(r'[一-鿿぀-ヿ가-힯]')
    assert _charset(cjk_ranges.CORE_CJK_CHAR_RE, _FULL_PROBE_RANGE) == _charset(
        original, _FULL_PROBE_RANGE
    )


def test_cjk_with_ext_a_pattern_full_charset_equivalent_to_original_helpers_literal():
    original = re.compile(r'[㐀-䶿一-鿿぀-ヿ가-힯]+')
    assert _charset(cjk_ranges.CJK_WITH_EXT_A_CHAR_RE, _FULL_PROBE_RANGE) == _charset(
        original, _FULL_PROBE_RANGE
    )


def test_chinese_japanese_korean_sub_patterns_full_charset_equivalent_to_originals():
    original_chinese = re.compile(r'[一-鿿]+')
    original_japanese = re.compile(r'[぀-ゟ゠-ヿ]+')
    original_korean = re.compile(r'[가-힯]+')
    assert _charset(cjk_ranges.CHINESE_CHAR_RE, _FULL_PROBE_RANGE) == _charset(
        original_chinese, _FULL_PROBE_RANGE
    )
    assert _charset(cjk_ranges.JAPANESE_CHAR_RE, _FULL_PROBE_RANGE) == _charset(
        original_japanese, _FULL_PROBE_RANGE
    )
    assert _charset(cjk_ranges.KOREAN_CHAR_RE, _FULL_PROBE_RANGE) == _charset(
        original_korean, _FULL_PROBE_RANGE
    )


# -----------------------------------------------------------------------------
# Ревью 2 (minor, untranslated_fixer_dialog.py): CJK Compatibility Ideographs
# (U+F900-FAFF) и Bopomofo (U+3100-312F) -- детектор кандидатов ищет их через
# широкий ALL_CJK_PATTERN, но классификация lang_tag по CJK_WITH_EXT_A_CHAR_RE
# их не покрывала -- тот же баг, что чинили для Ext-A. CJK_SCRIPTS_CHAR_RE
# закрывает разрыв: все "буквенные" CJK-письменности (без пунктуации/радикалов).
# -----------------------------------------------------------------------------

def test_cjk_scripts_pattern_covers_compat_ideographs_and_bopomofo():
    scripts = cjk_ranges.CJK_SCRIPTS_CHAR_RE
    assert scripts.search("一")
    assert scripts.search("㐀")  # Ext-A
    assert scripts.search("豈")  # CJK Compatibility Ideographs start
    assert scripts.search("ㄅ")  # Bopomofo ㄅ
    assert scripts.search("ぁ")
    assert scripts.search("가")


def test_cjk_scripts_pattern_excludes_punctuation_and_radicals():
    # '】' (U+3011, CJK Symbols and Punctuation) must stay 'other' --
    # tests/test_untranslated_fixer_navigation.py pins this.
    scripts = cjk_ranges.CJK_SCRIPTS_CHAR_RE
    assert scripts.search("】") is None
    assert scripts.search("　") is None  # ideographic space
    assert scripts.search("⼀") is None  # Kangxi radicals
