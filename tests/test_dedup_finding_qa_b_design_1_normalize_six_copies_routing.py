"""Routing tests for finding-qa-b_design_1-normalize-six-copies.

Each of these six former call sites used to hold its own private `_normalize`
body. This test monkeypatches the shared canonical helper
(`gemini_translator.qa.text_normalize.normalize_for_comparison`) as bound
into each consuming module's namespace and proves the call site actually goes
through it. Before the refactor, every one of these assertions fails because
the call site still uses its own local `_normalize` copy and the module has
no `normalize_for_comparison` attribute to patch (AttributeError from
monkeypatch.setattr) or the patched helper is simply never invoked.
"""

from __future__ import annotations

import hashlib

import pytest

from gemini_translator.qa import (
    addition_detector,
    foreign_text_filter,
    glossary_audit,
    glossary_terms,
    repair_validator,
    structural_repair,
)
from gemini_translator.qa.llm import CancellationToken, QaModelSelection
from gemini_translator.qa.llm import omission_repairer
from gemini_translator.qa.llm.schemas import OmissionVerdict, RepairProposal
from gemini_translator.qa.models import (
    AlignmentSpan,
    CandidateContext,
    GapCandidate,
    VerifiedCandidate,
)


def _recording_fake():
    calls: list[str] = []

    def fake(value: str) -> str:
        calls.append(value)
        return value

    return calls, fake


def test_addition_detector_routes_through_canonical(monkeypatch: pytest.MonkeyPatch) -> None:
    calls, fake = _recording_fake()
    monkeypatch.setattr(addition_detector, "normalize_for_comparison", fake)

    candidate_id = "gap-" + "a" * 20
    left_anchor = AlignmentSpan(("s1",), ("t1",), 0.9, "1:1")
    right_anchor = AlignmentSpan(("s2",), ("t3",), 0.9, "1:1")
    gap = GapCandidate(
        candidate_id, "target", (), ("t2",), left_anchor, right_anchor, False, ("addition",)
    )
    context = CandidateContext(
        candidate_id, "src", "  тест  ", "", "", "", "", "en", "ru", "ru"
    )
    chapter = addition_detector.ChapterContext(
        chapter_id="c1",
        model=QaModelSelection("gemini", "m"),
        cancellation=CancellationToken(),
        minimum_addition_chars=1000,
    )

    result = addition_detector._local_refusal(gap, context, {}, chapter)

    assert calls, "expected _local_refusal to normalize target_text via the canonical helper"
    assert result == "addition_below_minimum_size"


def test_foreign_text_filter_routes_through_canonical(monkeypatch: pytest.MonkeyPatch) -> None:
    calls, fake = _recording_fake()
    monkeypatch.setattr(foreign_text_filter, "normalize_for_comparison", fake)

    foreign_text_filter._meaningful("  hello  ")

    assert calls == ["  hello  "]


def test_structural_repair_routes_through_canonical(monkeypatch: pytest.MonkeyPatch) -> None:
    calls, fake = _recording_fake()
    monkeypatch.setattr(structural_repair, "normalize_for_comparison", fake)

    structural_repair._visible_occurrences({"blocks": []}, "hello")

    assert calls == ["hello"]


def test_repair_validator_routes_through_canonical(monkeypatch: pytest.MonkeyPatch) -> None:
    calls, fake = _recording_fake()
    monkeypatch.setattr(repair_validator, "normalize_for_comparison", fake)

    repair_validator._occurrences({"blocks": []}, "hello")

    assert calls == ["hello"]


def test_omission_repairer_routes_through_canonical(monkeypatch: pytest.MonkeyPatch) -> None:
    calls, fake = _recording_fake()
    monkeypatch.setattr(omission_repairer, "normalize_for_comparison", fake)

    name = "repair"
    candidate_id = "gap-" + hashlib.sha256(name.encode()).hexdigest()[:20]
    candidate = GapCandidate(
        candidate_id=candidate_id,
        side="source",
        source_unit_ids=(f"{name}-s1",),
        target_unit_ids=(),
        left_anchor=AlignmentSpan((f"{name}-s0",), (f"{name}-t0",), 0.94, "1:1"),
        right_anchor=AlignmentSpan((f"{name}-s2",), (f"{name}-t2",), 0.93, "1:1"),
        repairable=True,
        signals=("missing_in_target",),
    )
    context = CandidateContext(
        candidate_id=candidate_id,
        source_text="She spoke of the gate.",
        target_text="",
        source_before="She looked at the gate.",
        source_after="Then the rain started.",
        target_before="Она посмотрела на ворота.",
        target_after="Потом начался дождь.",
        source_language="en",
        target_language="ru",
        candidate_language="en",
    )
    foreign_text_decision = foreign_text_filter.ForeignTextFilter().classify(
        candidate, context
    )
    verdict = OmissionVerdict(
        decision="missing_content",
        confidence=0.97,
        source_unit_ids=candidate.source_unit_ids,
        missing_facts=("Пропущенный факт.",),
        explanation="Локальный перевод не содержит нужный факт.",
    )
    verified = VerifiedCandidate(
        candidate=candidate,
        context=context,
        verdict=verdict,
        foreign_text_decision=foreign_text_decision,
        eligible_for_repair=True,
        status="verified",
    )
    proposal = RepairProposal(
        candidate_id=candidate_id,
        translated_fragment="Она сказала про ворота.",
        glossary_terms_used=(),
    )

    omission_repairer._rejection_detail(proposal, verified, (), request=None)

    assert calls, "expected _rejection_detail to normalize the fragment via the canonical helper"


def test_glossary_audit_normalize_routes_through_canonical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, fake = _recording_fake()
    monkeypatch.setattr(glossary_audit, "normalize_for_comparison", fake)

    glossary_audit._normalize("hello")

    assert calls, "expected _normalize to normalize via the canonical helper"


def test_glossary_audit_normalize_policy_text_routes_through_canonical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # _normalize_policy_text lives in glossary_terms.py (moved there so that
    # importing it does not also import pandas) and closes over that
    # module's own `normalize_for_comparison` binding, so the patch target
    # must be glossary_terms, not glossary_audit.
    calls, fake = _recording_fake()
    monkeypatch.setattr(glossary_terms, "normalize_for_comparison", fake)

    glossary_terms._normalize_policy_text("hello")

    assert calls, "expected _normalize_policy_text to normalize via the canonical helper"
