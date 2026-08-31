"""One batched correction request and one batched validation request per chunk."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ..language_validation import (
    LanguageBatchValidation,
    LanguageBlock,
    LanguageQaRequest,
    LanguageRepairBatch,
    LanguageReplacement,
    LanguageReviewError,
)
from ..models import OmissionRepairerConfig, QaModelValidationError
from ..semantic_units import flatten_visible_text
from ...utils.epub_json import build_translation_payload
from .completion import QaCompletionClient
from .language_reviewer import request_qa_json
from .prompts import (
    PromptConfigurationError,
    escaped,
    load_prompt_template,
    render_prompt,
)
from .schemas import LanguageIssue


CORRECTION_PURPOSE = "language_batch_correction"
VALIDATION_PURPOSE = "language_batch_validation"


class LanguageBatchRepairer:
    """Turn every eligible issue of one chunk into replacements in one request."""

    def __init__(
        self,
        client: QaCompletionClient,
        config: OmissionRepairerConfig | None = None,
    ) -> None:
        if not callable(getattr(client, "complete_json", None)):
            raise TypeError("client must implement complete_json")
        self._client = client
        self._config = config or OmissionRepairerConfig(
            max_output_tokens=2048, prompt_version="language_batch_correction_v1"
        )

    async def propose_batch(
        self,
        request: LanguageQaRequest,
        blocks: Sequence[LanguageBlock],
        issues: Sequence[LanguageIssue],
    ) -> LanguageRepairBatch:
        """Return one replacement per issue ID, or raise a typed refusal."""
        if not isinstance(request, LanguageQaRequest):
            raise TypeError("request must be a LanguageQaRequest")
        if not issues:
            raise QaModelValidationError("a correction batch needs at least one issue")
        request.cancellation.raise_if_cancelled()
        try:
            template = load_prompt_template(
                self._config.prompt_path, self._config.prompt_version
            )
        except PromptConfigurationError:
            raise LanguageReviewError("prompt_configuration_unavailable") from None

        by_id = {issue.issue_id: issue for issue in issues}
        texts = {block.block_id: block.text for block in blocks}
        prompt = render_prompt(
            template, _correction_lines(request, issues, texts)
        )
        payload = await request_qa_json(
            self._client,
            prompt,
            request,
            self._config.max_output_tokens,
            CORRECTION_PURPOSE,
        )
        entries = _entries(payload, "replacements", CORRECTION_PURPOSE)
        replacements: list[LanguageReplacement] = []
        seen: set[str] = set()
        for entry in entries:
            if not isinstance(entry, Mapping):
                raise LanguageReviewError("correction_invalid_response")
            issue_id = entry.get("issue_id")
            replacement_text = entry.get("replacement_text")
            if (
                not isinstance(issue_id, str)
                or issue_id not in by_id
                or issue_id in seen
                or not isinstance(replacement_text, str)
                or not replacement_text.strip()
            ):
                raise LanguageReviewError("correction_invalid_response")
            seen.add(issue_id)
            issue = by_id[issue_id]
            replacements.append(
                LanguageReplacement(
                    issue_id=issue_id,
                    block_id=issue.block_id,
                    original_text=issue.original_text,
                    replacement_text=replacement_text,
                )
            )
        if not replacements:
            raise LanguageReviewError("correction_invalid_response")
        return LanguageRepairBatch(request.chapter_id, tuple(replacements))


class LanguageRepairValidator:
    """Confirm or refuse a whole batch of replacements in one request."""

    def __init__(
        self,
        client: QaCompletionClient,
        config: OmissionRepairerConfig | None = None,
    ) -> None:
        if not callable(getattr(client, "complete_json", None)):
            raise TypeError("client must implement complete_json")
        self._client = client
        self._config = config or OmissionRepairerConfig(
            max_output_tokens=1024, prompt_version="language_batch_validation_v2"
        )

    async def validate_batch(
        self,
        request: LanguageQaRequest,
        blocks: Sequence[LanguageBlock],
        batch: LanguageRepairBatch,
        preview_model: dict,
    ) -> LanguageBatchValidation:
        """Return the confirmed and refused issue IDs of one batch."""
        if not isinstance(batch, LanguageRepairBatch):
            raise TypeError("batch must be a LanguageRepairBatch")
        request.cancellation.raise_if_cancelled()
        try:
            template = load_prompt_template(
                self._config.prompt_path, self._config.prompt_version
            )
        except PromptConfigurationError:
            raise LanguageReviewError("prompt_configuration_unavailable") from None

        prompt = render_prompt(
            template, _validation_lines(request, batch, preview_model)
        )
        payload = await request_qa_json(
            self._client,
            prompt,
            request,
            self._config.max_output_tokens,
            VALIDATION_PURPOSE,
        )
        if not isinstance(payload, Mapping):
            raise LanguageReviewError("validation_invalid_response")
        unknown = set(payload) - {
            "confirmed_issue_ids",
            "rejected_issue_ids",
            "metadata",
        }
        if unknown:
            raise LanguageReviewError("validation_invalid_response")
        known = {replacement.issue_id for replacement in batch.replacements}
        confirmed = _id_tuple(payload.get("confirmed_issue_ids"), known)
        rejected = _id_tuple(payload.get("rejected_issue_ids"), known)
        if set(confirmed) & set(rejected):
            raise LanguageReviewError("validation_invalid_response")
        return LanguageBatchValidation(confirmed, rejected)


def _entries(payload: object, key: str, purpose: str) -> Sequence[object]:
    if not isinstance(payload, Mapping):
        raise LanguageReviewError(f"{purpose}_invalid_response")
    if set(payload) - {key, "metadata"} or key not in payload:
        raise LanguageReviewError(f"{purpose}_invalid_response")
    entries = payload[key]
    if not isinstance(entries, (list, tuple)):
        raise LanguageReviewError(f"{purpose}_invalid_response")
    return entries


def _id_tuple(value: object, known: set[str]) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise LanguageReviewError("validation_invalid_response")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or item not in known or item in result:
            raise LanguageReviewError("validation_invalid_response")
        result.append(item)
    return tuple(result)


def _correction_lines(
    request: LanguageQaRequest,
    issues: Sequence[LanguageIssue],
    texts: Mapping[str, str],
) -> list[str]:
    lines = [
        f"chapter_id: {escaped(request.chapter_id)}",
        f"target_language: {escaped(request.target_language)}",
        "issues:",
    ]
    for issue in issues:
        lines.append(f"- issue_id: {escaped(issue.issue_id)}")
        lines.append(f"  category: {escaped(issue.category)}")
        lines.append(f"  block_text: {escaped(texts.get(issue.block_id, ''))}")
        lines.append(f"  original_span: {escaped(issue.original_text)}")
        lines.append(f"  proposed: {escaped(issue.replacement_text or '')}")
        lines.append(f"  explanation: {escaped(issue.explanation)}")
        source_text = request.source_text_by_block.get(issue.block_id, "")
        if source_text:
            lines.append(f"  source_text: {escaped(source_text)}")
    lines.append("glossary:")
    lines.extend(
        [
            f"- {escaped(term.original_term)} → {escaped(term.canonical_translation)}"
            f" | policy={term.policy.value}"
            for term in request.glossary
        ]
        or ["- none supplied"]
    )
    return lines


def _validation_lines(
    request: LanguageQaRequest, batch: LanguageRepairBatch, preview_model: dict
) -> list[str]:
    preview_blocks = {
        block["id"]: flatten_visible_text(block["inlines"])[0]
        for block in build_translation_payload(preview_model)["blocks"]
    }
    lines = [
        f"chapter_id: {escaped(request.chapter_id)}",
        f"target_language: {escaped(request.target_language)}",
        "replacements:",
    ]
    for replacement in batch.replacements:
        lines.append(f"- issue_id: {escaped(replacement.issue_id)}")
        lines.append(f"  before: {escaped(replacement.original_text)}")
        lines.append(f"  after: {escaped(replacement.replacement_text)}")
        lines.append(
            f"  block_after: {escaped(preview_blocks.get(replacement.block_id, ''))}"
        )
        source_text = request.source_text_by_block.get(replacement.block_id, "")
        if source_text:
            lines.append(f"  source_text: {escaped(source_text)}")
    return lines
