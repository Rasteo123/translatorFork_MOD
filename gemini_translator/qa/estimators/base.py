"""Contracts for an optional, reference-free translation quality estimate.

A quality estimator answers one narrow question — how confident a model is in a
translation it can see next to its source — and nothing else.  It never sees a
reference translation, never decides risk, and never authorizes a repair: its
score is one more piece of evidence next to the semantic alignment and the LLM
verdict, which keep the final say.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import math
from types import MappingProxyType
from typing import Literal, Protocol


ESTIMATE_STATES = frozenset({"completed", "disabled", "unavailable"})

# COMETKiwi and every other reference-free estimator this contract admits report
# a probability-like score; anything outside says the runner is misconfigured.
MINIMUM_SCORE = 0.0
MAXIMUM_SCORE = 1.0


class QualityEstimateError(ValueError):
    """Raised when an estimate request or answer breaks this contract."""


def _nonempty(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise QualityEstimateError(f"{field_name} must be a nonempty string")
    return value


def _score(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise QualityEstimateError(f"{field_name} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise QualityEstimateError(f"{field_name} must be finite")
    if not MINIMUM_SCORE <= number <= MAXIMUM_SCORE:
        raise QualityEstimateError(f"{field_name} must be between 0 and 1")
    return number


@dataclass(frozen=True, slots=True)
class SourceTranslationWindow:
    """One source fragment and the translation that is supposed to carry it."""

    window_id: str
    source: str
    translation: str
    visible_chars: int

    def __post_init__(self) -> None:
        _nonempty(self.window_id, "window_id")
        _nonempty(self.source, "source")
        _nonempty(self.translation, "translation")
        if isinstance(self.visible_chars, bool) or not isinstance(
            self.visible_chars, int
        ):
            raise QualityEstimateError("visible_chars must be an integer")
        if self.visible_chars <= 0:
            raise QualityEstimateError("visible_chars must be positive")


@dataclass(frozen=True, slots=True)
class QualityEstimateRequest:
    """Everything an estimator may see: source, translation, and the languages."""

    chapter_id: str
    windows: tuple[SourceTranslationWindow, ...]
    source_language: str
    target_language: str

    def __post_init__(self) -> None:
        _nonempty(self.chapter_id, "chapter_id")
        _nonempty(self.source_language, "source_language")
        _nonempty(self.target_language, "target_language")
        if not isinstance(self.windows, tuple) or not self.windows:
            raise QualityEstimateError("windows must be a nonempty tuple")
        seen: set[str] = set()
        for window in self.windows:
            if not isinstance(window, SourceTranslationWindow):
                raise QualityEstimateError(
                    "windows entries must be SourceTranslationWindow"
                )
            if window.window_id in seen:
                raise QualityEstimateError("window ids must be unique")
            seen.add(window.window_id)


@dataclass(frozen=True, slots=True)
class QualityEstimate:
    """One estimator's answer, including the answer "I did not run"."""

    estimator: str
    model: str
    window_scores: tuple[float, ...] = ()
    chapter_score: float | None = None
    minimum_score: float | None = None
    p10_score: float | None = None
    status: Literal["completed", "disabled", "unavailable"] = "unavailable"
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _nonempty(self.estimator, "estimator")
        if not isinstance(self.model, str):
            raise QualityEstimateError("model must be a string")
        if self.status not in ESTIMATE_STATES:
            raise QualityEstimateError("unsupported estimate status")
        if not isinstance(self.window_scores, tuple):
            raise QualityEstimateError("window_scores must be a tuple")
        for value in self.window_scores:
            _score(value, "window score")
        for name in ("chapter_score", "minimum_score", "p10_score"):
            value = getattr(self, name)
            if value is not None:
                _score(value, name)
        if self.status != "completed" and (
            self.window_scores
            or self.chapter_score is not None
            or self.minimum_score is not None
            or self.p10_score is not None
        ):
            raise QualityEstimateError("only a completed estimate may carry scores")
        if self.status == "completed" and not self.window_scores:
            raise QualityEstimateError("a completed estimate must carry scores")
        metadata = self.metadata
        if not isinstance(metadata, Mapping):
            raise QualityEstimateError("metadata must be a mapping")
        frozen: dict[str, str] = {}
        for key, value in metadata.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise QualityEstimateError("metadata must map strings to strings")
            frozen[key] = value
        object.__setattr__(self, "metadata", MappingProxyType(frozen))


class TranslationQualityEstimator(Protocol):
    """The only thing the coordinator may ask of an estimator."""

    async def estimate(
        self,
        request: QualityEstimateRequest,
        cancellation,
    ) -> QualityEstimate:
        raise NotImplementedError


def length_weighted_mean(
    scores: Sequence[float], weights: Sequence[int]
) -> float:
    """Weight each window by the text it covers, so a one-line window cannot rule.

    Equal inputs always produce the same output: the sum is taken in the order
    the windows were sent, never over a set or a dict.
    """
    if len(scores) != len(weights):
        raise QualityEstimateError("scores and weights must be the same length")
    if not scores:
        raise QualityEstimateError("an aggregate needs at least one score")
    total_weight = 0
    total = 0.0
    for score, weight in zip(scores, weights, strict=True):
        if isinstance(weight, bool) or not isinstance(weight, int) or weight <= 0:
            raise QualityEstimateError("weights must be positive integers")
        total += float(score) * weight
        total_weight += weight
    return total / total_weight


def percentile_score(scores: Sequence[float], fraction: float) -> float:
    """Nearest-rank percentile: deterministic, and defined for a single score."""
    if not scores:
        raise QualityEstimateError("a percentile needs at least one score")
    if not 0.0 < fraction <= 1.0:
        raise QualityEstimateError("fraction must be within (0, 1]")
    ordered = sorted(float(value) for value in scores)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[rank - 1]


def aggregate(
    estimator: str,
    model: str,
    request: QualityEstimateRequest,
    scores: Sequence[float],
    metadata: Mapping[str, str] | None = None,
) -> QualityEstimate:
    """Turn one score per window into the chapter-level evidence that is stored."""
    if len(scores) != len(request.windows):
        raise QualityEstimateError("an estimate needs exactly one score per window")
    validated = tuple(_score(value, "window score") for value in scores)
    weights = tuple(window.visible_chars for window in request.windows)
    return QualityEstimate(
        estimator=estimator,
        model=model,
        window_scores=validated,
        chapter_score=length_weighted_mean(validated, weights),
        minimum_score=min(validated),
        p10_score=percentile_score(validated, 0.10),
        status="completed",
        metadata=dict(metadata or {}),
    )


def unavailable(
    estimator: str, model: str, reason: str, **metadata: str
) -> QualityEstimate:
    """The answer for every failure: no score, one short machine-readable reason."""
    return QualityEstimate(
        estimator=estimator,
        model=model,
        status="unavailable",
        metadata={"reason": reason, **metadata},
    )


def disabled(estimator: str, model: str, reason: str) -> QualityEstimate:
    """The answer when the user has not asked for this estimator at all."""
    return QualityEstimate(
        estimator=estimator,
        model=model,
        status="disabled",
        metadata={"reason": reason},
    )
