"""Reverse alignment: facts and lines the model added but the source never had."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import cast

import numpy as np
import pytest

from gemini_translator.qa.addition_detector import (
    AdditionDetector,
    ChapterContext,
)
from gemini_translator.qa.capabilities import QaCapabilitySettings
from gemini_translator.qa.coverage_service import (
    EMBEDDINGS_UNAVAILABLE_WARNING,
    STATISTICS_LLM_ONLY_MODE,
    CoverageAnalysis,
    CoverageRequest,
    DefaultCandidateFilter,
    DefaultCoverageMetricsCollector,
    SemanticCoverageService,
)
from gemini_translator.qa.embeddings.base import EmbeddingBatch
from gemini_translator.qa.llm import CancellationToken, QaModelSelection
from gemini_translator.qa.models import AlignmentResult, AlignmentSpan, GapCandidate
from gemini_translator.qa.semantic_units import SemanticUnitExtractor


_ADDITION = "Он вспомнил, что мать умерла в тот же день."


class RecordingClient:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls = 0
        self.last_prompt = ""

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


class _Provider:
    name = "recording"

    async def embed(self, request):
        dimensions = request.dimensions or 3
        vectors = np.zeros((len(request.texts), dimensions), dtype=np.float32)
        for index in range(len(request.texts)):
            vectors[index, index % dimensions] = 1.0
        return EmbeddingBatch(vectors, self.name, request.model, dimensions)


class _AdditionAligner:
    """Align every source unit and leave exactly one anchored target addition."""

    def __init__(self, *, anchored: bool = True) -> None:
        self._anchored = anchored

    def align(self, source, target):
        source_ids = tuple(unit.unit_id for unit in source.units)
        target_ids = tuple(unit.unit_id for unit in target.units)
        first = AlignmentSpan((source_ids[0],), (target_ids[0],), 0.99, "1:1")
        second = AlignmentSpan((source_ids[1],), (target_ids[1],), 0.98, "1:1")
        addition = AlignmentSpan((), (target_ids[2],), 0.0, "0:1")
        last = AlignmentSpan((source_ids[2],), (target_ids[3],), 0.97, "1:1")
        spans = (first, second, addition, last)
        gap = GapCandidate(
            "gap-aaaaaaaaaaaaaaaaaaaa",
            "target",
            (),
            (target_ids[2],),
            second if self._anchored else None,
            last if self._anchored else None,
            False,
            ("addition",),
        )
        return AlignmentResult(spans, (gap,), 8)


def _payload(document_id: str, *texts: str, tag: str = "p", role: str = "paragraph") -> dict:
    return {
        "document_id": document_id,
        "blocks": [
            {
                "id": f"b-{document_id}-{index}",
                "tag": tag,
                "role": role,
                "inlines": [
                    {"id": f"i-{document_id}-{index}", "type": "text", "text": text}
                ],
            }
            for index, text in enumerate(texts)
        ],
    }


def _coverage(*, addition: str = _ADDITION, anchored: bool = True, role: str = "paragraph"):
    service = SemanticCoverageService(
        extractor=SemanticUnitExtractor(QaCapabilitySettings()),
        provider=_Provider(),
        aligner=_AdditionAligner(anchored=anchored),
        candidate_filter=DefaultCandidateFilter(),
        metrics_collector=DefaultCoverageMetricsCollector(),
    )
    request = CoverageRequest(
        chapter_id="chapter-7",
        source_payload=_payload(
            "source-doc", "He opened the door.", "The room was empty.", "He left."
        ),
        target_payload=_payload(
            "target-doc",
            "Он открыл дверь.",
            "Комната была пуста.",
            addition,
            "Он ушёл.",
            role=role,
            tag="h1" if role == "heading" else "p",
        ),
        source_language="en",
        target_language="ru",
        embedding_model="embed-model",
        embedding_dimensions=3,
    )
    return asyncio.run(service.analyze(request))


def _context(**overrides: object) -> ChapterContext:
    values: dict[str, object] = {
        "chapter_id": "chapter-7",
        "model": QaModelSelection("gemini", "qa-model"),
        "cancellation": CancellationToken(),
    }
    values.update(overrides)
    return ChapterContext(**values)  # type: ignore[arg-type]


def _verdict(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "candidate_id": "gap-aaaaaaaaaaaaaaaaaaaa",
        "decision": "hallucinated_addition",
        "confidence": 0.94,
        "target_unit_ids": [],
        "added_facts": ["Смерть матери в тот же день отсутствует в оригинале."],
        "explanation": "Факт не поддержан ни одним исходным предложением.",
    }
    payload.update(overrides)
    return payload


def _detect(client: RecordingClient, coverage=None, **context_overrides):
    coverage = coverage if coverage is not None else _coverage()
    if isinstance(client.response, dict) and client.response.get("target_unit_ids") == []:
        client.response["target_unit_ids"] = list(
            coverage.alignment.gaps[0].target_unit_ids
        )
    return asyncio.run(
        AdditionDetector(client).detect(coverage, _context(**context_overrides))
    )


def test_anchored_addition_is_sent_to_the_model_and_blocks_the_gate():
    """An invented fact must be surfaced before the next chapter is translated."""
    client = RecordingClient(_verdict())

    results = _detect(client)

    assert client.calls == 1
    assert len(results) == 1
    assert results[0].status == "verified"
    assert results[0].verdict is not None
    assert results[0].verdict.decision == "hallucinated_addition"
    assert results[0].blocks_gate is True
    assert _ADDITION in client.last_prompt
    assert "The room was empty." in client.last_prompt
    assert "He opened the door." not in client.last_prompt


def test_detector_never_removes_anything_from_the_chapter():
    """The first version reports additions; deleting text is not authorized."""
    client = RecordingClient(_verdict())

    results = _detect(client)

    assert not hasattr(results[0], "removal")
    assert results[0].candidate.side == "target"
    assert results[0].candidate.repairable is False


@pytest.mark.parametrize(
    ("decision", "blocks_gate"),
    [("entailed", False), ("paraphrase", False), ("ambiguous", False)],
)
def test_supported_or_uncertain_additions_never_block_the_gate(decision, blocks_gate):
    """Ordinary explicitation and uncertainty are not hallucinations."""
    client = RecordingClient(
        _verdict(decision=decision, added_facts=[], confidence=0.88)
    )

    results = _detect(client)

    assert results[0].verdict is not None
    assert results[0].verdict.decision == decision
    assert results[0].blocks_gate is blocks_gate


def test_low_confidence_addition_is_reported_without_blocking():
    """A weak signal must not stop a translation session on its own."""
    client = RecordingClient(_verdict(confidence=0.55))

    results = _detect(client)

    assert results[0].blocks_gate is False


def test_addition_without_two_anchors_never_reaches_the_model():
    """Without both anchors there is no local evidence to reason about."""
    client = RecordingClient(_verdict())

    results = _detect(client, coverage=_coverage(anchored=False))

    assert client.calls == 0
    assert results[0].status == "filtered"
    assert results[0].warnings == ("addition_anchors_missing",)
    assert results[0].blocks_gate is False


def test_short_grammatical_addition_never_reaches_the_model():
    """A clarifying particle is a translation choice, not an invented fact."""
    client = RecordingClient(_verdict())

    results = _detect(client, coverage=_coverage(addition="Ну да."))

    assert client.calls == 0
    assert results[0].status == "filtered"
    assert results[0].warnings == ("addition_below_minimum_size",)


def test_service_block_additions_are_not_hallucinations():
    """A heading or navigation block is not narrative content."""
    client = RecordingClient(_verdict())

    results = _detect(client, coverage=_coverage(role="heading"))

    assert client.calls == 0
    assert results[0].status == "filtered"
    assert results[0].warnings == ("addition_non_narrative_block",)


@pytest.mark.parametrize(
    ("response", "status"),
    [
        ({"decision": "hallucinated_addition"}, "invalid_response"),
        ("not an object", "invalid_response"),
        (TimeoutError("slow"), "completion_timeout"),
        (RuntimeError("transport"), "completion_failed"),
    ],
)
def test_unusable_answers_stay_report_only(response, status):
    """An unreadable verdict is not evidence of a hallucination."""
    results = _detect(RecordingClient(response))

    assert results[0].status == status
    assert results[0].verdict is None
    assert results[0].blocks_gate is False


def test_verdict_bound_to_another_candidate_is_refused():
    """A verdict about a different gap proves nothing about this one."""
    results = _detect(RecordingClient(_verdict(candidate_id="gap-" + "0" * 20)))

    assert results[0].status == "invalid_response"
    assert results[0].blocks_gate is False


def test_limited_coverage_mode_produces_no_addition_candidates():
    """Without alignment there is no reverse evidence to report."""
    coverage = _coverage()
    client = RecordingClient(_verdict())
    limited = CoverageAnalysis(
        mode=STATISTICS_LLM_ONLY_MODE,
        source_units=coverage.source_units,
        target_units=coverage.target_units,
        alignment=None,
        candidates=(),
        excluded=(),
        report_only=(),
        contexts={},
        limited_windows=(),
        warnings=(EMBEDDINGS_UNAVAILABLE_WARNING,),
        metrics=coverage.metrics,
    )

    results = asyncio.run(AdditionDetector(client).detect(limited, _context()))

    assert results == ()
    assert client.calls == 0
