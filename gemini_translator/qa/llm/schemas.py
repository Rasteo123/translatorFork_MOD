"""Immutable, fail-closed schemas for structured translation QA responses."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from types import MappingProxyType
from typing import Literal, Mapping

from .json_response import QaResponseSchemaError


OmissionDecision = Literal[
    "missing_content", "covered", "intentional_foreign", "ambiguous"
]
LanguageIssueCategory = Literal[
    "typo",
    "grammar",
    "punctuation",
    "calque",
    "repetition",
    "meta_comment",
    "hallucinated_addition",
    "style_suggestion",
]

_OMISSION_DECISIONS = {
    "missing_content",
    "covered",
    "intentional_foreign",
    "ambiguous",
}
_ADDITION_DECISIONS = {
    "hallucinated_addition",
    "entailed",
    "paraphrase",
    "ambiguous",
}
_LANGUAGE_ISSUE_CATEGORIES = {
    "typo",
    "grammar",
    "punctuation",
    "calque",
    "repetition",
    "meta_comment",
    "hallucinated_addition",
    "style_suggestion",
}


def _empty_metadata() -> Mapping[str, object]:
    return MappingProxyType({})


def _nonempty_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise QaResponseSchemaError(f"{field_name} must be a nonempty string")
    return value


def _identifier(value: object, field_name: str) -> str:
    identifier = _nonempty_string(value, field_name)
    if identifier != identifier.strip():
        raise QaResponseSchemaError(
            f"{field_name} must not have surrounding whitespace"
        )
    return identifier


def _confidence(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise QaResponseSchemaError("confidence must be a finite number")
    try:
        normalized = float(value)
    except (OverflowError, ValueError):
        raise QaResponseSchemaError("confidence must be a finite number") from None
    if not math.isfinite(normalized) or not 0.0 <= normalized <= 1.0:
        raise QaResponseSchemaError("confidence must be between 0 and 1")
    return normalized


def _string_tuple(
    value: object,
    field_name: str,
    *,
    allow_empty: bool,
    identifiers: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise QaResponseSchemaError(f"{field_name} must be an array of strings")
    item_validator = _identifier if identifiers else _nonempty_string
    result = tuple(item_validator(item, field_name) for item in value)
    if not allow_empty and not result:
        raise QaResponseSchemaError(f"{field_name} must be nonempty")
    if len(set(result)) != len(result):
        raise QaResponseSchemaError(f"{field_name} must not contain duplicates")
    return result


def _freeze_json_value(value: object, field_name: str) -> object:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            normalized = float(value)
        except (OverflowError, ValueError):
            raise QaResponseSchemaError(f"{field_name} numbers must be finite") from None
        if not math.isfinite(normalized):
            raise QaResponseSchemaError(f"{field_name} numbers must be finite")
        return value
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json_value(item, field_name) for item in value)
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            normalized_key = _nonempty_string(key, f"{field_name} key")
            if normalized_key in frozen:
                raise QaResponseSchemaError(f"{field_name} keys must be unique")
            frozen[normalized_key] = _freeze_json_value(item, field_name)
        return MappingProxyType(frozen)
    raise QaResponseSchemaError(f"{field_name} must contain only JSON-compatible values")


def _metadata(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise QaResponseSchemaError("metadata must be an object")
    frozen = _freeze_json_value(value, "metadata")
    if not isinstance(frozen, Mapping):  # pragma: no cover - guarded above
        raise QaResponseSchemaError("metadata must be an object")
    return frozen


def _checked_payload(
    payload: object,
    *,
    required: frozenset[str],
) -> Mapping[str, object]:
    if not isinstance(payload, Mapping):
        raise QaResponseSchemaError("schema payload must be an object")
    keys = set(payload)
    if any(not isinstance(key, str) for key in keys):
        raise QaResponseSchemaError("schema keys must be strings")
    missing = required - keys
    unknown = keys - required - {"metadata"}
    if missing:
        raise QaResponseSchemaError(
            "missing required fields: " + ", ".join(sorted(missing))
        )
    if unknown:
        raise QaResponseSchemaError(
            "unknown fields are forbidden: " + ", ".join(sorted(unknown))
        )
    return payload


@dataclass(frozen=True, slots=True, repr=False)
class OmissionVerdict:
    decision: OmissionDecision
    confidence: float
    source_unit_ids: tuple[str, ...]
    missing_facts: tuple[str, ...]
    explanation: str
    metadata: Mapping[str, object] = field(default_factory=_empty_metadata)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.decision, str)
            or self.decision not in _OMISSION_DECISIONS
        ):
            raise QaResponseSchemaError("decision is not a supported omission verdict")
        object.__setattr__(self, "confidence", _confidence(self.confidence))
        object.__setattr__(
            self,
            "source_unit_ids",
            _string_tuple(
                self.source_unit_ids,
                "source_unit_ids",
                allow_empty=False,
                identifiers=True,
            ),
        )
        facts = _string_tuple(self.missing_facts, "missing_facts", allow_empty=True)
        if self.decision == "missing_content" and not facts:
            raise QaResponseSchemaError(
                "missing_content verdict requires at least one missing fact"
            )
        if self.decision != "missing_content" and facts:
            raise QaResponseSchemaError(
                "only missing_content verdict may contain missing facts"
            )
        object.__setattr__(self, "missing_facts", facts)
        object.__setattr__(
            self, "explanation", _nonempty_string(self.explanation, "explanation")
        )
        object.__setattr__(self, "metadata", _metadata(self.metadata))

    @classmethod
    def from_dict(cls, payload: object) -> "OmissionVerdict":
        data = _checked_payload(
            payload,
            required=frozenset(
                {
                    "decision",
                    "confidence",
                    "source_unit_ids",
                    "missing_facts",
                    "explanation",
                }
            ),
        )
        return cls(
            decision=data["decision"],
            confidence=data["confidence"],
            source_unit_ids=data["source_unit_ids"],
            missing_facts=data["missing_facts"],
            explanation=data["explanation"],
            metadata=data.get("metadata", {}),
        )


@dataclass(frozen=True, slots=True, repr=False)
class RepairProposal:
    candidate_id: str
    translated_fragment: str
    glossary_terms_used: tuple[str, ...]
    metadata: Mapping[str, object] = field(default_factory=_empty_metadata)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "candidate_id", _identifier(self.candidate_id, "candidate_id")
        )
        object.__setattr__(
            self,
            "translated_fragment",
            _nonempty_string(self.translated_fragment, "translated_fragment"),
        )
        object.__setattr__(
            self,
            "glossary_terms_used",
            _string_tuple(
                self.glossary_terms_used,
                "glossary_terms_used",
                allow_empty=True,
                identifiers=True,
            ),
        )
        object.__setattr__(self, "metadata", _metadata(self.metadata))

    @classmethod
    def from_dict(
        cls,
        payload: object,
        *,
        expected_candidate_id: str,
    ) -> "RepairProposal":
        data = _checked_payload(
            payload,
            required=frozenset(
                {"candidate_id", "translated_fragment", "glossary_terms_used"}
            ),
        )
        proposal = cls(
            candidate_id=data["candidate_id"],
            translated_fragment=data["translated_fragment"],
            glossary_terms_used=data["glossary_terms_used"],
            metadata=data.get("metadata", {}),
        )
        expected = _identifier(expected_candidate_id, "expected_candidate_id")
        if proposal.candidate_id != expected:
            raise QaResponseSchemaError(
                "repair proposal candidate_id does not match the request"
            )
        return proposal


@dataclass(frozen=True, slots=True, repr=False)
class AdditionVerdict:
    """Verdict about target-only content, stated in its own terms.

    This is deliberately not an inverted omission verdict: the facts it names
    exist only in the translation, and no field of it can authorize a removal.
    """

    candidate_id: str
    decision: str
    confidence: float
    target_unit_ids: tuple[str, ...]
    added_facts: tuple[str, ...]
    explanation: str
    metadata: Mapping[str, object] = field(default_factory=_empty_metadata)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "candidate_id", _identifier(self.candidate_id, "candidate_id")
        )
        if not isinstance(self.decision, str) or self.decision not in _ADDITION_DECISIONS:
            raise QaResponseSchemaError("decision is not a supported addition verdict")
        object.__setattr__(self, "confidence", _confidence(self.confidence))
        object.__setattr__(
            self,
            "target_unit_ids",
            _string_tuple(
                self.target_unit_ids,
                "target_unit_ids",
                allow_empty=False,
                identifiers=True,
            ),
        )
        facts = _string_tuple(self.added_facts, "added_facts", allow_empty=True)
        if self.decision == "hallucinated_addition" and not facts:
            raise QaResponseSchemaError(
                "a hallucinated addition requires at least one added fact"
            )
        if self.decision != "hallucinated_addition" and facts:
            raise QaResponseSchemaError(
                "only a hallucinated addition may list added facts"
            )
        object.__setattr__(self, "added_facts", facts)
        object.__setattr__(
            self, "explanation", _nonempty_string(self.explanation, "explanation")
        )
        object.__setattr__(self, "metadata", _metadata(self.metadata))

    @classmethod
    def from_dict(
        cls, payload: object, *, expected_candidate_id: str
    ) -> "AdditionVerdict":
        data = _checked_payload(
            payload,
            required=frozenset(
                {
                    "candidate_id",
                    "decision",
                    "confidence",
                    "target_unit_ids",
                    "added_facts",
                    "explanation",
                }
            ),
        )
        verdict = cls(
            candidate_id=data["candidate_id"],
            decision=data["decision"],
            confidence=data["confidence"],
            target_unit_ids=data["target_unit_ids"],
            added_facts=data["added_facts"],
            explanation=data["explanation"],
            metadata=data.get("metadata", {}),
        )
        expected = _identifier(expected_candidate_id, "expected_candidate_id")
        if verdict.candidate_id != expected:
            raise QaResponseSchemaError(
                "addition verdict candidate_id does not match the request"
            )
        return verdict


@dataclass(frozen=True, slots=True, repr=False)
class RepairPostCheck:
    """Model confirmation that one committed-in-memory repair did exactly its job."""

    candidate_id: str
    confirmed: bool
    missing_facts_present: tuple[str, ...]
    added_meaning: bool
    context_rewritten: bool
    explanation: str
    metadata: Mapping[str, object] = field(default_factory=_empty_metadata)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "candidate_id", _identifier(self.candidate_id, "candidate_id")
        )
        for field_name in ("confirmed", "added_meaning", "context_rewritten"):
            if not isinstance(getattr(self, field_name), bool):
                raise QaResponseSchemaError(f"{field_name} must be a boolean")
        object.__setattr__(
            self,
            "missing_facts_present",
            _string_tuple(
                self.missing_facts_present, "missing_facts_present", allow_empty=True
            ),
        )
        object.__setattr__(
            self, "explanation", _nonempty_string(self.explanation, "explanation")
        )
        object.__setattr__(self, "metadata", _metadata(self.metadata))
        if self.confirmed and (
            self.added_meaning
            or self.context_rewritten
            or not self.missing_facts_present
        ):
            raise QaResponseSchemaError(
                "a confirmed repair cannot add meaning, rewrite context, or "
                "confirm nothing"
            )

    @classmethod
    def from_dict(
        cls, payload: object, *, expected_candidate_id: str
    ) -> "RepairPostCheck":
        data = _checked_payload(
            payload,
            required=frozenset(
                {
                    "candidate_id",
                    "confirmed",
                    "missing_facts_present",
                    "added_meaning",
                    "context_rewritten",
                    "explanation",
                }
            ),
        )
        check = cls(
            candidate_id=data["candidate_id"],
            confirmed=data["confirmed"],
            missing_facts_present=data["missing_facts_present"],
            added_meaning=data["added_meaning"],
            context_rewritten=data["context_rewritten"],
            explanation=data["explanation"],
            metadata=data.get("metadata", {}),
        )
        expected = _identifier(expected_candidate_id, "expected_candidate_id")
        if check.candidate_id != expected:
            raise QaResponseSchemaError(
                "repair post-check candidate_id does not match the request"
            )
        return check


@dataclass(frozen=True, slots=True, repr=False)
class LanguageIssue:
    issue_id: str
    category: LanguageIssueCategory
    block_id: str
    original_text: str
    replacement_text: str | None
    objective: bool
    confidence: float
    explanation: str
    metadata: Mapping[str, object] = field(default_factory=_empty_metadata)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "issue_id", _identifier(self.issue_id, "issue_id")
        )
        if (
            not isinstance(self.category, str)
            or self.category not in _LANGUAGE_ISSUE_CATEGORIES
        ):
            raise QaResponseSchemaError("category is not a supported language issue")
        object.__setattr__(
            self, "block_id", _identifier(self.block_id, "block_id")
        )
        object.__setattr__(
            self,
            "original_text",
            _nonempty_string(self.original_text, "original_text"),
        )
        if self.replacement_text is not None:
            object.__setattr__(
                self,
                "replacement_text",
                _nonempty_string(self.replacement_text, "replacement_text"),
            )
        if not isinstance(self.objective, bool):
            raise QaResponseSchemaError("objective must be a boolean")
        if self.category == "style_suggestion" and self.objective:
            raise QaResponseSchemaError("style_suggestion must not be objective")
        object.__setattr__(self, "confidence", _confidence(self.confidence))
        object.__setattr__(
            self, "explanation", _nonempty_string(self.explanation, "explanation")
        )
        object.__setattr__(self, "metadata", _metadata(self.metadata))

    @classmethod
    def from_dict(cls, payload: object) -> "LanguageIssue":
        data = _checked_payload(
            payload,
            required=frozenset(
                {
                    "issue_id",
                    "category",
                    "block_id",
                    "original_text",
                    "replacement_text",
                    "objective",
                    "confidence",
                    "explanation",
                }
            ),
        )
        return cls(
            issue_id=data["issue_id"],
            category=data["category"],
            block_id=data["block_id"],
            original_text=data["original_text"],
            replacement_text=data["replacement_text"],
            objective=data["objective"],
            confidence=data["confidence"],
            explanation=data["explanation"],
            metadata=data.get("metadata", {}),
        )
