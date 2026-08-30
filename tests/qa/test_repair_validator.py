"""Full-cascade validation of one previewed repair before it may be committed."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
from typing import cast

import pytest

from gemini_translator.qa.foreign_text_filter import ForeignTextFilter
from gemini_translator.qa.llm import CancellationToken, QaModelSelection
from gemini_translator.qa.llm.schemas import OmissionVerdict, RepairProposal
from gemini_translator.qa.models import (
    AlignmentSpan,
    CandidateContext,
    GapCandidate,
    GlossaryPolicy,
    RelevantGlossaryTerm,
    VerifiedCandidate,
)
from gemini_translator.qa.repair_validator import ChapterSnapshot, RepairValidator


_LEFT_ANCHOR = "Она посмотрела на ворота."
_RIGHT_ANCHOR = "Потом начался дождь."
_FRAGMENT = "Он так и не сказал ей, что башня уже пала."
_MISSING_FACT = "Потеряно отрицание: он не сказал о падении башни."
_BEFORE_HTML = (
    f"<p>{_LEFT_ANCHOR} {_RIGHT_ANCHOR}</p><p>Башня стояла у самой реки.</p>"
)
_AFTER_HTML = (
    f"<p>{_LEFT_ANCHOR}<span data-qa-repair=\"patch-1\"> {_FRAGMENT}</span>"
    f" {_RIGHT_ANCHOR}</p><p>Башня стояла у самой реки.</p>"
)


class RecordingClient:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls = 0

    async def complete_json(
        self,
        prompt: str,
        *,
        model: QaModelSelection,
        max_output_tokens: int,
        cancellation: CancellationToken,
    ) -> dict[str, object]:
        self.calls += 1
        self.last_prompt = prompt
        if isinstance(self.response, BaseException):
            raise self.response
        return cast(dict[str, object], deepcopy(self.response))


def _candidate() -> VerifiedCandidate:
    candidate = GapCandidate(
        candidate_id="gap-" + hashlib.sha256(b"validator").hexdigest()[:20],
        side="source",
        source_unit_ids=("src-1",),
        target_unit_ids=(),
        left_anchor=AlignmentSpan(("src-0",), ("tgt-0",), 0.95, "1:1"),
        right_anchor=AlignmentSpan(("src-2",), ("tgt-2",), 0.94, "1:1"),
        repairable=True,
        signals=("missing_in_target",),
    )
    context = CandidateContext(
        candidate_id=candidate.candidate_id,
        source_text="He never told her the tower had already fallen.",
        target_text="",
        source_before="She looked at the gate.",
        source_after="Then the rain started.",
        target_before=_LEFT_ANCHOR,
        target_after=_RIGHT_ANCHOR,
        source_language="en",
        target_language="ru",
        candidate_language="en",
    )
    return VerifiedCandidate(
        candidate=candidate,
        context=context,
        verdict=OmissionVerdict(
            decision="missing_content",
            confidence=0.97,
            source_unit_ids=("src-1",),
            missing_facts=(_MISSING_FACT,),
            explanation="Отрицание отсутствует в переводе.",
        ),
        foreign_text_decision=ForeignTextFilter().classify(candidate, context),
        eligible_for_repair=True,
        status="verified",
    )


def _proposal(candidate: VerifiedCandidate, fragment: str = _FRAGMENT) -> RepairProposal:
    return RepairProposal(
        candidate_id=candidate.candidate.candidate_id,
        translated_fragment=fragment,
        glossary_terms_used=("tower",),
    )


def _post_check(candidate: VerifiedCandidate, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "candidate_id": candidate.candidate.candidate_id,
        "confirmed": True,
        "missing_facts_present": [_MISSING_FACT],
        "added_meaning": False,
        "context_rewritten": False,
        "explanation": "Отрицание восстановлено между якорями.",
    }
    payload.update(overrides)
    return payload


def _validate(
    client: RecordingClient,
    *,
    before_html: str = _BEFORE_HTML,
    after_html: str = _AFTER_HTML,
    fragment: str = _FRAGMENT,
    glossary: tuple[RelevantGlossaryTerm, ...] = (),
    current_html: str | None = None,
):
    candidate = _candidate()
    validator = RepairValidator(client)
    return asyncio.run(
        validator.validate(
            ChapterSnapshot("chapter-1", before_html),
            ChapterSnapshot("chapter-1", after_html),
            candidate,
            _proposal(candidate, fragment),
            glossary=glossary,
            model=QaModelSelection("gemini", "qa-model"),
            cancellation=CancellationToken(),
            current_html=current_html,
        )
    )


def test_clean_repair_is_accepted_after_a_confirming_post_check():
    """The success path must stay reachable, or no repair is ever applied."""
    client = RecordingClient(_post_check(_candidate()))

    validation = _validate(client)

    assert validation.accepted is True
    assert validation.reasons == ()
    assert client.calls == 1
    assert _FRAGMENT in client.last_prompt
    assert "Башня стояла у самой реки." not in client.last_prompt


def test_unchanged_chapter_is_rejected_as_a_missing_repair():
    """Committing a preview that changed nothing would mark a gap as fixed."""
    client = RecordingClient(_post_check(_candidate()))

    validation = _validate(client, after_html=_BEFORE_HTML)

    assert validation.accepted is False
    assert "fragment_not_inserted" in validation.reasons
    assert client.calls == 0


def test_duplicate_fragment_and_lost_anchor_are_reported_together():
    """Reporting only the first defect would hide the rest from the journal."""
    broken = (
        f"<p>{_FRAGMENT} {_FRAGMENT}</p><p>Башня стояла у самой реки.</p>"
    )
    client = RecordingClient(_post_check(_candidate()))

    validation = _validate(client, after_html=broken)

    assert validation.accepted is False
    assert "duplicate_fragment" in validation.reasons
    assert "anchor_missing" in validation.reasons
    assert client.calls == 0


def test_unrelated_chapter_change_is_rejected():
    """A repair that edits another paragraph is a rewrite, not a local insertion."""
    tampered = _AFTER_HTML.replace("Башня стояла у самой реки.", "Башня рухнула.")
    client = RecordingClient(_post_check(_candidate()))

    validation = _validate(client, after_html=tampered)

    assert validation.accepted is False
    assert "unrelated_text_changed" in validation.reasons


def test_broken_glossary_policy_is_rejected_before_the_post_check():
    """A protected term must not change meaning through a repair fragment."""
    fragment = "Он оставил the tower нетронутой."
    after = _AFTER_HTML.replace(_FRAGMENT, fragment)
    client = RecordingClient(_post_check(_candidate()))

    validation = _validate(
        client,
        after_html=after,
        fragment=fragment,
        glossary=(
            RelevantGlossaryTerm("tower", "башня", GlossaryPolicy.MUST_TRANSLATE, 1, 0),
        ),
    )

    assert validation.accepted is False
    assert "glossary_violation" in validation.reasons
    assert client.calls == 0


def test_broken_markup_changes_block_identity_and_is_rejected():
    """Losing a closing tag reshapes the chapter and must never be committed."""
    client = RecordingClient(_post_check(_candidate()))

    validation = _validate(
        client, after_html=_AFTER_HTML.replace("</p><p>Башня", "<p>Башня")
    )

    assert validation.accepted is False
    assert "block_identity_changed" in validation.reasons
    assert client.calls == 0


def test_empty_preview_is_rejected_as_invalid_html():
    """An empty candidate chapter is a failure, not a repair."""
    client = RecordingClient(_post_check(_candidate()))

    validation = _validate(client, after_html="")

    assert validation.accepted is False
    assert "invalid_html" in validation.reasons
    assert client.calls == 0


def test_added_markup_outside_the_repair_is_rejected():
    """A repair may add its own span and nothing else to the chapter markup."""
    client = RecordingClient(_post_check(_candidate()))

    validation = _validate(
        client,
        after_html=_AFTER_HTML.replace("Башня стояла", "<em>Башня</em> стояла"),
    )

    assert validation.accepted is False
    assert "structure_changed" in validation.reasons


def test_chapter_edited_after_the_preview_is_rejected():
    """A human edit made during verification must win over the automatic repair."""
    client = RecordingClient(_post_check(_candidate()))

    validation = _validate(
        client, current_html=_BEFORE_HTML + "<p>Ручная правка.</p>"
    )

    assert validation.accepted is False
    assert "chapter_changed" in validation.reasons
    assert client.calls == 0


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        ({"confirmed": True}, "post_check_invalid_response"),
        ("not an object", "post_check_invalid_response"),
        (TimeoutError("slow"), "post_check_timeout"),
        (RuntimeError("transport"), "post_check_failed"),
    ],
)
def test_unusable_post_check_answers_fail_closed(response, reason):
    """An unreadable confirmation is not a confirmation."""
    validation = _validate(RecordingClient(response))

    assert validation.accepted is False
    assert reason in validation.reasons


def test_post_check_must_confirm_exactly_the_listed_missing_facts():
    """Confirming other facts would accept a repair nobody asked for."""
    candidate = _candidate()

    for overrides, reason in (
        ({"confirmed": False, "missing_facts_present": []}, "post_check_rejected"),
        (
            {"missing_facts_present": [_MISSING_FACT, "Добавлен новый факт."]},
            "post_check_fact_mismatch",
        ),
        (
            {"confirmed": False, "added_meaning": True, "missing_facts_present": []},
            "post_check_added_meaning",
        ),
        (
            {
                "confirmed": False,
                "context_rewritten": True,
                "missing_facts_present": [],
            },
            "post_check_context_rewritten",
        ),
    ):
        validation = _validate(RecordingClient(_post_check(candidate, **overrides)))
        assert validation.accepted is False
        assert reason in validation.reasons


def test_candidate_identity_mismatch_is_rejected():
    """A confirmation bound to another candidate proves nothing about this one."""
    validation = _validate(
        RecordingClient(
            _post_check(_candidate(), candidate_id="gap-" + "0" * 20)
        )
    )

    assert validation.accepted is False
    assert "post_check_invalid_response" in validation.reasons
