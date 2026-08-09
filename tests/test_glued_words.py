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


def test_repairs_unambiguous_three_and_four_word_splits():
    analyzer = _FakeMorphAnalyzer({
        "посмотрел",
        "на",
        "него",
        "несмотря",
        "ни",
        "что",
    })

    repaired, candidates = repair_glued_russian_words(
        "Он посмотрелнанего, несмотряниначто.",
        analyzer=analyzer,
    )

    assert repaired == "Он посмотрел на него, несмотря ни на что."
    assert [candidate.replacement for candidate in candidates] == [
        "посмотрел на него",
        "несмотря ни на что",
    ]


def test_uses_required_hyphens_instead_of_inserting_invalid_spaces():
    analyzer = _FakeMorphAnalyzer({
        "страха",
        "кто",
        "русски",
        "прежнему",
    })

    repaired, _candidates = repair_glued_russian_words(
        "Иззастраха ктонибудь говорил порусски, но всё было попрежнему.",
        analyzer=analyzer,
    )

    assert repaired == (
        "Из-за страха кто-нибудь говорил по-русски, "
        "но всё было по-прежнему."
    )


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


def test_protected_single_word_term_is_not_split():
    analyzer = _FakeMorphAnalyzer({"страж", "моря"})

    repaired, candidates = repair_glued_russian_words(
        "Появился стражморя.",
        analyzer=analyzer,
        protected_terms={"стражморя"},
    )

    assert repaired == "Появился стражморя."
    assert candidates == []


def test_protected_multiword_term_supplies_canonical_boundaries():
    repaired, candidates = repair_glued_russian_words(
        "Появился Стражморя.",
        analyzer=None,
        protected_terms={"Страж моря"},
    )

    assert repaired == "Появился Страж моря."
    assert candidates[0].replacement == "Страж моря"


def test_does_not_split_indefinite_pronoun_suffix_after_hyphen():
    analyzer = _FakeMorphAnalyzer({"ни", "будь"})

    repaired, candidates = repair_glued_russian_words(
        "Он нашёл какой-нибудь выход.",
        analyzer=analyzer,
    )

    assert repaired == "Он нашёл какой-нибудь выход."
    assert candidates == []


def test_repairs_word_after_indefinite_suffix_without_breaking_koe_kakie():
    analyzer = _FakeMorphAnalyzer({"какие"})

    repaired, candidates = repair_glued_russian_words(
        "Он выбрал кое-какие вещи и какой-нибудьс узором.",
        analyzer=analyzer,
    )

    assert repaired == "Он выбрал кое-какие вещи и какой-нибудь с узором."
    assert [candidate.original for candidate in candidates] == ["нибудьс"]


def test_repairs_word_glued_after_digit_ordinal_suffix():
    analyzer = _FakeMorphAnalyzer({"кабинет"})

    repaired, candidates = repair_glued_russian_words(
        "Документы принесли в 314-йкабинет.",
        analyzer=analyzer,
    )

    assert repaired == "Документы принесли в 314-й кабинет."
    assert candidates[0].replacement == "й кабинет"


def test_glossary_name_inflections_are_protected_and_mark_boundaries():
    repaired, candidates = repair_glued_russian_words(
        "Филлипсу ответили, а Филлипсарефлекторно выпрямился.",
        protected_terms={"Филлипс"},
    )

    assert repaired == "Филлипсу ответили, а Филлипса рефлекторно выпрямился."
    assert [candidate.original for candidate in candidates] == ["Филлипсарефлекторно"]


def test_capitalized_component_of_multiword_glossary_term_marks_boundary():
    repaired, candidates = repair_glued_russian_words(
        "Это тот самый Старший Адепткласса Ученый.",
        protected_terms={"Старший Адепт"},
    )

    assert repaired == "Это тот самый Старший Адепт класса Ученый."
    assert candidates[0].replacement == "Адепт класса"


def test_real_analyzer_does_not_split_productive_words_compounds_or_names():
    source = (
        "Анализ разведданных закончен. Филлипсу ответили. "
        "Она говорила, пропевая слова. Однорукий поднялся. "
        "История драккаров описана в тысячедвухсотлетней летописи. "
        "Выстави предмет. В момент острижения он молчал."
    )

    repaired, candidates = repair_glued_russian_words(
        source,
        protected_terms={"Филлипс"},
    )

    assert repaired == source
    assert candidates == []


def test_hyphenated_glossary_name_components_are_not_split():
    source = "Они прибыли в Скапа-Флоу через Саут-Дейл."

    repaired, candidates = repair_glued_russian_words(source)

    assert repaired == source
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


def test_repairs_glued_words_across_inline_html_tags():
    analyzer = _FakeMorphAnalyzer({"боевых", "искусств"})
    source = "<p><em>боевых</em><strong>искусств</strong></p>"

    repaired, _candidates = repair_glued_russian_words_in_html(source, analyzer=analyzer)

    assert repaired == "<p><em>боевых</em> <strong>искусств</strong></p>"


def test_ai_artifact_repair_includes_glued_word_repair():
    source = "<body><p>С его уровнем боевых искусств.</p></body>"
    translated = "<body><p>С его уровнем боевыхискусств.</p></body>"

    repaired = repair_ai_html_artifacts(source, translated)

    assert "боевых искусств" in repaired
