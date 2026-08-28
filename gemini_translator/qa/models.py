"""Immutable, Qt-free data contracts for translation quality assurance."""

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, ClassVar, Mapping

from .capabilities import QaCapabilityKey


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


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

    def __post_init__(self) -> None:
        durations: dict[QaCapabilityKey, float] = {}
        for key, value in (self.capability_durations or {}).items():
            capability = QaCapabilityKey(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("capability durations must be numeric seconds")
            if value < 0:
                raise ValueError("capability durations must be non-negative")
            durations[capability] = float(value)

        object.__setattr__(self, "capability_durations", MappingProxyType(durations))
        object.__setattr__(
            self,
            "untranslated_by_script",
            MappingProxyType(dict(self.untranslated_by_script or {})),
        )
        object.__setattr__(self, "risk_level", RiskLevel(self.risk_level))
        object.__setattr__(
            self,
            "applied_actions",
            tuple(Action(action) for action in self.applied_actions),
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
            raise ValueError("Chapter metrics must be a JSON object")
        fields = set(cls.__dataclass_fields__) - {"_DATAFRAME_COLUMNS"}
        try:
            return cls(**{key: value for key, value in payload.items() if key in fields})
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Invalid chapter metrics") from exc

    @classmethod
    def dataframe_columns(cls) -> tuple[str, ...]:
        return cls._DATAFRAME_COLUMNS


@dataclass(frozen=True, slots=True)
class QaJournalEntry:
    entry_id: str
    chapter_id: str
    decision: Decision | str

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision", Decision(self.decision))

    def to_dict(self) -> dict[str, str]:
        return {
            "entry_id": self.entry_id,
            "chapter_id": self.chapter_id,
            "decision": self.decision.value,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "QaJournalEntry":
        if not isinstance(payload, Mapping):
            raise ValueError("Journal entry must be a JSON object")
        try:
            return cls(
                entry_id=payload["entry_id"],
                chapter_id=payload["chapter_id"],
                decision=payload["decision"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Invalid journal entry") from exc
