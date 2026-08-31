"""Read-only orchestration for chapter-local semantic translation coverage.

This module deliberately depends only on immutable QA contracts.  It extracts
defensive payload copies, requests embeddings, aligns and filters gaps, and
returns analysis data.  It never owns EPUB mutation, repair, journaling, UI, or
persistence.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
import re
from types import MappingProxyType
from typing import Protocol

from .alignment import AlignmentCapacityError
from .embeddings.base import (
    EmbeddingBatch,
    EmbeddingContractError,
    EmbeddingProvider,
    EmbeddingRequest,
    validate_and_normalize_batch,
)
from .embeddings.factory import (
    EmbeddingHttpError,
    EmbeddingResponseError,
    EmbeddingTransportError,
    EmbeddingUnavailableError,
)
from .foreign_text_filter import filter_gap_candidates
from .models import (
    AlignmentResult,
    CandidateContext,
    CandidateFilterResult,
    ChapterMetrics,
    EmbeddedUnits,
    FilteredCandidate,
    GapCandidate,
    GlossaryRule,
    ProtectedEntityHint,
    RiskLevel,
    SemanticUnit,
)


SEMANTIC_ALIGNMENT_MODE = "semantic_alignment"
STATISTICS_LLM_ONLY_MODE = "statistics_llm_only"

EMBEDDINGS_UNAVAILABLE_WARNING = "embeddings_unavailable"
INVALID_EMBEDDING_RESPONSE_WARNING = "invalid_embedding_response"
ALIGNMENT_CAPACITY_EXCEEDED_WARNING = "alignment_capacity_exceeded"
EMPTY_SEMANTIC_UNITS_WARNING = "empty_semantic_units"

_MODES = frozenset({SEMANTIC_ALIGNMENT_MODE, STATISTICS_LLM_ONLY_MODE})
_WARNING_CODES = frozenset(
    {
        EMBEDDINGS_UNAVAILABLE_WARNING,
        INVALID_EMBEDDING_RESPONSE_WARNING,
        ALIGNMENT_CAPACITY_EXCEEDED_WARNING,
        EMPTY_SEMANTIC_UNITS_WARNING,
    }
)
_PROTECTED_CONTEXTS = frozenset(
    {"dialogue", "foreign_dialogue", "foreign_quote", "quote", "sign"}
)
_SAFE_PROVIDER_ID = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


class CoverageValidationError(ValueError):
    """Raised when a coverage request, dependency, or result breaks its contract."""


def _nonempty_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CoverageValidationError(f"{field_name} must be a nonempty string")
    return value.strip()


def _optional_nonempty_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _nonempty_string(value, field_name)


def _freeze_json(value: object, path: str = "payload") -> object:
    """Copy JSON-like data recursively without relying on ``deepcopy`` hooks."""
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CoverageValidationError(f"{path} keys must be strings")
            frozen[key] = _freeze_json(item, f"{path}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_json(item, f"{path}[{index}]")
            for index, item in enumerate(value)
        )
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CoverageValidationError(f"{path} numbers must be finite")
        return value
    raise CoverageValidationError(f"{path} contains unsupported mutable or non-JSON data")


def _freeze_payload(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise CoverageValidationError(f"{field_name} must be a mapping")
    frozen = _freeze_json(value, field_name)
    assert isinstance(frozen, Mapping)
    document_id = frozen.get("document_id")
    blocks = frozen.get("blocks")
    if not isinstance(document_id, str) or not document_id.strip():
        raise CoverageValidationError(f"{field_name} document_id must be a nonempty string")
    if not isinstance(blocks, tuple):
        raise CoverageValidationError(f"{field_name} blocks must be an array")
    return frozen


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class UnitProtectionEvidence:
    """Explicit protection evidence scoped to one stable unit on one text side."""

    side: str
    unit_id: str
    protected_entities: tuple[ProtectedEntityHint, ...] = ()
    protected_contexts: tuple[str, ...] = ()
    candidate_language: str | None = None

    def __post_init__(self) -> None:
        if self.side not in {"source", "target"}:
            raise CoverageValidationError("protection evidence side must be source or target")
        _nonempty_string(self.unit_id, "protection evidence unit_id")
        if not isinstance(self.protected_entities, tuple):
            raise CoverageValidationError("protected_entities must be a tuple")
        if not all(
            isinstance(entity, ProtectedEntityHint)
            for entity in self.protected_entities
        ):
            raise CoverageValidationError(
                "protected_entities must contain ProtectedEntityHint values"
            )
        if len(set(self.protected_entities)) != len(self.protected_entities):
            raise CoverageValidationError("protected_entities must be unique")
        if not isinstance(self.protected_contexts, tuple):
            raise CoverageValidationError("protected_contexts must be a tuple")
        for context in self.protected_contexts:
            if not isinstance(context, str) or context not in _PROTECTED_CONTEXTS:
                raise CoverageValidationError("unsupported protected context")
        if len(set(self.protected_contexts)) != len(self.protected_contexts):
            raise CoverageValidationError("protected_contexts must be unique")
        object.__setattr__(
            self,
            "candidate_language",
            _optional_nonempty_string(self.candidate_language, "candidate_language"),
        )


@dataclass(frozen=True, slots=True)
class CoverageRequest:
    """Immutable, secret-free input snapshot for one source/translation chapter."""

    chapter_id: str
    source_payload: Mapping[str, object]
    target_payload: Mapping[str, object]
    source_language: str
    target_language: str
    embedding_model: str
    embedding_dimensions: int | None = None
    embedding_task: str = "semantic-similarity"
    glossary: tuple[GlossaryRule, ...] = ()
    protection_evidence: tuple[UnitProtectionEvidence, ...] = ()

    # This is a value snapshot containing mappings, not a stable dictionary key.
    __hash__ = None

    def __post_init__(self) -> None:
        for field_name in (
            "chapter_id",
            "source_language",
            "target_language",
            "embedding_model",
            "embedding_task",
        ):
            object.__setattr__(
                self,
                field_name,
                _nonempty_string(getattr(self, field_name), field_name),
            )
        if self.embedding_dimensions is not None and (
            isinstance(self.embedding_dimensions, bool)
            or not isinstance(self.embedding_dimensions, int)
            or self.embedding_dimensions <= 0
        ):
            raise CoverageValidationError(
                "embedding_dimensions must be a positive integer or None"
            )
        if not isinstance(self.glossary, tuple) or not all(
            isinstance(rule, GlossaryRule) for rule in self.glossary
        ):
            raise CoverageValidationError("glossary must be a tuple of GlossaryRule values")
        if not isinstance(self.protection_evidence, tuple) or not all(
            isinstance(item, UnitProtectionEvidence)
            for item in self.protection_evidence
        ):
            raise CoverageValidationError(
                "protection_evidence must be a tuple of UnitProtectionEvidence values"
            )
        identities = tuple(
            (item.side, item.unit_id) for item in self.protection_evidence
        )
        if len(set(identities)) != len(identities):
            raise CoverageValidationError("duplicate unit protection evidence")
        object.__setattr__(
            self, "source_payload", _freeze_payload(self.source_payload, "source_payload")
        )
        object.__setattr__(
            self, "target_payload", _freeze_payload(self.target_payload, "target_payload")
        )


@dataclass(frozen=True, slots=True)
class LimitedCoverageWindow:
    """One unaligned local unit window for later limited-mode LLM inspection."""

    side: str
    unit_id: str
    text: str
    before: str
    after: str
    language: str

    def __post_init__(self) -> None:
        if self.side not in {"source", "target"}:
            raise CoverageValidationError("limited window side must be source or target")
        for field_name in ("unit_id", "text", "language"):
            _nonempty_string(getattr(self, field_name), field_name)
        if not isinstance(self.before, str) or not isinstance(self.after, str):
            raise CoverageValidationError("limited window neighbors must be strings")


@dataclass(frozen=True, slots=True)
class CoverageMetricsInput:
    """Final immutable state passed exactly once to a cheap metrics collector."""

    request: CoverageRequest
    mode: str
    source_units: tuple[SemanticUnit, ...]
    target_units: tuple[SemanticUnit, ...]
    alignment: AlignmentResult | None
    candidates: tuple[GapCandidate, ...]
    excluded: tuple[FilteredCandidate, ...]
    report_only: tuple[FilteredCandidate, ...]
    contexts: Mapping[str, CandidateContext]
    limited_windows: tuple[LimitedCoverageWindow, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CoverageAnalysis:
    """Complete read-only result with exact normal/limited mode guarantees."""

    mode: str
    source_units: tuple[SemanticUnit, ...]
    target_units: tuple[SemanticUnit, ...]
    alignment: AlignmentResult | None
    candidates: tuple[GapCandidate, ...]
    excluded: tuple[FilteredCandidate, ...]
    report_only: tuple[FilteredCandidate, ...]
    contexts: Mapping[str, CandidateContext]
    limited_windows: tuple[LimitedCoverageWindow, ...]
    warnings: tuple[str, ...]
    metrics: ChapterMetrics

    # Analysis owns mapping snapshots and is deliberately not hashable.
    __hash__ = None

    def __post_init__(self) -> None:
        if self.mode not in _MODES:
            raise CoverageValidationError("unsupported coverage analysis mode")
        _semantic_units_tuple(self.source_units, "source_units")
        _semantic_units_tuple(self.target_units, "target_units")
        if self.alignment is not None and not isinstance(self.alignment, AlignmentResult):
            raise CoverageValidationError("alignment must be an AlignmentResult or None")
        if not isinstance(self.candidates, tuple) or not all(
            isinstance(candidate, GapCandidate) and candidate.side == "source"
            for candidate in self.candidates
        ):
            raise CoverageValidationError("candidates must be verifier-bound source gaps")
        _filtered_partition(self.excluded, "excluded", "exclude")
        _filtered_partition(self.report_only, "report_only", "report_only")
        if not isinstance(self.contexts, Mapping):
            raise CoverageValidationError("contexts must be a mapping")
        contexts = dict(self.contexts)
        if any(
            not isinstance(key, str)
            or not isinstance(value, CandidateContext)
            or key != value.candidate_id
            for key, value in contexts.items()
        ):
            raise CoverageValidationError("contexts must map candidate IDs to CandidateContext")
        object.__setattr__(self, "contexts", MappingProxyType(contexts))
        if not isinstance(self.limited_windows, tuple) or not all(
            isinstance(window, LimitedCoverageWindow)
            for window in self.limited_windows
        ):
            raise CoverageValidationError("limited_windows must be a typed tuple")
        if not isinstance(self.warnings, tuple):
            raise CoverageValidationError("warnings must be a tuple")
        if (
            any(warning not in _WARNING_CODES for warning in self.warnings)
            or len(set(self.warnings)) != len(self.warnings)
        ):
            raise CoverageValidationError("warnings must contain unique stable codes")
        if not isinstance(self.metrics, ChapterMetrics):
            raise CoverageValidationError("metrics must be ChapterMetrics")

        if self.mode == SEMANTIC_ALIGNMENT_MODE:
            if self.alignment is None or self.limited_windows or self.warnings:
                raise CoverageValidationError(
                    "semantic alignment mode requires alignment without limited data"
                )
            partition_ids = (
                tuple(candidate.candidate_id for candidate in self.candidates)
                + tuple(item.candidate_id for item in self.excluded)
                + tuple(item.candidate_id for item in self.report_only)
            )
            gap_ids = tuple(candidate.candidate_id for candidate in self.alignment.gaps)
            if len(partition_ids) != len(gap_ids) or set(partition_ids) != set(gap_ids):
                raise CoverageValidationError("filter partitions must cover every alignment gap")
            _validate_analysis_partitions(
                self.alignment,
                self.candidates,
                self.excluded,
                self.report_only,
            )
            if set(contexts) != set(gap_ids):
                raise CoverageValidationError("contexts must cover every alignment gap")
        elif (
            self.alignment is not None
            or self.candidates
            or self.excluded
            or self.report_only
            or contexts
            or len(self.warnings) != 1
        ):
            raise CoverageValidationError(
                "limited mode cannot contain semantic alignment claims"
            )


def _semantic_units_tuple(value: object, field_name: str) -> tuple[SemanticUnit, ...]:
    if not isinstance(value, tuple) or not all(
        isinstance(unit, SemanticUnit) for unit in value
    ):
        raise CoverageValidationError(f"{field_name} must be a tuple of SemanticUnit")
    return value


def _validate_extracted_units(
    value: object,
    field_name: str,
    expected_document_id: str,
) -> tuple[SemanticUnit, ...]:
    units = _semantic_units_tuple(value, field_name)
    unit_ids = tuple(unit.unit_id for unit in units)
    document_ids = tuple(unit.document_id for unit in units)
    ordinals = tuple(unit.ordinal for unit in units)
    if len(set(unit_ids)) != len(unit_ids):
        raise CoverageValidationError(f"{field_name} unit IDs must be unique")
    if any(document_id != expected_document_id for document_id in document_ids):
        raise CoverageValidationError(
            f"{field_name} must contain only the requested document"
        )
    if ordinals != tuple(range(len(units))):
        raise CoverageValidationError(
            f"{field_name} ordinals must be exactly ordered from zero"
        )
    return units


def _validate_alignment_units(
    alignment: AlignmentResult,
    source_units: tuple[SemanticUnit, ...],
    target_units: tuple[SemanticUnit, ...],
) -> None:
    aligned_source_ids = tuple(
        unit_id for span in alignment.spans for unit_id in span.source_unit_ids
    )
    aligned_target_ids = tuple(
        unit_id for span in alignment.spans for unit_id in span.target_unit_ids
    )
    expected_source_ids = tuple(unit.unit_id for unit in source_units)
    expected_target_ids = tuple(unit.unit_id for unit in target_units)
    if aligned_source_ids != expected_source_ids:
        raise CoverageValidationError(
            "alignment source unit IDs must exactly cover extracted source units in order"
        )
    if aligned_target_ids != expected_target_ids:
        raise CoverageValidationError(
            "alignment target unit IDs must exactly cover extracted target units in order"
        )


def _validate_filter_result(
    alignment: AlignmentResult, filtered: CandidateFilterResult
) -> None:
    original_by_id = {
        candidate.candidate_id: candidate for candidate in alignment.gaps
    }
    partitioned = filtered.accepted + filtered.excluded + filtered.report_only
    partition_ids = tuple(item.candidate_id for item in partitioned)
    if (
        len(partition_ids) != len(original_by_id)
        or len(set(partition_ids)) != len(partition_ids)
        or set(partition_ids) != set(original_by_id)
    ):
        raise CoverageValidationError(
            "filter partitions must cover every alignment gap exactly once"
        )
    if any(
        item.candidate != original_by_id[item.candidate_id]
        for item in partitioned
    ):
        raise CoverageValidationError(
            "filter partitions must preserve each original alignment gap value"
        )


def _validate_analysis_partitions(
    alignment: AlignmentResult,
    candidates: tuple[GapCandidate, ...],
    excluded: tuple[FilteredCandidate, ...],
    report_only: tuple[FilteredCandidate, ...],
) -> None:
    original_by_id = {
        candidate.candidate_id: candidate for candidate in alignment.gaps
    }
    values = tuple((candidate.candidate_id, candidate) for candidate in candidates)
    values += tuple((item.candidate_id, item.candidate) for item in excluded)
    values += tuple((item.candidate_id, item.candidate) for item in report_only)
    if any(original_by_id.get(candidate_id) != candidate for candidate_id, candidate in values):
        raise CoverageValidationError(
            "filter partitions must preserve each original alignment gap value"
        )


def _filtered_partition(
    value: object, field_name: str, action: str
) -> tuple[FilteredCandidate, ...]:
    if not isinstance(value, tuple) or not all(
        isinstance(item, FilteredCandidate) and item.decision.action == action
        for item in value
    ):
        raise CoverageValidationError(f"{field_name} has an invalid filter partition")
    return value


class SemanticUnitExtractorProtocol(Protocol):
    def extract(self, payload: dict, language: str) -> tuple[SemanticUnit, ...]: ...


class SemanticAlignerProtocol(Protocol):
    def align(self, source: EmbeddedUnits, target: EmbeddedUnits) -> AlignmentResult: ...


class CandidateFilterProtocol(Protocol):
    def filter(
        self,
        result: AlignmentResult,
        contexts: Mapping[str, CandidateContext],
        glossary: tuple[GlossaryRule, ...],
    ) -> CandidateFilterResult: ...


class CoverageMetricsCollectorProtocol(Protocol):
    def collect(self, inputs: CoverageMetricsInput) -> ChapterMetrics: ...


class DefaultCandidateFilter:
    """Adapter exposing the existing pure filter through the service DI contract."""

    def filter(
        self,
        result: AlignmentResult,
        contexts: Mapping[str, CandidateContext],
        glossary: tuple[GlossaryRule, ...],
    ) -> CandidateFilterResult:
        return filter_gap_candidates(result, contexts, glossary)


# The share of a chapter's visible text that has to be ordinary prose before
# the chapter counts as narrative.  A title page or a table of contents is
# mostly headings and links, and its volume ratio means nothing.
_NARRATIVE_TEXT_SHARE = 0.6
_NARRATIVE_KINDS = frozenset({"paragraph", "blockquote", "list_item", "text_flow"})


def _content_kind(units: Sequence[SemanticUnit]) -> str:
    """Classify the chapter, not its first block.

    This used to read the kind of unit zero, which is a block role such as
    "paragraph" — never the "narrative" the book statistics ask for, so no
    chapter ever entered the book baseline and the whole relative-ratio norm
    stayed empty.
    """
    if not units:
        return "narrative"
    total = sum(len(unit.text) for unit in units)
    if total <= 0:
        return units[0].kind
    prose = sum(
        len(unit.text) for unit in units if unit.kind in _NARRATIVE_KINDS
    )
    if prose / total >= _NARRATIVE_TEXT_SHARE:
        return "narrative"
    return units[0].kind


class DefaultCoverageMetricsCollector:
    """Build deterministic in-memory chapter metrics without persistence or pandas."""

    def collect(self, inputs: CoverageMetricsInput) -> ChapterMetrics:
        if not isinstance(inputs, CoverageMetricsInput):
            raise CoverageValidationError("metrics inputs must be CoverageMetricsInput")
        aligned_source_ids: set[str] = set()
        if inputs.alignment is not None:
            for span in inputs.alignment.spans:
                if span.source_unit_ids and span.target_unit_ids:
                    aligned_source_ids.update(span.source_unit_ids)
        gaps = len(inputs.alignment.gaps) if inputs.alignment is not None else 0
        return ChapterMetrics(
            chapter_id=inputs.request.chapter_id,
            source_language=inputs.request.source_language,
            target_language=inputs.request.target_language,
            content_kind=_content_kind(inputs.source_units),
            source_chars=sum(len(unit.text) for unit in inputs.source_units),
            translated_chars=sum(len(unit.text) for unit in inputs.target_units),
            source_units=len(inputs.source_units),
            aligned_units=len(aligned_source_ids),
            possible_gaps=gaps,
            allowed_foreign_fragments=len(inputs.excluded),
            risk_level=RiskLevel.MEDIUM if gaps or inputs.warnings else RiskLevel.LOW,
        )


class SemanticCoverageService:
    """Coordinate semantic coverage dependencies without mutating project state."""

    def __init__(
        self,
        *,
        extractor: SemanticUnitExtractorProtocol,
        provider: EmbeddingProvider,
        aligner: SemanticAlignerProtocol,
        candidate_filter: CandidateFilterProtocol,
        metrics_collector: CoverageMetricsCollectorProtocol,
    ) -> None:
        dependencies = (
            (extractor, "extract", "extractor"),
            (provider, "embed", "provider"),
            (aligner, "align", "aligner"),
            (candidate_filter, "filter", "candidate_filter"),
            (metrics_collector, "collect", "metrics_collector"),
        )
        for dependency, method_name, field_name in dependencies:
            if not callable(getattr(dependency, method_name, None)):
                raise CoverageValidationError(
                    f"{field_name} must provide callable {method_name}"
                )
        self._extractor = extractor
        self._provider = provider
        self._aligner = aligner
        self._candidate_filter = candidate_filter
        self._metrics_collector = metrics_collector

    async def analyze(self, request: CoverageRequest) -> CoverageAnalysis:
        if not isinstance(request, CoverageRequest):
            raise CoverageValidationError("request must be CoverageRequest")

        source_payload = _thaw_json(request.source_payload)
        target_payload = _thaw_json(request.target_payload)
        assert isinstance(source_payload, dict) and isinstance(target_payload, dict)
        source_document_id = request.source_payload["document_id"]
        target_document_id = request.target_payload["document_id"]
        assert isinstance(source_document_id, str) and isinstance(target_document_id, str)
        source_units = _validate_extracted_units(
            self._extractor.extract(source_payload, request.source_language),
            "source_units",
            source_document_id,
        )
        target_units = _validate_extracted_units(
            self._extractor.extract(target_payload, request.target_language),
            "target_units",
            target_document_id,
        )
        evidence = self._validate_evidence(request, source_units, target_units)

        if not source_units or not target_units:
            return self._limited(
                request,
                source_units,
                target_units,
                EMPTY_SEMANTIC_UNITS_WARNING,
            )

        source_request = self._embedding_request(
            source_units, request.source_language, request
        )
        target_request = self._embedding_request(
            target_units, request.target_language, request
        )
        try:
            source_batch = await self._provider.embed(source_request)
            source_batch = self._validate_embedding_batch(source_batch, source_request)
            target_batch = await self._provider.embed(target_request)
            target_batch = self._validate_embedding_batch(target_batch, target_request)
            if source_batch.provider != target_batch.provider:
                raise EmbeddingContractError(
                    "source and target embedding providers must match"
                )
            if source_batch.dimensions != target_batch.dimensions:
                raise EmbeddingContractError(
                    "source and target embedding dimensions must match"
                )
        except (EmbeddingResponseError, EmbeddingContractError):
            return self._limited(
                request,
                source_units,
                target_units,
                INVALID_EMBEDDING_RESPONSE_WARNING,
            )
        except (
            EmbeddingUnavailableError,
            EmbeddingTransportError,
            EmbeddingHttpError,
        ):
            return self._limited(
                request,
                source_units,
                target_units,
                EMBEDDINGS_UNAVAILABLE_WARNING,
            )

        source_embedded = EmbeddedUnits(
            source_units[0].document_id, source_units, source_batch.vectors
        )
        target_embedded = EmbeddedUnits(
            target_units[0].document_id, target_units, target_batch.vectors
        )
        try:
            alignment = self._aligner.align(source_embedded, target_embedded)
        except AlignmentCapacityError:
            return self._limited(
                request,
                source_units,
                target_units,
                ALIGNMENT_CAPACITY_EXCEEDED_WARNING,
            )
        if not isinstance(alignment, AlignmentResult):
            raise CoverageValidationError("aligner must return AlignmentResult")
        _validate_alignment_units(alignment, source_units, target_units)

        contexts = self._candidate_contexts(
            request, source_units, target_units, alignment, evidence
        )
        filtered = self._candidate_filter.filter(
            alignment, contexts, request.glossary
        )
        if not isinstance(filtered, CandidateFilterResult):
            raise CoverageValidationError(
                "candidate_filter must return CandidateFilterResult"
            )
        _validate_filter_result(alignment, filtered)
        candidates = tuple(item.candidate for item in filtered.accepted)
        if any(candidate.side != "source" for candidate in candidates):
            raise CoverageValidationError(
                "verifier candidates must contain source-side gaps only"
            )
        return self._finish(
            request=request,
            mode=SEMANTIC_ALIGNMENT_MODE,
            source_units=source_units,
            target_units=target_units,
            alignment=alignment,
            candidates=candidates,
            excluded=filtered.excluded,
            report_only=filtered.report_only,
            contexts=contexts,
            limited_windows=(),
            warnings=(),
        )

    @staticmethod
    def _embedding_request(
        units: tuple[SemanticUnit, ...], language: str, request: CoverageRequest
    ) -> EmbeddingRequest:
        return EmbeddingRequest(
            texts=tuple(unit.normalized_text for unit in units),
            language=language,
            model=request.embedding_model,
            dimensions=request.embedding_dimensions,
            task_type=request.embedding_task,
        )

    @staticmethod
    def _validate_embedding_batch(
        batch: EmbeddingBatch, request: EmbeddingRequest
    ) -> EmbeddingBatch:
        checked = validate_and_normalize_batch(batch, len(request.texts))
        if _SAFE_PROVIDER_ID.fullmatch(checked.provider) is None:
            raise EmbeddingContractError(
                "embedding batch provider must be a safe canonical identifier"
            )
        if checked.model != request.model:
            raise EmbeddingContractError("embedding batch model does not match request")
        if request.dimensions is not None and checked.dimensions != request.dimensions:
            raise EmbeddingContractError(
                "embedding batch dimensions do not match request"
            )
        return checked

    @staticmethod
    def _validate_evidence(
        request: CoverageRequest,
        source_units: tuple[SemanticUnit, ...],
        target_units: tuple[SemanticUnit, ...],
    ) -> Mapping[tuple[str, str], UnitProtectionEvidence]:
        known = {
            "source": {unit.unit_id for unit in source_units},
            "target": {unit.unit_id for unit in target_units},
        }
        evidence: dict[tuple[str, str], UnitProtectionEvidence] = {}
        for item in request.protection_evidence:
            if item.unit_id not in known[item.side]:
                raise CoverageValidationError(
                    "protection evidence must reference an extracted stable unit"
                )
            evidence[(item.side, item.unit_id)] = item
        return MappingProxyType(evidence)

    @classmethod
    def _candidate_contexts(
        cls,
        request: CoverageRequest,
        source_units: tuple[SemanticUnit, ...],
        target_units: tuple[SemanticUnit, ...],
        alignment: AlignmentResult,
        evidence: Mapping[tuple[str, str], UnitProtectionEvidence],
    ) -> Mapping[str, CandidateContext]:
        source_by_id = {unit.unit_id: unit for unit in source_units}
        target_by_id = {unit.unit_id: unit for unit in target_units}
        groups = cls._alignment_gap_groups(alignment)
        contexts: dict[str, CandidateContext] = {}
        for candidate, (start, end) in zip(alignment.gaps, groups, strict=True):
            left = alignment.spans[start - 1] if start else None
            right = alignment.spans[end] if end < len(alignment.spans) else None
            relevant_side = candidate.side
            relevant_ids = (
                candidate.source_unit_ids
                if candidate.side == "source"
                else candidate.target_unit_ids
            )
            applicable = tuple(
                evidence[(relevant_side, unit_id)]
                for unit_id in relevant_ids
                if (relevant_side, unit_id) in evidence
            )
            protected_entities = tuple(
                dict.fromkeys(
                    entity
                    for item in applicable
                    for entity in item.protected_entities
                )
            )
            protected_contexts = tuple(
                dict.fromkeys(
                    context
                    for item in applicable
                    for context in item.protected_contexts
                )
            )
            explicit_languages = tuple(
                dict.fromkeys(
                    item.candidate_language
                    for item in applicable
                    if item.candidate_language is not None
                )
            )
            if len(explicit_languages) > 1:
                raise CoverageValidationError(
                    "candidate unit protection evidence has conflicting languages"
                )
            candidate_language = (
                explicit_languages[0]
                if explicit_languages
                else request.source_language
            )
            contexts[candidate.candidate_id] = CandidateContext(
                candidate_id=candidate.candidate_id,
                source_text=cls._unit_text(candidate.source_unit_ids, source_by_id),
                target_text=cls._unit_text(candidate.target_unit_ids, target_by_id),
                source_before=cls._span_text(left, "source", source_by_id),
                source_after=cls._span_text(right, "source", source_by_id),
                target_before=cls._span_text(left, "target", target_by_id),
                target_after=cls._span_text(right, "target", target_by_id),
                source_language=request.source_language,
                target_language=request.target_language,
                candidate_language=candidate_language,
                protected_entities=protected_entities,
                protected_contexts=protected_contexts,
            )
        return MappingProxyType(contexts)

    @staticmethod
    def _alignment_gap_groups(
        alignment: AlignmentResult,
    ) -> tuple[tuple[int, int], ...]:
        groups: list[tuple[int, int]] = []
        index = 0
        while index < len(alignment.spans):
            span = alignment.spans[index]
            if span.source_unit_ids and span.target_unit_ids:
                index += 1
                continue
            shape = (bool(span.source_unit_ids), bool(span.target_unit_ids))
            end = index + 1
            while end < len(alignment.spans):
                next_span = alignment.spans[end]
                if (
                    bool(next_span.source_unit_ids),
                    bool(next_span.target_unit_ids),
                ) != shape:
                    break
                end += 1
            groups.append((index, end))
            index = end
        return tuple(groups)

    @staticmethod
    def _unit_text(
        unit_ids: Sequence[str], units: Mapping[str, SemanticUnit]
    ) -> str:
        try:
            return " ".join(units[unit_id].text for unit_id in unit_ids)
        except KeyError:
            raise CoverageValidationError(
                "alignment references an unknown semantic unit"
            ) from None

    @classmethod
    def _span_text(
        cls,
        span,
        side: str,
        units: Mapping[str, SemanticUnit],
    ) -> str:
        if span is None:
            return ""
        ids = span.source_unit_ids if side == "source" else span.target_unit_ids
        return cls._unit_text(ids, units)

    @staticmethod
    def _limited_windows(
        source_units: tuple[SemanticUnit, ...],
        target_units: tuple[SemanticUnit, ...],
        request: CoverageRequest,
    ) -> tuple[LimitedCoverageWindow, ...]:
        windows: list[LimitedCoverageWindow] = []
        for side, units, language in (
            ("source", source_units, request.source_language),
            ("target", target_units, request.target_language),
        ):
            for index, unit in enumerate(units):
                windows.append(
                    LimitedCoverageWindow(
                        side=side,
                        unit_id=unit.unit_id,
                        text=unit.text,
                        before=units[index - 1].text if index else "",
                        after=units[index + 1].text if index + 1 < len(units) else "",
                        language=language,
                    )
                )
        return tuple(windows)

    def _limited(
        self,
        request: CoverageRequest,
        source_units: tuple[SemanticUnit, ...],
        target_units: tuple[SemanticUnit, ...],
        warning: str,
    ) -> CoverageAnalysis:
        return self._finish(
            request=request,
            mode=STATISTICS_LLM_ONLY_MODE,
            source_units=source_units,
            target_units=target_units,
            alignment=None,
            candidates=(),
            excluded=(),
            report_only=(),
            contexts=MappingProxyType({}),
            limited_windows=self._limited_windows(
                source_units, target_units, request
            ),
            warnings=(warning,),
        )

    def _finish(
        self,
        *,
        request: CoverageRequest,
        mode: str,
        source_units: tuple[SemanticUnit, ...],
        target_units: tuple[SemanticUnit, ...],
        alignment: AlignmentResult | None,
        candidates: tuple[GapCandidate, ...],
        excluded: tuple[FilteredCandidate, ...],
        report_only: tuple[FilteredCandidate, ...],
        contexts: Mapping[str, CandidateContext],
        limited_windows: tuple[LimitedCoverageWindow, ...],
        warnings: tuple[str, ...],
    ) -> CoverageAnalysis:
        inputs = CoverageMetricsInput(
            request=request,
            mode=mode,
            source_units=source_units,
            target_units=target_units,
            alignment=alignment,
            candidates=candidates,
            excluded=excluded,
            report_only=report_only,
            contexts=MappingProxyType(dict(contexts)),
            limited_windows=limited_windows,
            warnings=warnings,
        )
        metrics = self._metrics_collector.collect(inputs)
        if not isinstance(metrics, ChapterMetrics):
            raise CoverageValidationError(
                "metrics_collector must return ChapterMetrics"
            )
        self._validate_metrics(metrics, inputs)
        return CoverageAnalysis(
            mode=mode,
            source_units=source_units,
            target_units=target_units,
            alignment=alignment,
            candidates=candidates,
            excluded=excluded,
            report_only=report_only,
            contexts=contexts,
            limited_windows=limited_windows,
            warnings=warnings,
            metrics=metrics,
        )

    @staticmethod
    def _validate_metrics(
        metrics: ChapterMetrics, inputs: CoverageMetricsInput
    ) -> None:
        aligned_source_ids: set[str] = set()
        if inputs.alignment is not None:
            for span in inputs.alignment.spans:
                if span.source_unit_ids and span.target_unit_ids:
                    aligned_source_ids.update(span.source_unit_ids)
        expected = {
            "chapter_id": inputs.request.chapter_id,
            "source_language": inputs.request.source_language,
            "target_language": inputs.request.target_language,
            "source_chars": sum(len(unit.text) for unit in inputs.source_units),
            "translated_chars": sum(len(unit.text) for unit in inputs.target_units),
            "source_units": len(inputs.source_units),
            "aligned_units": len(aligned_source_ids),
            "possible_gaps": (
                len(inputs.alignment.gaps) if inputs.alignment is not None else 0
            ),
            "allowed_foreign_fragments": len(inputs.excluded),
            "applied_actions": (),
        }
        for field_name, expected_value in expected.items():
            if getattr(metrics, field_name) != expected_value:
                raise CoverageValidationError(
                    f"metrics {field_name} does not match final coverage state"
                )
