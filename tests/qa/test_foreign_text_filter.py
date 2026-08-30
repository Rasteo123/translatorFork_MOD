"""Safety contracts for filtering intentional foreign text before LLM verification."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from gemini_translator.qa.foreign_text_filter import ForeignTextFilter, filter_gap_candidates
from gemini_translator.qa.glossary_audit import match_glossary_policies
from gemini_translator.qa.models import (
    AlignmentResult,
    AlignmentSpan,
    CandidateContext,
    CandidateFilterResult,
    FilteredCandidate,
    ForeignTextDecision,
    GapCandidate,
    GlossaryPolicy,
    GlossaryRule,
    ProtectedEntityHint,
    QaModelValidationError,
)


_CASES = json.loads(
    (Path(__file__).parents[1] / "fixtures/qa/foreign_text_cases.json").read_text()
)


def _candidate(name: str, *, side: str = "source", repairable: bool = True):
    left = AlignmentSpan((f"{name}-s0",), (f"{name}-t0",), 1.0, "1:1")
    right = AlignmentSpan((f"{name}-s2",), (f"{name}-t2",), 1.0, "1:1")
    if side == "source":
        gap_span = AlignmentSpan((f"{name}-s1",), (), 0.0, "1:0")
        source_ids, target_ids = gap_span.source_unit_ids, ()
        signals = ("missing_in_target",)
    else:
        gap_span = AlignmentSpan((), (f"{name}-t1",), 0.0, "0:1")
        source_ids, target_ids = (), gap_span.target_unit_ids
        signals = ("addition",)
        repairable = False
    candidate_id = "gap-" + hashlib.sha256(name.encode()).hexdigest()[:20]
    candidate = GapCandidate(
        candidate_id, side, source_ids, target_ids, left, right, repairable, signals
    )
    return candidate, AlignmentResult((left, gap_span, right), (candidate,), 3)


def _loaded_case(name: str):
    raw = deepcopy(_CASES[name])
    candidate, result = _candidate(name, side=raw.pop("side", "source"))
    glossary = tuple(GlossaryRule(**rule) for rule in raw.pop("glossary"))
    entities = tuple(ProtectedEntityHint(**hint) for hint in raw.pop("protected_entities"))
    expected_action = raw.pop("expected_action")
    expected_category = raw.pop("expected_category")
    context = CandidateContext(
        candidate_id=candidate.candidate_id,
        source_before="previous source sentence",
        source_after="next source sentence",
        target_before="предыдущее предложение",
        target_after="следующее предложение",
        protected_entities=entities,
        protected_contexts=tuple(raw.pop("protected_contexts")),
        **raw,
    )
    return candidate, result, context, glossary, expected_action, expected_category


@pytest.mark.parametrize("case_name", tuple(_CASES))
def test_fixture_cases_follow_the_false_positive_safety_policy(case_name):
    """A broad regex or name heuristic must not discard narrative omissions."""
    candidate, _, context, glossary, expected_action, expected_category = _loaded_case(
        case_name
    )

    decision = ForeignTextFilter(glossary).classify(candidate, context)

    assert (decision.action, decision.category) == (expected_action, expected_category)
    assert decision.confidence in {"high", "medium", "low"}
    assert decision.confidence != "high_repair"
    assert decision.reasons and len(set(decision.reasons)) == len(decision.reasons)


@pytest.mark.parametrize(
    "case_name",
    (
        "brand",
        "person_name",
        "device_model",
        "isbn",
        "sku",
        "url",
        "email",
        "organization",
        "title",
        "sign",
        "foreign_quote",
        "foreign_dialogue",
        "glossary_keep_original",
        "glossary_either",
    ),
)
def test_allowed_foreign_content_never_enters_the_llm_repair_path(case_name):
    """An intentional protected item reaching the verifier would revive false repairs."""
    candidate, _, context, glossary, _, _ = _loaded_case(case_name)

    decision = ForeignTextFilter(glossary).classify(candidate, context)

    assert decision.action in {"exclude", "report_only"}


def test_must_translate_policy_overrides_entity_and_pattern_heuristics():
    """Running brand detection first would hide an explicit translation obligation."""
    candidate, _, context, glossary, _, _ = _loaded_case("explicit_must_translate")

    decision = ForeignTextFilter(glossary).classify(candidate, context)

    assert decision == ForeignTextDecision(
        category="glossary_must_translate",
        action="send_to_llm_verifier",
        confidence="high",
        reasons=("glossary_must_translate:Apple",),
    )


def test_conflicting_glossary_rules_fail_closed_and_must_translate_wins():
    """Selecting whichever duplicate rule comes first would make policy order unsafe."""
    candidate, _, context, glossary, _, _ = _loaded_case("mixed_multiple_policies")

    decision = ForeignTextFilter(glossary).classify(candidate, context)
    reversed_decision = ForeignTextFilter(tuple(reversed(glossary))).classify(
        candidate, context
    )

    assert decision == reversed_decision
    assert decision.action == "send_to_llm_verifier"
    assert decision.reasons == (
        "glossary_policy_conflict:Apple:keep_original,must_translate",
        "glossary_must_translate:Apple",
    )


def test_unicode_policy_matching_uses_boundaries_and_stable_longest_order():
    """Substring matching would protect Art inside Arthur and shorter overlapping terms first."""
    rules = (
        GlossaryRule("Art", GlossaryPolicy.KEEP_ORIGINAL),
        GlossaryRule("Soul", GlossaryPolicy.EITHER),
        GlossaryRule("Soul Hall", GlossaryPolicy.MUST_TRANSLATE),
        GlossaryRule("武魂殿", GlossaryPolicy.KEEP_ORIGINAL),
    )

    matches = match_glossary_policies("Arthur met SOUL HALL near 武魂殿; art stayed.", rules)

    assert tuple((match.term, match.policy.value) for match in matches) == (
        ("Soul Hall", "must_translate"),
        ("Soul", "either"),
        ("武魂殿", "keep_original"),
        ("Art", "keep_original"),
    )
    assert all(match.exact for match in matches)


def test_keep_original_does_not_discard_narrative_or_negation():
    """A protected token embedded in an action must not erase the rest of the missing sentence."""
    candidate, _, context, glossary, _, _ = _loaded_case(
        "protected_token_in_narrative"
    )

    decision = ForeignTextFilter(glossary).classify(candidate, context)

    assert decision.action == "send_to_llm_verifier"
    assert decision.reasons == (
        "glossary_protected_term_embedded_in_narrative:Apple",
        "candidate_requires_semantic_verification",
    )


def test_quotes_and_dialogue_need_explicit_foreign_context_evidence():
    """Treating punctuation as intent would suppress ordinary missing dialogue."""
    candidate, _, ordinary, glossary, _, _ = _loaded_case("ordinary_dialogue")
    protected = replace(
        ordinary,
        source_language="zh",
        candidate_language="fr",
        protected_contexts=("foreign_dialogue",),
    )

    ordinary_decision = ForeignTextFilter(glossary).classify(candidate, ordinary)
    protected_decision = ForeignTextFilter(glossary).classify(candidate, protected)

    assert ordinary_decision.action == "send_to_llm_verifier"
    assert protected_decision.action == "exclude"


def test_full_protected_items_are_distinguished_from_items_embedded_in_prose():
    """A URL or ISBN example inside a missing sentence still carries narrative meaning."""
    candidate, _, context, glossary, _, _ = _loaded_case("url")
    prose = replace(
        context,
        source_text="She wrote to https://example.org/help but received no answer.",
        target_text="https://example.org/help",
        candidate_language="en",
    )

    assert ForeignTextFilter(glossary).classify(candidate, context).action == "exclude"
    embedded = ForeignTextFilter(glossary).classify(candidate, prose)
    assert embedded.action == "send_to_llm_verifier"
    assert embedded.reasons == (
        "protected_item_embedded_in_prose:url",
        "candidate_requires_semantic_verification",
    )


def test_numbered_narrative_is_not_mistaken_for_a_device_model():
    """A permissive digit pattern would exclude an ordinary sentence such as a duration."""
    candidate, _, context, glossary, _, _ = _loaded_case("missing_negation")
    numbered = replace(
        context,
        source_text="He waited 2 days.",
    )

    decision = ForeignTextFilter(glossary).classify(candidate, numbered)

    assert decision.action == "send_to_llm_verifier"
    assert not any("whole_protected_item:model" in reason for reason in decision.reasons)


@pytest.mark.parametrize("text", ("2024", "1234", "3.14", "10/10", "v2", "A2"))
def test_bare_numeric_surfaces_are_not_excluded_as_codes(text):
    """A numeric-only surface has no alphabetic code evidence and must reach semantics."""
    candidate, _, context, glossary, _, _ = _loaded_case("missing_negation")

    decision = ForeignTextFilter(glossary).classify(
        candidate, replace(context, source_text=text)
    )

    assert decision.action == "send_to_llm_verifier"
    assert not any("whole_protected_item:code" in reason for reason in decision.reasons)


def test_alpha_numeric_separator_surface_remains_a_strong_code_item():
    """Narrowing numeric matching must retain a genuine compact identifier."""
    candidate, _, context, glossary, _, _ = _loaded_case("missing_negation")

    decision = ForeignTextFilter(glossary).classify(
        candidate, replace(context, source_text="AB-2048")
    )

    assert decision.action == "exclude"
    assert decision.reasons == ("whole_protected_item:code",)


@pytest.mark.parametrize(
    "text", ("Chapter 2048", "Version 2", "Page 1234", "Section A2")
)
def test_generic_label_and_number_are_not_excluded_as_device_models(text):
    """A generic noun plus number is ordinary content, not strong product structure."""
    candidate, _, context, glossary, _, _ = _loaded_case("missing_negation")

    decision = ForeignTextFilter(glossary).classify(
        candidate, replace(context, source_text=text)
    )

    assert decision.action == "send_to_llm_verifier"
    assert not any("whole_protected_item:model" in reason for reason in decision.reasons)


@pytest.mark.parametrize(
    "text",
    (
        "Floor 2A",
        "Part B2",
        "Room A2",
        "Map A2",
        "Section C3",
        "Figure B2",
        "Chapter D4",
    ),
)
def test_label_and_alphanumeric_reference_reaches_semantics(text):
    """An alphanumeric reference token does not make an ordinary label a product model."""
    candidate, _, context, glossary, _, _ = _loaded_case("missing_negation")

    decision = ForeignTextFilter(glossary).classify(
        candidate, replace(context, source_text=text)
    )

    assert decision.action == "send_to_llm_verifier"
    assert not any("whole_protected_item:model" in reason for reason in decision.reasons)


@pytest.mark.parametrize(
    "text",
    (
        "He 2 Pro",
        "My 2 Pro",
        "Open 2 Pro",
        "His 3 Max",
        "Close 4 Ultra",
        "Winter 5 Pro",
    ),
)
def test_generic_title_case_number_and_product_suffix_reaches_semantics(text):
    """A marketing suffix cannot turn a pronoun, verb, or ordinary title into a model."""
    candidate, _, context, glossary, _, _ = _loaded_case("missing_negation")

    decision = ForeignTextFilter(glossary).classify(
        candidate, replace(context, source_text=text)
    )

    assert decision.action == "send_to_llm_verifier"
    assert not any("whole_protected_item:model" in reason for reason in decision.reasons)


@pytest.mark.parametrize(
    "text", ("iPhone 15 Pro", "Galaxy S23 Ultra", "S23 Pro", "Pixel 8 Pro")
)
def test_product_model_surface_without_explicit_evidence_reaches_semantics(text):
    """A product-looking surface cannot discard a gap without authoritative evidence."""
    candidate, _, context, glossary, _, _ = _loaded_case("missing_negation")

    decision = ForeignTextFilter(glossary).classify(
        candidate, replace(context, source_text=text)
    )

    assert decision.action == "send_to_llm_verifier"


@pytest.mark.parametrize(
    "text",
    (
        "Room S23 Pro",
        "Figure S23 Pro",
        "Table S23 Pro",
        "Model S23 Pro",
        "Item S23 Pro",
        "The S23 Pro",
    ),
)
def test_label_and_product_looking_surface_without_hint_reaches_semantics(text):
    """Adding a model-like suffix to an ordinary label cannot authorize exclusion."""
    candidate, _, context, glossary, _, _ = _loaded_case("missing_negation")

    decision = ForeignTextFilter(glossary).classify(
        candidate, replace(context, source_text=text)
    )

    assert decision.action == "send_to_llm_verifier"


@pytest.mark.parametrize("text", ("S23", "A2", "Galaxy S23"))
def test_alphanumeric_token_without_product_structure_reaches_semantics(text):
    """A standalone identifier-shaped token is ambiguous without product structure."""
    candidate, _, context, glossary, _, _ = _loaded_case("missing_negation")

    decision = ForeignTextFilter(glossary).classify(
        candidate, replace(context, source_text=text)
    )

    assert decision.action == "send_to_llm_verifier"


@pytest.mark.parametrize(
    ("text", "category"),
    (("S23", "device_model"), ("Pixel 8 Pro", "product")),
)
def test_explicit_entity_hint_protects_an_ambiguous_model_surface(text, category):
    """Explicit upstream evidence can protect a surface that heuristics leave ambiguous."""
    candidate, _, context, glossary, _, _ = _loaded_case("missing_negation")
    hinted = replace(
        context,
        source_text=text,
        protected_entities=(ProtectedEntityHint(text, category),),
    )

    decision = ForeignTextFilter(glossary).classify(candidate, hinted)

    assert decision.action == "exclude"
    assert decision.reasons == (f"explicit_protected_entity:{category}:{text}",)


@pytest.mark.parametrize(
    "policy", (GlossaryPolicy.KEEP_ORIGINAL, GlossaryPolicy.EITHER)
)
def test_exact_glossary_policy_protects_a_model_surface(policy):
    """Exact authoritative glossary policy may explicitly protect a whole model surface."""
    candidate, _, context, glossary, _, _ = _loaded_case("missing_negation")
    glossary = (GlossaryRule("Galaxy S23 Ultra", policy),)

    decision = ForeignTextFilter(glossary).classify(
        candidate, replace(context, source_text="Galaxy S23 Ultra")
    )

    assert decision.action == "exclude"
    assert decision.category == "glossary_protected"


def test_strong_model_identifier_embedded_in_narrative_reaches_semantics():
    """Strong identifier evidence must not suppress the surrounding missing narrative."""
    candidate, _, context, glossary, _, _ = _loaded_case("missing_negation")

    decision = ForeignTextFilter(glossary).classify(
        candidate,
        replace(context, source_text="He returned the Galaxy S23 Ultra yesterday."),
    )

    assert decision.action == "send_to_llm_verifier"


def test_unrepairable_product_model_surface_without_hint_stays_report_only():
    """Removing surface exclusion must retain the two-anchor repair boundary."""
    _, _, context, glossary, _, _ = _loaded_case("missing_negation")
    unrepairable, _ = _candidate("unrepairable-model-surface", repairable=False)
    context = replace(
        context,
        candidate_id=unrepairable.candidate_id,
        source_text="iPhone 15 Pro",
    )

    decision = ForeignTextFilter(glossary).classify(unrepairable, context)

    assert decision.action == "report_only"


@pytest.mark.parametrize("text", ("2024", "Chapter 2048"))
def test_unrepairable_numbered_surface_stays_report_only(text):
    """Removing protected-item false positives must not bypass the two-anchor requirement."""
    candidate, _, context, glossary, _, _ = _loaded_case("missing_negation")
    unrepairable, _ = _candidate(f"unrepairable-{text}", repairable=False)
    context = replace(
        context,
        candidate_id=unrepairable.candidate_id,
        source_text=text,
    )

    decision = ForeignTextFilter(glossary).classify(unrepairable, context)

    assert decision.action == "report_only"


def test_mixed_glossary_terms_keep_outer_latin_and_digit_boundaries():
    """A CJK edge must not let a mixed policy term match inside a larger code-like token."""
    rules = (GlossaryRule("Art武", GlossaryPolicy.KEEP_ORIGINAL),)

    assert match_glossary_policies("ArthurArt武 arrived", rules) == ()
    assert match_glossary_policies("Art武A arrived", rules) == ()
    assert match_glossary_policies("9Art武 arrived", rules) == ()
    assert match_glossary_policies("Art武9 arrived", rules) == ()
    assert len(match_glossary_policies("Art武 arrived", rules)) == 1


def test_pure_cjk_glossary_terms_still_match_with_cjk_neighbors():
    """Outer token boundaries for mixed terms must not disable deterministic CJK substrings."""
    rules = (GlossaryRule("武魂", GlossaryPolicy.KEEP_ORIGINAL),)

    matches = match_glossary_policies("斗罗武魂殿", rules)

    assert tuple(match.term for match in matches) == ("武魂",)


@pytest.mark.parametrize(
    "text", ("Blue House", "His Sword", "Open Door", "Winter Night", "John Smith")
)
def test_title_case_surface_without_explicit_entity_evidence_reaches_semantics(text):
    """Title casing alone cannot distinguish a name from missing narrative or a phrase."""
    candidate, _, context, glossary, _, _ = _loaded_case("missing_negation")

    decision = ForeignTextFilter(glossary).classify(
        candidate, replace(context, source_text=text, candidate_language="en")
    )

    assert decision.action == "send_to_llm_verifier"


def test_explicit_person_hint_still_excludes_the_whole_entity():
    """Removing title-case inference must preserve explicit protected entity evidence."""
    candidate, _, context, glossary, _, _ = _loaded_case("person_name")

    decision = ForeignTextFilter(glossary).classify(candidate, context)

    assert decision.action == "exclude"
    assert decision.reasons == (
        "explicit_protected_entity:person:Arthur Morgan",
    )


def test_filter_partitions_every_gap_once_without_mutating_inputs():
    """Filtering in place would corrupt alignment evidence consumed by later QA stages."""
    must_candidate, must_result, must_context, must_glossary, _, _ = _loaded_case(
        "explicit_must_translate"
    )
    entity_candidate, _, entity_context, entity_glossary, _, _ = _loaded_case(
        "person_name"
    )
    addition_candidate, _, addition_context, addition_glossary, _, _ = _loaded_case(
        "target_addition"
    )
    # Build independent one-gap results and combine through a helper that preserves immediate anchors.
    candidates_and_results = (
        (must_candidate, must_result, must_context, must_glossary),
        (
            entity_candidate,
            _loaded_case("person_name")[1],
            entity_context,
            entity_glossary,
        ),
        (
            addition_candidate,
            _loaded_case("target_addition")[1],
            addition_context,
            addition_glossary,
        ),
    )
    contexts = {item[0].candidate_id: item[2] for item in candidates_and_results}
    glossary = tuple(rule for item in candidates_and_results for rule in item[3])
    combined_result = _combine_results(tuple(item[1] for item in candidates_and_results))
    before_result = deepcopy(combined_result)
    before_contexts = deepcopy(contexts)

    first = filter_gap_candidates(combined_result, contexts, glossary)
    second = filter_gap_candidates(combined_result, contexts, glossary)

    assert first == second
    assert first.accepted == (
        FilteredCandidate(
            must_candidate.candidate_id,
            must_candidate,
            ForeignTextDecision(
                "glossary_must_translate",
                "send_to_llm_verifier",
                "high",
                ("glossary_must_translate:Apple",),
            ),
        ),
    )
    assert tuple(item.candidate_id for item in first.excluded) == (
        entity_candidate.candidate_id,
    )
    assert tuple(item.candidate_id for item in first.report_only) == (
        addition_candidate.candidate_id,
    )
    assert combined_result == before_result
    assert contexts == before_contexts


def _combine_results(results: tuple[AlignmentResult, ...]) -> AlignmentResult:
    spans: list[AlignmentSpan] = []
    gaps: list[GapCandidate] = []
    for index, result in enumerate(results):
        left, gap_span, right = result.spans
        if index:
            # Adjacent match spans keep each gap's immediate anchors while remaining unique.
            spans.append(left)
        else:
            spans.append(left)
        spans.extend((gap_span, right))
        gaps.append(result.gaps[0])
    return AlignmentResult(tuple(spans), tuple(gaps), len(spans))


def test_missing_or_mismatched_context_fails_closed_and_extra_context_is_ignored():
    """Context lookup failure must produce a deterministic report instead of a crash or repair."""
    candidate, result, context, glossary, _, _ = _loaded_case("missing_context")
    wrong = replace(context, candidate_id="gap-" + "f" * 20)
    extra = replace(context, candidate_id="gap-" + "e" * 20)

    missing = filter_gap_candidates(result, {}, glossary)
    mismatched = filter_gap_candidates(result, {candidate.candidate_id: wrong}, glossary)
    with_extra = filter_gap_candidates(
        result,
        {candidate.candidate_id: context, extra.candidate_id: extra},
        glossary,
    )

    assert missing.report_only[0].decision.reasons == ("candidate_context_missing",)
    assert mismatched.report_only[0].decision.reasons == (
        "candidate_context_id_mismatch",
    )
    assert with_extra.accepted[0].candidate_id == candidate.candidate_id


@pytest.mark.parametrize(
    "builder",
    (
        lambda: CandidateContext(
            candidate_id="bad",
            source_text="text",
            target_text="",
            source_before="",
            source_after="",
            target_before="",
            target_after="",
            source_language="en",
            target_language="ru",
            candidate_language="en",
        ),
        lambda: CandidateContext(
            candidate_id="gap-" + "a" * 20,
            source_text="text",
            target_text="",
            source_before="",
            source_after="",
            target_before="",
            target_after="",
            source_language="en",
            target_language="ru",
            candidate_language=True,
        ),
        lambda: ForeignTextDecision("ambiguous", "high_repair", "high", ("x",)),
        lambda: ForeignTextDecision("anything", "exclude", "high", ["x"]),
        lambda: ForeignTextDecision(
            "target_addition", "send_to_llm_verifier", "medium", ("x",)
        ),
        lambda: ProtectedEntityHint("Apple", "unknown"),
        lambda: GlossaryRule("", GlossaryPolicy.EITHER),
    ),
)
def test_filter_models_reject_malformed_or_ambiguous_values(builder):
    """Loose strings, booleans, or mutable collections would weaken the safety boundary."""
    with pytest.raises(QaModelValidationError):
        builder()


def test_filter_models_are_frozen_and_result_enforces_partition_actions():
    """A mutable decision or action in the wrong partition could silently authorize a repair."""
    candidate, _, _, _, _, _ = _loaded_case("missing_negation")
    accepted = FilteredCandidate(
        candidate.candidate_id,
        candidate,
        ForeignTextDecision(
            "ambiguous",
            "send_to_llm_verifier",
            "medium",
            ("candidate_requires_semantic_verification",),
        ),
    )
    with pytest.raises(FrozenInstanceError):
        accepted.candidate_id = "gap-" + "b" * 20
    with pytest.raises(QaModelValidationError):
        replace(accepted, candidate_id="gap-" + "b" * 20)
    with pytest.raises(QaModelValidationError):
        CandidateFilterResult((), (accepted,), ())


def test_context_rejects_duplicate_entity_hints_before_classification():
    """Duplicate upstream hints must not create duplicate reasons or a runtime model error."""
    _, _, context, _, _, _ = _loaded_case("brand")

    with pytest.raises(QaModelValidationError, match="unique"):
        replace(
            context,
            protected_entities=context.protected_entities + context.protected_entities,
        )


@pytest.mark.parametrize(
    "builder",
    (
        lambda: ForeignTextDecision([], "exclude", "high", ("x",)),
        lambda: ForeignTextDecision("ambiguous", [], "medium", ("x",)),
        lambda: ForeignTextDecision(
            "ambiguous", "send_to_llm_verifier", [], ("x",)
        ),
        lambda: ForeignTextDecision(
            "ambiguous", "send_to_llm_verifier", "medium", ([],)
        ),
        lambda: ProtectedEntityHint("Apple", []),
    ),
)
def test_unhashable_filter_enum_and_reason_values_raise_typed_validation(builder):
    """Membership and duplicate checks must not leak raw TypeError for malformed values."""
    with pytest.raises(QaModelValidationError):
        builder()


def test_unhashable_protected_context_value_raises_typed_validation():
    """Protected context entries must be type-checked before uniqueness hashing."""
    _, _, context, _, _, _ = _loaded_case("brand")

    with pytest.raises(QaModelValidationError):
        replace(context, protected_contexts=([],))


def test_foreign_filter_import_boundary_has_no_ui_engine_or_network_dependency():
    """Importing the pure safety filter must not initialize runtime translation services."""
    project_root = Path(__file__).parents[2]
    script = f"""
import builtins
import sys
sys.path.insert(0, {str(project_root)!r})
blocked_roots = {{"PyQt5", "PyQt6", "PySide6", "requests", "aiohttp", "httpx"}}
real_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name.split('.', 1)[0] in blocked_roots or "translation_engine" in name:
        raise AssertionError(f"forbidden import: {{name}}")
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
import gemini_translator.qa.foreign_text_filter
"""

    completed = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
