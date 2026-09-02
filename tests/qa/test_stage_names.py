"""Every stage that spends a request must say which stage it was.

A check costs eight requests a chapter across four different stages, and a
stage that sends no name is invisible to any accounting: it cannot be counted,
budgeted, or found in a log when a book turns out to have cost too much.  The
language stages already name themselves; these four did not.
"""

from __future__ import annotations

import asyncio
import hashlib
from copy import deepcopy

import pytest

from gemini_translator.qa.addition_detector import DETECTION_PURPOSE
from gemini_translator.qa.llm import CancellationToken, QaModelSelection
from gemini_translator.qa.llm.omission_repairer import REPAIR_PURPOSE
from gemini_translator.qa.llm.omission_verifier import VERIFICATION_PURPOSE
from gemini_translator.qa.repair_validator import POST_CHECK_PURPOSE


class _Naming:
    """A client that remembers what every caller called itself."""

    def __init__(self, response: object) -> None:
        self.response = response
        self.purposes: list[str] = []

    async def complete_json(
        self, prompt, *, model, max_output_tokens, cancellation, purpose=""
    ):
        self.purposes.append(purpose)
        return deepcopy(self.response)


def _model() -> QaModelSelection:
    return QaModelSelection("gemini", "qa-model")


def _candidate(name: str = "named"):
    from gemini_translator.qa.models import AlignmentSpan, GapCandidate

    left = AlignmentSpan((f"{name}-s0",), (f"{name}-t0",), 1.0, "1:1")
    right = AlignmentSpan((f"{name}-s2",), (f"{name}-t2",), 1.0, "1:1")
    return GapCandidate(
        "gap-" + hashlib.sha256(name.encode()).hexdigest()[:20],
        "source",
        (f"{name}-s1",),
        (),
        left,
        right,
        True,
        ("missing_in_target",),
    )


def _context(candidate):
    from gemini_translator.qa.models import CandidateContext

    return CandidateContext(
        candidate_id=candidate.candidate_id,
        source_text="原文",
        target_text="",
        source_before="перед",
        source_after="после",
        target_before="Слева.",
        target_after="Справа.",
        source_language="zh",
        target_language="ru",
        candidate_language="zh",
    )


def test_the_verifier_names_itself():
    from gemini_translator.qa.llm import OmissionVerifier

    candidate = _candidate()
    client = _Naming(
        {
            "decision": "no_omission",
            "confidence": 0.9,
            "source_unit_ids": list(candidate.source_unit_ids),
            "missing_facts": [],
            "explanation": "Всё на месте.",
        }
    )

    asyncio.run(
        OmissionVerifier(client).verify(
            candidate, _context(candidate), (), _model(), CancellationToken()
        )
    )

    assert client.purposes == [VERIFICATION_PURPOSE]


def test_the_four_names_are_distinct_and_readable():
    """A shared or empty name would defeat the counting this exists for."""
    names = (
        VERIFICATION_PURPOSE,
        REPAIR_PURPOSE,
        POST_CHECK_PURPOSE,
        DETECTION_PURPOSE,
    )

    assert len(set(names)) == len(names)
    assert all(name and name == name.strip().lower() for name in names)
    assert all(" " not in name for name in names)
