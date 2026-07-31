from gemini_translator.utils.glued_words import (
    find_glued_russian_words,
    repair_glued_russian_words,
    repair_glued_russian_words_in_html,
)
from gemini_translator.utils.text import repair_ai_html_artifacts


class _FakeMorphAnalyzer:
    def __init__(self, known_words):
        self.known_words = {word.lower().replace("ё", "е") for word in known_words}

    def word_is_known(self, word):
        return word.lower().replace("ё", "е") in self.known_words


def test_repairs_only_unambiguous_dictionary_split():
    analyzer = _FakeMorphAnalyzer({"окутала", "ледяная", "потому"})

    repaired, candidates = repair_glued_russian_words(
        "Его окуталаледяная жажда, потому он замер.",
        analyzer=analyzer,
    )

    assert repaired == "Его окутала ледяная жажда, потому он замер."
    assert [candidate.original for candidate in candidates] == ["окуталаледяная"]


def test_reports_ambiguous_split_without_changing_it():
    analyzer = _FakeMorphAnalyzer({"прорвал", "астрой", "прорвала", "строй"})

    repaired, candidates = repair_glued_russian_words(
        "Стрела прорваластрой.",
        analyzer=analyzer,
    )

    assert repaired == "Стрела прорваластрой."
    assert candidates[0].replacement is None
    assert candidates[0].alternatives == ("прорвал астрой", "прорвала строй")


def test_repairs_cyrillic_camel_case_without_dictionary():
    candidates = find_glued_russian_words("СюйБинчжэн поднял руку.", analyzer=None)

    assert candidates[0].replacement == "Сюй Бинчжэн"


def test_skips_unknown_title_case_name_inside_sentence():
    analyzer = _FakeMorphAnalyzer({"страж", "моря"})

    repaired, candidates = repair_glued_russian_words(
        "Навстречу вышел Стражморя.",
        analyzer=analyzer,
    )

    assert repaired == "Навстречу вышел Стражморя."
    assert candidates == []


def test_does_not_split_indefinite_pronoun_suffix_after_hyphen():
    analyzer = _FakeMorphAnalyzer({"ни", "будь"})

    repaired, candidates = repair_glued_russian_words(
        "Он нашёл какой-нибудь выход.",
        analyzer=analyzer,
    )

    assert repaired == "Он нашёл какой-нибудь выход."
    assert candidates == []


def test_repairs_visible_html_without_touching_markup_or_code():
    analyzer = _FakeMorphAnalyzer({"боевых", "искусств"})
    source = (
        '<body data-note="боевыхискусств"><p>Мастер боевыхискусств.</p>'
        '<!-- боевыхискусств --><code>боевыхискусств</code></body>'
    )

    repaired, candidates = repair_glued_russian_words_in_html(source, analyzer=analyzer)

    assert repaired == (
        '<body data-note="боевыхискусств"><p>Мастер боевых искусств.</p>'
        '<!-- боевыхискусств --><code>боевыхискусств</code></body>'
    )
    assert [candidate.original for candidate in candidates] == ["боевыхискусств"]


def test_ai_artifact_repair_includes_glued_word_repair():
    source = "<body><p>С его уровнем боевых искусств.</p></body>"
    translated = "<body><p>С его уровнем боевыхискусств.</p></body>"

    repaired = repair_ai_html_artifacts(source, translated)

    assert "боевых искусств" in repaired
