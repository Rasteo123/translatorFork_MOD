"""Post-repair validation: every local check first, the model only at the end."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re
import unicodedata

from ..utils.epub_json import build_html_document_model, build_translation_payload
from ..utils.text import validate_html_structure
from .llm.completion import CancellationToken, QaCompletionClient, QaModelSelection
from .llm.json_response import QaResponseSchemaError
from .llm.prompts import (
    PromptConfigurationError,
    escaped,
    load_prompt_template,
    render_prompt,
)
from .llm.schemas import RepairPostCheck, RepairProposal
from .models import (
    GlossaryPolicy,
    GlossaryRule,
    OmissionRepairerConfig,
    QaModelValidationError,
    RelevantGlossaryTerm,
    VerifiedCandidate,
)
from .glossary_audit import match_glossary_policies
from .semantic_units import flatten_visible_text
from .structural_repair import REPAIR_MARKER_ATTRIBUTE, RepairValidation


# An inline repair wraps its text in one span; a restored paragraph is one new
# block-level tag of the same kind as the block it follows.
_ALLOWED_ADDED_TAGS = Counter({"span": 1})
_BLOCK_REPAIR_TAGS = frozenset({"p", "div", "li", "blockquote", "dd", "dt"})


@dataclass(frozen=True, slots=True)
class ChapterSnapshot:
    """One immutable chapter state, before or after a previewed repair."""

    chapter_id: str
    html: str

    def __post_init__(self) -> None:
        if not isinstance(self.chapter_id, str) or not self.chapter_id.strip():
            raise QaModelValidationError("chapter_id must be a nonempty string")
        if not isinstance(self.html, str):
            raise QaModelValidationError("html must be a string")

    def payload(self) -> dict:
        """Return the translation payload of this snapshot."""
        return build_translation_payload(
            build_html_document_model(self.html, document_id=self.chapter_id)
        )


class RepairValidator:
    """Decide whether one previewed repair may be written to a chapter.

    Local checks always run to completion so the journal records every reason a
    repair was refused, not just the first one. The model post-check costs a
    request and can only ever confirm a repair that already passed every local
    check, so it runs last and only then.
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
            max_output_tokens=600, prompt_version="repair_post_check_v1"
        )

    async def validate(
        self,
        before: ChapterSnapshot,
        preview: ChapterSnapshot,
        candidate: VerifiedCandidate,
        proposal: RepairProposal,
        *,
        model: QaModelSelection,
        cancellation: CancellationToken,
        glossary: Sequence[RelevantGlossaryTerm] = (),
        current_html: str | None = None,
    ) -> RepairValidation:
        """Return the complete verdict for one previewed repair."""
        for snapshot, name in ((before, "before"), (preview, "preview")):
            if not isinstance(snapshot, ChapterSnapshot):
                raise TypeError(f"{name} must be a ChapterSnapshot")
        if not isinstance(candidate, VerifiedCandidate):
            raise TypeError("candidate must be a VerifiedCandidate")
        if not isinstance(proposal, RepairProposal):
            raise TypeError("proposal must be a RepairProposal")
        if proposal.candidate_id != candidate.candidate.candidate_id:
            return RepairValidation(False, ("candidate_identity_mismatch",))

        reasons = list(
            _local_reasons(before, preview, candidate, proposal, glossary, current_html)
        )
        if reasons:
            return RepairValidation(False, tuple(reasons))

        cancellation.raise_if_cancelled()
        try:
            template = load_prompt_template(
                self._config.prompt_path, self._config.prompt_version
            )
        except PromptConfigurationError:
            return RepairValidation(False, ("post_check_configuration_unavailable",))

        prompt = _build_prompt(candidate, proposal, before, preview)
        try:
            payload = await self._client.complete_json(
                render_prompt(template, prompt),
                model=model,
                max_output_tokens=self._config.max_output_tokens,
                cancellation=cancellation,
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return RepairValidation(False, ("post_check_timeout",))
        except QaResponseSchemaError:
            return RepairValidation(False, ("post_check_invalid_response",))
        except Exception:  # noqa: BLE001 - any transport failure is a refusal
            return RepairValidation(False, ("post_check_failed",))

        try:
            if not isinstance(payload, Mapping):
                raise QaResponseSchemaError("post-check result must be an object")
            check = RepairPostCheck.from_dict(
                payload, expected_candidate_id=candidate.candidate.candidate_id
            )
        except (QaResponseSchemaError, TypeError, ValueError):
            return RepairValidation(False, ("post_check_invalid_response",))

        return RepairValidation(*_post_check_verdict(check, candidate))


def _post_check_verdict(
    check: RepairPostCheck, candidate: VerifiedCandidate
) -> tuple[bool, tuple[str, ...]]:
    reasons: list[str] = []
    if check.added_meaning:
        reasons.append("post_check_added_meaning")
    if check.context_rewritten:
        reasons.append("post_check_context_rewritten")
    expected = set(candidate.verdict.missing_facts if candidate.verdict else ())
    if set(check.missing_facts_present) != expected:
        reasons.append("post_check_fact_mismatch")
    if not check.confirmed:
        reasons.append("post_check_rejected")
    return (not reasons, tuple(reasons))


def _local_reasons(
    before: ChapterSnapshot,
    preview: ChapterSnapshot,
    candidate: VerifiedCandidate,
    proposal: RepairProposal,
    glossary: Sequence[RelevantGlossaryTerm],
    current_html: str | None,
) -> tuple[str, ...]:
    """Collect every local defect; an empty result means the preview is usable."""
    reasons: list[str] = []
    if current_html is not None and _normalize(current_html) != _normalize(before.html):
        reasons.append("chapter_changed")

    try:
        is_valid, _reason, _html = validate_html_structure(before.html, preview.html)
    except Exception:  # noqa: BLE001 - a validator crash cannot authorize a write
        is_valid = False
    if not is_valid:
        reasons.append("invalid_html")

    before_payload = before.payload()
    preview_payload = preview.payload()
    before_ids = [block["id"] for block in before_payload["blocks"]]
    preview_ids = [block["id"] for block in preview_payload["blocks"]]
    fragment = proposal.translated_fragment
    # Block identifiers are positional, so a restored paragraph renumbers every
    # block after it.  Only for that case is identity checked by text instead.
    restored_paragraph = before_ids != preview_ids

    added_tags = _tag_counts(preview.html) - _tag_counts(before.html)
    removed_tags = _tag_counts(before.html) - _tag_counts(preview.html)
    allowed = Counter(_ALLOWED_ADDED_TAGS)
    if restored_paragraph:
        allowed += Counter(
            {tag: 1 for tag in added_tags if tag in _BLOCK_REPAIR_TAGS}
        )
    if removed_tags or added_tags - allowed:
        reasons.append("structure_changed")

    if restored_paragraph:
        reasons.extend(
            _new_block_reasons(before_payload, preview_payload, fragment)
        )
    else:
        reasons.extend(
            _text_difference_reasons(before_payload, preview_payload, fragment)
        )

    if _occurrences(preview_payload, fragment) != 1:
        reasons.append("duplicate_fragment")
    if not _anchors_surround_fragment(preview_payload, candidate, fragment):
        reasons.append("anchor_missing")
    if _violates_glossary(fragment, candidate.context.source_text, glossary):
        reasons.append("glossary_violation")
    return tuple(dict.fromkeys(reasons))


def _block_texts(payload: dict) -> list[str]:
    return [
        flatten_visible_text(block["inlines"])[0] for block in payload["blocks"]
    ]


def _new_block_reasons(
    before_payload: dict, preview_payload: dict, fragment: str
) -> tuple[str, ...]:
    """A restored paragraph must be exactly the fragment and change nothing else."""
    before_texts = _block_texts(before_payload)
    preview_texts = _block_texts(preview_payload)
    if len(preview_texts) != len(before_texts) + 1:
        return ("block_identity_changed",)
    for position in range(len(preview_texts)):
        if preview_texts[:position] + preview_texts[position + 1:] == before_texts:
            if preview_texts[position] != fragment:
                return ("fragment_not_inserted",)
            return ()
    return ("unrelated_text_changed",)


def _text_difference_reasons(
    before_payload: dict, preview_payload: dict, fragment: str
) -> tuple[str, ...]:
    changed_blocks = 0
    inserted_anywhere = False
    for before_block, preview_block in zip(
        before_payload["blocks"], preview_payload["blocks"], strict=True
    ):
        before_text = flatten_visible_text(before_block["inlines"])[0]
        preview_text = flatten_visible_text(preview_block["inlines"])[0]
        if before_text == preview_text:
            continue
        changed_blocks += 1
        insertion = _single_insertion(before_text, preview_text)
        if insertion is None or fragment not in insertion:
            return ("unrelated_text_changed",)
        inserted_anywhere = True
    if changed_blocks == 0:
        return ("fragment_not_inserted",)
    if changed_blocks > 1:
        return ("unrelated_text_changed",)
    return () if inserted_anywhere else ("fragment_not_inserted",)


def _single_insertion(before_text: str, after_text: str) -> str | None:
    """Return the inserted substring when ``after`` only adds text to ``before``."""
    if len(after_text) <= len(before_text):
        return None
    prefix = 0
    while prefix < len(before_text) and before_text[prefix] == after_text[prefix]:
        prefix += 1
    suffix = 0
    while (
        suffix < len(before_text) - prefix
        and before_text[len(before_text) - 1 - suffix]
        == after_text[len(after_text) - 1 - suffix]
    ):
        suffix += 1
    if prefix + suffix != len(before_text):
        return None
    return after_text[prefix : len(after_text) - suffix]


def _anchors_surround_fragment(
    payload: dict, candidate: VerifiedCandidate, fragment: str
) -> bool:
    """Check reading order, not block membership.

    A restored paragraph sits in a block of its own, so its anchors are in the
    neighbouring blocks; only the order of the chapter's visible text can say
    whether the fragment landed where the alignment put it.
    """
    context = candidate.context
    text = "\n".join(
        flatten_visible_text(block["inlines"])[0] for block in payload["blocks"]
    )
    position = text.find(fragment)
    if position < 0:
        return False
    if context.target_before:
        left = text.find(context.target_before)
        if left < 0 or left >= position:
            return False
    if context.target_after:
        if text.find(context.target_after, position + len(fragment)) < 0:
            return False
    return True


def _occurrences(payload: dict, fragment: str) -> int:
    normalized = _normalize(fragment)
    if not normalized:
        return 0
    return sum(
        _normalize(flatten_visible_text(block["inlines"])[0]).count(normalized)
        for block in payload["blocks"]
    )


def _tag_counts(html: str) -> Counter:
    return Counter(
        match.group(1).lower() for match in re.finditer(r"<\s*([a-zA-Z][\w:-]*)", html)
    )


def _violates_glossary(
    fragment: str, source_text: str, glossary: Sequence[RelevantGlossaryTerm]
) -> bool:
    relevant = tuple(
        term
        for term in glossary
        if term.policy in {GlossaryPolicy.MUST_TRANSLATE, GlossaryPolicy.KEEP_ORIGINAL}
    )
    if not relevant or not source_text.strip():
        return False
    rules = tuple(GlossaryRule(term.original_term, term.policy) for term in relevant)
    in_source = {match.term for match in match_glossary_policies(source_text, rules)}
    in_fragment = {match.term for match in match_glossary_policies(fragment, rules)}
    for term in relevant:
        if term.original_term not in in_source:
            continue
        present = term.original_term in in_fragment
        if term.policy is GlossaryPolicy.MUST_TRANSLATE and present:
            return True
        if term.policy is GlossaryPolicy.KEEP_ORIGINAL and not present:
            return True
    return False


def _local_window(payload: dict, fragment: str, marker: str) -> str:
    """Return only the block that carries the repair, never the whole chapter."""
    for block in payload["blocks"]:
        text = flatten_visible_text(block["inlines"])[0]
        if fragment in text or marker in text:
            return text
    return ""


def _build_prompt(
    candidate: VerifiedCandidate,
    proposal: RepairProposal,
    before: ChapterSnapshot,
    preview: ChapterSnapshot,
) -> list[str]:
    context = candidate.context
    before_window = _local_window(
        before.payload(), context.target_before, REPAIR_MARKER_ATTRIBUTE
    )
    preview_window = _local_window(
        preview.payload(), proposal.translated_fragment, REPAIR_MARKER_ATTRIBUTE
    )
    lines = [
        f"candidate_id: {escaped(candidate.candidate.candidate_id)}",
        f"source_language: {escaped(context.source_language)}",
        f"target_language: {escaped(context.target_language)}",
        f"missing_source_fragment: {escaped(context.source_text)}",
        f"left_target_anchor: {escaped(context.target_before)}",
        f"right_target_anchor: {escaped(context.target_after)}",
        f"inserted_fragment: {escaped(proposal.translated_fragment)}",
        f"local_window_before: {escaped(before_window)}",
        f"local_window_after: {escaped(preview_window)}",
        "missing_facts:",
    ]
    lines.extend(
        f"- {escaped(fact)}"
        for fact in (candidate.verdict.missing_facts if candidate.verdict else ())
    )
    return lines


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip().casefold()
