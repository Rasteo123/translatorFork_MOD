# -*- coding: utf-8 -*-
"""
cluster-32: маршрутизационные тесты.

Каждый тест подменяет (monkeypatch) один из канонических паттернов в
gemini_translator.utils.cjk_ranges и проверяет, что бывшее место-дубликат
реально смотрит на канонический атрибут МОДУЛЯ (а не на свою собственную,
скомпилированную при импорте копию regex). До рефакторинга (когда каждое
место держало собственный re.compile(...)) эти тесты падают, потому что
подмена cjk_ranges.XXX никак не влияет на локальную копию.
"""

import re
import types


def test_auto_workflow_helpers_text_has_cjk_routes_through_core_pattern(monkeypatch):
    from gemini_translator.core import auto_workflow_helpers
    from gemini_translator.utils import cjk_ranges

    fake = re.compile(r"Q")
    monkeypatch.setattr(cjk_ranges, "CORE_CJK_CHAR_RE", fake)

    # A non-CJK "probe" char that only the faked canonical pattern matches.
    assert auto_workflow_helpers.text_has_cjk("Q") is True
    # A real CJK char no longer matches once the canonical pattern is faked --
    # proves the call site does not keep its own compiled copy.
    assert auto_workflow_helpers.text_has_cjk("一") is False


def test_validation_initial_cjk_scan_routes_through_core_pattern(monkeypatch, tmp_path):
    import zipfile

    from gemini_translator.ui.dialogs import validation as validation_mod
    from gemini_translator.utils import cjk_ranges

    epub_path = tmp_path / "book.epub"
    # 150 occurrences of a "fake CJK" probe char, zero real CJK chars.
    chapter_html = "<p>" + "Q" * 150 + "</p>"
    with zipfile.ZipFile(epub_path, "w") as zf:
        zf.writestr("chapter1.xhtml", chapter_html)

    fake = re.compile(r"Q")
    monkeypatch.setattr(cjk_ranges, "CORE_CJK_CHAR_RE", fake)

    switched_to = []

    class FakeCombo:
        def findText(self, text):
            return 0

        def setCurrentIndex(self, idx):
            switched_to.append(idx)

    fake_self = types.SimpleNamespace(
        original_epub_path=str(epub_path),
        ratio_presets_combo=FakeCombo(),
    )

    validation_mod.TranslationValidatorDialog._perform_initial_cjk_scan(fake_self)

    # The preset switch only fires once >=100 "CJK" chars are counted -- with
    # the canonical pattern faked to match 'Q', the 150 probe chars must be
    # what triggers it (the real regex would count 0 and never switch).
    assert switched_to == [0]


def test_fixer_dialog_lang_tag_routes_through_cjk_scripts_pattern(monkeypatch):
    from gemini_translator.ui.dialogs.validation_dialogs import untranslated_fixer_dialog as fixer_mod
    from gemini_translator.utils import cjk_ranges

    # NB: the classification deliberately routes through CJK_SCRIPTS_CHAR_RE
    # (narrow + Ext-A + compat ideographs + bopomofo), not the wide
    # ALL_CJK_CHAR_RE -- see test_fixer_dialog_lang_tag_uses_ext_a_pattern_not_wide_pattern
    # below for why (ALL_CJK_CHAR_RE also matches CJK punctuation, which would
    # wrongly reclassify symbols like '】' as 'cjk').
    fake = re.compile(r"Q")
    monkeypatch.setattr(cjk_ranges, "CJK_SCRIPTS_CHAR_RE", fake)

    fake_self = types.SimpleNamespace(
        original_data=[
            {"term": "Q", "context": ""},
            {"term": "一", "context": ""},  # real CJK char "一"
        ]
    )

    fixer_mod.UntranslatedFixerPage._pre_analyze_data(fake_self)

    assert fake_self.original_data[0]["lang_tag"] == "cjk"
    # Once the canonical pattern is faked, a real CJK char no longer gets
    # classified as 'cjk' by this call site's OWN copy of the regex --
    # proving it goes through cjk_ranges.CJK_WITH_EXT_A_CHAR_RE, not a local one.
    assert fake_self.original_data[1]["lang_tag"] != "cjk"


def test_fixer_dialog_lang_tag_fixes_confirmed_ext_a_bug(monkeypatch):
    """
    Confirmed bug (cluster-32 evidence): a term containing a CJK Ext-A
    character used to get lang_tag='other' (narrow pattern had no Ext-A) and
    silently vanished from the untranslated-fixer table when the user
    filtered to only the 'cjk' category. Locks the fix without monkeypatching.
    """
    from gemini_translator.ui.dialogs.validation_dialogs import untranslated_fixer_dialog as fixer_mod

    fake_self = types.SimpleNamespace(
        original_data=[{"term": "㐀", "context": ""}],  # U+3400, CJK Ext-A
    )

    fixer_mod.UntranslatedFixerPage._pre_analyze_data(fake_self)

    assert fake_self.original_data[0]["lang_tag"] == "cjk"


def test_fixer_dialog_lang_tag_uses_scripts_pattern_not_wide_pattern():
    """
    Guards against a naive "just use the widest pattern" fix: the wide
    ALL_CJK_CHAR_RE also matches CJK symbols/punctuation (e.g. the bracket
    '】', U+3011), which tests/test_untranslated_fixer_navigation.py pins as
    lang_tag='other' (it is an alien symbol, not a CJK term). Routing through
    CJK_SCRIPTS_CHAR_RE instead fixes the Ext-A/compat/bopomofo gap without
    this regression.
    """
    from gemini_translator.ui.dialogs.validation_dialogs import untranslated_fixer_dialog as fixer_mod

    fake_self = types.SimpleNamespace(
        original_data=[{"term": "】", "context": ""}],
    )

    fixer_mod.UntranslatedFixerPage._pre_analyze_data(fake_self)

    assert fake_self.original_data[0]["lang_tag"] == "other"


def test_fixer_dialog_lang_tag_fixes_compat_ideograph_and_bopomofo_gap():
    """
    Confirmed follow-up gap (cluster-32 review round 2): the detector finds
    candidates via the wide ALL_CJK_PATTERN (which includes CJK Compatibility
    Ideographs U+F900-FAFF and Bopomofo U+3100-312F), but classification used
    to route through CJK_WITH_EXT_A_CHAR_RE, which has neither range -- the
    exact same "vanishes from the cjk filter" bug that was fixed for Ext-A,
    just for a different pair of ranges. CJK_SCRIPTS_CHAR_RE closes it.
    """
    from gemini_translator.ui.dialogs.validation_dialogs import untranslated_fixer_dialog as fixer_mod

    fake_self = types.SimpleNamespace(
        original_data=[
            {"term": "豈", "context": ""},  # CJK Compatibility Ideographs start
            {"term": "ㄅ", "context": ""},  # Bopomofo
        ],
    )

    fixer_mod.UntranslatedFixerPage._pre_analyze_data(fake_self)

    assert fake_self.original_data[0]["lang_tag"] == "cjk"
    assert fake_self.original_data[1]["lang_tag"] == "cjk"


def test_helpers_estimate_gemini_tokens_routes_through_ext_a_run_pattern(monkeypatch):
    from gemini_translator.utils import cjk_ranges, helpers

    # A Greek letter: not ASCII, not Cyrillic, not (real) CJK -- lands in the
    # 'other' bucket unless the canonical CJK-with-Ext-A RUN pattern is faked
    # to match it. NB: this call site must use the "+"-quantified RUN variant
    # (not the per-char CHAR_RE) -- see helpers.py:_count_chars docstring on
    # why series matter for perf; this test patches the RUN name specifically
    # so a regression back to CJK_WITH_EXT_A_CHAR_RE would leave it unaffected
    # and fail.
    text = "Ω" * 40  # 'Ω' x 40

    fake = re.compile("Ω+")
    monkeypatch.setattr(cjk_ranges, "CJK_WITH_EXT_A_RUN_RE", fake)

    tokens = helpers.estimate_gemini_tokens(text)

    # cjk_chars=40 -> 40 / GEMINI_CJK_CHARS_PER_TOKEN(1.5) = 26.67 -> ceil 27.
    # If the call site still used its own copy (unaffected by the patch),
    # all 40 chars would fall into 'other' -> 40/2.5=16.
    assert tokens == 27


def test_helpers_estimate_gemini_tokens_does_not_use_non_run_ext_a_pattern(monkeypatch):
    # Companion guard: faking the per-char CHAR_RE (not the RUN_RE) must have
    # NO effect on estimate_gemini_tokens -- proves the call site was moved to
    # the "+"-quantified pattern rather than left on the old name.
    from gemini_translator.utils import cjk_ranges, helpers

    text = "Ω" * 40
    fake = re.compile("Ω")
    monkeypatch.setattr(cjk_ranges, "CJK_WITH_EXT_A_CHAR_RE", fake)

    tokens = helpers.estimate_gemini_tokens(text)

    # Unaffected by the CHAR_RE patch -> falls into 'other': 40/2.5=16.
    assert tokens == 16


def test_language_detector_contains_chinese_routes_through_canonical_pattern(monkeypatch):
    from gemini_translator.utils import language_tools as lt
    from gemini_translator.utils import cjk_ranges

    fake = re.compile(r"Q+")
    monkeypatch.setattr(cjk_ranges, "CHINESE_CHAR_RE", fake)

    assert lt.LanguageDetector.contains_chinese("Q") is True
    assert lt.LanguageDetector.contains_chinese("一") is False  # real "一"


def test_language_detector_contains_japanese_routes_through_canonical_pattern(monkeypatch):
    from gemini_translator.utils import language_tools as lt
    from gemini_translator.utils import cjk_ranges

    fake = re.compile(r"Q+")
    monkeypatch.setattr(cjk_ranges, "JAPANESE_CHAR_RE", fake)

    assert lt.LanguageDetector.contains_japanese("Q") is True
    assert lt.LanguageDetector.contains_japanese("ぁ") is False  # real hiragana


def test_language_detector_contains_korean_routes_through_canonical_pattern(monkeypatch):
    from gemini_translator.utils import language_tools as lt
    from gemini_translator.utils import cjk_ranges

    fake = re.compile(r"Q+")
    monkeypatch.setattr(cjk_ranges, "KOREAN_CHAR_RE", fake)

    assert lt.LanguageDetector.contains_korean("Q") is True
    assert lt.LanguageDetector.contains_korean("가") is False  # real hangul


def test_get_chinese_script_variants_routes_through_canonical_chinese_pattern(monkeypatch):
    # Ревью 2: language_tools.py держал ЕЩЁ один вручную набранный дубль,
    # _HAN_RE = re.compile(r'[一-鿿]+'), побайтово совпадающий с
    # cjk_ranges.CHINESE_CHAR_RE, используемый в get_chinese_script_variants
    # для гейта "есть ли смысл вызывать OpenCC". Тест доказывает, что после
    # правки этот гейт смотрит на модульный атрибут cjk_ranges, а не на свою
    # копию regex.
    from gemini_translator.utils import language_tools as lt
    from gemini_translator.utils import cjk_ranges

    calls = []

    def spy_converter(config_name):
        calls.append(config_name)
        return None

    monkeypatch.setattr(lt, "_get_opencc_converter", spy_converter)

    fake_no_match = re.compile(r"(?!)")  # никогда не матчит
    monkeypatch.setattr(cjk_ranges, "CHINESE_CHAR_RE", fake_no_match)

    # A real Chinese character: with the canonical gate faked to never match,
    # get_chinese_script_variants must short-circuit BEFORE ever asking for an
    # OpenCC converter. If the call site still held its own _HAN_RE copy
    # (unaffected by the patch), the gate would still pass and the converter
    # would be requested twice ("t2s", "s2t").
    lt.get_chinese_script_variants("一")

    assert calls == []


def test_smart_glossary_filter_is_masked_single_char_routes_through_core_pattern(monkeypatch):
    from gemini_translator.utils import language_tools as lt
    from gemini_translator.utils import cjk_ranges

    fake = re.compile(r"Q")
    monkeypatch.setattr(cjk_ranges, "CORE_CJK_CHAR_RE", fake)

    # 'Q' now looks CJK-like per the faked canonical pattern and is absent
    # from the residual text -> considered "masked".
    assert lt.SmartGlossaryFilter._is_masked_single_char("Q", "") is True
    # A real single CJK char no longer matches the (faked) canonical pattern
    # -> not eligible for masking at all.
    assert lt.SmartGlossaryFilter._is_masked_single_char("一", "") is False
