"""Detect content the translation added, by reading the alignment backwards."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field

from .coverage_service import SEMANTIC_ALIGNMENT_MODE, CoverageAnalysis
from .llm.completion import CancellationToken, QaCompletionClient, QaModelSelection
from .llm.json_response import QaResponseSchemaError
from .llm.prompts import (
    PromptConfigurationError,
    escaped,
    load_prompt_template,
    render_prompt,
)
from .llm.schemas import AdditionVerdict
from .models import (
    CandidateContext,
    GapCandidate,
    OmissionRepairerConfig,
    QaModelValidationError,
    SemanticUnit,
)
from .text_normalize import normalize_for_comparison


MINIMUM_ADDITION_CHARS = 12
NON_NARRATIVE_KINDS = frozenset(
    {
        "heading",
        "list",
        "list_item",
        "table_cell",
        "caption",
        "note_ref",
        "link",
        "media",
        "definition_item",
    }
)
_STATUSES = frozenset(
    {
        "verified",
        "filtered",
        "configuration_failed",
        "invalid_response",
        "completion_timeout",
        "completion_failed",
    }
)


DETECTION_PURPOSE = "addition_detection"


@dataclass(frozen=True, slots=True)
class ChapterContext:
    """Routing and policy for one chapter-level addition sweep."""

    chapter_id: str
    model: QaModelSelection
    cancellation: CancellationToken
    high_confidence: float = 0.90
    minimum_addition_chars: int = MINIMUM_ADDITION_CHARS

    def __post_init__(self) -> None:
        if not isinstance(self.chapter_id, str) or not self.chapter_id.strip():
            raise QaModelValidationError("chapter_id must be a nonempty string")
        if not isinstance(self.model, QaModelSelection):
            raise QaModelValidationError("model must be a QaModelSelection")
        if not callable(getattr(self.cancellation, "raise_if_cancelled", None)):
            raise QaModelValidationError(
                "cancellation must implement raise_if_cancelled"
            )
        if (
            isinstance(self.high_confidence, bool)
            or not isinstance(self.high_confidence, (int, float))
            or not 0.0 <= self.high_confidence <= 1.0
        ):
            raise QaModelValidationError("high_confidence must be between 0 and 1")
        if (
            isinstance(self.minimum_addition_chars, bool)
            or not isinstance(self.minimum_addition_chars, int)
            or self.minimum_addition_chars < 1
        ):
            raise QaModelValidationError("minimum_addition_chars must be positive")


@dataclass(frozen=True, slots=True)
class AdditionCandidate:
    """One target-only fragment and what is known about it. Never a removal."""

    candidate_id: str
    candidate: GapCandidate
    context: CandidateContext
    verdict: AdditionVerdict | None
    status: str
    warnings: tuple[str, ...] = field(default_factory=tuple)
    blocks_gate: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, GapCandidate):
            raise QaModelValidationError("candidate must be a GapCandidate")
        if self.candidate.side != "target":
            raise QaModelValidationError("addition candidates are target-side gaps")
        if self.candidate_id != self.candidate.candidate_id:
            raise QaModelValidationError("addition candidate identity must match")
        if not isinstance(self.context, CandidateContext):
            raise QaModelValidationError("context must be a CandidateContext")
        if self.status not in _STATUSES:
            raise QaModelValidationError("unsupported addition detector status")
        if self.status != "verified" and self.verdict is not None:
            raise QaModelValidationError("only a verified status may carry a verdict")
        if self.status != "verified" and not self.warnings:
            raise QaModelValidationError(
                "non-verified addition statuses must record a warning"
            )
        if self.blocks_gate and (
            self.status != "verified"
            or self.verdict is None
            or self.verdict.decision != "hallucinated_addition"
        ):
            raise QaModelValidationError(
                "only a confirmed hallucinated addition may block the gate"
            )


class AdditionDetector:
    """Ask about target-only content only where the local evidence justifies it.

    Cheap local rules answer first: an addition without two anchors, inside a
    heading or list, or shorter than a clause is a translation decision, not an
    invented fact, and never costs a request. Nothing here can delete text.
    """

    def __init__(
        self,
        client: QaCompletionClient,
        config: OmissionRepairerConfig | None = None,
    ) -> None:
        if not callable(getattr(client, "complete_json", None)):
            raise TypeError("client must implement complete_json")
        self._client = client
        self._config = config or OmissionRepairerConfig(
            max_output_tokens=700, prompt_version="addition_detector_v1"
        )

    async def detect(
        self, coverage: CoverageAnalysis, context: ChapterContext
    ) -> tuple[AdditionCandidate, ...]:
        """Return one result per target-side gap, in alignment order."""
        if not isinstance(coverage, CoverageAnalysis):
            raise TypeError("coverage must be a CoverageAnalysis")
        if not isinstance(context, ChapterContext):
            raise TypeError("context must be a ChapterContext")
        if coverage.mode != SEMANTIC_ALIGNMENT_MODE or coverage.alignment is None:
            return ()

        units = {unit.unit_id: unit for unit in coverage.target_units}
        results: list[AdditionCandidate] = []
        for gap in coverage.alignment.gaps:
            if gap.side != "target":
                continue
            candidate_context = coverage.contexts.get(gap.candidate_id)
            if not isinstance(candidate_context, CandidateContext):
                results.append(
                    _filtered(gap, _empty_context(gap), "addition_context_missing")
                )
                continue
            warning = _local_refusal(gap, candidate_context, units, context)
            if warning is not None:
                results.append(_filtered(gap, candidate_context, warning))
                continue
            results.append(
                await self._verify(gap, candidate_context, coverage, context)
            )
        return tuple(results)

    async def _verify(
        self,
        gap: GapCandidate,
        candidate_context: CandidateContext,
        coverage: CoverageAnalysis,
        context: ChapterContext,
    ) -> AdditionCandidate:
        context.cancellation.raise_if_cancelled()
        try:
            template = load_prompt_template(
                self._config.prompt_path, self._config.prompt_version
            )
        except PromptConfigurationError:
            return _failed(
                gap, candidate_context, "configuration_failed",
                "prompt_configuration_unavailable",
            )

        prompt = render_prompt(template, _prompt_lines(gap, candidate_context))
        try:
            payload = await self._client.complete_json(
                prompt,
                model=context.model,
                max_output_tokens=self._config.max_output_tokens,
                cancellation=context.cancellation,
                purpose=DETECTION_PURPOSE,
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return _failed(
                gap, candidate_context, "completion_timeout", "completion_timeout"
            )
        except QaResponseSchemaError:
            return _failed(
                gap, candidate_context, "invalid_response", "invalid_response"
            )
        except Exception:  # noqa: BLE001 - any transport failure stays report-only
            return _failed(
                gap, candidate_context, "completion_failed", "completion_failed"
            )

        try:
            if not isinstance(payload, Mapping):
                raise QaResponseSchemaError("addition result must be an object")
            verdict = AdditionVerdict.from_dict(
                payload, expected_candidate_id=gap.candidate_id
            )
        except (QaResponseSchemaError, TypeError, ValueError):
            return _failed(
                gap, candidate_context, "invalid_response", "invalid_response"
            )

        if not set(verdict.target_unit_ids).issubset(gap.target_unit_ids):
            return _failed(
                gap,
                candidate_context,
                "invalid_response",
                "verdict_target_unit_ids_mismatch",
            )
        blocks_gate = (
            verdict.decision == "hallucinated_addition"
            and verdict.confidence >= context.high_confidence
            and bool(verdict.added_facts)
        )
        return AdditionCandidate(
            candidate_id=gap.candidate_id,
            candidate=gap,
            context=candidate_context,
            verdict=verdict,
            status="verified",
            warnings=(),
            blocks_gate=blocks_gate,
        )


def _local_refusal(
    gap: GapCandidate,
    context: CandidateContext,
    units: Mapping[str, SemanticUnit],
    chapter: ChapterContext,
) -> str | None:
    if gap.signals != ("addition",):
        return "addition_signal_missing"
    if gap.left_anchor is None or gap.right_anchor is None:
        return "addition_anchors_missing"
    kinds = {
        units[unit_id].kind
        for unit_id in gap.target_unit_ids
        if unit_id in units
    }
    if kinds & NON_NARRATIVE_KINDS:
        return "addition_non_narrative_block"
    if len(normalize_for_comparison(context.target_text)) < chapter.minimum_addition_chars:
        return "addition_below_minimum_size"
    return None


def _filtered(
    gap: GapCandidate, context: CandidateContext, warning: str
) -> AdditionCandidate:
    return AdditionCandidate(
        candidate_id=gap.candidate_id,
        candidate=gap,
        context=context,
        verdict=None,
        status="filtered",
        warnings=(warning,),
    )


def _failed(
    gap: GapCandidate, context: CandidateContext, status: str, warning: str
) -> AdditionCandidate:
    return AdditionCandidate(
        candidate_id=gap.candidate_id,
        candidate=gap,
        context=context,
        verdict=None,
        status=status,
        warnings=(warning,),
    )


def _empty_context(gap: GapCandidate) -> CandidateContext:
    return CandidateContext(
        candidate_id=gap.candidate_id,
        source_text="",
        target_text="",
        source_before="",
        source_after="",
        target_before="",
        target_after="",
        source_language="unknown",
        target_language="unknown",
        candidate_language="unknown",
    )


def _prompt_lines(gap: GapCandidate, context: CandidateContext) -> list[str]:
    return [
        f"candidate_id: {escaped(gap.candidate_id)}",
        f"target_unit_ids: {', '.join(escaped(value) for value in gap.target_unit_ids)}",
        f"source_language: {escaped(context.source_language)}",
        f"target_language: {escaped(context.target_language)}",
        f"target_only_fragment: {escaped(context.target_text)}",
        f"left_target_anchor: {escaped(context.target_before)}",
        f"right_target_anchor: {escaped(context.target_after)}",
        f"left_source_context: {escaped(context.source_before)}",
        f"right_source_context: {escaped(context.source_after)}",
    ]
