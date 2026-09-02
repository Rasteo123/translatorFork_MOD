"""Single-attempt local repair proposals for confirmed semantic omissions."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
from pathlib import Path
from typing import cast

import pytest

from gemini_translator.qa.foreign_text_filter import ForeignTextFilter
from gemini_translator.qa.llm import (
    CancellationToken,
    OmissionRepairer,
    OmissionRepairError,
    QaModelSelection,
    RepairContext,
)
from gemini_translator.qa.llm.schemas import OmissionVerdict
from gemini_translator.qa.models import (
    AlignmentSpan,
    CandidateContext,
    GapCandidate,
    GlossaryPolicy,
    OmissionRepairerConfig,
    ProtectedEntityHint,
    QaModelValidationError,
    RelevantGlossaryTerm,
    VerifiedCandidate,
)


_SOURCE_GAP = "He never told her the tower had already fallen."
_FRAGMENT = "Он так и не сказал ей, что башня уже пала."
_MISSING_FACT = "Потеряно отрицание: он не сказал о падении башни."


class RecordingClient:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls = 0
        self.last_prompt: str | None = None
        self.last_max_output_tokens: int | None = None

    async def complete_json(
        self,
        prompt: str,
        *,
        model: QaModelSelection,
        max_output_tokens: int,
        cancellation: CancellationToken,
        purpose: str = "",
    ) -> dict[str, object]:
        self.calls += 1
        self.last_prompt = prompt
        self.last_max_output_tokens = max_output_tokens
        if isinstance(self.response, BaseException):
            raise self.response
        return cast(dict[str, object], deepcopy(self.response))


def _candidate(name: str = "repair") -> GapCandidate:
    return GapCandidate(
        candidate_id="gap-" + hashlib.sha256(name.encode()).hexdigest()[:20],
        side="source",
        source_unit_ids=(f"{name}-s1",),
        target_unit_ids=(),
        left_anchor=AlignmentSpan((f"{name}-s0",), (f"{name}-t0",), 0.94, "1:1"),
        right_anchor=AlignmentSpan((f"{name}-s2",), (f"{name}-t2",), 0.93, "1:1"),
        repairable=True,
        signals=("missing_in_target",),
    )


def _context(
    candidate: GapCandidate,
    *,
    source_text: str = _SOURCE_GAP,
    protected_entities: tuple[ProtectedEntityHint, ...] = (),
) -> CandidateContext:
    return CandidateContext(
        candidate_id=candidate.candidate_id,
        source_text=source_text,
        target_text="",
        source_before="She looked at the gate.",
        source_after="Then the rain started.",
        target_before="Она посмотрела на ворота.",
        target_after="Потом начался дождь.",
        source_language="en",
        target_language="ru",
        candidate_language="en",
        protected_entities=protected_entities,
    )


def _verified(
    *,
    eligible: bool = True,
    source_text: str = _SOURCE_GAP,
    protected_entities: tuple[ProtectedEntityHint, ...] = (),
) -> VerifiedCandidate:
    candidate = _candidate()
    context = _context(
        candidate, source_text=source_text, protected_entities=protected_entities
    )
    verdict = OmissionVerdict(
        decision="missing_content",
        confidence=0.97,
        source_unit_ids=candidate.source_unit_ids,
        missing_facts=(_MISSING_FACT,),
        explanation="Локальный перевод не содержит отрицания.",
    )
    return VerifiedCandidate(
        candidate=candidate,
        context=context,
        verdict=verdict,
        foreign_text_decision=ForeignTextFilter().classify(candidate, context),
        eligible_for_repair=eligible,
        status="verified",
    )


def _glossary() -> tuple[RelevantGlossaryTerm, ...]:
    return (
        RelevantGlossaryTerm("tower", "башня", GlossaryPolicy.MUST_TRANSLATE, 1, 0),
    )


def _request(**overrides: object) -> RepairContext:
    values: dict[str, object] = {
        "chapter_id": "chapter-1",
        "model": QaModelSelection("gemini", "qa-model"),
        "cancellation": CancellationToken(),
        "glossary": _glossary(),
        "style_guide": "Третье лицо, прошедшее время.",
    }
    values.update(overrides)
    return RepairContext(**values)  # type: ignore[arg-type]


def _response(verified: VerifiedCandidate, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "candidate_id": verified.candidate.candidate_id,
        "translated_fragment": _FRAGMENT,
        "glossary_terms_used": ["tower"],
    }
    payload.update(overrides)
    return payload


def _propose(client: RecordingClient, verified: VerifiedCandidate, **overrides: object):
    return asyncio.run(
        OmissionRepairer(client).propose(verified, _request(**overrides))
    )


def test_prompt_carries_only_the_gap_context_facts_and_relevant_glossary():
    """Sending the whole chapter would invite a rewrite instead of a local repair."""
    verified = _verified()
    client = RecordingClient(_response(verified))

    proposal = _propose(client, verified)
    prompt = client.last_prompt or ""

    assert "Переведи только потерянный фрагмент" in prompt
    assert _SOURCE_GAP in prompt
    assert "Она посмотрела на ворота." in prompt
    assert "Потом начался дождь." in prompt
    assert _MISSING_FACT in prompt
    assert "tower → башня" in prompt
    assert "Третье лицо, прошедшее время." in prompt
    assert "chapter-unrelated-tail" not in prompt
    assert proposal.candidate_id == verified.candidate.candidate_id
    assert proposal.translated_fragment == _FRAGMENT
    assert client.calls == 1


def test_ineligible_candidate_never_reaches_the_model():
    """Repairing an unconfirmed candidate would bypass every upstream safeguard."""
    client = RecordingClient(_response(_verified()))

    with pytest.raises(OmissionRepairError) as excinfo:
        _propose(client, _verified(eligible=False))

    assert excinfo.value.reason == "not_eligible"
    assert client.calls == 0


def test_missing_prompt_configuration_fails_closed():
    """A broken data bundle must not silently fall back to an unreviewed prompt."""
    verified = _verified()
    client = RecordingClient(_response(verified))
    repairer = OmissionRepairer(
        client, OmissionRepairerConfig(prompt_path=Path("/nonexistent/qa-prompts.json"))
    )

    with pytest.raises(OmissionRepairError) as excinfo:
        asyncio.run(repairer.propose(verified, _request()))

    assert excinfo.value.reason == "prompt_configuration_unavailable"
    assert client.calls == 0


@pytest.mark.parametrize(
    ("fragment", "detail"),
    [
        ("```\n" + _FRAGMENT + "\n```", "markdown_fence"),
        ("<!DOCTYPE html><html><body>" + _FRAGMENT + "</body></html>", "html_document"),
        ("Она посмотрела на ворота. " + _FRAGMENT, "anchor_echo"),
        (_FRAGMENT + " Потом начался дождь.", "anchor_echo"),
        (_SOURCE_GAP, "untranslated_source"),
        ("Он так и не сказал ей, что the tower уже пала.", "original_term_kept"),
    ],
)
def test_unusable_fragments_are_rejected_after_exactly_one_attempt(fragment, detail):
    """A rewrite, a document, or an untranslated echo is not a local repair."""
    verified = _verified()
    client = RecordingClient(_response(verified, translated_fragment=fragment))

    with pytest.raises(OmissionRepairError) as excinfo:
        _propose(client, verified)

    assert excinfo.value.reason == "fragment_rejected"
    assert excinfo.value.detail == detail
    assert client.calls == 1


def test_keep_original_term_must_survive_in_the_repaired_fragment():
    """Translating a protected brand would break the book's own convention."""
    verified = _verified(source_text="The Nokia handset lay on the table.")
    client = RecordingClient(
        _response(
            verified,
            translated_fragment="Трубка Нокиа лежала на столе.",
            glossary_terms_used=["Nokia"],
        )
    )
    glossary = (
        RelevantGlossaryTerm("Nokia", "Nokia", GlossaryPolicy.KEEP_ORIGINAL, 1, 0),
    )

    with pytest.raises(OmissionRepairError) as excinfo:
        _propose(client, verified, glossary=glossary)

    assert excinfo.value.detail == "original_term_dropped"


def test_candidate_mismatch_and_invalid_payloads_are_rejected():
    """A proposal bound to another candidate must never be applied anywhere."""
    verified = _verified()

    for response, reason in (
        (_response(verified, candidate_id="gap-" + "0" * 20), "invalid_response"),
        ({"translated_fragment": _FRAGMENT}, "invalid_response"),
        ("not an object", "invalid_response"),
        (TimeoutError("slow"), "completion_timeout"),
        (RuntimeError("transport"), "completion_failed"),
    ):
        client = RecordingClient(response)
        with pytest.raises(OmissionRepairError) as excinfo:
            _propose(client, verified)
        assert excinfo.value.reason == reason
        assert client.calls == 1


def test_cancellation_propagates_before_any_completion():
    """A cancelled session must stop before spending a repair request."""
    verified = _verified()
    client = RecordingClient(_response(verified))
    token = CancellationToken()
    token.cancel()

    with pytest.raises(asyncio.CancelledError):
        _propose(client, verified, cancellation=token)

    assert client.calls == 0


def test_repair_context_and_config_validate_their_own_contracts():
    """Loose repair inputs would let unbounded or unattributed edits through."""
    with pytest.raises(QaModelValidationError):
        RepairContext("", QaModelSelection("gemini", "m"), CancellationToken())
    with pytest.raises(QaModelValidationError):
        RepairContext(
            "chapter-1",
            QaModelSelection("gemini", "m"),
            CancellationToken(),
            glossary=("tower",),  # type: ignore[arg-type]
        )
    with pytest.raises(QaModelValidationError):
        OmissionRepairerConfig(max_output_tokens=4097)
    with pytest.raises(QaModelValidationError):
        OmissionRepairerConfig(prompt_version="not-versioned")
