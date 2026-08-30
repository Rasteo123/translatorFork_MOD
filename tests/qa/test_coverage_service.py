"""Behavioral contracts for the read-only semantic coverage orchestrator."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from functools import wraps
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from gemini_translator.qa.alignment import AlignmentCapacityError
from gemini_translator.qa.coverage_service import (
    CoverageRequest,
    CoverageValidationError,
    DefaultCoverageMetricsCollector,
    SemanticCoverageService,
    UnitProtectionEvidence,
)
from gemini_translator.qa.embeddings.base import (
    EmbeddingBatch,
    EmbeddingContractError,
)
from gemini_translator.qa.embeddings.cache import CachedEmbeddingProvider, EmbeddingCache
from gemini_translator.qa.embeddings.factory import (
    EmbeddingAttempt,
    EmbeddingHttpError,
    EmbeddingResponseError,
    EmbeddingTransportError,
    EmbeddingUnavailableError,
)
from gemini_translator.qa.foreign_text_filter import filter_gap_candidates
from gemini_translator.qa.models import (
    AlignmentResult,
    AlignmentSpan,
    ChapterMetrics,
    GapCandidate,
    GlossaryPolicy,
    GlossaryRule,
    ProtectedEntityHint,
    QaModelValidationError,
)
from gemini_translator.qa.semantic_units import (
    SemanticUnitExtractionError,
    SemanticUnitExtractor,
)
from gemini_translator.qa.capabilities import QaCapabilitySettings


def async_test(function):
    """Run one async behavioral test without adding a pytest plugin dependency."""
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return run


def _payload(document_id: str, *texts: str) -> dict:
    return {
        "document_id": document_id,
        "blocks": [
            {
                "id": f"b-{document_id}-{index}",
                "tag": "p",
                "role": "paragraph",
                "inlines": [
                    {
                        "id": f"i-{document_id}-{index}",
                        "type": "text",
                        "text": text,
                    }
                ],
            }
            for index, text in enumerate(texts)
        ],
    }


def _request(
    *,
    source_payload: object | None = None,
    target_payload: object | None = None,
    protection_evidence: tuple[UnitProtectionEvidence, ...] = (),
) -> CoverageRequest:
    return CoverageRequest(
        chapter_id="chapter-7",
        source_payload=source_payload
        if source_payload is not None
        else _payload("source-doc", "Before.", "Ordinary missing sentence.", "Middle.", "SKU AB-2048", "After."),
        target_payload=target_payload
        if target_payload is not None
        else _payload("target-doc", "До.", "Середина.", "После.", "Добавление."),
        source_language="en",
        target_language="ru",
        embedding_model="embed-model",
        embedding_dimensions=3,
        embedding_task="semantic-similarity",
        glossary=(GlossaryRule("MUST", GlossaryPolicy.MUST_TRANSLATE),),
        protection_evidence=protection_evidence,
    )


class RecordingExtractor:
    def __init__(self, events: list[str] | None = None) -> None:
        self.events = events
        self.real = SemanticUnitExtractor(QaCapabilitySettings())

    def extract(self, payload: dict, language: str):
        if self.events is not None:
            self.events.append(f"extract:{language}")
        return self.real.extract(payload, language)


class RecordingProvider:
    name = "recording"

    def __init__(self, events: list[str] | None = None) -> None:
        self.events = events
        self.requests = []

    async def embed(self, request):
        if self.events is not None:
            self.events.append(f"embed:{request.language}")
        self.requests.append(request)
        dimensions = request.dimensions or 3
        vectors = np.zeros((len(request.texts), dimensions), dtype=np.float32)
        for index in range(len(request.texts)):
            vectors[index, index % dimensions] = 1.0
        return EmbeddingBatch(vectors, self.name, request.model, dimensions)


def _gap(
    candidate_id: str,
    *,
    side: str,
    source_ids: tuple[str, ...],
    target_ids: tuple[str, ...],
    left: AlignmentSpan | None,
    right: AlignmentSpan | None,
) -> GapCandidate:
    return GapCandidate(
        candidate_id,
        side,
        source_ids,
        target_ids,
        left,
        right,
        side == "source" and left is not None and right is not None,
        ("missing_in_target",) if side == "source" else ("addition",),
    )


class ThreePartitionAligner:
    """Creates accepted, excluded, and report-only gaps from ordered real units."""

    def __init__(self, events: list[str] | None = None) -> None:
        self.events = events

    def align(self, source, target):
        if self.events is not None:
            self.events.append("align")
        source_ids = tuple(unit.unit_id for unit in source.units)
        target_ids = tuple(unit.unit_id for unit in target.units)
        first = AlignmentSpan((source_ids[0],), (target_ids[0],), 0.99, "1:1")
        gap_one_span = AlignmentSpan((source_ids[1],), (), 0.0, "1:0")
        middle = AlignmentSpan((source_ids[2],), (target_ids[1],), 0.98, "1:1")
        gap_two_span = AlignmentSpan((source_ids[3],), (), 0.0, "1:0")
        last = AlignmentSpan((source_ids[4],), (target_ids[2],), 0.97, "1:1")
        addition_span = AlignmentSpan((), (target_ids[3],), 0.0, "0:1")
        return AlignmentResult(
            (first, gap_one_span, middle, gap_two_span, last, addition_span),
            (
                _gap(
                    "gap-11111111111111111111",
                    side="source",
                    source_ids=(source_ids[1],),
                    target_ids=(),
                    left=first,
                    right=middle,
                ),
                _gap(
                    "gap-22222222222222222222",
                    side="source",
                    source_ids=(source_ids[3],),
                    target_ids=(),
                    left=middle,
                    right=last,
                ),
                _gap(
                    "gap-33333333333333333333",
                    side="target",
                    source_ids=(),
                    target_ids=(target_ids[3],),
                    left=last,
                    right=None,
                ),
            ),
            12,
        )


class RecordingFilter:
    def __init__(self, events: list[str] | None = None) -> None:
        self.events = events
        self.contexts = None
        self.glossary = None

    def filter(self, result, contexts, glossary):
        if self.events is not None:
            self.events.append("filter")
        self.contexts = contexts
        self.glossary = glossary
        return filter_gap_candidates(result, contexts, glossary)


class RecordingMetricsCollector:
    def __init__(self, events: list[str] | None = None) -> None:
        self.events = events
        self.calls = []

    def collect(self, inputs):
        if self.events is not None:
            self.events.append("metrics")
        self.calls.append(inputs)
        return DefaultCoverageMetricsCollector().collect(inputs)


def _service(
    *,
    extractor=None,
    provider=None,
    aligner=None,
    candidate_filter=None,
    metrics_collector=None,
):
    return SemanticCoverageService(
        extractor=extractor or RecordingExtractor(),
        provider=provider or RecordingProvider(),
        aligner=aligner or ThreePartitionAligner(),
        candidate_filter=candidate_filter or RecordingFilter(),
        metrics_collector=metrics_collector or RecordingMetricsCollector(),
    )


@async_test
async def test_normal_path_runs_in_contract_order_and_retains_complete_partitions():
    """Reordering orchestration or dropping a filter partition breaks this boundary."""
    events: list[str] = []
    candidate_filter = RecordingFilter(events)
    metrics = RecordingMetricsCollector(events)
    analysis = await _service(
        extractor=RecordingExtractor(events),
        provider=RecordingProvider(events),
        aligner=ThreePartitionAligner(events),
        candidate_filter=candidate_filter,
        metrics_collector=metrics,
    ).analyze(_request())

    assert events == [
        "extract:en",
        "extract:ru",
        "embed:en",
        "embed:ru",
        "align",
        "filter",
        "metrics",
    ]
    assert analysis.mode == "semantic_alignment"
    assert analysis.alignment is not None
    assert [item.candidate_id for item in analysis.candidates] == [
        "gap-11111111111111111111"
    ]
    assert [item.candidate_id for item in analysis.excluded] == [
        "gap-22222222222222222222"
    ]
    assert [item.candidate_id for item in analysis.report_only] == [
        "gap-33333333333333333333"
    ]
    assert analysis.warnings == ()
    assert analysis.limited_windows == ()
    assert len(metrics.calls) == 1
    assert analysis.metrics.possible_gaps == 3
    assert analysis.metrics.aligned_units == 3


@async_test
async def test_contexts_use_exact_candidate_units_and_immediate_alignment_neighbors():
    """Using guessed or distant text would send the verifier the wrong neighborhood."""
    candidate_filter = RecordingFilter()
    analysis = await _service(candidate_filter=candidate_filter).analyze(_request())

    first = analysis.contexts["gap-11111111111111111111"]
    assert (
        first.source_text,
        first.target_text,
        first.source_before,
        first.source_after,
        first.target_before,
        first.target_after,
    ) == (
        "Ordinary missing sentence.",
        "",
        "Before.",
        "Middle.",
        "До.",
        "Середина.",
    )
    addition = analysis.contexts["gap-33333333333333333333"]
    assert addition.source_text == ""
    assert addition.target_text == "Добавление."
    assert addition.source_before == "After."
    assert addition.target_before == "После."
    assert addition.source_after == addition.target_after == ""
    assert first.candidate_language == addition.candidate_language == "en"
    assert first.protected_entities == first.protected_contexts == ()
    assert candidate_filter.contexts == analysis.contexts


@async_test
async def test_only_unit_bound_protection_evidence_reaches_its_candidate_context():
    """Chapter-wide hints must not leak onto unrelated candidates."""
    source_payload = _payload("source-doc", "Before.", "Apple.", "Middle.", "SKU AB-2048", "After.")
    units = SemanticUnitExtractor(QaCapabilitySettings()).extract(source_payload, "en")
    evidence = UnitProtectionEvidence(
        side="source",
        unit_id=units[1].unit_id,
        protected_entities=(ProtectedEntityHint("Apple", "brand"),),
        protected_contexts=("foreign_quote",),
        candidate_language="fr",
    )

    analysis = await _service().analyze(
        _request(source_payload=source_payload, protection_evidence=(evidence,))
    )

    protected = analysis.contexts["gap-11111111111111111111"]
    unrelated = analysis.contexts["gap-22222222222222222222"]
    assert protected.protected_entities == evidence.protected_entities
    assert protected.protected_contexts == ("foreign_quote",)
    assert protected.candidate_language == "fr"
    assert unrelated.protected_entities == unrelated.protected_contexts == ()
    assert unrelated.candidate_language == "en"


@async_test
async def test_embedding_requests_are_ordered_normalized_text_batches_with_exact_settings():
    """Embedding raw text, merging languages, or losing model settings breaks cache identity."""
    provider = RecordingProvider()
    analysis = await _service(provider=provider).analyze(_request())

    assert len(provider.requests) == 2
    source_request, target_request = provider.requests
    assert source_request.texts == tuple(unit.normalized_text for unit in analysis.source_units)
    assert target_request.texts == tuple(unit.normalized_text for unit in analysis.target_units)
    assert (source_request.language, target_request.language) == ("en", "ru")
    assert source_request.model == target_request.model == "embed-model"
    assert source_request.dimensions == target_request.dimensions == 3
    assert source_request.task_type == target_request.task_type == "semantic-similarity"


class CountingUpstream(RecordingProvider):
    name = "counting"


@async_test
async def test_real_cached_provider_makes_no_additional_upstream_calls_on_repeat(tmp_path):
    """Calling the upstream twice for unchanged source/target defeats content caching."""
    upstream = CountingUpstream()
    cached = CachedEmbeddingProvider(
        upstream,
        EmbeddingCache(tmp_path / "cache"),
        preprocessing_identity="coverage-test-v1",
    )
    service = _service(provider=cached)
    request = _request()

    first = await service.analyze(request)
    first_call_count = len(upstream.requests)
    second = await service.analyze(request)

    assert first_call_count == 2
    assert len(upstream.requests) == first_call_count
    assert [item.language for item in upstream.requests] == ["en", "ru"]
    assert first == second


class FailOnCallProvider(RecordingProvider):
    def __init__(self, call_number: int, error: BaseException) -> None:
        super().__init__()
        self.call_number = call_number
        self.error = error
        self.calls = 0

    async def embed(self, request):
        self.calls += 1
        if self.calls == self.call_number:
            self.requests.append(request)
            raise self.error
        return await super().embed(request)


@pytest.mark.parametrize("call_number", [1, 2], ids=["source", "target"])
@async_test
async def test_provider_outage_on_either_batch_enters_honest_limited_mode(call_number):
    """A partial source batch must never become a fake alignment."""
    metrics = RecordingMetricsCollector()
    provider = FailOnCallProvider(
        call_number,
        EmbeddingUnavailableError(
            (EmbeddingAttempt("recording", "RuntimeError", "secret detail"),)
        ),
    )

    analysis = await _service(provider=provider, metrics_collector=metrics).analyze(_request())

    assert analysis.mode == "statistics_llm_only"
    assert analysis.alignment is None
    assert analysis.candidates == analysis.excluded == analysis.report_only == ()
    assert analysis.contexts == {}
    assert analysis.warnings == ("embeddings_unavailable",)
    assert len(analysis.source_units) == 5
    assert len(analysis.target_units) == 4
    assert len(analysis.limited_windows) == 9
    assert all(not hasattr(window, "similarity") for window in analysis.limited_windows)
    assert len(metrics.calls) == 1
    assert metrics.calls[0].mode == "statistics_llm_only"
    assert analysis.metrics.possible_gaps == 0
    assert "secret" not in repr(analysis)


@pytest.mark.parametrize(
    ("error", "warning"),
    [
        (EmbeddingTransportError("recording"), "embeddings_unavailable"),
        (EmbeddingHttpError(429, "recording", True), "embeddings_unavailable"),
        (EmbeddingResponseError("recording"), "invalid_embedding_response"),
        (EmbeddingContractError("secret malformed vector"), "invalid_embedding_response"),
    ],
)
@async_test
async def test_each_typed_adapter_error_maps_to_one_sanitized_warning(error, warning):
    """Raw adapter messages or secrets must not escape through coverage results."""
    analysis = await _service(provider=FailOnCallProvider(1, error)).analyze(_request())

    assert analysis.warnings == (warning,)
    assert str(error) not in repr(analysis)


@pytest.mark.parametrize(
    "error",
    [asyncio.CancelledError(), KeyboardInterrupt(), SystemExit()],
    ids=["cancelled", "keyboard-interrupt", "system-exit"],
)
@async_test
async def test_cancellation_and_system_exceptions_propagate(error):
    """Treating cancellation as provider downtime would make the QA task unresponsive."""
    with pytest.raises(type(error)):
        await _service(provider=FailOnCallProvider(1, error)).analyze(_request())


@async_test
async def test_unrelated_programming_error_propagates():
    """Catching arbitrary RuntimeError would conceal service defects as limited mode."""
    with pytest.raises(RuntimeError, match="programming defect"):
        await _service(
            provider=FailOnCallProvider(1, RuntimeError("programming defect"))
        ).analyze(_request())


class CapacityAligner:
    def align(self, source, target):
        raise AlignmentCapacityError(51, 50)


@async_test
async def test_alignment_capacity_enters_limited_mode_without_fake_gap_or_score():
    """Capacity fallback must not pretend statistical windows were semantically aligned."""
    metrics = RecordingMetricsCollector()
    analysis = await _service(
        aligner=CapacityAligner(), metrics_collector=metrics
    ).analyze(_request())

    assert analysis.mode == "statistics_llm_only"
    assert analysis.alignment is None
    assert analysis.warnings == ("alignment_capacity_exceeded",)
    assert analysis.candidates == analysis.excluded == analysis.report_only == ()
    assert len(analysis.limited_windows) == 9
    assert len(metrics.calls) == 1


@async_test
async def test_empty_valid_units_skip_embedding_and_return_explicit_limited_warning():
    """Sending an empty EmbeddingRequest would turn valid empty chapters into contract errors."""
    provider = RecordingProvider()
    metrics = RecordingMetricsCollector()
    analysis = await _service(provider=provider, metrics_collector=metrics).analyze(
        _request(
            source_payload=_payload("source-empty", "   "),
            target_payload=_payload("target-empty", "   "),
        )
    )

    assert provider.requests == []
    assert analysis.source_units == analysis.target_units == ()
    assert analysis.mode == "statistics_llm_only"
    assert analysis.warnings == ("empty_semantic_units",)
    assert analysis.limited_windows == ()
    assert len(metrics.calls) == 1


@async_test
async def test_malformed_payload_extraction_error_remains_visible():
    """Invalid EPUB structure is caller data failure, not an embedding outage."""
    request = _request(source_payload={"document_id": "broken", "blocks": []})
    object.__setattr__(request, "source_payload", {"document_id": "broken", "blocks": "bad"})

    with pytest.raises(SemanticUnitExtractionError, match="blocks"):
        await _service().analyze(request)


@async_test
async def test_metrics_collector_exception_propagates_after_one_call():
    """Silently replacing a collector failure would corrupt persisted chapter statistics."""
    class BrokenMetrics:
        def __init__(self):
            self.calls = 0

        def collect(self, inputs):
            self.calls += 1
            raise RuntimeError("metrics bug")

    collector = BrokenMetrics()
    with pytest.raises(RuntimeError, match="metrics bug"):
        await _service(metrics_collector=collector).analyze(_request())
    assert collector.calls == 1


@async_test
async def test_request_snapshot_and_all_dependencies_leave_caller_payloads_unchanged():
    """Holding caller lists or passing them through would allow EPUB mutation leaks."""
    source = _payload("source-doc", "Before.", "Ordinary missing sentence.", "Middle.", "SKU AB-2048", "After.")
    target = _payload("target-doc", "До.", "Середина.", "После.", "Добавление.")
    source_before = deepcopy(source)
    target_before = deepcopy(target)
    request = _request(source_payload=source, target_payload=target)
    source["blocks"][0]["inlines"][0]["text"] = "MUTATED."
    target["blocks"].append({"bad": "data"})

    analysis = await _service().analyze(request)

    assert analysis.source_units[0].text == "Before."
    assert analysis.target_units[-1].text == "Добавление."
    assert request.source_payload["blocks"][0]["inlines"][0]["text"] == "Before."
    assert source_before["blocks"][0]["inlines"][0]["text"] == "Before."
    assert target_before["blocks"][-1]["id"] == "b-target-doc-3"


@async_test
async def test_analysis_performs_no_epub_journal_or_filesystem_write(monkeypatch):
    """A coverage-only analysis must remain read-only even when gaps are repairable."""
    from gemini_translator.qa.journal import QaJournal

    def forbidden(*args, **kwargs):
        raise AssertionError("write attempted")

    monkeypatch.setattr(QaJournal, "append", forbidden)
    monkeypatch.setattr(QaJournal, "upsert_metrics", forbidden)
    monkeypatch.setattr(QaJournal, "save", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(os, "replace", forbidden)

    analysis = await _service().analyze(_request())

    assert analysis.mode == "semantic_alignment"


def test_import_boundary_stays_free_of_gui_translation_and_persistence_modules():
    """Importing coverage must not pull application workers or mutation services into QA."""
    script = (
        "import sys; import gemini_translator.qa.coverage_service; "
        "blocked=('PyQt5','PyQt6','gemini_translator.core.translation_engine',"
        "'gemini_translator.core.worker','gemini_translator.utils.project_manager',"
        "'gemini_translator.qa.journal'); "
        "print([name for name in sys.modules if name.startswith(blocked)])"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[2],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "[]"


def test_request_is_strict_frozen_and_defensively_freezes_json_payloads():
    """Mutable containers or bool dimensions would make cache requests unstable."""
    request = _request()

    assert isinstance(request.source_payload["blocks"], tuple)
    with pytest.raises(TypeError):
        request.source_payload["blocks"][0]["id"] = "changed"
    with pytest.raises(FrozenInstanceError):
        request.chapter_id = "changed"
    with pytest.raises((CoverageValidationError, TypeError)):
        replace(request, embedding_dimensions=True)
    with pytest.raises((CoverageValidationError, TypeError)):
        replace(request, glossary=list(request.glossary))
    with pytest.raises((CoverageValidationError, TypeError)):
        replace(request, protection_evidence=list(request.protection_evidence))
    with pytest.raises(TypeError):
        CoverageRequest(**{**_request_kwargs(), "api_key": "secret"})


def _request_kwargs() -> dict:
    request = _request()
    return {
        "chapter_id": request.chapter_id,
        "source_payload": request.source_payload,
        "target_payload": request.target_payload,
        "source_language": request.source_language,
        "target_language": request.target_language,
        "embedding_model": request.embedding_model,
        "embedding_dimensions": request.embedding_dimensions,
        "embedding_task": request.embedding_task,
        "glossary": request.glossary,
        "protection_evidence": request.protection_evidence,
    }


@pytest.mark.parametrize(
    "bad_payload",
    [
        {"document_id": "doc", "blocks": object()},
        {"document_id": "doc", "blocks": [{1: "non-string key"}]},
        {"document_id": "doc", "blocks": [{"value": float("nan")}]},
        ["not", "a", "mapping"],
    ],
)
def test_request_rejects_non_json_or_extraction_incompatible_payload_snapshots(bad_payload):
    """Opaque or non-finite data cannot be copied safely into a stable request."""
    with pytest.raises(CoverageValidationError):
        _request(source_payload=bad_payload)


def test_unit_protection_evidence_is_strict_and_has_no_mutable_or_implicit_fields():
    """Untyped chapter-wide protection would let callers suppress unrelated gaps."""
    with pytest.raises(CoverageValidationError):
        UnitProtectionEvidence("source", "unit", protected_entities=[])
    with pytest.raises(CoverageValidationError):
        UnitProtectionEvidence("source", "unit", protected_contexts=["quote"])
    with pytest.raises(CoverageValidationError):
        UnitProtectionEvidence("source", "unit", candidate_language=True)
    with pytest.raises(TypeError):
        UnitProtectionEvidence("source", "unit", protected_model=True)


@async_test
async def test_unknown_or_duplicate_unit_protection_evidence_is_rejected():
    """Evidence not tied to one extracted stable unit cannot be used safely."""
    unknown = UnitProtectionEvidence("source", "u-does-not-exist")
    with pytest.raises(CoverageValidationError, match="unit"):
        await _service().analyze(_request(protection_evidence=(unknown,)))
    with pytest.raises(CoverageValidationError, match="duplicate"):
        _request(protection_evidence=(unknown, unknown))


@async_test
async def test_analysis_contract_is_frozen_exact_and_rejects_invalid_modes_or_partitions():
    """Loosening the result schema could blur semantic and limited-mode guarantees."""
    analysis = await _service().analyze(_request())

    with pytest.raises(FrozenInstanceError):
        analysis.mode = "statistics_llm_only"
    with pytest.raises(CoverageValidationError):
        replace(analysis, mode="other")
    target_gap = analysis.alignment.gaps[-1]
    with pytest.raises(CoverageValidationError):
        replace(analysis, candidates=(target_gap,))
    with pytest.raises(CoverageValidationError):
        replace(analysis, warnings=["embeddings_unavailable"])
    with pytest.raises(CoverageValidationError):
        replace(analysis, metrics="not metrics")


def test_service_constructor_requires_typed_dependency_shapes():
    """Late AttributeError would hide configuration mistakes inside an async run."""
    with pytest.raises(CoverageValidationError):
        _service(extractor=object())
    with pytest.raises(CoverageValidationError):
        _service(provider=object())
    with pytest.raises(CoverageValidationError):
        _service(aligner=object())
    with pytest.raises(CoverageValidationError):
        _service(candidate_filter=object())
    with pytest.raises(CoverageValidationError):
        _service(metrics_collector=object())


def test_default_metrics_rejects_non_chapter_metrics_dependency_result():
    """The analysis contract cannot carry an untyped metrics dictionary."""
    request = _request()
    with pytest.raises(QaModelValidationError):
        ChapterMetrics(
            chapter_id=request.chapter_id,
            source_language=request.source_language,
            target_language=request.target_language,
            source_chars=True,
        )
