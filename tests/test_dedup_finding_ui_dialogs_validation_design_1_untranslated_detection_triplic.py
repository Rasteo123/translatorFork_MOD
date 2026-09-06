# -*- coding: utf-8 -*-
"""finding-ui-dialogs-validation_design_1-untranslated-detection-triplic

Детекция недоперевода была реализована трижды с разными правилами:

1. UntranslatedWordDetector.detect (validation_dialogs/untranslated_detector.py)
   -- канонический stateless-детектор: MIN_LATIN_WORD_LENGTH=3, фильтр
   рейтингов/одиночных латинских букв, extract_visible_text, широкий
   UnicodeRanges.ALL_CJK_PATTERN (с CJK Ext-A).
2. Инлайн-копия внутри ValidationThread._analyze_html_content -- порог
   len<2 (т.е. пропускает 2-буквенные слова), без фильтра рейтингов/
   одиночных латинских букв, узкий cjk_ranges.CORE_CJK_CHAR_RE (без Ext-A).
3. TranslationValidatorPage._recalculate_untranslated_words_for_rows --
   тот же узкий regex-проход, что и (2), но ещё и BeautifulSoup.get_text(' ')
   вместо extract_visible_text.

Канонической объявлена (1). Копии (2) и (3) должны быть удалены, оба места
вызова должны идти через один и тот же UntranslatedWordDetector.

(а) Характеризационные тесты канонической реализации фиксируют ИМЕННО те
    крайние случаи, которые различали копии (2-буквенное латинское слово,
    CJK Ext-A символ).
(б) Тесты-маршрутизация подменяют validation_module.UntranslatedWordDetector
    и проверяют, что оба места вызова используют его, а не собственную
    regex-копию. Эти тесты обязаны ПАДАТЬ до рефакторинга.
"""

from gemini_translator.ui.dialogs import validation as validation_module
from gemini_translator.ui.dialogs.validation import ValidationThread, TranslationValidatorPage
from gemini_translator.ui.dialogs.validation_dialogs.untranslated_detector import (
    UntranslatedWordDetector,
)
from gemini_translator.utils.validation_cache import build_detector_signature


def _worker(word_exceptions=None):
    return ValidationThread(
        translated_folder="",
        original_epub_path="",
        checks_config={},
        word_exceptions_set=word_exceptions or set(),
        project_manager=None,
    )


class _FakeValidatorPage:
    """Минимальный дублёр TranslationValidatorPage для вызова
    _recalculate_untranslated_words_for_rows без поднятия реального QWidget
    (метод трогает только эти атрибуты/методы)."""

    def __init__(self, results_data, exceptions=None):
        self.results_data = results_data
        self._exceptions = set(exceptions or set())
        self._fixer_data_fingerprint = "stale-before-recalc"

    def _build_current_untranslated_exceptions(self):
        return set(self._exceptions)

    def _ensure_row_translated_html_loaded(self, row_idx):
        return self.results_data[row_idx].get("translated_html")


def _recalc(page, rows):
    return TranslationValidatorPage._recalculate_untranslated_words_for_rows(page, rows)


# =============================================================================
# (а) Характеризационные тесты канонической реализации
# =============================================================================

def test_canonical_detector_requires_three_latin_letters_minimum():
    detector = UntranslatedWordDetector(set())
    # "he" -- ровно тот случай, который различал копии: инлайн-копии считали
    # порог len<2 (т.е. пропускали слова из 2+ букв), канонический детектор
    # требует MIN_LATIN_WORD_LENGTH=3.
    assert detector.detect("<p>Он сказал he.</p>") == []
    assert detector.detect("<p>Он сказал yes.</p>") == ["yes"]


def test_canonical_detector_flags_cjk_ext_a_character():
    detector = UntranslatedWordDetector(set())
    # U+3400 -- CJK Unified Ideographs Extension A. Входит в широкий
    # UnicodeRanges.ALL_CJK_PATTERN канонического детектора, но НЕ входит в
    # узкий cjk_ranges.CORE_CJK_CHAR_RE, которым пользовались обе inline-копии.
    ext_a_char = "㐀"
    assert detector.detect(f"<p>Он сказал {ext_a_char}.</p>") == [ext_a_char]


# =============================================================================
# (б) Маршрутизация: ValidationThread._analyze_html_content
# =============================================================================

def test_analyze_html_content_does_not_flag_two_letter_latin_word():
    """До рефакторинга: inline-регекс-проход (порог len<2) добавлял 'he' в
    untranslated_words в дополнение к результату канонического детектора.
    После рефакторинга единственный источник -- detector.detect(), который
    двухбуквенные латинские слова не флагует (MIN_LATIN_WORD_LENGTH=3)."""
    result = {
        "path": "Text/chapter.xhtml",
        "internal_html_path": "Text/chapter.xhtml",
    }
    original = "<html><body><p>Он сказал что-то.</p></body></html>"
    translated = "<html><body><p>Он сказал he.</p></body></html>"

    analyzed = _worker()._analyze_html_content(original, translated, result)

    assert "he" not in analyzed.get("untranslated_words", [])


def test_analyze_html_content_routes_untranslated_words_through_canonical_detector(monkeypatch):
    """Подменяем канонический UntranslatedWordDetector сентинел-версией и
    проверяем, что итоговый список untranslated_words -- РОВНО то, что вернул
    детектор, без довеска от собственной regex-копии.

    До рефакторинга этот тест ПАДАЕТ: inline-проход (1841-1853 в исходнике)
    добавляет 'he' поверх сентинела, так что список перестаёт совпадать
    ровно с тем, что вернул фейковый детектор.
    """
    calls = []

    class _FakeDetector:
        def __init__(self, word_exceptions):
            calls.append(word_exceptions)

        def detect(self, translated_content):
            return ["SENTINEL"]

        def detect_mixed_script(self, translated_content):
            return []

    monkeypatch.setattr(validation_module, "UntranslatedWordDetector", _FakeDetector)

    result = {
        "path": "Text/chapter.xhtml",
        "internal_html_path": "Text/chapter.xhtml",
    }
    original = "<html><body><p>Он сказал что-то.</p></body></html>"
    # 'he' -- слово, которое собственная regex-копия ловит, а фейковый
    # детектор -- нет (он игнорирует свой аргумент и всегда возвращает
    # SENTINEL).
    translated = "<html><body><p>Он сказал he.</p></body></html>"

    analyzed = _worker()._analyze_html_content(original, translated, result)

    assert calls, "ValidationThread must construct the canonical UntranslatedWordDetector"
    assert analyzed.get("untranslated_words") == ["SENTINEL"], (
        "ValidationThread must rely solely on the canonical detector's result, "
        "not extend it with its own regex pass"
    )


# =============================================================================
# (б) Маршрутизация: TranslationValidatorPage._recalculate_untranslated_words_for_rows
# =============================================================================

def test_recalculate_flags_cjk_ext_a_character_missed_by_old_narrow_regex():
    """До рефакторинга: третья копия использует узкий cjk_ranges.CORE_CJK_CHAR_RE
    и порог len<2 -- одиночный CJK Ext-A символ им не покрывается (не CJK по
    узкому регексу, длина 1 < 2), поэтому он просто выбрасывается. Каноническая
    реализация ловит его через широкий UnicodeRanges.ALL_CJK_PATTERN."""
    ext_a_char = "㐀"
    results_data = {
        0: {"translated_html": f"<html><body><p>Он сказал {ext_a_char}.</p></body></html>"},
    }
    page = _FakeValidatorPage(results_data)

    _recalc(page, [0])

    assert ext_a_char in results_data[0].get("untranslated_words", [])


def test_recalculate_does_not_flag_two_letter_latin_word():
    """Симметрично тесту для ValidationThread: после перехода на канонический
    детектор двухбуквенное латинское слово больше не считается недопереводом
    (MIN_LATIN_WORD_LENGTH=3), тогда как старая копия (порог len<2) его ловила."""
    results_data = {
        0: {"translated_html": "<html><body><p>Он сказал he.</p></body></html>"},
    }
    page = _FakeValidatorPage(results_data)

    _recalc(page, [0])

    assert "he" not in results_data[0].get("untranslated_words", [])


def test_recalculate_routes_through_canonical_detector(monkeypatch):
    """До рефакторинга этот тест ПАДАЕТ: _recalculate_untranslated_words_for_rows
    вообще не использует UntranslatedWordDetector (собственный BeautifulSoup +
    regex проход), так что подмена класса ни на что не влияет."""
    calls = []

    class _FakeDetector:
        def __init__(self, word_exceptions):
            calls.append(set(word_exceptions))

        def detect(self, translated_content):
            return ["SENTINEL"]

        def detect_mixed_script(self, translated_content):
            return []

    monkeypatch.setattr(validation_module, "UntranslatedWordDetector", _FakeDetector)

    results_data = {
        0: {"translated_html": "<html><body><p>Он сказал he.</p></body></html>"},
    }
    page = _FakeValidatorPage(results_data, exceptions={"custom"})

    _recalc(page, [0])

    assert calls, "_recalculate_untranslated_words_for_rows must construct the canonical UntranslatedWordDetector"
    assert calls[0] == {"custom"}, "must build the detector from the current exceptions set"
    assert results_data[0].get("untranslated_words") == ["SENTINEL"], (
        "must rely solely on the canonical detector's result"
    )


def test_recalculate_still_invalidates_fixer_fingerprint():
    """Побочный эффект копии (3), не входящий в детекцию как таковую, должен
    сохраниться после рефакторинга."""
    results_data = {
        0: {"translated_html": "<html><body><p>Всё переведено.</p></body></html>"},
    }
    page = _FakeValidatorPage(results_data)

    _recalc(page, [0])

    assert page._fixer_data_fingerprint is None


def test_recalculate_pops_untranslated_words_when_html_missing():
    results_data = {
        0: {"translated_html": "", "untranslated_words": ["stale"]},
    }
    page = _FakeValidatorPage(results_data)

    _recalc(page, [0])

    assert "untranslated_words" not in results_data[0]


# =============================================================================
# Ревью (needs_work): устранение blocker/major/minor
# =============================================================================

def test_should_include_word_respects_cjk_exception():
    """MAJOR (validation.py:5807 в отчёте рецензента): каноническая
    UntranslatedWordDetector._should_include_word проверяла CJK-паттерн
    ДО проверки исключений, поэтому CJK-слово из набора исключений всё
    равно флагуется. _build_current_untranslated_exceptions реально кладёт
    в набор именно CJK-остатки из глоссария (кириллица вычтена из 'rus'),
    так что сценарий glossary_updated -> пересчёт был no-op для CJK-терминов.
    До фикса detect() возвращает ['小明'] даже с исключением {'小明'}."""
    detector = UntranslatedWordDetector({"小明"})
    assert detector.detect("<p>Он сказал 小明.</p>") == []


def test_recalculate_clears_cjk_word_added_to_exceptions():
    """Тот же сценарий на уровне маршрутизации: glossary_updated добавляет
    CJK-термин в исключения (_build_current_untranslated_exceptions), и
    пересчёт должен снять флаг с уже помеченной главы."""
    results_data = {
        0: {
            "translated_html": "<html><body><p>Он сказал 小明.</p></body></html>",
            "untranslated_words": ["小明"],
        },
    }
    page = _FakeValidatorPage(results_data, exceptions={"小明"})

    _recalc(page, [0])

    assert "untranslated_words" not in results_data[0]


class _FakeSnapshotPage:
    """Минимальный дублёр для _load_validation_snapshot_state -- метод
    трогает только project_manager/original_epub_path/settings_manager и
    self._get_effective_word_exceptions()."""

    def __init__(self, exceptions):
        self.original_epub_path = ""
        self.project_manager = None
        self.settings_manager = None
        self._exceptions = set(exceptions)

    def _get_effective_word_exceptions(self):
        return set(self._exceptions)


def test_detector_signature_includes_rules_marker_to_invalidate_stale_cache():
    """MAJOR (validation.py:2359 в отчёте рецензента): build_detector_signature
    хешировал только набор исключений, без версии правил детекции. При
    неизменном EPUB и неизменных исключениях старый снапшот (посчитанный ДО
    перехода на канонический детектор) признавался is_snapshot_compatible
    совместимым, и untranslated_words восстанавливались по старым правилам
    (2-буквенные латинские слова, узкий CJK) вперемешку с пересчитанными
    главами по новым правилам. Сигнатура должна меняться при смене правил
    детекции, даже если набор исключений тот же самый."""
    page = _FakeSnapshotPage({"foo", "bar"})

    TranslationValidatorPage._load_validation_snapshot_state(page)

    exceptions_only_signature = build_detector_signature({"foo", "bar"})
    assert page.current_detector_signature != exceptions_only_signature


def test_two_letter_uppercase_residue_is_no_longer_flagged():
    """MINOR: характеризационный тест на явную потерю, упомянутую в
    behavior_choice отчёта. До рефакторинга ValidationThread._analyze_html_content
    отдавал объединение canonical detector.detect() и inline-прохода с порогом
    len<2 -- 2-символьные заглавные остатки вроде 'HP' (RPG-статы и т.п.)
    ловились. После перехода на единственный источник -- канонический
    детектор с MIN_LATIN_WORD_LENGTH=3 -- такие остатки недопереводом не
    считаются. Это осознанное сужение поведения, а не регрессия дедупа."""
    detector = UntranslatedWordDetector(set())
    assert detector.detect("<p>Его показатель HP вырос.</p>") == []


def test_fake_validator_page_matches_real_class_api():
    """MINOR: _FakeValidatorPage -- утиный дублёр без привязки к реальному
    классу. Этот тест не делает дублёр строгим автоспеком, но хотя бы
    фиксирует, что оба метода, которые он подделывает, всё ещё существуют
    у реального TranslationValidatorPage -- если один из них переименуют,
    тест сломается вместо того, чтобы дублёр молча продолжал зеленеть."""
    assert hasattr(TranslationValidatorPage, "_build_current_untranslated_exceptions")
    assert hasattr(TranslationValidatorPage, "_ensure_row_translated_html_loaded")
