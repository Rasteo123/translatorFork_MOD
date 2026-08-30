"""Contracts for optional Russian NLP evidence. Nothing here may edit text."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

from ..models import ProtectedEntityHint, QaModelValidationError, SemanticUnit


ENTITY_TYPES = ("PER", "ORG", "LOC")
CONFIDENCE_LEVELS = frozenset({"high", "medium", "ambiguous"})
NLP_RESULT_STATUSES = frozenset({"completed", "disabled", "unavailable"})
# The filter speaks in semantic categories, the model in tags.
ENTITY_CATEGORY_BY_TYPE = {
    "PER": "person",
    "ORG": "organization",
    "LOC": "location",
}


class RussianNlpError(RuntimeError):
    """Base error for the optional Russian NLP path."""


class RussianNlpUnavailable(RussianNlpError):
    """Raised when the analyzer is switched on but cannot run right now."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ProtectedEntity:
    """One name the repair paths must not rewrite, with its exact range."""

    unit_id: str
    block_id: str
    start: int
    end: int
    entity_type: Literal["PER", "ORG", "LOC"]
    text: str

    def __post_init__(self) -> None:
        _require_identity(self)
        if self.entity_type not in ENTITY_TYPES:
            raise QaModelValidationError("unsupported entity type")
        if not isinstance(self.text, str) or not self.text.strip():
            raise QaModelValidationError("entity text must be a nonempty string")

    def as_hint(self) -> ProtectedEntityHint:
        """Return the protection hint the foreign-text filter understands."""
        return ProtectedEntityHint(
            text=self.text, category=ENTITY_CATEGORY_BY_TYPE[self.entity_type]
        )


@dataclass(frozen=True, slots=True)
class MorphologyCandidate:
    """One morphological oddity. Evidence only: it can never authorize an edit."""

    unit_id: str
    block_id: str
    start: int
    end: int
    category: str
    confidence: Literal["high", "medium", "ambiguous"] = "ambiguous"
    auto_fix_allowed: Literal[False] = False

    def __post_init__(self) -> None:
        _require_identity(self)
        _require_signal(self)


@dataclass(frozen=True, slots=True)
class SyntaxCandidate:
    """One syntactic oddity, named by the tokens it covers. Evidence only."""

    unit_id: str
    block_id: str
    token_ids: tuple[int, ...]
    category: str
    text: str = ""
    confidence: Literal["high", "medium", "ambiguous"] = "ambiguous"
    auto_fix_allowed: Literal[False] = False

    def __post_init__(self) -> None:
        for field_name in ("unit_id", "block_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise QaModelValidationError(f"{field_name} must be a nonempty string")
        if not isinstance(self.token_ids, tuple) or not self.token_ids:
            raise QaModelValidationError("token_ids must be a nonempty tuple")
        if any(
            isinstance(item, bool) or not isinstance(item, int) or item < 0
            for item in self.token_ids
        ):
            raise QaModelValidationError("token_ids must be non-negative integers")
        _require_signal(self)


@dataclass(frozen=True, slots=True)
class RussianNlpReport:
    """Everything the local analyzer saw, in this project's own vocabulary."""

    protected_entities: tuple[ProtectedEntity, ...] = ()
    morphology_candidates: tuple[MorphologyCandidate, ...] = ()
    syntax_candidates: tuple[SyntaxCandidate, ...] = ()
    model_versions: Mapping[str, str] = field(default_factory=dict)

    def as_analysis(self):
        """Return the unconfirmed-hint shape the language diagnosis consumes."""
        from ..language_validation import (
            NamedEntitySpan,
            RussianNlpAnalysis,
            SyntaxCandidate as SyntaxHint,
        )

        return RussianNlpAnalysis(
            entities=tuple(
                NamedEntitySpan(entity.block_id, entity.text, entity.entity_type)
                for entity in self.protected_entities
            ),
            syntax_candidates=tuple(
                SyntaxHint(candidate.block_id, candidate.text, candidate.category)
                for candidate in self.syntax_candidates
                if candidate.text.strip()
            ),
        )

    def protection_hints(self) -> tuple[ProtectedEntityHint, ...]:
        """Return every name the foreign-text filter must treat as protected."""
        return tuple(
            dict.fromkeys(entity.as_hint() for entity in self.protected_entities)
        )


@dataclass(frozen=True, slots=True)
class RussianNlpResult:
    """The outcome of one local analysis, including why it produced nothing."""

    status: Literal["completed", "disabled", "unavailable"]
    analysis: RussianNlpReport | None = None
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in NLP_RESULT_STATUSES:
            raise QaModelValidationError("unsupported Russian NLP status")
        if self.status != "completed" and self.analysis is not None:
            raise QaModelValidationError("only a completed analysis may carry a report")


class RussianNlpProvider(Protocol):
    def analyze(self, units: Sequence[SemanticUnit]) -> RussianNlpReport:
        """Return local evidence for one chapter, or raise RussianNlpUnavailable."""


def _require_identity(value) -> None:
    for field_name in ("unit_id", "block_id"):
        item = getattr(value, field_name)
        if not isinstance(item, str) or not item.strip():
            raise QaModelValidationError(f"{field_name} must be a nonempty string")
    for field_name in ("start", "end"):
        item = getattr(value, field_name)
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise QaModelValidationError(f"{field_name} must be a non-negative integer")
    if value.start >= value.end:
        raise QaModelValidationError("range must be nonempty and ordered")


def _require_signal(value) -> None:
    if not isinstance(value.category, str) or not value.category.strip():
        raise QaModelValidationError("category must be a nonempty string")
    if value.confidence not in CONFIDENCE_LEVELS:
        raise QaModelValidationError("unsupported confidence level")
    if value.auto_fix_allowed is not False:
        raise QaModelValidationError(
            "local NLP candidates may never be auto-fixable on their own"
        )
