"""The size of a language-check request is the user's call, not a constant.

A chapter is cut into pieces before it is diagnosed, and the piece size decides
how many requests the chapter costs: at four thousand characters a fourteen
thousand character chapter costs four diagnoses, at sixteen thousand it costs
one.  Bigger is cheaper and not always better, so the number belongs in the
settings where it can be tried, not in the source where it cannot.
"""

from __future__ import annotations

import asyncio

import pytest

from gemini_translator.qa.language_validation import (
    DEFAULT_MAX_CHUNK_CHARS,
    LanguageQaRequest,
)
from gemini_translator.qa.settings import QaSettings
from gemini_translator.qa.service import QaOptions


def test_the_setting_reaches_the_pass_configuration():
    settings = QaSettings(language_chunk_chars=12000)

    assert settings.to_options().language_chunk_chars == 12000


def test_the_default_keeps_todays_behaviour():
    """Nobody's book may change its cost because this became configurable."""
    assert QaSettings().language_chunk_chars == DEFAULT_MAX_CHUNK_CHARS
    assert QaOptions().language_chunk_chars == DEFAULT_MAX_CHUNK_CHARS


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (100, 1000),        # below a sentence's worth of context
        (0, 1000),
        (-5, 1000),
        (999999, 32000),    # past what a model will answer in one reply
        ("нечисло", DEFAULT_MAX_CHUNK_CHARS),
        (None, DEFAULT_MAX_CHUNK_CHARS),
    ],
)
def test_an_impossible_size_is_brought_back_into_range(given, expected):
    """A hand-edited settings file must not be able to break a check."""
    assert QaSettings(language_chunk_chars=given).language_chunk_chars == expected


def test_the_setting_survives_a_save_and_load():
    stored = QaSettings(language_chunk_chars=8000).to_dict()

    assert stored["language_chunk_chars"] == 8000
    assert QaSettings.from_dict(stored).language_chunk_chars == 8000


def test_the_pass_hands_the_size_to_the_request():
    """The number is useless unless the stage that chunks actually receives it."""
    from gemini_translator.qa.llm import CancellationToken, QaModelSelection

    request = LanguageQaRequest(
        chapter_id="chapter-1",
        document_model={"document_id": "chapter-1", "blocks": []},
        source_language="zh",
        target_language="ru",
        model=QaModelSelection("gemini", "m"),
        cancellation=CancellationToken(),
        max_chunk_chars=QaOptions(language_chunk_chars=16000).language_chunk_chars,
    )

    assert request.max_chunk_chars == 16000
