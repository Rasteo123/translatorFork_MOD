"""Contracts for optional rule-based checkers, which never edit text themselves."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

from ..models import QaModelValidationError, SemanticUnit


RULE_RESULT_STATUSES = frozenset({"completed", "disabled", "unavailable"})


class LanguageRuleError(RuntimeError):
    """Base error for rule providers."""


class LanguageRuleUnavailable(LanguageRuleError):
    """Raised when a configured rule service cannot answer right now.

    The message is deliberately safe to show: it never carries the endpoint's
    response body, and no text is ever retried against a different service.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class LanguageRuleRequest:
    """One batch of translated units to check, in their own order."""

    units: tuple[SemanticUnit, ...]
    language: str
    disabled_rule_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.units, tuple) or not all(
            isinstance(unit, SemanticUnit) for unit in self.units
        ):
            raise QaModelValidationError("units must be a tuple of SemanticUnit")
        if not isinstance(self.language, str) or not self.language.strip():
            raise QaModelValidationError("language must be a nonempty string")
        object.__setattr__(
            self,
            "disabled_rule_ids",
            tuple(
                dict.fromkeys(
                    str(rule).strip()
                    for rule in self.disabled_rule_ids or ()
                    if str(rule).strip()
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class LanguageRuleMatch:
    """One rule hit, already mapped onto the semantic unit that contains it.

    ``report_only`` marks a hit that could not be tied to a single unit or that
    the server offered no usable replacement for: it may be shown, but it must
    never become an automatic edit.
    """

    rule_id: str
    category: str
    message: str
    unit_id: str
    block_id: str
    unit_start: int
    unit_end: int
    matched_text: str
    replacements: tuple[str, ...] = ()
    report_only: bool = False

    def __post_init__(self) -> None:
        for field_name in ("rule_id", "category", "message", "unit_id", "block_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise QaModelValidationError(f"{field_name} must be a nonempty string")
        for field_name in ("unit_start", "unit_end"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise QaModelValidationError(f"{field_name} must be a non-negative integer")
        if self.unit_start >= self.unit_end:
            raise QaModelValidationError("rule match range must be nonempty and ordered")
        if not isinstance(self.matched_text, str) or not self.matched_text:
            raise QaModelValidationError("matched_text must be a nonempty string")
        object.__setattr__(
            self,
            "replacements",
            tuple(
                dict.fromkeys(
                    str(value)
                    for value in self.replacements or ()
                    if isinstance(value, str) and value.strip()
                )
            ),
        )
        if not isinstance(self.report_only, bool):
            raise QaModelValidationError("report_only must be a boolean")

    def as_hint(self):
        """Return the unconfirmed hint shape the diagnosis request understands."""
        from ..language_validation import LanguageRuleIssue

        return LanguageRuleIssue(
            block_id=self.block_id,
            rule_id=self.rule_id,
            message=self.message,
            original_text=self.matched_text,
            replacements=() if self.report_only else self.replacements,
        )


@dataclass(frozen=True, slots=True)
class LanguageRuleResult:
    """The outcome of one rule pass, including why it produced nothing."""

    status: Literal["completed", "disabled", "unavailable"]
    issues: tuple[LanguageRuleMatch, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.status not in RULE_RESULT_STATUSES:
            raise QaModelValidationError("unsupported language rule status")
        if self.status != "completed" and self.issues:
            raise QaModelValidationError(
                "only a completed rule pass may carry issues"
            )

    def hints(self):
        """Return every match in the shape the diagnosis prompt consumes."""
        return tuple(issue.as_hint() for issue in self.issues)


class LanguageRuleProvider(Protocol):
    async def check(
        self, request: LanguageRuleRequest
    ) -> tuple[LanguageRuleMatch, ...]:
        """Return every rule hit for one batch, or raise LanguageRuleUnavailable."""


def language_tool_code(language: str) -> str:
    """Map a QA language onto the code the rule service expects."""
    base = str(language or "").strip().lower().replace("_", "-")
    if not base:
        return "ru-RU"
    if "-" in base:
        head, tail = base.split("-", 1)
        return f"{head}-{tail.upper()}"
    return {"ru": "ru-RU", "en": "en-US", "de": "de-DE", "fr": "fr-FR"}.get(base, base)


def batch_text(units: Sequence[SemanticUnit], separator: str = "\n\n") -> tuple[str, tuple[tuple[int, int, SemanticUnit], ...]]:
    """Join units into one request body and remember where each one landed."""
    pieces: list[str] = []
    spans: list[tuple[int, int, SemanticUnit]] = []
    offset = 0
    for index, unit in enumerate(units):
        if index:
            offset += len(separator)
        pieces.append(unit.text)
        spans.append((offset, offset + len(unit.text), unit))
        offset += len(unit.text)
    return separator.join(pieces), tuple(spans)
