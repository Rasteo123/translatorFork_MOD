"""Conservative LLM verification for source-side semantic omission candidates."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from html import escape
from importlib import resources
import json
from pathlib import Path
from uuid import uuid4

from ..foreign_text_filter import ForeignTextFilter
from ..models import (
    CandidateContext,
    GapCandidate,
    GlossaryRule,
    OmissionVerifierConfig,
    QaModelValidationError,
    RelevantGlossaryTerm,
    VerifiedCandidate,
)
from .completion import CancellationToken, QaCompletionClient, QaModelSelection
from .json_response import QaResponseSchemaError
from .schemas import OmissionVerdict


_PROMPT_FILENAME = "translation_qa_prompts.json"
_TAG_MARKER = "__QA_DATA_TAG__"
_PAYLOAD_MARKER = "__QA_DATA_PAYLOAD__"


class _PromptConfigurationError(Exception):
    """Internal sanitized signal for unavailable verifier prompt configuration."""


class OmissionVerifier:
    """Verify one bounded candidate without granting authority on weak evidence."""

    def __init__(
        self,
        client: QaCompletionClient,
        config: OmissionVerifierConfig | None = None,
    ) -> None:
        if not callable(getattr(client, "complete_json", None)):
            raise TypeError("client must implement complete_json")
        if config is not None and not isinstance(config, OmissionVerifierConfig):
            raise TypeError("config must be an OmissionVerifierConfig")
        self._client = client
        self._config = config or OmissionVerifierConfig()

    async def verify(
        self,
        candidate: GapCandidate,
        context: CandidateContext,
        glossary: Sequence[RelevantGlossaryTerm],
        model: QaModelSelection,
        cancellation: CancellationToken,
    ) -> VerifiedCandidate:
        self._validate_request(candidate, context, glossary, model, cancellation)
        cancellation.raise_if_cancelled()

        glossary_rules = tuple(
            GlossaryRule(term.original_term, term.policy) for term in glossary
        )
        filter_decision = ForeignTextFilter(glossary_rules).classify(candidate, context)
        if filter_decision.action != "send_to_llm_verifier":
            return VerifiedCandidate(
                candidate=candidate,
                context=context,
                verdict=None,
                foreign_text_decision=filter_decision,
                eligible_for_repair=False,
                status="filtered",
                warnings=("foreign_text_filtered",),
            )

        try:
            template = _load_prompt_template(self._config)
            prompt = _build_prompt(template, candidate, context, glossary)
        except _PromptConfigurationError:
            return VerifiedCandidate(
                candidate=candidate,
                context=context,
                verdict=None,
                foreign_text_decision=filter_decision,
                eligible_for_repair=False,
                status="configuration_failed",
                warnings=("prompt_configuration_unavailable",),
            )

        cancellation.raise_if_cancelled()
        try:
            payload = await self._client.complete_json(
                prompt,
                model=model,
                max_output_tokens=self._config.max_output_tokens,
                cancellation=cancellation,
            )
        except asyncio.CancelledError:
            raise
        except QaResponseSchemaError:
            return self._failure_result(
                candidate, context, filter_decision, "invalid_response"
            )
        except TimeoutError:
            return self._failure_result(
                candidate, context, filter_decision, "completion_timeout"
            )
        except Exception:
            return self._failure_result(
                candidate, context, filter_decision, "completion_failed"
            )

        try:
            if not isinstance(payload, Mapping):
                raise QaResponseSchemaError("completion result must be an object")
            verdict = OmissionVerdict.from_dict(payload)
        except (QaResponseSchemaError, TypeError, ValueError):
            return self._failure_result(
                candidate, context, filter_decision, "invalid_response"
            )

        ids_match = set(verdict.source_unit_ids).issubset(candidate.source_unit_ids)
        if not ids_match:
            return VerifiedCandidate(
                candidate=candidate,
                context=context,
                verdict=verdict,
                foreign_text_decision=filter_decision,
                eligible_for_repair=False,
                status="identity_mismatch",
                warnings=("verdict_source_unit_ids_mismatch",),
            )

        eligible = (
            candidate.repairable
            and candidate.side == "source"
            and candidate.left_anchor is not None
            and candidate.right_anchor is not None
            and candidate.signals == ("missing_in_target",)
            and filter_decision.action == "send_to_llm_verifier"
            and verdict.decision == "missing_content"
            and verdict.confidence >= self._config.high_confidence
            and bool(verdict.missing_facts)
            and ids_match
        )
        return VerifiedCandidate(
            candidate=candidate,
            context=context,
            verdict=verdict,
            foreign_text_decision=filter_decision,
            eligible_for_repair=eligible,
            status="verified",
        )

    @staticmethod
    def _validate_request(
        candidate: GapCandidate,
        context: CandidateContext,
        glossary: Sequence[RelevantGlossaryTerm],
        model: QaModelSelection,
        cancellation: CancellationToken,
    ) -> None:
        if not isinstance(candidate, GapCandidate):
            raise TypeError("candidate must be a GapCandidate")
        if not isinstance(context, CandidateContext):
            raise TypeError("context must be a CandidateContext")
        if context.candidate_id != candidate.candidate_id:
            raise QaModelValidationError("candidate and context identities must match")
        if isinstance(glossary, (str, bytes)) or not isinstance(glossary, Sequence):
            raise TypeError("glossary must be a sequence")
        if not all(isinstance(term, RelevantGlossaryTerm) for term in glossary):
            raise TypeError("glossary entries must be RelevantGlossaryTerm")
        if not isinstance(model, QaModelSelection):
            raise TypeError("model must be a QaModelSelection")
        if not callable(getattr(cancellation, "raise_if_cancelled", None)):
            raise TypeError("cancellation must implement raise_if_cancelled")

    @staticmethod
    def _failure_result(
        candidate: GapCandidate,
        context: CandidateContext,
        filter_decision,
        status: str,
    ) -> VerifiedCandidate:
        return VerifiedCandidate(
            candidate=candidate,
            context=context,
            verdict=None,
            foreign_text_decision=filter_decision,
            eligible_for_repair=False,
            status=status,
            warnings=(status,),
        )


def _load_prompt_template(config: OmissionVerifierConfig) -> str:
    try:
        if config.prompt_path is None:
            resource = resources.files("gemini_translator").joinpath(
                "config", _PROMPT_FILENAME
            )
            raw_config = resource.read_text(encoding="utf-8")
        else:
            raw_config = Path(config.prompt_path).read_text(encoding="utf-8")
        payload = json.loads(raw_config)
        if not isinstance(payload, Mapping):
            raise _PromptConfigurationError
        template = payload.get(config.prompt_version)
        if not isinstance(template, str) or not template.strip():
            raise _PromptConfigurationError
        if template.count(_TAG_MARKER) != 2 or template.count(_PAYLOAD_MARKER) != 1:
            raise _PromptConfigurationError
        return template
    except _PromptConfigurationError:
        raise
    except (
        OSError,
        UnicodeError,
        RecursionError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ):
        raise _PromptConfigurationError from None


def _build_prompt(
    template: str,
    candidate: GapCandidate,
    context: CandidateContext,
    glossary: Sequence[RelevantGlossaryTerm],
) -> str:
    data_tag = f"qa_data_{uuid4().hex}"
    source_ids = ", ".join(_escaped(value) for value in candidate.source_unit_ids)
    signals = ", ".join(_escaped(value) for value in candidate.signals)
    lines = [
        f"candidate_id: {_escaped(candidate.candidate_id)}",
        f"side: {_escaped(candidate.side)}",
        f"source_unit_ids: {source_ids}",
        f"signals: {signals}",
        f"source_language: {_escaped(context.source_language)}",
        f"target_language: {_escaped(context.target_language)}",
        f"candidate_language: {_escaped(context.candidate_language)}",
        f"source_gap: {_escaped(context.source_text)}",
        f"target_local_text: {_escaped(context.target_text)}",
        f"left_source_anchor: {_escaped(context.source_before)}",
        f"right_source_anchor: {_escaped(context.source_after)}",
        f"left_target_anchor: {_escaped(context.target_before)}",
        f"right_target_anchor: {_escaped(context.target_after)}",
        "relevant_glossary:",
    ]
    if glossary:
        lines.extend(
            "- "
            + _escaped(term.original_term)
            + " → "
            + _escaped(term.canonical_translation)
            + f" | policy={term.policy.value}"
            + f" | occurrences={term.occurrences}"
            + f" | priority={term.priority}"
            for term in glossary
        )
    else:
        lines.append("- none supplied")
    data_payload = "\n".join(lines)
    return template.replace(_TAG_MARKER, data_tag).replace(
        _PAYLOAD_MARKER, data_payload
    )


def _escaped(value: str) -> str:
    return escape(value, quote=True)
