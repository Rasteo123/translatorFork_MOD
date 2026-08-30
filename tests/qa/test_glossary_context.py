"""Candidate-local glossary selection for bounded repair and verification prompts."""

from __future__ import annotations

import pytest

from gemini_translator.qa.glossary_context import (
    GlossaryContextSelector,
    GlossaryTerm,
    glossary_terms_from_project_entries,
)
from gemini_translator.qa.models import GlossaryPolicy, QaModelValidationError


_GAP_TEXT = "武魂殿的長老看著唐三，把 Nokia 手機放在桌上。"
_GAP_CONTEXT = "唐三想起了藍銀草的味道。"


def _unrelated(count: int) -> tuple[GlossaryTerm, ...]:
    return tuple(
        GlossaryTerm(
            original=f"unrelated-glossary-secret-{index}",
            translation=f"несвязанный-{index}",
            occurrences=1000 + index,
        )
        for index in range(count)
    )


def _book_glossary() -> tuple[GlossaryTerm, ...]:
    return (
        *_unrelated(200),
        GlossaryTerm("藍銀草", "Голубая Серебряная Трава", occurrences=12),
        GlossaryTerm("唐三", "Тан Сань", occurrences=90),
        GlossaryTerm("武魂殿", "Зал Духов", occurrences=40),
        GlossaryTerm("Nokia", "Nokia", GlossaryPolicy.KEEP_ORIGINAL, occurrences=2),
    )


def test_gap_terms_precede_context_terms_and_unrelated_terms_are_dropped():
    """Sending the whole book glossary would leak unrelated terms into every prompt."""
    selected = GlossaryContextSelector().select_for_candidate(
        _book_glossary(), _GAP_TEXT, _GAP_CONTEXT, max_terms=4
    )

    assert [term.original_term for term in selected] == [
        "唐三",
        "武魂殿",
        "Nokia",
        "藍銀草",
    ]
    assert [term.priority for term in selected] == [0, 1, 2, 3]
    assert all("unrelated-glossary-secret" not in term.original_term for term in selected)


def test_selection_preserves_policy_and_canonical_translation():
    """A repair prompt must repeat the authoritative translation, not a paraphrase."""
    selected = GlossaryContextSelector().select_for_candidate(
        _book_glossary(), _GAP_TEXT, _GAP_CONTEXT, max_terms=10
    )
    by_term = {term.original_term: term for term in selected}

    assert by_term["武魂殿"].canonical_translation == "Зал Духов"
    assert by_term["武魂殿"].policy is GlossaryPolicy.MUST_TRANSLATE
    assert by_term["Nokia"].policy is GlossaryPolicy.KEEP_ORIGINAL
    assert by_term["Nokia"].canonical_translation == "Nokia"
    assert by_term["唐三"].occurrences == 2


def test_max_terms_truncates_after_ordering_without_reordering():
    """Truncation must drop the least relevant terms, never the gap terms."""
    selected = GlossaryContextSelector().select_for_candidate(
        _book_glossary(), _GAP_TEXT, _GAP_CONTEXT, max_terms=2
    )

    assert [term.original_term for term in selected] == ["唐三", "武魂殿"]


def test_popularity_alone_never_selects_a_term():
    """Statistical popularity outside the candidate is not evidence of relevance."""
    glossary = (
        GlossaryTerm("唐三", "Тан Сань", occurrences=9000),
        GlossaryTerm("武魂殿", "Зал Духов", occurrences=8000),
    )

    assert (
        GlossaryContextSelector().select_for_candidate(
            glossary, "Полностью несвязанный фрагмент.", "", max_terms=5
        )
        == ()
    )


def test_case_exact_surface_outranks_a_normalized_only_match():
    """An exact surface match is stronger local evidence than a casefolded one."""
    glossary = (
        GlossaryTerm("spirit hall", "Зал Духов", occurrences=50),
        GlossaryTerm("Blue Silver", "Голубое Серебро", occurrences=50),
    )

    selected = GlossaryContextSelector().select_for_candidate(
        glossary, "Blue Silver grew near the Spirit Hall gate.", "", max_terms=5
    )

    assert [term.original_term for term in selected] == ["Blue Silver", "spirit hall"]


def test_must_translate_outranks_keep_original_at_equal_evidence():
    """A term the model may leave alone is less urgent than one it must translate."""
    glossary = (
        GlossaryTerm("Nokia", "Nokia", GlossaryPolicy.KEEP_ORIGINAL, occurrences=5),
        GlossaryTerm("塔樓", "Башня", occurrences=5),
    )

    selected = GlossaryContextSelector().select_for_candidate(
        glossary, "Nokia 就在塔樓旁邊。", "", max_terms=5
    )

    assert [term.original_term for term in selected] == ["塔樓", "Nokia"]


def test_invalid_selection_requests_are_rejected():
    """A silently clamped limit would let an unbounded prompt through."""
    selector = GlossaryContextSelector()

    with pytest.raises(QaModelValidationError):
        selector.select_for_candidate(_book_glossary(), _GAP_TEXT, "", max_terms=0)
    with pytest.raises(QaModelValidationError):
        selector.select_for_candidate(_book_glossary(), _GAP_TEXT, "", max_terms=True)
    with pytest.raises(TypeError):
        selector.select_for_candidate([{"original": "唐三"}], _GAP_TEXT, "", max_terms=3)
    with pytest.raises(TypeError):
        selector.select_for_candidate(_book_glossary(), None, "", max_terms=3)


def test_glossary_term_validates_its_own_policy_contract():
    """A KEEP_ORIGINAL term with a foreign translation would authorize a wrong edit."""
    with pytest.raises(QaModelValidationError):
        GlossaryTerm("", "Тан Сань")
    with pytest.raises(QaModelValidationError):
        GlossaryTerm("唐三", "")
    with pytest.raises(QaModelValidationError):
        GlossaryTerm("Nokia", "Нокиа", GlossaryPolicy.KEEP_ORIGINAL)
    with pytest.raises(QaModelValidationError):
        GlossaryTerm("唐三", "Тан Сань", occurrences=-1)


def test_project_entries_map_to_terms_without_inventing_policies():
    """Legacy project glossaries carry no policy field and must not gain one silently."""
    terms = glossary_terms_from_project_entries(
        [
            {"original": "唐三", "rus": "Тан Сань", "note": ""},
            {"original": "Nokia", "rus": "Nokia"},
            {"original": "武魂殿", "translation": "Зал Духов"},
            {"original": "  ", "rus": "пусто"},
            {"original": "藍銀草", "rus": ""},
            "не словарная запись",
        ]
    )

    assert [(term.original, term.translation, term.policy) for term in terms] == [
        ("唐三", "Тан Сань", GlossaryPolicy.MUST_TRANSLATE),
        ("Nokia", "Nokia", GlossaryPolicy.KEEP_ORIGINAL),
        ("武魂殿", "Зал Духов", GlossaryPolicy.MUST_TRANSLATE),
    ]
