# -*- coding: utf-8 -*-
"""
Каноническое место для CJK/Unicode диапазонов (cluster-32).

До этого рефакторинга один и тот же узкий диапазон
``r'[\\u4e00-\\u9fff\\u3040-\\u30ff\\uac00-\\ud7af]'`` (CJK Unified Ideographs
без Ext-A + хирагана/катакана + хангыль) был набран вручную минимум в 4
местах (``core/auto_workflow_helpers.py``, ``ui/dialogs/validation.py``,
``ui/dialogs/validation_dialogs/untranslated_fixer_dialog.py``,
``utils/language_tools.py``), а полное определение (с CJK Ext-A,
compatibility ideographs, CJK-пунктуацией и Kangxi radicals) жило только в
``ui/dialogs/validation_dialogs/untranslated_detector.py`` как
``UnicodeRanges.ALL_CJK_PATTERN``.

``UnicodeRanges`` перенесён сюда БЕЗ ИЗМЕНЕНИЙ, посимвольно (включая
неиспользуемые ``*_EXTENDED``-константы с невалидным \\uXXXXX-эскейпом
внутри raw-строки — это тот же самый "мёртвый" текст, что и в оригинале,
трогать его не входит в этот дедуп). ``untranslated_detector.py`` теперь
импортирует класс отсюда — старые импорты/реэкспорты (например,
``validation_dialogs/__init__.py``) продолжают работать без изменений, потому что это
тот же объект класса (``untranslated_detector.UnicodeRanges is
cjk_ranges.UnicodeRanges``).

Дополнительно здесь собраны готовые скомпилированные паттерны,
покрывающие диапазоны, которые реально дублировались, чтобы
места-дубликаты могли ссылаться на атрибут этого модуля
(``cjk_ranges.XXX``) вместо своей копии скомпилированного regex:

- ``CORE_CJK_CHAR_RE`` — узкий диапазон (Unified без Ext-A + кана + хангыль),
  использовавшийся в 4 местах выше. Поведенчески НЕ изменён: тот же
  набор символов, что и раньше, только один источник истины.
- ``CJK_WITH_EXT_A_CHAR_RE`` — узкий диапазон + CJK Ext-A. Ровно то, что уже
  (до этого рефакторинга) использовал utils/helpers.py для оценки
  токенов (``_CJK_RUN_PATTERN``) — тоже без изменения поведения.
- ``ALL_CJK_PATTERN`` / ``ALL_CJK_CHAR_RE`` — alias на широкий
  ``UnicodeRanges.ALL_CJK_PATTERN``.
- ``CHINESE_CHAR_RE`` / ``JAPANESE_CHAR_RE`` / ``KOREAN_CHAR_RE`` — те же три
  под-диапазона, которые ``LanguageDetector`` в language_tools.py набирал
  вручную по отдельности для ``contains_chinese``/``contains_japanese``/
  ``contains_korean``.

Единственное реальное изменение поведения при переходе на этот модуль —
классификация ``lang_tag`` в untranslated_fixer_dialog.py: она переведена
с узкого диапазона на ``CJK_WITH_EXT_A_CHAR_RE`` (см. dedup cluster-32),
потому что термины с символами CJK Ext-A получали lang_tag='other' и
пропадали из списка при фильтре только по категории 'cjk' — подтверждённый
баг. НЕ широкий ``ALL_CJK_CHAR_RE``: он также включает CJK-пунктуацию и
Kangxi radicals, из-за чего одиночные символы вроде '】' (U+3011) стали бы
'cjk' вместо 'other' — это сломало бы существующий пин поведения в
tests/test_untranslated_fixer_navigation.py. Остальные места (``helpers.py``,
``auto_workflow_helpers.py``, ``validation.py``, ``language_tools.py``)
сохраняют прежний узкий диапазон намеренно: они участвуют в калиброванных
числовых порогах/эвристиках (оценка токенов, ``AUTO_CJK_SHORT_RATIO_LIMIT``),
и расширение диапазона там без отдельной проверки/перекалибровки — не входит
в этот дедуп.

Раунд 2 (по замечаниям ревью): добавлены ``CORE_CJK_RUN_RE`` /
``CJK_WITH_EXT_A_RUN_RE`` — те же диапазоны, что и одноимённые ``*_CHAR_RE``,
но со скомпилированным квантификатором "+" (нужен utils/helpers.py для
подсчёта СЕРИЙ CJK-символов, а не Match-объекта на каждый символ — иначе
finditer на длинном тексте ~40x медленнее, см. docstring над определениями
ниже); и ``CJK_SCRIPTS_CHAR_RE`` — все CJK-письменности без пунктуации и
радикалов (закрывает разрыв между широким детектором кандидатов и узкой
классификацией lang_tag в untranslated_fixer_dialog.py для Compatibility
Ideographs и Bopomofo).
"""

import re


# =============================================================================
# CJK and Unicode Character Ranges (перенесено без изменений из
# ui/dialogs/validation_dialogs/untranslated_detector.py)
# =============================================================================

class UnicodeRanges:
    """Comprehensive Unicode ranges for character classification."""
    
    # CJK Unified Ideographs (Chinese)
    CJK_UNIFIED_IDEOGRAPHS = r'\u4e00-\u9fff'
    CJK_UNIFIED_IDEOGRAPHS_EXT_A = r'\u3400-\u4dbf'
    CJK_UNIFIED_IDEOGRAPHS_EXT_B = r'\U00020000-\U0002a6df'
    CJK_UNIFIED_IDEOGRAPHS_EXT_C = r'\U0002a700-\U0002b73f'
    CJK_UNIFIED_IDEOGRAPHS_EXT_D = r'\U0002b740-\U0002b81f'
    CJK_UNIFIED_IDEOGRAPHS_EXT_E = r'\U0002b820-\U0002ceaf'
    CJK_UNIFIED_IDEOGRAPHS_EXT_F = r'\U0002ceb0-\U0002ebef'
    CJK_COMPATIBILITY_IDEOGRAPHS = r'\uf900-\ufaff'
    CJK_COMPATIBILITY_IDEOGRAPHS_SUPPLEMENT = r'\U0002f800-\U0002fa1f'
    
    # Japanese Hiragana and Katakana
    HIRAGANA = r'\u3040-\u309f'
    HIRAGANA_EXTENDED = r'\u1b001-\u1b11f'
    KATAKANA = r'\u30a0-\u30ff'
    KATAKANA_PHONETIC_EXTENSIONS = r'\u31f0-\u31ff'
    KATAKANA_SMALL = r'\u3248-\u324f'
    KATAKANA_EXTENDED = r'\u1b000-\u1b001'
    
    # Korean Hangul
    HANGUL_SYLLABLES = r'\uac00-\ud7af'
    HANGUL_JAMO = r'\u1100-\u11ff'
    HANGUL_COMPATIBILITY_JAMO = r'\u3130-\u318f'
    HANGUL_JAMO_EXTENDED_A = r'\ua960-\ua97f'
    HANGUL_JAMO_EXTENDED_B = r'\ud7b0-\ud7ff'
    
    # Bopomofo (Zhuyin) - Used for Chinese phonetic notation
    BOPOMOFO = r'\u3100-\u312f'
    BOPOMOFO_EXTENDED = r'\u31a0-\u31bf'
    
    # Other CJK symbols and punctuation
    CJK_SYMBOLS_AND_PUNCTUATION = r'\u3000-\u303f'
    CJK_STROKES = r'\u31c0-\u31ef'
    CJK_RADICALS_SUPPLEMENT = r'\u2e80-\u2eff'
    KANGXI_RADICALS = r'\u2f00-\u2fdf'
    IDEOGRAPHIC_DESCRIPTION_CHARACTERS = r'\u2ff0-\u2fff'
    
    # Combined pattern for all CJK characters (EXPANDED)
    ALL_CJK_PATTERN = (
        f'[{CJK_UNIFIED_IDEOGRAPHS}'
        f'{CJK_UNIFIED_IDEOGRAPHS_EXT_A}'
        f'{HIRAGANA}'
        f'{KATAKANA}'
        f'{HANGUL_SYLLABLES}'
        f'{BOPOMOFO}'
        f'{CJK_COMPATIBILITY_IDEOGRAPHS}'
        f'{CJK_SYMBOLS_AND_PUNCTUATION}'
        f'{KANGXI_RADICALS}'
        f']'
    )
    
    # Extended pattern including less common ranges
    ALL_CJK_EXTENDED_PATTERN = (
        f'[{CJK_UNIFIED_IDEOGRAPHS}'
        f'{CJK_UNIFIED_IDEOGRAPHS_EXT_A}'
        f'{CJK_UNIFIED_IDEOGRAPHS_EXT_B}'
        f'{HIRAGANA}'
        f'{HIRAGANA_EXTENDED}'
        f'{KATAKANA}'
        f'{KATAKANA_PHONETIC_EXTENSIONS}'
        f'{HANGUL_SYLLABLES}'
        f'{HANGUL_JAMO}'
        f'{BOPOMOFO}'
        f'{BOPOMOFO_EXTENDED}'
        f']'
    )


# =============================================================================
# Готовые паттерны для мест-дубликатов
# =============================================================================

# Узкий диапазон: CJK Unified (без Ext-A) + кана + хангыль. Ровно то, что было
# набрано вручную в auto_workflow_helpers.py, untranslated_fixer_dialog.py
# (до фикса lang_tag-бага), validation.py и как три отдельных под-паттерна в
# language_tools.py.
CORE_CJK_PATTERN = (
    f'[{UnicodeRanges.CJK_UNIFIED_IDEOGRAPHS}'
    f'{UnicodeRanges.HIRAGANA}'
    f'{UnicodeRanges.KATAKANA}'
    f'{UnicodeRanges.HANGUL_SYLLABLES}]'
)
CORE_CJK_CHAR_RE = re.compile(CORE_CJK_PATTERN)

# Узкий диапазон + CJK Ext-A. Ровно то, что уже использовал utils/helpers.py
# (_CJK_RUN_PATTERN) для оценки токенов.
CJK_WITH_EXT_A_PATTERN = (
    f'[{UnicodeRanges.CJK_UNIFIED_IDEOGRAPHS_EXT_A}'
    f'{UnicodeRanges.CJK_UNIFIED_IDEOGRAPHS}'
    f'{UnicodeRanges.HIRAGANA}'
    f'{UnicodeRanges.KATAKANA}'
    f'{UnicodeRanges.HANGUL_SYLLABLES}]'
)
CJK_WITH_EXT_A_CHAR_RE = re.compile(CJK_WITH_EXT_A_PATTERN)

# Широкий диапазон (Ext-A + compat ideographs + бопомофо + CJK-пунктуация +
# Kangxi radicals) — alias на UnicodeRanges.ALL_CJK_PATTERN.
ALL_CJK_PATTERN = UnicodeRanges.ALL_CJK_PATTERN
ALL_CJK_CHAR_RE = re.compile(ALL_CJK_PATTERN)

# Под-диапазоны по языку — то, что LanguageDetector в language_tools.py набирал
# вручную по отдельности.
CHINESE_CHAR_RE = re.compile(f'[{UnicodeRanges.CJK_UNIFIED_IDEOGRAPHS}]+')
JAPANESE_CHAR_RE = re.compile(f'[{UnicodeRanges.HIRAGANA}{UnicodeRanges.KATAKANA}]+')
KOREAN_CHAR_RE = re.compile(f'[{UnicodeRanges.HANGUL_SYLLABLES}]+')


# =============================================================================
# Run-варианты (ревью cluster-32, раунд 2, major): для мест, которые считают
# ОБЩЕЕ ЧИСЛО CJK-символов в длинном тексте (а не просто "есть ли хоть один"),
# посимвольный `search`/`findall` без квантификатора "+" на длинной серии
# аллоцирует один Match-объект на КАЖДЫЙ символ — на тексте в сотни КБ это
# сотни тысяч Match. С "+" одна непрерывная серия CJK-символов — это один
# Match (замер: 120 000 символов подряд — 0.22мс с "+" против 10.04мс без,
# ~40x). Числовой результат (сумма длин серий) идентичен в обоих случаях —
# меняется только то, как символы группируются в Match-объекты.
#
# utils/helpers.py._count_chars (оценка токенов Gemini, вызывается на каждый
# API-запрос и в цикле по всем главам книги в UI-потоке) полагается именно на
# эту оптимизацию — используй RUN-варианты там, где считаешь длину/количество
# символов в потенциально длинном тексте; CHAR_RE-варианты выше — там, где
# достаточно знать, есть ли совпадение (`search`/`match` на одном символе).
# =============================================================================

CORE_CJK_RUN_RE = re.compile(CORE_CJK_PATTERN + '+')
CJK_WITH_EXT_A_RUN_RE = re.compile(CJK_WITH_EXT_A_PATTERN + '+')


# =============================================================================
# CJK_SCRIPTS_*  (ревью cluster-32, раунд 2, minor): все "буквенные" CJK-
# письменности — Unified + Ext-A + Compatibility Ideographs + кана + хангыль +
# бопомофо — БЕЗ CJK-пунктуации (CJK_SYMBOLS_AND_PUNCTUATION) и БЕЗ Kangxi
# radicals. Нужен потому что untranslated_detector.py ищет кандидатов через
# широкий ALL_CJK_PATTERN (который включает Compatibility Ideographs
# U+F900-FAFF и Bopomofo U+3100-312F), а классификация lang_tag в
# untranslated_fixer_dialog.py раньше шла через CJK_WITH_EXT_A_CHAR_RE, где
# этих двух диапазонов нет — термин из совместимых иероглифов или бопомофо
# находился детектором, но получал lang_tag='other' и пропадал при фильтре
# 'cjk' (тот же класс бага, что уже чинили для Ext-A). Пунктуация/радикалы
# сюда намеренно НЕ входят: одиночные символы вроде '】' (U+3011) должны
# остаться 'other' — это закреплённое поведение,
# tests/test_untranslated_fixer_navigation.py::test_symbol_term_is_available_as_filter_candidate.
# =============================================================================

CJK_SCRIPTS_PATTERN = (
    f'[{UnicodeRanges.CJK_UNIFIED_IDEOGRAPHS}'
    f'{UnicodeRanges.CJK_UNIFIED_IDEOGRAPHS_EXT_A}'
    f'{UnicodeRanges.CJK_COMPATIBILITY_IDEOGRAPHS}'
    f'{UnicodeRanges.HIRAGANA}'
    f'{UnicodeRanges.KATAKANA}'
    f'{UnicodeRanges.HANGUL_SYLLABLES}'
    f'{UnicodeRanges.BOPOMOFO}]'
)
CJK_SCRIPTS_CHAR_RE = re.compile(CJK_SCRIPTS_PATTERN)
