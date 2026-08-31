"""One bounded, glossary-aware translation of a single confirmed missing fragment."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
import re
import unicodedata

from ..glossary_audit import glossary_violation_reason
from ..models import (
    OmissionRepairerConfig,
    QaModelValidationError,
    RelevantGlossaryTerm,
    VerifiedCandidate,
)
from .completion import CancellationToken, QaCompletionClient, QaModelSelection
from .json_response import QaResponseSchemaError
from .prompts import PromptConfigurationError, escaped, load_prompt_template, render_prompt
from .schemas import RepairProposal


_MIN_ANCHOR_ECHO_CHARS = 12
_MIN_SOURCE_ECHO_CHARS = 8
# A window is longer than an anchor and holds ordinary prose, so a short
# coincidence is a real possibility: only a substantial verbatim run counts as
# copying the neighbourhood instead of translating the gap.
_MIN_WINDOW_ECHO_CHARS = 40
_HTML_DOCUMENT_RE = re.compile(r"<!doctype\s|</?html[\s>]|</?body[\s>]", re.IGNORECASE)
_FENCE_RE = re.compile(r"```|~~~")


class OmissionRepairError(RuntimeError):
    """Typed, sanitized reason why one repair attempt produced nothing usable."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}:{detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True, slots=True)
class RepairContext:
    """Everything one repair request may see beyond the candidate itself."""

    chapter_id: str
    model: QaModelSelection
    cancellation: CancellationToken
    glossary: tuple[RelevantGlossaryTerm, ...] = field(default_factory=tuple)
    style_guide: str = ""
    # A few already-translated sentences on each side of the gap.  Two anchor
    # sentences are not enough of a sample for the model to reuse the book's own
    # wording, and it then invents its own terms for the scene — the observed
    # failure was a restored paragraph calling the same concept by another name
    # than every chapter around it.  Shown for style and terminology only.
    target_window_before: tuple[str, ...] = field(default_factory=tuple)
    target_window_after: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.chapter_id, str) or not self.chapter_id.strip():
            raise QaModelValidationError("chapter_id must be a nonempty string")
        if not isinstance(self.model, QaModelSelection):
            raise QaModelValidationError("model must be a QaModelSelection")
        if not callable(getattr(self.cancellation, "raise_if_cancelled", None)):
            raise QaModelValidationError(
                "cancellation must implement raise_if_cancelled"
            )
        if not isinstance(self.glossary, tuple) or not all(
            isinstance(term, RelevantGlossaryTerm) for term in self.glossary
        ):
            raise QaModelValidationError(
                "glossary must be a tuple of RelevantGlossaryTerm values"
            )
        if not isinstance(self.style_guide, str):
            raise QaModelValidationError("style_guide must be a string")
        for field_name in ("target_window_before", "target_window_after"):
            value = getattr(self, field_name)
            if not isinstance(value, tuple) or not all(
                isinstance(item, str) for item in value
            ):
                raise QaModelValidationError(f"{field_name} must be a tuple of strings")


class OmissionRepairer:
    """Translate exactly one confirmed gap without rewriting anything around it.

    The repairer makes a single semantic attempt. Network-level retries stay in
    the API handler; a valid but unusable proposal is rejected here so the caller
    records one attempt instead of looping a model against the same gap.
    """

    def __init__(
        self,
        client: QaCompletionClient,
        config: OmissionRepairerConfig | None = None,
    ) -> None:
        if not callable(getattr(client, "complete_json", None)):
            raise TypeError("client must implement complete_json")
        if config is not None and not isinstance(config, OmissionRepairerConfig):
            raise TypeError("config must be an OmissionRepairerConfig")
        self._client = client
        self._config = config or OmissionRepairerConfig()

    async def propose(
        self, verified: VerifiedCandidate, request: RepairContext
    ) -> RepairProposal:
        """Return one validated fragment proposal or raise a typed refusal."""
        if not isinstance(verified, VerifiedCandidate):
            raise TypeError("verified must be a VerifiedCandidate")
        if not isinstance(request, RepairContext):
            raise TypeError("request must be a RepairContext")
        if not verified.eligible_for_repair or verified.verdict is None:
            raise OmissionRepairError("not_eligible")

        request.cancellation.raise_if_cancelled()
        try:
            template = load_prompt_template(
                self._config.prompt_path, self._config.prompt_version
            )
        except PromptConfigurationError:
            raise OmissionRepairError("prompt_configuration_unavailable") from None

        glossary = request.glossary[: self._config.max_glossary_terms]
        prompt = _build_prompt(template, verified, request, glossary)
        request.cancellation.raise_if_cancelled()
        try:
            payload = await self._client.complete_json(
                prompt,
                model=request.model,
                max_output_tokens=self._config.max_output_tokens,
                cancellation=request.cancellation,
            )
        except asyncio.CancelledError:
            raise
        except QaResponseSchemaError:
            raise OmissionRepairError("invalid_response") from None
        except TimeoutError:
            raise OmissionRepairError("completion_timeout") from None
        except Exception:
            raise OmissionRepairError("completion_failed") from None

        try:
            if not isinstance(payload, Mapping):
                raise QaResponseSchemaError("completion result must be an object")
            proposal = RepairProposal.from_dict(
                payload, expected_candidate_id=verified.candidate.candidate_id
            )
        except (QaResponseSchemaError, TypeError, ValueError):
            raise OmissionRepairError("invalid_response") from None

        detail = _rejection_detail(proposal, verified, glossary, request)
        if detail:
            raise OmissionRepairError("fragment_rejected", detail)
        return proposal


def _rejection_detail(
    proposal: RepairProposal,
    verified: VerifiedCandidate,
    glossary: tuple[RelevantGlossaryTerm, ...],
    request: RepairContext | None = None,
) -> str:
    """Return the first stable reason the fragment cannot be a local repair."""
    fragment = proposal.translated_fragment
    context = verified.context
    if _FENCE_RE.search(fragment):
        return "markdown_fence"
    if _HTML_DOCUMENT_RE.search(fragment):
        return "html_document"
    normalized_fragment = _normalize(fragment)
    for anchor in (context.target_before, context.target_after):
        normalized_anchor = _normalize(anchor)
        if (
            len(normalized_anchor) >= _MIN_ANCHOR_ECHO_CHARS
            and normalized_anchor in normalized_fragment
        ):
            return "anchor_echo"
    for sentence in (
        *(request.target_window_before if request else ()),
        *(request.target_window_after if request else ()),
    ):
        normalized_sentence = _normalize(sentence)
        if (
            len(normalized_sentence) >= _MIN_WINDOW_ECHO_CHARS
            and normalized_sentence in normalized_fragment
        ):
            return "window_echo"
    normalized_source = _normalize(context.source_text)
    if (
        len(normalized_source) >= _MIN_SOURCE_ECHO_CHARS
        and normalized_source in normalized_fragment
    ):
        return "untranslated_source"
    glossary_reason = glossary_violation_reason(fragment, context.source_text, glossary)
    if glossary_reason:
        return glossary_reason
    return ""


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip().casefold()


def _build_prompt(
    template: str,
    verified: VerifiedCandidate,
    request: RepairContext,
    glossary: tuple[RelevantGlossaryTerm, ...],
) -> str:
    context = verified.context
    verdict = verified.verdict
    lines = [
        f"candidate_id: {escaped(verified.candidate.candidate_id)}",
        f"chapter_id: {escaped(request.chapter_id)}",
        f"source_language: {escaped(context.source_language)}",
        f"target_language: {escaped(context.target_language)}",
        f"style_guide: {escaped(request.style_guide)}",
        f"missing_source_fragment: {escaped(context.source_text)}",
        f"left_source_context: {escaped(context.source_before)}",
        f"right_source_context: {escaped(context.source_after)}",
        f"left_target_anchor: {escaped(context.target_before)}",
        f"right_target_anchor: {escaped(context.target_after)}",
    ]
    if request.target_window_before or request.target_window_after:
        lines.append("target_style_window_before:")
        lines.extend(f"- {escaped(item)}" for item in request.target_window_before)
        lines.append("target_style_window_after:")
        lines.extend(f"- {escaped(item)}" for item in request.target_window_after)
    lines.append("missing_facts:")
    lines.extend(
        f"- {escaped(fact)}" for fact in (verdict.missing_facts if verdict else ())
    )
    lines.append("relevant_glossary:")
    if glossary:
        lines.extend(
            "- "
            + escaped(term.original_term)
            + " → "
            + escaped(term.canonical_translation)
            + f" | policy={term.policy.value}"
            + f" | priority={term.priority}"
            for term in glossary
        )
    else:
        lines.append("- none supplied")
    return render_prompt(template, lines)
