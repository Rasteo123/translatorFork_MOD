"""Characterization tests for the canonical QA text-normalization helper.

Covers finding finding-qa-b_design_1-normalize-six-copies: `_normalize` with
NFKC was independently implemented six times across gemini_translator/qa, and
the copies had already drifted (addition_detector missing `.casefold()`;
glossary_audit adding quote-translation, `.casefold()`, `ё`->`е` folding, and
quote-stripping on top). These tests pin the edge cases that distinguished the
copies before the refactor collapses them onto
`gemini_translator.qa.text_normalize`.
"""

from __future__ import annotations

from gemini_translator.qa import addition_detector, glossary_audit, glossary_terms
from gemini_translator.qa.llm import CancellationToken, QaModelSelection
from gemini_translator.qa.models import AlignmentSpan, CandidateContext, GapCandidate
from gemini_translator.qa.text_normalize import normalize_for_comparison


def test_collapses_internal_whitespace_runs() -> None:
    assert normalize_for_comparison("a   b\t\tc\n\nd") == "a b c d"


def test_strips_leading_and_trailing_whitespace() -> None:
    assert normalize_for_comparison("  hello world  ") == "hello world"


def test_applies_nfkc_normalization() -> None:
    # U+FF21 FULLWIDTH LATIN CAPITAL LETTER A -> "a" after NFKC + casefold.
    assert normalize_for_comparison("Ａ") == "a"
    # Ligature U+FB01 LATIN SMALL LIGATURE FI -> "fi" under NFKC.
    assert normalize_for_comparison("ﬁle") == "file"


def test_casefolds_case_differences_away() -> None:
    # This is the exact behavior addition_detector's copy was missing before
    # the fix: majority behavior (4 of 6 original copies) casefolds.
    assert normalize_for_comparison("Hello WORLD") == normalize_for_comparison(
        "hello world"
    )
    assert normalize_for_comparison("HELLO") == "hello"


def test_casefold_beyond_simple_lowercase() -> None:
    # German sharp s casefolds to "ss" (differs from .lower()), proving the
    # canonical helper truly casefolds rather than lowercasing.
    assert normalize_for_comparison("Straße") == "strasse"


def test_empty_string_stays_empty() -> None:
    assert normalize_for_comparison("") == ""


def test_does_not_fold_yo_to_ye_by_default() -> None:
    # Unlike glossary_audit's extra-fold variant, the shared canonical helper
    # must not silently fold "ё" -> "е" (that stays a deliberate, separate
    # behavior local to glossary comparisons).
    assert normalize_for_comparison("ё") == "ё"  # ё unchanged (casefolded is same)


def test_does_not_translate_curly_quotes_by_default() -> None:
    # Unlike glossary_audit's extra-fold variant, curly quotes are left as-is.
    assert normalize_for_comparison("«hi»") == "«hi»".casefold()


def test_glossary_normalize_runs_nfkc_before_quote_translation() -> None:
    # Reviewer-flagged regression: NFKC on U+2033 DOUBLE PRIME decomposes to
    # two U+2032 PRIME characters, which the quote-translation table maps to
    # ASCII apostrophes. Order matters -- NFKC must run before translate, or
    # the just-produced U+2032 characters are missed and survive untranslated.
    assert glossary_audit._normalize("a″b") == "a''b"


def test_glossary_normalize_runs_nfkc_before_translating_produced_apostrophe() -> None:
    # NFKC on U+0149 LATIN SMALL LETTER N PRECEDED BY APOSTROPHE decomposes to
    # U+02BC MODIFIER LETTER APOSTROPHE + "n", which the quote-translation
    # table maps to an ASCII apostrophe -- again, only if NFKC runs first.
    assert glossary_audit._normalize("aŉb") == "a'nb"


def test_glossary_normalize_policy_text_delegates_to_canonical_helper() -> None:
    # _normalize_policy_text must stay byte-for-byte identical to _normalize
    # minus the glossary-only ё->е fold and quote-stripping, so both glossary
    # normalizers share one canonical tail instead of drifting independently.
    assert glossary_terms._normalize_policy_text("a″b") == "a''b"
    assert glossary_terms._normalize_policy_text("Ёлка") == "ёлка"


def _build_addition_context(target_text: str, minimum_addition_chars: int):
    left_anchor = AlignmentSpan(("s1",), ("t1",), 0.9, "1:1")
    right_anchor = AlignmentSpan(("s2",), ("t3",), 0.9, "1:1")
    gap = GapCandidate(
        "gap-" + "a" * 20,
        "target",
        (),
        ("t2",),
        left_anchor,
        right_anchor,
        False,
        ("addition",),
    )
    context = CandidateContext(
        gap.candidate_id, "src", target_text, "", "", "", "", "en", "ru", "ru"
    )
    chapter = addition_detector.ChapterContext(
        chapter_id="c1",
        model=QaModelSelection("gemini", "m"),
        cancellation=CancellationToken(),
        minimum_addition_chars=minimum_addition_chars,
    )
    return gap, context, chapter


def test_local_refusal_minimum_length_is_measured_after_casefold() -> None:
    # German sharp s (ß) casefolds to "ss", lengthening the string. The
    # minimum-size check must measure the casefolded length (matching
    # normalize_for_comparison), not the raw length -- otherwise a text that
    # is "below minimum" raw could wrongly pass, or vice versa. Six raw ß
    # characters (len 6) casefold to 12 chars, clearing a threshold of 10.
    gap, context, chapter = _build_addition_context("ß" * 6, minimum_addition_chars=10)

    assert len("ß" * 6) < 10  # raw length would trip the "below minimum" branch
    assert addition_detector._local_refusal(gap, context, {}, chapter) is None
