"""Immutable, Qt-free data contracts for translation quality assurance."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math
import os
from pathlib import Path
import re
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Mapping

import numpy as np

from .capabilities import QaCapabilityKey

if TYPE_CHECKING:
    from .llm.schemas import OmissionVerdict


class QaModelValidationError(ValueError):
    """Raised when persisted or constructed QA model data is invalid."""


def _require_string(value: object, field_name: str, *, allow_none: bool = False) -> None:
    if value is None and allow_none:
        return
    if not isinstance(value, str):
        raise QaModelValidationError(f"{field_name} must be a string")


def _require_nonempty_string(value: object, field_name: str) -> None:
    _require_string(value, field_name)
    if not value.strip():
        raise QaModelValidationError(f"{field_name} must be a nonempty string")


def _require_integer(value: object, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise QaModelValidationError(f"{field_name} must be an integer")


def _require_finite_number(
    value: object, field_name: str, *, allow_none: bool = False
) -> None:
    if value is None and allow_none:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise QaModelValidationError(f"{field_name} must be a finite number")
    if not math.isfinite(value):
        raise QaModelValidationError(f"{field_name} must be finite")


def _validate_prompt_resources(config: object) -> None:
    """Validate the output budget and versioned prompt location of one QA request."""
    max_output_tokens = getattr(config, "max_output_tokens")
    prompt_version = getattr(config, "prompt_version")
    prompt_path = getattr(config, "prompt_path")
    _require_integer(max_output_tokens, "max_output_tokens")
    if not 1 <= max_output_tokens <= _MAX_QA_OUTPUT_TOKENS:
        raise QaModelValidationError(
            f"max_output_tokens must be between 1 and {_MAX_QA_OUTPUT_TOKENS}"
        )
    _require_nonempty_string(prompt_version, "prompt_version")
    if re.fullmatch(r"[a-z][a-z0-9_]*_v[1-9][0-9]*", prompt_version) is None:
        raise QaModelValidationError("prompt_version must be a stable versioned key")
    if prompt_path is None:
        return
    if not isinstance(prompt_path, (str, os.PathLike)):
        raise QaModelValidationError("prompt_path must be a filesystem path")
    if not str(prompt_path).strip():
        raise QaModelValidationError("prompt_path must not be empty")
    object.__setattr__(config, "prompt_path", Path(prompt_path))


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    FAILED = "failed"


class CandidateKind(StrEnum):
    LENGTH_ANOMALY = "length_anomaly"
    POSSIBLE_GAP = "possible_gap"
    GLOSSARY_CONFLICT = "glossary_conflict"
    UNTRANSLATED_FRAGMENT = "untranslated_fragment"
    LANGUAGE_ISSUE = "language_issue"
    SYNTAX_ISSUE = "syntax_issue"
    SEMANTIC_GAP = "semantic_gap"
    HALLUCINATED_ADDITION = "hallucinated_addition"


class Decision(StrEnum):
    NO_GAP = "no_gap"
    MISSING_CONTENT = "missing_content"
    COVERED = "covered"
    INTENTIONAL_FOREIGN = "intentional_foreign"
    AMBIGUOUS = "ambiguous"
    ENTAILED = "entailed"
    PARAPHRASE = "paraphrase"
    HALLUCINATED_ADDITION = "hallucinated_addition"
    REPAIR_REJECTED = "repair_rejected"
    FIXED = "fixed"
    WARNING = "warning"
    EXCLUDED = "excluded"
    ERROR = "error"
    CANCELLED = "cancelled"


class Action(StrEnum):
    REPORT_ONLY = "report_only"
    REPAIR_APPLIED = "repair_applied"
    REPAIR_REJECTED = "repair_rejected"
    IGNORE = "ignore"
    UNDO = "undo"


class GlossaryPolicy(StrEnum):
    MUST_TRANSLATE = "must_translate"
    KEEP_ORIGINAL = "keep_original"
    EITHER = "either"


_MAX_QA_OUTPUT_TOKENS = 4096

_OMISSION_VERIFIER_STATUSES = frozenset(
    {
        "verified",
        "filtered",
        "configuration_failed",
        "invalid_response",
        "completion_timeout",
        "completion_failed",
        "identity_mismatch",
    }
)
_OMISSION_VERIFIER_WARNINGS = frozenset(
    {
        "foreign_text_filtered",
        "prompt_configuration_unavailable",
        "invalid_response",
        "completion_timeout",
        "completion_failed",
        "verdict_source_unit_ids_mismatch",
    }
)


@dataclass(frozen=True, slots=True)
class SemanticInlineSpan:
    """One text-fragment range inside a semantic unit and its parent block."""

    inline_id: str
    source_start: int
    source_end: int
    unit_start: int
    unit_end: int

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        _require_nonempty_string(self.inline_id, "inline_id")
        for field_name in ("source_start", "source_end", "unit_start", "unit_end"):
            _require_integer(getattr(self, field_name), field_name)
        if self.source_start < 0 or self.unit_start < 0:
            raise QaModelValidationError("semantic inline span offsets must be non-negative")
        if self.source_start >= self.source_end or self.unit_start >= self.unit_end:
            raise QaModelValidationError("semantic inline span ranges must be nonempty and ordered")
        if self.source_end - self.source_start != self.unit_end - self.unit_start:
            raise QaModelValidationError("semantic inline span ranges must have equal lengths")


@dataclass(frozen=True, slots=True)
class SemanticUnit:
    """One stable, sentence-sized span of visible text in an EPUB block."""

    unit_id: str
    document_id: str
    block_id: str
    ordinal: int
    text: str
    normalized_text: str
    source_start: int
    source_end: int
    kind: str
    inline_spans: tuple[SemanticInlineSpan, ...]

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        for field_name in (
            "unit_id",
            "document_id",
            "block_id",
            "text",
            "normalized_text",
            "kind",
        ):
            _require_nonempty_string(getattr(self, field_name), field_name)
        _require_integer(self.ordinal, "ordinal")
        _require_integer(self.source_start, "source_start")
        _require_integer(self.source_end, "source_end")
        if self.ordinal < 0:
            raise QaModelValidationError("ordinal must be non-negative")
        if self.source_start < 0 or self.source_start >= self.source_end:
            raise QaModelValidationError("semantic unit source range must be nonempty and ordered")
        if self.source_end - self.source_start != len(self.text):
            raise QaModelValidationError("semantic unit source range must match text length")
        if not isinstance(self.inline_spans, tuple) or not self.inline_spans:
            raise QaModelValidationError("inline_spans must be a nonempty tuple")

        expected_source_start = self.source_start
        expected_unit_start = 0
        seen_inline_ids: set[str] = set()
        for span in self.inline_spans:
            if not isinstance(span, SemanticInlineSpan):
                raise QaModelValidationError("inline_spans entries must be SemanticInlineSpan")
            span.validate()
            if span.inline_id in seen_inline_ids:
                raise QaModelValidationError("inline spans must not repeat an inline_id")
            seen_inline_ids.add(span.inline_id)
            if (
                span.source_start < self.source_start
                or span.source_end > self.source_end
                or span.unit_start < 0
                or span.unit_end > len(self.text)
            ):
                raise QaModelValidationError("inline span falls outside semantic unit range")
            if (
                span.source_start != expected_source_start
                or span.unit_start != expected_unit_start
            ):
                raise QaModelValidationError("inline spans must be ordered and cover semantic unit text")
            expected_source_start = span.source_end
            expected_unit_start = span.unit_end
        if expected_source_start != self.source_end or expected_unit_start != len(self.text):
            raise QaModelValidationError("inline spans must cover semantic unit text")


@dataclass(frozen=True, slots=True)
class SemanticWindow:
    """A deterministic contiguous view over one document's semantic units."""

    unit_ids: tuple[str, ...]
    text: str

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not isinstance(self.unit_ids, tuple) or not self.unit_ids:
            raise QaModelValidationError("unit_ids must be a nonempty tuple")
        for unit_id in self.unit_ids:
            _require_nonempty_string(unit_id, "unit_id")
        if len(set(self.unit_ids)) != len(self.unit_ids):
            raise QaModelValidationError("unit_ids must be unique")
        _require_nonempty_string(self.text, "text")


@dataclass(frozen=True, slots=True)
class ChapterMetrics:
    chapter_id: str
    source_language: str
    target_language: str
    content_kind: str = "narrative"
    source_chars: int = 0
    translated_chars: int = 0
    source_units: int = 0
    aligned_units: int = 0
    possible_gaps: int = 0
    glossary_expected: int = 0
    glossary_matched: int = 0
    glossary_conflicts: int = 0
    untranslated_by_script: Mapping[str, int] | None = None
    allowed_foreign_fragments: int = 0
    language_tool_issues: int = 0
    protected_entities: int = 0
    syntax_candidates: int = 0
    quality_estimator: str | None = None
    quality_score: float | None = None
    quality_score_status: str = "not_run"
    capability_durations: Mapping[QaCapabilityKey, float] | None = None
    retries: int = 0
    llm_requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    duration_seconds: float = 0.0
    risk_level: RiskLevel | str = RiskLevel.LOW
    applied_actions: tuple[Action | str, ...] = ()

    _DATAFRAME_COLUMNS: ClassVar[tuple[str, ...]] = (
        "chapter_id",
        "source_language",
        "target_language",
        "content_kind",
        "source_chars",
        "translated_chars",
        "length_ratio",
        "source_units",
        "aligned_units",
        "possible_gaps",
        "glossary_expected",
        "glossary_matched",
        "glossary_conflicts",
        "untranslated_by_script",
        "allowed_foreign_fragments",
        "language_tool_issues",
        "protected_entities",
        "syntax_candidates",
        "quality_estimator",
        "quality_score",
        "quality_score_status",
        "capability_durations",
        "retries",
        "llm_requests",
        "input_tokens",
        "output_tokens",
        "duration_seconds",
        "risk_level",
        "applied_actions",
    )

    _INTEGER_FIELDS: ClassVar[tuple[str, ...]] = (
        "source_chars",
        "translated_chars",
        "source_units",
        "aligned_units",
        "possible_gaps",
        "glossary_expected",
        "glossary_matched",
        "glossary_conflicts",
        "allowed_foreign_fragments",
        "language_tool_issues",
        "protected_entities",
        "syntax_candidates",
        "retries",
        "llm_requests",
        "input_tokens",
        "output_tokens",
    )

    def __post_init__(self) -> None:
        for field_name in (
            "chapter_id",
            "source_language",
            "target_language",
            "content_kind",
            "quality_score_status",
        ):
            _require_string(getattr(self, field_name), field_name)
        _require_string(self.quality_estimator, "quality_estimator", allow_none=True)
        for field_name in self._INTEGER_FIELDS:
            _require_integer(getattr(self, field_name), field_name)
        _require_finite_number(
            self.quality_score, "quality_score", allow_none=True
        )
        _require_finite_number(self.duration_seconds, "duration_seconds")

        if not isinstance(self.untranslated_by_script, (Mapping, type(None))):
            raise QaModelValidationError("untranslated_by_script must be an object")
        for script, count in (self.untranslated_by_script or {}).items():
            _require_string(script, "untranslated_by_script key")
            _require_integer(count, "untranslated_by_script count")

        if not isinstance(self.capability_durations, (Mapping, type(None))):
            raise QaModelValidationError("capability_durations must be an object")
        durations: dict[QaCapabilityKey, float] = {}
        for key, value in (self.capability_durations or {}).items():
            try:
                capability = QaCapabilityKey(key)
            except ValueError as exc:
                raise QaModelValidationError("unsupported QA capability key") from exc
            _require_finite_number(value, "capability duration")
            if value < 0:
                raise QaModelValidationError(
                    "capability durations must be non-negative"
                )
            durations[capability] = float(value)

        object.__setattr__(self, "capability_durations", MappingProxyType(durations))
        object.__setattr__(
            self,
            "untranslated_by_script",
            MappingProxyType(dict(self.untranslated_by_script or {})),
        )
        try:
            object.__setattr__(self, "risk_level", RiskLevel(self.risk_level))
        except ValueError as exc:
            raise QaModelValidationError("unsupported risk level") from exc
        if not isinstance(self.applied_actions, (tuple, list)):
            raise QaModelValidationError("applied_actions must be a sequence")
        try:
            actions = tuple(Action(action) for action in self.applied_actions)
        except ValueError as exc:
            raise QaModelValidationError("unsupported applied action") from exc
        object.__setattr__(
            self,
            "applied_actions",
            actions,
        )

    @property
    def length_ratio(self) -> float:
        if self.source_chars <= 0:
            return 0.0
        return self.translated_chars / self.source_chars

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter_id": self.chapter_id,
            "source_language": self.source_language,
            "target_language": self.target_language,
            "content_kind": self.content_kind,
            "source_chars": self.source_chars,
            "translated_chars": self.translated_chars,
            "length_ratio": self.length_ratio,
            "source_units": self.source_units,
            "aligned_units": self.aligned_units,
            "possible_gaps": self.possible_gaps,
            "glossary_expected": self.glossary_expected,
            "glossary_matched": self.glossary_matched,
            "glossary_conflicts": self.glossary_conflicts,
            "untranslated_by_script": dict(self.untranslated_by_script),
            "allowed_foreign_fragments": self.allowed_foreign_fragments,
            "language_tool_issues": self.language_tool_issues,
            "protected_entities": self.protected_entities,
            "syntax_candidates": self.syntax_candidates,
            "quality_estimator": self.quality_estimator,
            "quality_score": self.quality_score,
            "quality_score_status": self.quality_score_status,
            "capability_durations": {
                key.value: value for key, value in self.capability_durations.items()
            },
            "retries": self.retries,
            "llm_requests": self.llm_requests,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "duration_seconds": self.duration_seconds,
            "risk_level": self.risk_level.value,
            "applied_actions": [action.value for action in self.applied_actions],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ChapterMetrics":
        if not isinstance(payload, Mapping):
            raise QaModelValidationError("Chapter metrics must be a JSON object")
        expected_fields = set(cls.dataframe_columns())
        if set(payload) != expected_fields:
            raise QaModelValidationError("Chapter metrics have an invalid schema")
        _require_finite_number(payload["length_ratio"], "length_ratio")
        fields = set(cls.__dataclass_fields__) - {
            "_DATAFRAME_COLUMNS",
            "_INTEGER_FIELDS",
        }
        try:
            metrics = cls(
                **{key: value for key, value in payload.items() if key in fields}
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise QaModelValidationError("Invalid chapter metrics") from exc
        if payload["length_ratio"] != metrics.length_ratio:
            raise QaModelValidationError(
                "Persisted length_ratio does not match chapter metrics"
            )
        return metrics

    @classmethod
    def dataframe_columns(cls) -> tuple[str, ...]:
        return cls._DATAFRAME_COLUMNS


@dataclass(frozen=True, slots=True)
class QaJournalEntry:
    entry_id: str
    chapter_id: str
    decision: Decision | str

    def __post_init__(self) -> None:
        _require_string(self.entry_id, "entry_id")
        _require_string(self.chapter_id, "chapter_id")
        try:
            object.__setattr__(self, "decision", Decision(self.decision))
        except ValueError as exc:
            raise QaModelValidationError("unsupported journal decision") from exc

    def to_dict(self) -> dict[str, str]:
        return {
            "entry_id": self.entry_id,
            "chapter_id": self.chapter_id,
            "decision": self.decision.value,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "QaJournalEntry":
        if not isinstance(payload, Mapping):
            raise QaModelValidationError("Journal entry must be a JSON object")
        if set(payload) != {"entry_id", "chapter_id", "decision"}:
            raise QaModelValidationError("Journal entry has an invalid schema")
        try:
            return cls(
                entry_id=payload["entry_id"],
                chapter_id=payload["chapter_id"],
                decision=payload["decision"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise QaModelValidationError("Invalid journal entry") from exc


_CHAPTER_QA_STATUSES = frozenset({"checked", "deferred", "blocked"})


@dataclass(frozen=True, slots=True)
class QaChapterState:
    """What is known about one chapter's last quality check, and under what rules.

    The final book pass reads exactly this: a chapter is re-checked when it was
    never checked, was deferred, still carries unresolved risk, or was checked
    under different segmentation or before the book had a statistical baseline.
    """

    chapter_id: str
    status: str
    analysis_identity: str = ""
    risk_level: RiskLevel | str = RiskLevel.LOW
    book_sample_size: int = 0
    fingerprint: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        _require_nonempty_string(self.chapter_id, "chapter_id")
        _require_nonempty_string(self.status, "status")
        if self.status not in _CHAPTER_QA_STATUSES:
            raise QaModelValidationError("unsupported chapter QA status")
        for field_name in ("analysis_identity", "fingerprint", "updated_at"):
            _require_string(getattr(self, field_name), field_name)
        try:
            object.__setattr__(self, "risk_level", RiskLevel(self.risk_level))
        except ValueError as exc:
            raise QaModelValidationError("unsupported chapter risk level") from exc
        _require_integer(self.book_sample_size, "book_sample_size")
        if self.book_sample_size < 0:
            raise QaModelValidationError("book_sample_size must be non-negative")

    def to_dict(self) -> dict[str, object]:
        return {
            "chapter_id": self.chapter_id,
            "status": self.status,
            "analysis_identity": self.analysis_identity,
            "risk_level": str(self.risk_level),
            "book_sample_size": self.book_sample_size,
            "fingerprint": self.fingerprint,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "QaChapterState":
        if not isinstance(payload, Mapping):
            raise QaModelValidationError("chapter state must be an object")
        unknown = set(payload) - set(cls.__dataclass_fields__)
        if unknown:
            raise QaModelValidationError("chapter state has unsupported fields")
        try:
            return cls(**dict(payload))
        except (TypeError, ValueError) as exc:
            raise QaModelValidationError("invalid chapter state") from exc


@dataclass(frozen=True, slots=True)
class GlossaryObservation:
    """One observed translation of a source glossary term in a chapter."""

    original_term: str
    observed_translation: str
    canonical_translation: str | None
    morphology_signature: tuple[str, ...]
    morphology_confidence: Literal["high", "ambiguous"]
    chapter_id: str
    occurrences: int
    policy: GlossaryPolicy

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "original_term",
            "observed_translation",
            "canonical_translation",
            "morphology_signature",
            "morphology_confidence",
            "chapter_id",
            "occurrences",
            "policy",
        }
    )

    def __post_init__(self) -> None:
        for field_name in ("original_term", "observed_translation", "chapter_id"):
            _require_string(getattr(self, field_name), field_name)
        _require_string(
            self.canonical_translation, "canonical_translation", allow_none=True
        )
        if not isinstance(self.morphology_signature, tuple):
            raise QaModelValidationError("morphology_signature must be a tuple")
        for signature in self.morphology_signature:
            _require_string(signature, "morphology_signature entry")
        if self.morphology_confidence not in {"high", "ambiguous"}:
            raise QaModelValidationError("unsupported morphology confidence")
        _require_integer(self.occurrences, "occurrences")
        if self.occurrences <= 0:
            raise QaModelValidationError("occurrences must be positive")
        try:
            object.__setattr__(self, "policy", GlossaryPolicy(self.policy))
        except (TypeError, ValueError) as exc:
            raise QaModelValidationError("unsupported glossary policy") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_term": self.original_term,
            "observed_translation": self.observed_translation,
            "canonical_translation": self.canonical_translation,
            "morphology_signature": list(self.morphology_signature),
            "morphology_confidence": self.morphology_confidence,
            "chapter_id": self.chapter_id,
            "occurrences": self.occurrences,
            "policy": self.policy.value,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GlossaryObservation":
        if not isinstance(payload, Mapping):
            raise QaModelValidationError("Glossary observation must be a JSON object")
        if set(payload) != cls._FIELDS:
            raise QaModelValidationError("Glossary observation has an invalid schema")
        signature = payload.get("morphology_signature")
        if not isinstance(signature, list):
            raise QaModelValidationError("morphology_signature must be an array")
        try:
            return cls(
                original_term=payload["original_term"],
                observed_translation=payload["observed_translation"],
                canonical_translation=payload["canonical_translation"],
                morphology_signature=tuple(signature),
                morphology_confidence=payload["morphology_confidence"],
                chapter_id=payload["chapter_id"],
                occurrences=payload["occurrences"],
                policy=payload["policy"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise QaModelValidationError("Invalid glossary observation") from exc


@dataclass(frozen=True, slots=True)
class AlignmentConfig:
    """Bounded, deterministic scoring policy for chapter-local alignment."""

    max_span_size: int = 3
    max_drift_units: int = 8
    max_cells: int = 250_000
    merge_penalty: float = 0.05
    gap_penalty: float = 1.2
    local_gap_penalty: float = 0.0
    anchor_similarity: float = 0.85
    # Cross-lingual similarity alone cannot tell a lost paragraph from a merged
    # one: measured on real chapters the two distributions overlap.  Missing
    # volume can: text the chapter's own ratio predicts but nobody wrote is
    # counted in characters, so a lost paragraph costs far more than the ratio
    # wobble of a terse one.
    volume_penalty: float = 0.5
    volume_tolerance: float = 0.0
    volume_surplus_weight: float = 0.5
    # A paragraph nobody translated has no counterpart to be similar to.  Its
    # best match across the chapter therefore sits measurably below the
    # chapter's own median.  Calibrated on eight real chapters: the median best
    # match is 0.93 and a paragraph whose translation was deleted falls to 0.87.
    # Short dialogue lines are formulaic enough to drift on their own, so the
    # evidence is only read on paragraphs a reader would notice losing.
    orphan_drop: float = 0.04
    orphan_penalty: float = 2.0
    # Which paragraphs are worth reading the evidence on.  An absolute size is
    # not portable: 60 characters is a paragraph in Chinese and a short sentence
    # in English, and even inside one Chinese book that threshold covered
    # anywhere from 23% to 48% of the paragraphs chapter by chapter.  The floor
    # is therefore the chapter's own median paragraph, with a small absolute
    # sanity bound under it for a chapter made of one-word lines.
    orphan_min_chars: int = 25
    orphan_min_share: float = 1.0
    # How many paragraphs a chapter needs before its own volume ratio is taken
    # from a median of paired paragraphs rather than from its totals.  Totals
    # are what a damaged chapter poisons: a chapter that lost a third of its
    # text lowers its own expectation by a third, and the volume evidence goes
    # quiet exactly where the loss is worst.  Measured on real chapters, the
    # total ratio falls 3.0 -> 1.4 under 40% loss while the paired median holds
    # 2.7 -> 2.8.
    robust_ratio_min_blocks: int = 5
    operation_order: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        for field in (
            "max_span_size",
            "max_drift_units",
            "max_cells",
            "orphan_min_chars",
            "robust_ratio_min_blocks",
        ):
            _require_integer(getattr(self, field), field)
        if (
            self.max_span_size < 1
            or self.max_span_size > 3
            or self.max_drift_units < 0
            or self.max_cells < 1
            or self.orphan_min_chars < 0
            or self.robust_ratio_min_blocks < 0
        ):
            raise QaModelValidationError("alignment limits are outside supported bounds")
        for field in (
            "merge_penalty",
            "gap_penalty",
            "local_gap_penalty",
            "anchor_similarity",
            "volume_penalty",
            "volume_tolerance",
            "volume_surplus_weight",
            "orphan_drop",
            "orphan_penalty",
            "orphan_min_share",
        ):
            _require_finite_number(getattr(self, field), field)
        if (
            self.merge_penalty < 0
            or self.gap_penalty < 0
            or self.local_gap_penalty < 0
            or self.volume_penalty < 0
            or not 0.0 <= self.volume_tolerance <= 1.0
            or not 0.0 <= self.volume_surplus_weight <= 1.0
            or not 0.0 <= self.orphan_drop <= 2.0
            or self.orphan_penalty < 0
            or self.orphan_min_share < 0
            or not -1.0 <= self.anchor_similarity <= 1.0
        ):
            raise QaModelValidationError("alignment scores are outside supported bounds")
        valid = {
            f"{left}:{right}"
            for left in range(1, self.max_span_size + 1)
            for right in range(1, self.max_span_size + 1)
        } | {"1:0", "0:1"}
        if self.operation_order is None:
            preferred = (
                "1:1", "1:2", "2:1", "2:2", "1:3", "3:1",
                "2:3", "3:2", "3:3", "1:0", "0:1",
            )
            object.__setattr__(
                self, "operation_order", tuple(operation for operation in preferred if operation in valid)
            )
        if not isinstance(self.operation_order, tuple) or not self.operation_order:
            raise QaModelValidationError("operation_order must be a nonempty tuple")
        if len(set(self.operation_order)) != len(self.operation_order) or set(self.operation_order) != valid:
            raise QaModelValidationError("operation_order must contain every supported operation once")


@dataclass(frozen=True, slots=True)
class EmbeddedUnits:
    """One document's ordered semantic units and defensive vector snapshot."""

    document_id: str
    units: tuple[SemanticUnit, ...]
    vectors: np.ndarray

    def __post_init__(self) -> None:
        _require_nonempty_string(self.document_id, "document_id")
        if not isinstance(self.units, tuple) or not self.units:
            raise QaModelValidationError("units must be a nonempty tuple")
        if not isinstance(self.vectors, np.ndarray):
            raise QaModelValidationError("vectors must be an ndarray")
        if self.vectors.dtype.kind not in {"i", "u", "f"}:
            raise QaModelValidationError("vectors must be real numeric data")
        try:
            vectors = np.array(self.vectors, dtype=np.float32, order="C", copy=True)
        except (TypeError, ValueError, OverflowError):
            raise QaModelValidationError("vectors cannot be converted to float32") from None
        if vectors.ndim != 2 or vectors.shape[0] != len(self.units) or vectors.shape[1] < 1:
            raise QaModelValidationError("vectors must have one nonempty row per unit")
        if not np.isfinite(vectors).all():
            raise QaModelValidationError("vectors must be finite")
        norms = np.linalg.norm(vectors.astype(np.float64), axis=1)
        if np.any(norms <= np.finfo(np.float32).eps):
            raise QaModelValidationError("vectors must not contain zero or near-zero rows")
        vectors = np.ascontiguousarray(vectors / norms[:, None], dtype=np.float32)
        if not np.allclose(np.linalg.norm(vectors.astype(np.float64), axis=1), 1.0, atol=1e-5, rtol=1e-5):
            raise QaModelValidationError("vectors must normalize to unit length")
        seen: set[str] = set()
        for expected_ordinal, unit in enumerate(self.units):
            if not isinstance(unit, SemanticUnit):
                raise QaModelValidationError("units entries must be SemanticUnit")
            unit.validate()
            if unit.document_id != self.document_id or unit.ordinal != expected_ordinal or unit.unit_id in seen:
                raise QaModelValidationError("units must be unique, ordered, and from one document")
            seen.add(unit.unit_id)
        vectors.setflags(write=False)
        object.__setattr__(self, "vectors", vectors)


@dataclass(frozen=True, slots=True)
class AlignmentSpan:
    source_unit_ids: tuple[str, ...]
    target_unit_ids: tuple[str, ...]
    similarity: float
    operation: str

    def __post_init__(self) -> None:
        for field in ("source_unit_ids", "target_unit_ids"):
            ids = getattr(self, field)
            if not isinstance(ids, tuple):
                raise QaModelValidationError(f"{field} must be a tuple")
            for unit_id in ids:
                _require_nonempty_string(unit_id, field)
        _require_finite_number(self.similarity, "similarity")
        if not -1.0 <= self.similarity <= 1.0:
            raise QaModelValidationError("similarity must be a cosine score")
        source_size = len(self.source_unit_ids)
        target_size = len(self.target_unit_ids)
        if len(set(self.source_unit_ids)) != source_size or len(set(self.target_unit_ids)) != target_size:
            raise QaModelValidationError("alignment span unit ids must be unique")
        if source_size and target_size:
            if source_size > 3 or target_size > 3:
                raise QaModelValidationError("alignment match spans are limited to three units per side")
        elif (source_size, target_size) not in {(1, 0), (0, 1)} or self.similarity != 0.0:
            raise QaModelValidationError("alignment gaps must contain one unit and zero similarity")
        expected = f"{source_size}:{target_size}"
        if self.operation != expected or not (self.source_unit_ids or self.target_unit_ids):
            raise QaModelValidationError("operation must match a nonempty span")


@dataclass(frozen=True, slots=True)
class GapCandidate:
    candidate_id: str
    side: str
    source_unit_ids: tuple[str, ...]
    target_unit_ids: tuple[str, ...]
    left_anchor: AlignmentSpan | None
    right_anchor: AlignmentSpan | None
    repairable: bool
    signals: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_nonempty_string(self.candidate_id, "candidate_id")
        if re.fullmatch(r"gap-[0-9a-f]{20}", self.candidate_id) is None:
            raise QaModelValidationError("candidate_id must be a deterministic SHA-256 prefix")
        if self.side not in {"source", "target"}:
            raise QaModelValidationError("gap side must be source or target")
        if not isinstance(self.source_unit_ids, tuple) or not isinstance(self.target_unit_ids, tuple):
            raise QaModelValidationError("gap unit ids must be tuples")
        if (self.side == "source") != (bool(self.source_unit_ids) and not self.target_unit_ids):
            raise QaModelValidationError("gap side must name the unmatched content")
        if (self.side == "target") != (bool(self.target_unit_ids) and not self.source_unit_ids):
            raise QaModelValidationError("gap side must name the unmatched content")
        for ids in (self.source_unit_ids, self.target_unit_ids):
            for unit_id in ids:
                _require_nonempty_string(unit_id, "gap unit id")
            if len(set(ids)) != len(ids):
                raise QaModelValidationError("gap unit ids must be unique")
        if self.left_anchor is not None and not isinstance(self.left_anchor, AlignmentSpan):
            raise QaModelValidationError("left_anchor must be an AlignmentSpan")
        if self.right_anchor is not None and not isinstance(self.right_anchor, AlignmentSpan):
            raise QaModelValidationError("right_anchor must be an AlignmentSpan")
        for anchor in (self.left_anchor, self.right_anchor):
            if anchor is not None and (not anchor.source_unit_ids or not anchor.target_unit_ids):
                raise QaModelValidationError("gap anchors must be non-gap alignment spans")
        if not isinstance(self.repairable, bool) or not isinstance(self.signals, tuple):
            raise QaModelValidationError("gap repairability and signals have invalid types")
        for signal in self.signals:
            _require_nonempty_string(signal, "signal")
        expected_signals = ("missing_in_target",) if self.side == "source" else ("addition",)
        if self.signals != expected_signals:
            raise QaModelValidationError("gap signals must describe the unmatched content side")
        if self.repairable and (
            self.side != "source" or self.left_anchor is None or self.right_anchor is None
        ):
            raise QaModelValidationError("repairable gaps require source content and two anchors")
        source_groups = [set(self.source_unit_ids)]
        target_groups = [set(self.target_unit_ids)]
        for anchor in (self.left_anchor, self.right_anchor):
            if anchor is not None:
                source_groups.append(set(anchor.source_unit_ids))
                target_groups.append(set(anchor.target_unit_ids))
        if any(left & right for index, left in enumerate(source_groups) for right in source_groups[index + 1:]):
            raise QaModelValidationError("source gap and anchors must not overlap")
        if any(left & right for index, left in enumerate(target_groups) for right in target_groups[index + 1:]):
            raise QaModelValidationError("target gap and anchors must not overlap")


@dataclass(frozen=True, slots=True)
class AlignmentResult:
    spans: tuple[AlignmentSpan, ...]
    gaps: tuple[GapCandidate, ...]
    visited_cells: int

    def __post_init__(self) -> None:
        if not isinstance(self.spans, tuple) or not self.spans or not isinstance(self.gaps, tuple):
            raise QaModelValidationError("alignment results must use tuples")
        if not all(isinstance(span, AlignmentSpan) for span in self.spans) or not all(isinstance(gap, GapCandidate) for gap in self.gaps):
            raise QaModelValidationError("alignment results contain invalid entries")
        _require_integer(self.visited_cells, "visited_cells")
        if self.visited_cells < 1:
            raise QaModelValidationError("visited_cells must be positive")

        seen_source_ids: set[str] = set()
        seen_target_ids: set[str] = set()
        gap_groups: list[tuple[int, int]] = []
        index = 0
        while index < len(self.spans):
            span = self.spans[index]
            if seen_source_ids.intersection(span.source_unit_ids) or seen_target_ids.intersection(
                span.target_unit_ids
            ):
                raise QaModelValidationError("alignment spans must not repeat unit ids")
            seen_source_ids.update(span.source_unit_ids)
            seen_target_ids.update(span.target_unit_ids)
            if span.source_unit_ids and span.target_unit_ids:
                index += 1
                continue
            side_shape = (bool(span.source_unit_ids), bool(span.target_unit_ids))
            end = index + 1
            while end < len(self.spans):
                following = self.spans[end]
                if (bool(following.source_unit_ids), bool(following.target_unit_ids)) != side_shape:
                    break
                if seen_source_ids.intersection(following.source_unit_ids) or seen_target_ids.intersection(
                    following.target_unit_ids
                ):
                    raise QaModelValidationError("alignment spans must not repeat unit ids")
                seen_source_ids.update(following.source_unit_ids)
                seen_target_ids.update(following.target_unit_ids)
                end += 1
            gap_groups.append((index, end))
            index = end

        if len(gap_groups) != len(self.gaps):
            raise QaModelValidationError("alignment gaps must cover every consecutive gap group")
        if len({gap.candidate_id for gap in self.gaps}) != len(self.gaps):
            raise QaModelValidationError("alignment candidate ids must be unique")
        for candidate, (start, end) in zip(self.gaps, gap_groups, strict=True):
            group = self.spans[start:end]
            source_ids = tuple(unit_id for span in group for unit_id in span.source_unit_ids)
            target_ids = tuple(unit_id for span in group for unit_id in span.target_unit_ids)
            side = "source" if source_ids else "target"
            if (
                candidate.side != side
                or candidate.source_unit_ids != source_ids
                or candidate.target_unit_ids != target_ids
            ):
                raise QaModelValidationError("alignment candidate does not match its gap group")
            immediate_left = self.spans[start - 1] if start else None
            immediate_right = self.spans[end] if end < len(self.spans) else None
            if candidate.left_anchor is not None and candidate.left_anchor != immediate_left:
                raise QaModelValidationError("left anchor must immediately precede its gap")
            if candidate.right_anchor is not None and candidate.right_anchor != immediate_right:
                raise QaModelValidationError("right anchor must immediately follow its gap")


_ENTITY_CATEGORIES = frozenset(
    # "location" arrives from local NER, which recognizes PER/ORG/LOC.
    {"brand", "device_model", "location", "organization", "person", "product", "title"}
)
_PROTECTED_CONTEXTS = frozenset(
    {"dialogue", "foreign_dialogue", "foreign_quote", "quote", "sign"}
)
_FOREIGN_TEXT_CATEGORIES = frozenset(
    {
        "ambiguous",
        "glossary_must_translate",
        "glossary_protected",
        "intentional_foreign",
        "invalid_context",
        "protected_entity",
        "protected_item",
        "target_addition",
    }
)
_FOREIGN_TEXT_ACTIONS = frozenset(
    {"exclude", "report_only", "send_to_llm_verifier"}
)
_FOREIGN_TEXT_CONFIDENCES = frozenset({"high", "medium", "low"})
_FOREIGN_TEXT_CATEGORY_ACTIONS = {
    "ambiguous": frozenset({"report_only", "send_to_llm_verifier"}),
    "glossary_must_translate": frozenset({"send_to_llm_verifier"}),
    "glossary_protected": frozenset({"exclude"}),
    "intentional_foreign": frozenset({"exclude"}),
    "invalid_context": frozenset({"report_only"}),
    "protected_entity": frozenset({"exclude"}),
    "protected_item": frozenset({"exclude"}),
    "target_addition": frozenset({"report_only"}),
}


@dataclass(frozen=True, slots=True)
class ProtectedEntityHint:
    """Explicit upstream evidence that a particular surface is intentionally preserved."""

    text: str
    category: str

    def __post_init__(self) -> None:
        _require_nonempty_string(self.text, "protected entity text")
        _require_nonempty_string(self.category, "protected entity category")
        if self.category not in _ENTITY_CATEGORIES:
            raise QaModelValidationError("unsupported protected entity category")


@dataclass(frozen=True, slots=True)
class GlossaryRule:
    """One exact glossary surface and its authoritative translation policy."""

    term: str
    policy: GlossaryPolicy

    def __post_init__(self) -> None:
        _require_nonempty_string(self.term, "glossary term")
        try:
            object.__setattr__(self, "policy", GlossaryPolicy(self.policy))
        except (TypeError, ValueError) as exc:
            raise QaModelValidationError("unsupported glossary policy") from exc


@dataclass(frozen=True, slots=True, repr=False)
class RelevantGlossaryTerm:
    """One candidate-local glossary instruction safe to include in an LLM prompt."""

    original_term: str
    canonical_translation: str
    policy: GlossaryPolicy
    occurrences: int
    priority: int

    def __post_init__(self) -> None:
        _require_nonempty_string(self.original_term, "original_term")
        _require_nonempty_string(self.canonical_translation, "canonical_translation")
        try:
            object.__setattr__(self, "policy", GlossaryPolicy(self.policy))
        except (TypeError, ValueError) as exc:
            raise QaModelValidationError("unsupported glossary policy") from exc
        _require_integer(self.occurrences, "occurrences")
        _require_integer(self.priority, "priority")
        if self.occurrences < 1:
            raise QaModelValidationError("occurrences must be positive")
        if self.priority < 0:
            raise QaModelValidationError("priority must be non-negative")
        if (
            self.policy is GlossaryPolicy.KEEP_ORIGINAL
            and self.canonical_translation != self.original_term
        ):
            raise QaModelValidationError(
                "KEEP_ORIGINAL canonical translation must retain the original term"
            )


@dataclass(frozen=True, slots=True, repr=False)
class OmissionVerifierConfig:
    """Fail-closed policy and resource selection for omission verification."""

    high_confidence: float = 0.95
    max_output_tokens: int = 700
    prompt_version: str = "omission_verifier_v1"
    prompt_path: Path | None = None

    def __post_init__(self) -> None:
        _require_finite_number(self.high_confidence, "high_confidence")
        if not 0.0 <= self.high_confidence <= 1.0:
            raise QaModelValidationError("high_confidence must be between 0 and 1")
        _validate_prompt_resources(self)


@dataclass(frozen=True, slots=True, repr=False)
class OmissionRepairerConfig:
    """Bounded resource and prompt selection for one local repair attempt."""

    max_output_tokens: int = 900
    prompt_version: str = "omission_repairer_v1"
    prompt_path: Path | None = None
    max_glossary_terms: int = 12

    def __post_init__(self) -> None:
        _validate_prompt_resources(self)
        _require_integer(self.max_glossary_terms, "max_glossary_terms")
        if self.max_glossary_terms < 1:
            raise QaModelValidationError("max_glossary_terms must be positive")


@dataclass(frozen=True, slots=True)
class GlossaryPolicyMatch:
    """An exact normalized glossary match; offsets refer to normalized candidate text."""

    term: str
    policy: GlossaryPolicy
    start: int
    end: int
    exact: bool = True

    def __post_init__(self) -> None:
        _require_nonempty_string(self.term, "glossary match term")
        try:
            object.__setattr__(self, "policy", GlossaryPolicy(self.policy))
        except (TypeError, ValueError) as exc:
            raise QaModelValidationError("unsupported glossary policy") from exc
        _require_integer(self.start, "glossary match start")
        _require_integer(self.end, "glossary match end")
        if self.start < 0 or self.start >= self.end:
            raise QaModelValidationError("glossary match range must be nonempty and ordered")
        if not isinstance(self.exact, bool) or not self.exact:
            raise QaModelValidationError("glossary policy matches must be exact")


@dataclass(frozen=True, slots=True)
class CandidateContext:
    """Immutable source/target neighborhood and explicit protection evidence for one gap."""

    candidate_id: str
    source_text: str
    target_text: str
    source_before: str
    source_after: str
    target_before: str
    target_after: str
    source_language: str
    target_language: str
    candidate_language: str
    protected_entities: tuple[ProtectedEntityHint, ...] = ()
    protected_contexts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_nonempty_string(self.candidate_id, "candidate_id")
        if re.fullmatch(r"gap-[0-9a-f]{20}", self.candidate_id) is None:
            raise QaModelValidationError("candidate context id must match a gap candidate id")
        for field_name in (
            "source_text",
            "target_text",
            "source_before",
            "source_after",
            "target_before",
            "target_after",
        ):
            _require_string(getattr(self, field_name), field_name)
        for field_name in ("source_language", "target_language", "candidate_language"):
            _require_nonempty_string(getattr(self, field_name), field_name)
        if not isinstance(self.protected_entities, tuple):
            raise QaModelValidationError("protected_entities must be a tuple")
        if not all(
            isinstance(entity, ProtectedEntityHint) for entity in self.protected_entities
        ):
            raise QaModelValidationError(
                "protected_entities entries must be ProtectedEntityHint"
            )
        if len(set(self.protected_entities)) != len(self.protected_entities):
            raise QaModelValidationError("protected_entities entries must be unique")
        if not isinstance(self.protected_contexts, tuple):
            raise QaModelValidationError("protected_contexts must be a tuple")
        for context in self.protected_contexts:
            _require_nonempty_string(context, "protected context")
        if len(set(self.protected_contexts)) != len(self.protected_contexts):
            raise QaModelValidationError("protected_contexts entries must be unique")
        for context in self.protected_contexts:
            if context not in _PROTECTED_CONTEXTS:
                raise QaModelValidationError("unsupported protected context")


@dataclass(frozen=True, slots=True)
class ForeignTextDecision:
    """A filter-only decision that can never authorize or apply a repair."""

    category: str
    action: str
    confidence: str
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_nonempty_string(self.category, "foreign-text category")
        _require_nonempty_string(self.action, "foreign-text action")
        _require_nonempty_string(self.confidence, "foreign-text confidence")
        if self.category not in _FOREIGN_TEXT_CATEGORIES:
            raise QaModelValidationError("unsupported foreign-text category")
        if self.action not in _FOREIGN_TEXT_ACTIONS:
            raise QaModelValidationError("unsupported foreign-text action")
        if self.action not in _FOREIGN_TEXT_CATEGORY_ACTIONS[self.category]:
            raise QaModelValidationError("foreign-text action does not match its category")
        if self.confidence not in _FOREIGN_TEXT_CONFIDENCES:
            raise QaModelValidationError("unsupported foreign-text confidence")
        if not isinstance(self.reasons, tuple) or not self.reasons:
            raise QaModelValidationError("foreign-text reasons must be a nonempty tuple")
        for reason in self.reasons:
            _require_nonempty_string(reason, "foreign-text reason")
        if len(set(self.reasons)) != len(self.reasons):
            raise QaModelValidationError("foreign-text reasons must be unique")


@dataclass(frozen=True, slots=True, repr=False)
class VerifiedCandidate:
    """Auditable, fail-closed result of one omission-verification attempt."""

    candidate: GapCandidate
    context: CandidateContext
    verdict: OmissionVerdict | None
    foreign_text_decision: ForeignTextDecision
    eligible_for_repair: bool
    status: str
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, GapCandidate):
            raise QaModelValidationError("candidate must be a GapCandidate")
        if not isinstance(self.context, CandidateContext):
            raise QaModelValidationError("context must be a CandidateContext")
        if self.context.candidate_id != self.candidate.candidate_id:
            raise QaModelValidationError("verified candidate context identity must match")
        if not isinstance(self.foreign_text_decision, ForeignTextDecision):
            raise QaModelValidationError(
                "foreign_text_decision must be a ForeignTextDecision"
            )

        from .llm.schemas import OmissionVerdict

        if self.verdict is not None and not isinstance(self.verdict, OmissionVerdict):
            raise QaModelValidationError("verdict must be an OmissionVerdict or None")
        if not isinstance(self.eligible_for_repair, bool):
            raise QaModelValidationError("eligible_for_repair must be a bool")
        _require_nonempty_string(self.status, "status")
        if self.status not in _OMISSION_VERIFIER_STATUSES:
            raise QaModelValidationError("unsupported omission verifier status")
        if not isinstance(self.warnings, tuple):
            raise QaModelValidationError("warnings must be a tuple")
        for warning in self.warnings:
            _require_nonempty_string(warning, "warning")
            if warning not in _OMISSION_VERIFIER_WARNINGS:
                raise QaModelValidationError("unsupported omission verifier warning")
        if len(set(self.warnings)) != len(self.warnings):
            raise QaModelValidationError("warnings must be unique")
        if self.status != "verified" and not self.warnings:
            raise QaModelValidationError(
                "non-verified omission statuses must record a warning"
            )

        if self.status == "verified" and self.verdict is None:
            raise QaModelValidationError("verified status requires a verdict")
        if self.status == "identity_mismatch" and self.verdict is None:
            raise QaModelValidationError("identity mismatch status requires a verdict")
        if self.status not in {"verified", "identity_mismatch"} and self.verdict is not None:
            raise QaModelValidationError("failure and filter statuses cannot carry a verdict")
        if self.eligible_for_repair:
            if (
                self.status != "verified"
                or self.verdict is None
                or self.verdict.decision != "missing_content"
                or not self.verdict.missing_facts
                or not set(self.verdict.source_unit_ids).issubset(
                    self.candidate.source_unit_ids
                )
                or not self.candidate.repairable
                or self.candidate.side != "source"
                or self.candidate.left_anchor is None
                or self.candidate.right_anchor is None
                or self.candidate.signals != ("missing_in_target",)
                or self.foreign_text_decision.action != "send_to_llm_verifier"
            ):
                raise QaModelValidationError(
                    "repair eligibility lacks required independent evidence"
                )


@dataclass(frozen=True, slots=True)
class FilteredCandidate:
    """One gap and the filter-only disposition tied to its stable identity."""

    candidate_id: str
    candidate: GapCandidate
    decision: ForeignTextDecision

    def __post_init__(self) -> None:
        _require_nonempty_string(self.candidate_id, "candidate_id")
        if not isinstance(self.candidate, GapCandidate):
            raise QaModelValidationError("candidate must be a GapCandidate")
        if self.candidate_id != self.candidate.candidate_id:
            raise QaModelValidationError("filtered candidate id must match its candidate")
        if not isinstance(self.decision, ForeignTextDecision):
            raise QaModelValidationError("decision must be a ForeignTextDecision")


@dataclass(frozen=True, slots=True)
class CandidateFilterResult:
    """Complete, immutable partition of alignment gaps after safety filtering."""

    accepted: tuple[FilteredCandidate, ...]
    excluded: tuple[FilteredCandidate, ...]
    report_only: tuple[FilteredCandidate, ...]

    def __post_init__(self) -> None:
        partitions = (
            ("accepted", self.accepted, "send_to_llm_verifier"),
            ("excluded", self.excluded, "exclude"),
            ("report_only", self.report_only, "report_only"),
        )
        seen: set[str] = set()
        for name, values, expected_action in partitions:
            if not isinstance(values, tuple):
                raise QaModelValidationError(f"{name} must be a tuple")
            for value in values:
                if not isinstance(value, FilteredCandidate):
                    raise QaModelValidationError(
                        f"{name} entries must be FilteredCandidate"
                    )
                if value.decision.action != expected_action:
                    raise QaModelValidationError(
                        f"{name} entries must use {expected_action}"
                    )
                if value.candidate_id in seen:
                    raise QaModelValidationError(
                        "candidate filter partitions must not overlap"
                    )
                seen.add(value.candidate_id)
