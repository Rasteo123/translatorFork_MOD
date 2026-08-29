"""Immutable, Qt-free data contracts for translation quality assurance."""

from dataclasses import dataclass
from enum import StrEnum
import math
from types import MappingProxyType
from typing import Any, ClassVar, Literal, Mapping

from .capabilities import QaCapabilityKey


class QaModelValidationError(ValueError):
    """Raised when persisted or constructed QA model data is invalid."""


def _require_string(value: object, field_name: str, *, allow_none: bool = False) -> None:
    if value is None and allow_none:
        return
    if not isinstance(value, str):
        raise QaModelValidationError(f"{field_name} must be a string")


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
