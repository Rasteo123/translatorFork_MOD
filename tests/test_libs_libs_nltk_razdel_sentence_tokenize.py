"""Characterization tests for the libs-nltk-razdel verdict.

_sentence_tokenize must route Russian text through razdel.sentenize (which
handles abbreviations like "Т.е." and quoted direct speech correctly) while
non-Russian text keeps using the original regex fallback. nltk must no
longer be imported or used anywhere in the module.
"""

from pathlib import Path
from unittest import mock

import gemini_reader_v3 as reader


def test_russian_paragraph_uses_razdel_for_abbreviations_and_dialogue():
    text = 'Он сказал: "Идём, г-н Иванов." Она молчала. Т.е. это было странно.'

    sentences = reader._sentence_tokenize(text)

    # razdel keeps the quoted-dialogue sentence intact and does not split
    # "Т.е." into its own fragment the way the naive regex fallback does.
    assert sentences == [
        'Он сказал: "Идём, г-н Иванов."',
        "Она молчала.",
        "Т.е. это было странно.",
    ]


def test_non_russian_text_still_uses_regex_fallback():
    text = "Hello world. Dr. Smith is here. It works!"

    sentences = reader._sentence_tokenize(text)

    # Unchanged legacy behaviour: the regex fallback naively splits "Dr."
    # into its own fragment because it only looks at punctuation+whitespace.
    assert sentences == ["Hello world.", "Dr.", "Smith is here.", "It works!"]


def test_empty_text_returns_no_sentences():
    assert reader._sentence_tokenize("") == []


def test_nltk_is_no_longer_imported_or_used():
    assert not hasattr(reader, "nltk")
    module_source = Path(reader.__file__).read_text(encoding="utf-8")
    assert "nltk" not in module_source


def test_multiline_russian_text_without_periods_still_splits_on_newlines():
    # razdel.sentenize only breaks on sentence punctuation, not on line
    # breaks. Russian text is full of unpunctuated lines (headings, list
    # items, verse, dialogue ending in a comma or em dash) that the legacy
    # regex fallback always split on "\n+". Feeding a whole multi-line
    # paragraph straight into razdel would silently merge every line into
    # one giant "sentence" with embedded "\n" characters, which then blows
    # past live-TTS chunk limits (see _split_live_paragraph below).
    lines = [f"Строка номер {i} без точки в конце текста тут" for i in range(60)]
    paragraph = "\n".join(lines)

    sentences = reader._sentence_tokenize(paragraph)

    assert len(sentences) == len(lines)
    assert all("\n" not in sentence for sentence in sentences)


def test_split_live_paragraph_respects_max_chars_for_unpunctuated_russian_lines():
    lines = [f"Строка номер {i} без точки в конце текста тут" for i in range(60)]
    paragraph = "\n".join(lines)

    chunks = reader._split_live_paragraph(paragraph, max_chars=2200)

    assert len(chunks) > 1
    assert all(len(chunk) <= 2200 for chunk in chunks)


def test_russian_text_falls_back_to_regex_when_razdel_unavailable():
    text = "Он сказал: 'Идём, г-н Иванов.' Она молчала. Т.е. это было странно."

    with mock.patch.object(reader, "_razdel_sentenize", None):
        sentences = reader._sentence_tokenize(text)

    fallback_sentences = [
        part.strip()
        for part in reader.re.split(r"(?<=[.!?])\s+|\n+", text)
        if part.strip()
    ]
    assert sentences == fallback_sentences
    # Confirms this is genuinely the degraded path: without razdel the
    # abbreviation "Т.е." gets split off into its own fragment.
    assert "Т.е." in sentences


def test_mixed_russian_latin_paragraph_uses_regex_fallback_boundary():
    # _is_russian_text counts characters, not words: a Russian paragraph
    # dense with Latin names/terms can have latin_count >= cyrillic_count
    # and silently route to the plain regex tokenizer instead of razdel.
    # This pins that documented boundary so a future threshold change is
    # visible as a test change, not a silent behaviour drift.
    text = (
        "Иван сказал John Smith and Peter Parker about the new "
        "ExampleCorporationName software release version."
    )

    assert reader._is_russian_text(text) is False

    sentences = reader._sentence_tokenize(text)
    fallback_sentences = [
        part.strip()
        for part in reader.re.split(r"(?<=[.!?])\s+|\n+", text)
        if part.strip()
    ]
    assert sentences == fallback_sentences
