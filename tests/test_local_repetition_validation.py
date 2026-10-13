import random

import pytest

from gemini_translator.core.worker_helpers.response_parser import ResponseParser
from gemini_translator.utils.text import (
    degenerate_repetition_ratio,
    sanitize_partial_translation,
    validate_html_structure,
)


_rng = random.Random(120)
_words = "дорога лес река дом ветер утро вечер свет тень голос шаг окно дверь небо земля камень рука взгляд город письмо".split()
PROSE = "".join(
    "<p>" + " ".join(_rng.choices(_words, k=18)) + ".</p>"
    for _ in range(60)
)
SOURCE = "<body><p>他看到腰间的那块小玉牌。</p>" + PROSE + "</body>"


def translation(fragment):
    return "<body>" + PROSE + "<p>Он увидел на поясе " + fragment + " подвеску.</p></body>"


@pytest.mark.parametrize("fragment", [
    "那块" * 280,
    "подвеска " * 80,
    "amulet " * 80,
    "я" * 400,
    "那块\n" * 280,
    "<span>那块</span>" * 280,
    "&#37027;&#22359;" * 280,
], ids=["cjk", "russian", "english", "character", "newlines", "inline-tags", "entities"])
def test_rejects_local_loop_hidden_in_otherwise_valid_chapter(fragment):
    translated = translation(fragment)
    # Whole-response compression misses these local defects.
    assert degenerate_repetition_ratio(translated) < 0.90

    valid, reason, _ = validate_html_structure(SOURCE, translated)

    assert not valid
    assert "повтор" in reason.lower()


@pytest.mark.parametrize("fragment", [
    "ха-ха-ха, да, да, да",
    "那块",
    "нет " * 5,
    "—" * 220,
], ids=["dialogue", "single-cjk", "short-word-run", "separator"])
def test_preserves_short_repetitions_and_decorative_separators(fragment):
    valid, reason, _ = validate_html_structure(SOURCE, translation(fragment))
    assert valid, reason


def test_partial_with_internal_loop_is_not_reused_for_continuation():
    assert sanitize_partial_translation(translation("那块" * 280)) == ""


def test_partial_with_markup_split_loop_is_not_reused():
    assert sanitize_partial_translation(translation("<span>那块</span>" * 280)) == ""


@pytest.mark.parametrize("repeats", [8, 20])
def test_short_inner_repeats_do_not_hide_a_large_repeated_block(repeats):
    translated = translation(("да " * 8 + "нет " * 8) * repeats)
    assert degenerate_repetition_ratio(translated) < 0.90
    valid, reason, _ = validate_html_structure(SOURCE, translated)
    assert not valid
    assert "повтор" in reason.lower()


@pytest.mark.parametrize("repeats", [8, 20])
def test_partial_with_nested_repetition_is_discarded(repeats):
    assert sanitize_partial_translation(translation(("да " * 8 + "нет " * 8) * repeats)) == ""


class _PromptBuilderStub:
    def _replace_media_with_placeholders(self, html_content, return_maps=False):
        return ({}, {}) if return_maps else html_content


def test_batch_rejects_corrupted_chapter_but_keeps_valid_neighbour():
    parser = ResponseParser(
        worker=None,
        log_callback=lambda _message: None,
        validator_func=validate_html_structure,
        prompt_builder=_PromptBuilderStub(),
    )
    response = (
        "<!-- 0 -->" + translation("那块" * 280)
        + "<!-- 1 -->" + translation("красивую") + "<!-- 2 -->"
    )

    report = parser.unpack_and_validate_batch(
        response, ["bad.xhtml", "good.xhtml"],
        {"bad.xhtml": SOURCE, "good.xhtml": SOURCE},
    )

    assert [entry["original_path"] for entry in report["successful"]] == ["good.xhtml"]
    assert len(report["failed"]) == 1
    assert report["failed"][0][0] == "bad.xhtml"
    assert "повтор" in report["failed"][0][1].lower()
