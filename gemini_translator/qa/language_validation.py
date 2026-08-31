"""Batched language quality control: diagnose, correct, and validate per chapter."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
import re

from ..utils.epub_json import build_translation_payload
from .glossary_audit import contains_term_forms
from .llm.completion import CancellationToken, QaCompletionClient, QaModelSelection
from .llm.schemas import LanguageIssue
from .models import GlossaryPolicy, QaModelValidationError, RelevantGlossaryTerm
from .semantic_units import flatten_visible_text


DEFAULT_MIN_CONFIDENCE = 0.85
# Observed on a real book: the model marks a stylistic rewrite of a repetition
# or a calque as "objective" with high confidence, and such a fix changes the
# author's wording rather than a defect. Those categories are shown instead.
DEFAULT_AUTO_FIX_CATEGORIES = ("typo", "grammar", "punctuation")
LANGUAGE_ISSUE_CATEGORIES = (
    "typo",
    "grammar",
    "punctuation",
    "calque",
    "repetition",
    "meta_comment",
    "hallucinated_addition",
)
DEFAULT_MAX_CHUNK_CHARS = 4000
PROTECTED_ENTITY_CATEGORIES = frozenset({"PER", "ORG", "LOC"})


class LanguageRepairConflict(RuntimeError):
    """Raised when a batch of replacements cannot be applied exactly as written."""


class LanguageReviewError(RuntimeError):
    """Typed, sanitized refusal of one language QA request stage."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class LanguageBlock:
    """One translated block the reviewer may inspect and repair."""

    block_id: str
    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.block_id, str) or not self.block_id.strip():
            raise QaModelValidationError("block_id must be a nonempty string")
        if not isinstance(self.text, str):
            raise QaModelValidationError("block text must be a string")


@dataclass(frozen=True, slots=True)
class LanguageRuleIssue:
    """One unconfirmed rule-based hint, typically from LanguageTool."""

    block_id: str
    rule_id: str
    message: str
    original_text: str
    replacements: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("block_id", "rule_id", "message", "original_text"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise QaModelValidationError(f"{field_name} must be a nonempty string")
        if not isinstance(self.replacements, tuple) or not all(
            isinstance(item, str) for item in self.replacements
        ):
            raise QaModelValidationError("replacements must be a tuple of strings")


@dataclass(frozen=True, slots=True)
class NamedEntitySpan:
    """One entity that must survive any automatic language repair unchanged."""

    block_id: str
    text: str
    category: str

    def __post_init__(self) -> None:
        for field_name in ("block_id", "text", "category"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise QaModelValidationError(f"{field_name} must be a nonempty string")


@dataclass(frozen=True, slots=True)
class SyntaxCandidate:
    """One unconfirmed syntactic oddity, typically from a Russian parser."""

    block_id: str
    original_text: str
    reason: str

    def __post_init__(self) -> None:
        for field_name in ("block_id", "original_text", "reason"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise QaModelValidationError(f"{field_name} must be a nonempty string")


@dataclass(frozen=True, slots=True)
class RussianNlpAnalysis:
    """Optional local NLP evidence; never an instruction to change text."""

    entities: tuple[NamedEntitySpan, ...] = ()
    syntax_candidates: tuple[SyntaxCandidate, ...] = ()


@dataclass(frozen=True, slots=True, eq=False)
class LanguageQaRequest:
    """One chapter of translated markup plus everything a review may consult."""

    chapter_id: str
    document_model: dict
    source_language: str
    target_language: str
    model: QaModelSelection
    cancellation: CancellationToken
    source_text_by_block: Mapping[str, str] = field(default_factory=dict)
    glossary: tuple[RelevantGlossaryTerm, ...] = ()
    min_confidence: float = DEFAULT_MIN_CONFIDENCE
    auto_fix_categories: tuple[str, ...] = DEFAULT_AUTO_FIX_CATEGORIES
    max_chunk_chars: int = DEFAULT_MAX_CHUNK_CHARS

    def __post_init__(self) -> None:
        for field_name in ("chapter_id", "source_language", "target_language"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise QaModelValidationError(f"{field_name} must be a nonempty string")
        if not isinstance(self.document_model, dict):
            raise QaModelValidationError("document_model must be a dict")
        if not isinstance(self.model, QaModelSelection):
            raise QaModelValidationError("model must be a QaModelSelection")
        if not callable(getattr(self.cancellation, "raise_if_cancelled", None)):
            raise QaModelValidationError(
                "cancellation must implement raise_if_cancelled"
            )
        if not isinstance(self.glossary, tuple) or not all(
            isinstance(term, RelevantGlossaryTerm) for term in self.glossary
        ):
            raise QaModelValidationError("glossary must be RelevantGlossaryTerm values")
        if (
            isinstance(self.min_confidence, bool)
            or not isinstance(self.min_confidence, (int, float))
            or not 0.0 <= self.min_confidence <= 1.0
        ):
            raise QaModelValidationError("min_confidence must be between 0 and 1")
        if (
            isinstance(self.max_chunk_chars, bool)
            or not isinstance(self.max_chunk_chars, int)
            or self.max_chunk_chars < 1
        ):
            raise QaModelValidationError("max_chunk_chars must be positive")


@dataclass(frozen=True, slots=True)
class LanguageReplacement:
    """One exact text substitution bound to a diagnosed issue and its block."""

    issue_id: str
    block_id: str
    original_text: str
    replacement_text: str

    def __post_init__(self) -> None:
        for field_name in (
            "issue_id",
            "block_id",
            "original_text",
            "replacement_text",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise QaModelValidationError(f"{field_name} must be a nonempty string")


@dataclass(frozen=True, slots=True)
class LanguageRepairBatch:
    """Every replacement proposed for one chunk, applied all-or-nothing."""

    chapter_id: str
    replacements: tuple[LanguageReplacement, ...]


@dataclass(frozen=True, slots=True)
class LanguageBatchValidation:
    """Which issue IDs of a batch the validator confirmed and which it refused."""

    confirmed_issue_ids: tuple[str, ...] = ()
    rejected_issue_ids: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, eq=False)
class LanguageQaResult:
    """Everything one chapter-level language pass diagnosed, applied, or deferred."""

    chapter_id: str
    issues: tuple[LanguageIssue, ...] = ()
    applied: tuple[LanguageReplacement, ...] = ()
    suggestions: tuple[LanguageIssue, ...] = ()
    preview_model: dict | None = None
    warnings: tuple[str, ...] = ()
    # Why each suggestion was not applied, by issue id.  A refusal nobody can
    # see teaches the user nothing; this is what the log renders next to the
    # suggestion.  Codes are stable; a parenthesized tail may add specifics.
    refusals: Mapping[str, str] = field(default_factory=dict)


# One honest sentence per refusal code.  The keys are contracts: tests and the
# log rely on them, so a new refusal path must add its code here.
REFUSAL_DESCRIPTIONS: Mapping[str, str] = {
    "style_suggestion": "стилистическое предложение — автоматически не правится",
    "subjective": "модель не считает правку объективной",
    "no_replacement": "нет текста замены",
    "low_confidence": "уверенность ниже порога",
    "ambiguous_span": "фрагмент встречается в абзаце не один раз",
    "no_change": "замена совпадает с исходным текстом",
    "protected_entity": "правка затрагивает защищённое имя",
    "glossary_term_dropped": "правка теряет термин глоссария",
    "category_not_auto_fixable": "категория не входит в список автоправки",
    "punctuation_rewrite": "замена одного знака препинания другим — это выбор автора",
    "paragraph_break": "правка просит разбить абзац — это делает человек",
    "validation_declined": "модель-проверщик не подтвердила правку",
    "apply_conflict": "не удалось применить однозначно: конфликт с текстом главы",
    "language_diagnosis_failed": "диагностика не удалась (сбой запроса)",
    "language_diagnosis_timeout": "диагностика превысила время ожидания",
    "language_diagnosis_invalid_response": "модель вернула непригодный ответ диагностики",
    "language_batch_correction_failed": "запрос пакета исправлений не удался",
    "language_batch_correction_timeout": "запрос пакета исправлений превысил время",
    "language_batch_correction_invalid_response": "модель вернула непригодный пакет исправлений",
    "language_batch_validation_failed": "проверка пакета исправлений не удалась",
    "language_batch_validation_timeout": "проверка пакета исправлений превысила время",
    "language_batch_validation_invalid_response": "модель вернула непригодный ответ проверки",
}


def describe_refusal(code: str) -> str:
    """Turn a stable refusal code into the sentence the log shows.

    An unknown code comes back as itself: better an honest identifier than a
    silent blank when a new path forgets to register its description.
    """
    text = str(code or "")
    base, _, tail = text.partition(" (")
    described = REFUSAL_DESCRIPTIONS.get(base, base)
    return f"{described} ({tail}" if tail else described


def blocks_from_model(document_model: dict) -> tuple[LanguageBlock, ...]:
    """Return one reviewable block per translation payload block, in order."""
    payload = build_translation_payload(document_model)
    return tuple(
        LanguageBlock(block["id"], flatten_visible_text(block["inlines"])[0])
        for block in payload["blocks"]
    )


def chunk_blocks(
    blocks: Sequence[LanguageBlock], max_chars: int
) -> tuple[tuple[LanguageBlock, ...], ...]:
    """Split blocks into deterministic chunks that never reorder or drop one."""
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 1:
        raise QaModelValidationError("max_chars must be a positive integer")
    chunks: list[tuple[LanguageBlock, ...]] = []
    current: list[LanguageBlock] = []
    size = 0
    for block in blocks:
        if current and size + len(block.text) > max_chars:
            chunks.append(tuple(current))
            current = []
            size = 0
        current.append(block)
        size += len(block.text)
    if current:
        chunks.append(tuple(current))
    return tuple(chunks)


# Dashes are one mark spelled several ways; turning any of them into an em dash
# is typography, not a change of the author's punctuation.
_DASHES = "-\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
_MARK = re.compile(r"[^\w\s]", re.UNICODE)


def _marks(text: str) -> tuple[str, ...]:
    return tuple(
        "\u2014" if character in _DASHES else character
        for character in _MARK.findall(str(text or ""))
    )


def _is_subsequence(shorter: tuple[str, ...], longer: tuple[str, ...]) -> bool:
    position = 0
    for mark in longer:
        if position < len(shorter) and shorter[position] == mark:
            position += 1
    return position == len(shorter)


def _rewrites_punctuation(original: str, replacement: str) -> bool:
    """Report whether a "punctuation fix" swaps one valid mark for another.

    Measured on a real book: with punctuation in the auto-fix list the check
    replaced semicolons with commas and a colon with a full stop — legitimate
    choices of whoever wrote the sentence, rewritten without being asked.  A
    mark that is only added, only dropped, or merely spelled differently (any
    dash becomes an em dash) is a defect; a mark exchanged for a different one
    is an opinion, and opinions stay suggestions.
    """
    before = _marks(original)
    after = _marks(replacement)
    if before == after:
        return False
    return not (
        _is_subsequence(before, after) or _is_subsequence(after, before)
    )


_BREAK = re.compile(r"[\n\r\u2028\u2029]")


def _asks_for_a_new_paragraph(original: str, replacement: str) -> bool:
    """Report whether the replacement wants a break the block cannot hold.

    Measured on a real book: twice the model answered a run-on replica with a
    line break, and a replacement lives inside one paragraph, where a break is
    just whitespace.  Once that turned a vocative in the middle of a single
    speech into what reads as an attribution dash; once it changed nothing at
    all.  Splitting a paragraph is an edit a person makes.
    """
    return bool(_BREAK.search(replacement)) and not _BREAK.search(original)


def auto_fix_refusal(
    issue: LanguageIssue,
    block_text: str,
    *,
    entities: Sequence[NamedEntitySpan] = (),
    glossary: Sequence[RelevantGlossaryTerm] = (),
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    auto_fix_categories: Sequence[str] = DEFAULT_AUTO_FIX_CATEGORIES,
) -> str:
    """Return the stable reason one issue may not be fixed automatically.

    An empty string means the issue is eligible. Subjective judgements, weak
    confidence, ambiguous spans, protected entities, and glossary terms are all
    refusals: they stay visible as suggestions instead of editing the book.
    """

    if issue.category == "style_suggestion":
        return "style_suggestion"
    if not issue.objective:
        return "subjective"
    if issue.replacement_text is None:
        return "no_replacement"
    if issue.confidence < min_confidence:
        return "low_confidence"
    if block_text.count(issue.original_text) != 1:
        return "ambiguous_span"
    if issue.replacement_text == issue.original_text:
        return "no_change"
    if _asks_for_a_new_paragraph(issue.original_text, issue.replacement_text):
        return "paragraph_break"
    if issue.category == "punctuation" and _rewrites_punctuation(
        issue.original_text, issue.replacement_text
    ):
        return "punctuation_rewrite"
    for entity in entities:
        if entity.category.upper() not in PROTECTED_ENTITY_CATEGORIES:
            continue
        if entity.text in issue.original_text and entity.text not in issue.replacement_text:
            return "protected_entity"
    for term in glossary:
        if term.policy is not GlossaryPolicy.MUST_TRANSLATE:
            continue
        canonical = term.canonical_translation
        if contains_term_forms(issue.original_text, canonical) and not (
            contains_term_forms(issue.replacement_text, canonical)
        ):
            return "glossary_term_dropped"
    # The policy check comes last: an issue that could never be applied anyway
    # should say why in its own terms.
    if auto_fix_categories and issue.category not in auto_fix_categories:
        return "category_not_auto_fixable"
    return ""


def apply_language_replacements(
    document_model: dict, replacements: Sequence[LanguageReplacement]
) -> dict:
    """Return a deep copy with every replacement applied, or raise a conflict.

    A replacement must match exactly one span inside one inline text node, and
    two replacements may never overlap: an ambiguous or overlapping batch is
    refused whole rather than applied partially.
    """

    if not isinstance(document_model, dict):
        raise QaModelValidationError("document_model must be a dict")
    payload = build_translation_payload(document_model)
    blocks = {block["id"]: block for block in payload["blocks"]}
    planned: list[tuple[str, int, int, str]] = []
    spans_by_block: dict[str, list[tuple[int, int]]] = {}

    for replacement in replacements:
        if not isinstance(replacement, LanguageReplacement):
            raise QaModelValidationError(
                "replacements must be LanguageReplacement values"
            )
        block = blocks.get(replacement.block_id)
        if block is None:
            raise LanguageRepairConflict(
                f"unknown block for issue {replacement.issue_id}"
            )
        text, segments = flatten_visible_text(block["inlines"])
        if text.count(replacement.original_text) != 1:
            raise LanguageRepairConflict(
                f"stale or ambiguous span for issue {replacement.issue_id}"
            )
        start = text.index(replacement.original_text)
        end = start + len(replacement.original_text)
        for other_start, other_end in spans_by_block.get(replacement.block_id, ()):
            if start < other_end and other_start < end:
                raise LanguageRepairConflict(
                    f"overlapping replacement for issue {replacement.issue_id}"
                )
        spans_by_block.setdefault(replacement.block_id, []).append((start, end))
        segment = next(
            (
                item
                for item in segments
                if item[1] <= start and end <= item[2]
            ),
            None,
        )
        if segment is None:
            raise LanguageRepairConflict(
                f"span crosses inline nodes for issue {replacement.issue_id}"
            )
        planned.append(
            (segment[0], start - segment[1], end - segment[1], replacement.replacement_text)
        )

    model = deepcopy(document_model)
    nodes = _text_nodes_by_id(model)
    for node_id, start, end, text in sorted(planned, key=lambda item: -item[1]):
        node = nodes.get(node_id)
        if node is None:
            raise LanguageRepairConflict("inline node disappeared before the edit")
        current = node.get("text", "")
        node["text"] = current[:start] + text + current[end:]
    return model


class LanguageQualityPipeline:
    """Run diagnosis, one batched correction, and one batched validation.

    Each chunk of a chapter costs at most three requests, whatever the number of
    defects. When diagnosis finds nothing that may be fixed automatically, the
    correction and validation requests are never made.
    """

    def __init__(
        self,
        client: QaCompletionClient,
        *,
        reviewer: object | None = None,
        repairer: object | None = None,
        validator: object | None = None,
        diagnosis_cache=None,
    ) -> None:
        if not callable(getattr(client, "complete_json", None)):
            raise TypeError("client must implement complete_json")
        # Imported here so the request stages may depend on these contracts.
        from .llm.language_repairer import LanguageBatchRepairer, LanguageRepairValidator
        from .llm.language_reviewer import LanguageQualityReviewer

        self._reviewer = reviewer or LanguageQualityReviewer(
            client, cache=diagnosis_cache
        )
        self._repairer = repairer or LanguageBatchRepairer(client)
        self._validator = validator or LanguageRepairValidator(client)

    async def check_chapter(
        self,
        request: LanguageQaRequest,
        *,
        rule_candidates: Sequence[LanguageRuleIssue] = (),
        nlp_analysis: RussianNlpAnalysis | None = None,
    ) -> LanguageQaResult:
        """Diagnose and, where it is safe, repair one chapter's language defects."""
        if not isinstance(request, LanguageQaRequest):
            raise TypeError("request must be a LanguageQaRequest")

        current_model = request.document_model
        issues: list[LanguageIssue] = []
        applied: list[LanguageReplacement] = []
        suggestions: list[LanguageIssue] = []
        refusals: dict[str, str] = {}

        def defer(issue: LanguageIssue, reason: str) -> None:
            suggestions.append(issue)
            refusals[issue.issue_id] = reason
        warnings: list[str] = []
        changed = False
        seen_issue_ids: set[str] = set()

        for chunk in chunk_blocks(blocks_from_model(current_model), request.max_chunk_chars):
            request.cancellation.raise_if_cancelled()
            block_ids = {block.block_id for block in chunk}
            chunk_rules = tuple(
                rule for rule in rule_candidates if rule.block_id in block_ids
            )
            chunk_nlp = _scoped_analysis(nlp_analysis, block_ids)
            try:
                chunk_issues = await self._reviewer.diagnose_chapter(
                    request, chunk, chunk_rules, chunk_nlp
                )
            except LanguageReviewError as error:
                warnings.append(error.reason)
                continue

            chunk_issues = _with_unique_ids(chunk_issues, seen_issue_ids)
            issues.extend(chunk_issues)
            texts = {block.block_id: block.text for block in chunk}
            eligible: list[LanguageIssue] = []
            for issue in chunk_issues:
                refusal = auto_fix_refusal(
                    issue,
                    texts.get(issue.block_id, ""),
                    entities=chunk_nlp.entities if chunk_nlp else (),
                    glossary=request.glossary,
                    min_confidence=request.min_confidence,
                    auto_fix_categories=request.auto_fix_categories,
                )
                if refusal == "low_confidence":
                    refusal = (
                        f"low_confidence ({issue.confidence:.2f}"
                        f" < {request.min_confidence:.2f})"
                    )
                if refusal:
                    defer(issue, refusal)
                else:
                    eligible.append(issue)
            if not eligible:
                continue

            try:
                batch = await self._repairer.propose_batch(
                    request, chunk, tuple(eligible)
                )
            except LanguageReviewError as error:
                warnings.append(error.reason)
                for issue in eligible:
                    defer(issue, error.reason)
                continue

            preview = _apply_or_warn(current_model, batch.replacements, warnings)
            if preview is None:
                for issue in eligible:
                    defer(issue, "apply_conflict")
                continue

            try:
                validation = await self._validator.validate_batch(
                    request, chunk, batch, preview
                )
            except LanguageReviewError as error:
                warnings.append(error.reason)
                for issue in eligible:
                    defer(issue, error.reason)
                continue

            confirmed = tuple(
                replacement
                for replacement in batch.replacements
                if replacement.issue_id in set(validation.confirmed_issue_ids)
            )
            refused_ids = {
                replacement.issue_id
                for replacement in batch.replacements
                if replacement not in confirmed
            }
            for issue in eligible:
                if issue.issue_id in refused_ids:
                    defer(issue, "validation_declined")
            if not confirmed:
                continue
            if len(confirmed) != len(batch.replacements):
                preview = _apply_or_warn(current_model, confirmed, warnings)
                if preview is None:
                    # Only the confirmed ones fall back here: the refused ones
                    # were already deferred above and must not be listed twice.
                    confirmed_ids = {item.issue_id for item in confirmed}
                    for issue in eligible:
                        if issue.issue_id in confirmed_ids:
                            defer(issue, "apply_conflict")
                    continue
            current_model = preview
            applied.extend(confirmed)
            changed = True

        return LanguageQaResult(
            chapter_id=request.chapter_id,
            issues=tuple(issues),
            applied=tuple(applied),
            suggestions=tuple(suggestions),
            preview_model=current_model if changed else None,
            warnings=tuple(dict.fromkeys(warnings)),
            refusals=refusals,
        )


def _apply_or_warn(
    model: dict, replacements: Sequence[LanguageReplacement], warnings: list[str]
) -> dict | None:
    try:
        return apply_language_replacements(model, replacements)
    except LanguageRepairConflict:
        warnings.append("language_batch_conflict")
        return None


def _with_unique_ids(
    issues: Sequence[LanguageIssue], seen: set[str]
) -> tuple[LanguageIssue, ...]:
    """Keep issue IDs disjoint across chunks without trusting the model for it."""
    unique: list[LanguageIssue] = []
    for issue in issues:
        issue_id = issue.issue_id
        suffix = 1
        while issue_id in seen:
            suffix += 1
            issue_id = f"{issue.issue_id}#{suffix}"
        seen.add(issue_id)
        unique.append(
            issue
            if issue_id == issue.issue_id
            else LanguageIssue(
                issue_id=issue_id,
                category=issue.category,
                block_id=issue.block_id,
                original_text=issue.original_text,
                replacement_text=issue.replacement_text,
                objective=issue.objective,
                confidence=issue.confidence,
                explanation=issue.explanation,
                metadata=issue.metadata,
            )
        )
    return tuple(unique)


def _scoped_analysis(
    analysis: RussianNlpAnalysis | None, block_ids: set[str]
) -> RussianNlpAnalysis | None:
    if analysis is None:
        return None
    return RussianNlpAnalysis(
        entities=tuple(
            entity for entity in analysis.entities if entity.block_id in block_ids
        ),
        syntax_candidates=tuple(
            candidate
            for candidate in analysis.syntax_candidates
            if candidate.block_id in block_ids
        ),
    )


def _text_nodes_by_id(document_model: dict) -> dict[str, dict]:
    nodes: dict[str, dict] = {}

    def walk(node: dict) -> None:
        if node.get("kind") == "text":
            nodes[node.get("node_id", "")] = node
        for child in node.get("children", []) or []:
            walk(child)

    walk(document_model.get("body", {}))
    return nodes
