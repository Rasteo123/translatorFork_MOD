"""One entry point for chapter quality control: check, repair once, or report."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
from pathlib import Path

from ..utils.epub_json import build_html_document_model, render_document_html
from .addition_detector import AdditionCandidate, ChapterContext
from .capabilities import QaCapabilitySettings
from .coverage_service import SEMANTIC_ALIGNMENT_MODE, CoverageAnalysis, CoverageRequest
from .glossary_context import GlossaryContextSelector, GlossaryTerm
from .journal import QaJournal
from .language_validation import (
    LanguageQaRequest,
    LanguageQaResult,
    LanguageRuleIssue,
    RussianNlpAnalysis,
)
from .llm.completion import CancellationToken, QaModelSelection
from .llm.omission_repairer import OmissionRepairError, RepairContext
from .models import (
    ChapterMetrics,
    Decision,
    QaJournalEntry,
    QaModelValidationError,
    RiskLevel,
    SemanticUnit,
    VerifiedCandidate,
)
from .repair_store import ManualEditConflict, RepairStore, UndoResult
from .repair_validator import ChapterSnapshot
from .structural_repair import (
    RepairValidationContext,
    StructuralPatch,
    StructuralRepairError,
    StructuralRepairEngine,
    document_fingerprint,
)


MAX_GLOSSARY_TERMS_PER_CANDIDATE = 12


@dataclass(frozen=True, slots=True)
class QaOptions:
    """Everything the user's settings decide about one QA pass."""

    capabilities: QaCapabilitySettings = field(default_factory=QaCapabilitySettings)
    check_completeness: bool = True
    auto_repair_omissions: bool = True
    check_language: bool = True
    auto_repair_language: bool = True
    detect_additions: bool = True
    max_repairs_per_chapter: int = 5

    def __post_init__(self) -> None:
        if not isinstance(self.capabilities, QaCapabilitySettings):
            raise QaModelValidationError("capabilities must be QaCapabilitySettings")
        if (
            isinstance(self.max_repairs_per_chapter, bool)
            or not isinstance(self.max_repairs_per_chapter, int)
            or self.max_repairs_per_chapter < 0
        ):
            raise QaModelValidationError(
                "max_repairs_per_chapter must be a non-negative integer"
            )


@dataclass(frozen=True, slots=True, eq=False)
class ChapterQaRequest:
    """One chapter, its files, and the project state a QA pass may consult."""

    chapter_id: str
    coverage_request: CoverageRequest
    translated_path: Path
    model: QaModelSelection
    session_id: str = "session"
    glossary: tuple[GlossaryTerm, ...] = ()
    style_guide: str = ""
    source_text_by_block: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in ("chapter_id", "session_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise QaModelValidationError(f"{field_name} must be a nonempty string")
        if not isinstance(self.coverage_request, CoverageRequest):
            raise QaModelValidationError("coverage_request must be a CoverageRequest")
        if not isinstance(self.model, QaModelSelection):
            raise QaModelValidationError("model must be a QaModelSelection")
        object.__setattr__(self, "translated_path", Path(self.translated_path))
        if not isinstance(self.glossary, tuple) or not all(
            isinstance(term, GlossaryTerm) for term in self.glossary
        ):
            raise QaModelValidationError("glossary must be GlossaryTerm values")

    @property
    def target_document_id(self) -> str:
        """The identity semantic units were extracted under, not the chapter id."""
        document_id = self.coverage_request.target_payload["document_id"]
        return str(document_id)

    @property
    def source_language(self) -> str:
        return self.coverage_request.source_language

    @property
    def target_language(self) -> str:
        return self.coverage_request.target_language


@dataclass(frozen=True, slots=True)
class OmissionRepairOutcome:
    """What happened to exactly one verified omission candidate."""

    candidate_id: str
    decision: Decision
    attempted: bool = False
    patch_id: str = ""
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, eq=False)
class ChapterQaResult:
    """The complete, auditable outcome of one chapter QA pass."""

    chapter_id: str
    risk_level: RiskLevel
    may_continue_translation: bool
    coverage_mode: str
    verified: tuple[VerifiedCandidate, ...] = ()
    repairs: tuple[OmissionRepairOutcome, ...] = ()
    additions: tuple[AdditionCandidate, ...] = ()
    language: LanguageQaResult | None = None
    metrics: ChapterMetrics | None = None
    warnings: tuple[str, ...] = ()

    @property
    def applied_repair_ids(self) -> tuple[str, ...]:
        return tuple(
            repair.candidate_id
            for repair in self.repairs
            if repair.decision is Decision.FIXED
        )


class TranslationQualityService:
    """Run the whole QA cascade for one chapter without touching the queue.

    The service decides nothing about the translation session: it returns a
    typed result carrying ``may_continue_translation`` and leaves the queue to
    the caller. Every stage is optional and every failure is a warning, so a QA
    outage never blocks or corrupts a translation.
    """

    def __init__(
        self,
        *,
        coverage,
        verifier,
        repairer,
        repair_engine: StructuralRepairEngine,
        repair_validator,
        store: RepairStore,
        journal: QaJournal,
        journal_path: Path | str,
        additions=None,
        language=None,
        language_rules=None,
        russian_nlp=None,
        glossary_selector: GlossaryContextSelector | None = None,
    ) -> None:
        for dependency, method, name in (
            (coverage, "analyze", "coverage"),
            (verifier, "verify", "verifier"),
            (repairer, "propose", "repairer"),
            (repair_engine, "preview", "repair_engine"),
            (repair_validator, "validate", "repair_validator"),
        ):
            if not callable(getattr(dependency, method, None)):
                raise QaModelValidationError(f"{name} must provide {method}")
        self._coverage = coverage
        self._verifier = verifier
        self._repairer = repairer
        self._engine = repair_engine
        self._repair_validator = repair_validator
        self._store = store
        self._journal = journal
        self._journal_path = Path(journal_path)
        self._additions = additions
        self._language = language
        self._language_rules = language_rules
        self._russian_nlp = russian_nlp
        self._glossary_selector = glossary_selector or GlossaryContextSelector()

    async def check_chapter(
        self,
        request: ChapterQaRequest,
        options: QaOptions,
        cancellation: CancellationToken,
    ) -> ChapterQaResult:
        """Check one chapter, repair at most once per candidate, and record it."""
        if not isinstance(request, ChapterQaRequest):
            raise TypeError("request must be a ChapterQaRequest")
        if not isinstance(options, QaOptions):
            raise TypeError("options must be a QaOptions")
        cancellation.raise_if_cancelled()

        warnings: list[str] = []
        coverage: CoverageAnalysis | None = None
        if options.check_completeness:
            try:
                coverage = await self._coverage.analyze(request.coverage_request)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - QA never breaks the translation
                warnings.append("coverage_failed")
        else:
            warnings.append("completeness_check_disabled")

        verified: tuple[VerifiedCandidate, ...] = ()
        repairs: tuple[OmissionRepairOutcome, ...] = ()
        additions: tuple[AdditionCandidate, ...] = ()
        if coverage is not None:
            warnings.extend(coverage.warnings)
            verified = await self._verify_candidates(
                coverage, request, cancellation, warnings
            )
            repairs = await self._repair_verified_once(
                verified, coverage, request, options, cancellation, warnings
            )
            additions = await self._detect_additions(
                coverage, request, options, cancellation, warnings
            )

        language = await self._check_language(request, options, cancellation, warnings)
        metrics = coverage.metrics if coverage is not None else None
        risk, may_continue = _risk(verified, repairs, additions, coverage, warnings)
        result = ChapterQaResult(
            chapter_id=request.chapter_id,
            risk_level=risk,
            may_continue_translation=may_continue,
            coverage_mode=coverage.mode if coverage is not None else "unavailable",
            verified=verified,
            repairs=repairs,
            additions=additions,
            language=language,
            metrics=metrics,
            warnings=tuple(dict.fromkeys(warnings)),
        )
        self._record(result)
        return result

    @property
    def session_id(self) -> str:
        """The session every automatic repair of this service is recorded under."""
        return self._store.session_id

    async def undo_chapter(self, chapter_id: str) -> UndoResult:
        """Revert every automatic repair of one chapter without a model call."""
        return self._store.undo_chapter(chapter_id)

    async def undo_session(self, session_id: str) -> UndoResult:
        """Revert every automatic repair of one session, newest chapter first."""
        return self._store.undo_session(session_id)

    async def _verify_candidates(
        self,
        coverage: CoverageAnalysis,
        request: ChapterQaRequest,
        cancellation: CancellationToken,
        warnings: list[str],
    ) -> tuple[VerifiedCandidate, ...]:
        verified: list[VerifiedCandidate] = []
        for candidate in coverage.candidates:
            cancellation.raise_if_cancelled()
            context = coverage.contexts.get(candidate.candidate_id)
            if context is None:
                warnings.append("candidate_context_missing")
                continue
            glossary = self._glossary_selector.select_for_candidate(
                request.glossary,
                context.source_text,
                f"{context.source_before} {context.source_after}".strip(),
                MAX_GLOSSARY_TERMS_PER_CANDIDATE,
            )
            try:
                verified.append(
                    await self._verifier.verify(
                        candidate, context, glossary, request.model, cancellation
                    )
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one bad candidate is not a failure
                warnings.append("verification_failed")
        return tuple(verified)

    async def _repair_verified_once(
        self,
        verified: Sequence[VerifiedCandidate],
        coverage: CoverageAnalysis,
        request: ChapterQaRequest,
        options: QaOptions,
        cancellation: CancellationToken,
        warnings: list[str],
    ) -> tuple[OmissionRepairOutcome, ...]:
        outcomes: list[OmissionRepairOutcome] = []
        eligible = [item for item in verified if item.eligible_for_repair]
        if not eligible:
            return ()
        if not options.auto_repair_omissions:
            warnings.append("omission_auto_repair_disabled")
            return tuple(
                OmissionRepairOutcome(item.candidate.candidate_id, Decision.WARNING)
                for item in eligible
            )

        units = {unit.unit_id: unit for unit in coverage.target_units}
        try:
            html = request.translated_path.read_text(encoding="utf-8")
        except OSError:
            warnings.append("chapter_not_readable")
            return tuple(
                OmissionRepairOutcome(item.candidate.candidate_id, Decision.WARNING)
                for item in eligible
            )
        model = build_html_document_model(
            html, document_id=request.target_document_id
        )

        for index, item in enumerate(eligible):
            cancellation.raise_if_cancelled()
            if index >= options.max_repairs_per_chapter:
                outcomes.append(
                    OmissionRepairOutcome(
                        item.candidate.candidate_id,
                        Decision.WARNING,
                        reasons=("repair_budget_exhausted",),
                    )
                )
                continue
            outcome, model = await self._repair_one(
                item, units, model, request, cancellation
            )
            outcomes.append(outcome)
        return tuple(outcomes)

    async def _repair_one(
        self,
        item: VerifiedCandidate,
        units: Mapping[str, SemanticUnit],
        model: dict,
        request: ChapterQaRequest,
        cancellation: CancellationToken,
    ) -> tuple[OmissionRepairOutcome, dict]:
        candidate_id = item.candidate.candidate_id
        glossary = self._glossary_selector.select_for_candidate(
            request.glossary,
            item.context.source_text,
            f"{item.context.source_before} {item.context.source_after}".strip(),
            MAX_GLOSSARY_TERMS_PER_CANDIDATE,
        )
        try:
            proposal = await self._repairer.propose(
                item,
                RepairContext(
                    chapter_id=request.chapter_id,
                    model=request.model,
                    cancellation=cancellation,
                    glossary=glossary,
                    style_guide=request.style_guide,
                ),
            )
        except asyncio.CancelledError:
            raise
        except OmissionRepairError as error:
            return (
                OmissionRepairOutcome(
                    candidate_id,
                    Decision.REPAIR_REJECTED,
                    attempted=error.reason != "not_eligible",
                    reasons=(error.reason, error.detail) if error.detail else (error.reason,),
                ),
                model,
            )

        patch = _patch_for(item, units, model, request.chapter_id, proposal.translated_fragment)
        if patch is None:
            return (
                OmissionRepairOutcome(
                    candidate_id,
                    Decision.REPAIR_REJECTED,
                    attempted=True,
                    reasons=("anchor_units_unavailable",),
                ),
                model,
            )
        try:
            preview = self._engine.preview(model, patch)
        except StructuralRepairError as error:
            return (
                OmissionRepairOutcome(
                    candidate_id,
                    Decision.REPAIR_REJECTED,
                    attempted=True,
                    patch_id=patch.patch_id,
                    reasons=(type(error).__name__,),
                ),
                model,
            )
        if preview.status == "already_applied":
            return (
                OmissionRepairOutcome(
                    candidate_id, Decision.FIXED, attempted=False, patch_id=patch.patch_id
                ),
                model,
            )

        local = self._engine.validate(
            preview,
            RepairValidationContext(item.context.source_text, glossary),
        )
        if not local.accepted:
            return (
                OmissionRepairOutcome(
                    candidate_id,
                    Decision.REPAIR_REJECTED,
                    attempted=True,
                    patch_id=patch.patch_id,
                    reasons=local.reasons,
                ),
                model,
            )

        validation = await self._repair_validator.validate(
            ChapterSnapshot(request.chapter_id, preview.before_html),
            ChapterSnapshot(request.chapter_id, preview.rendered_html),
            item,
            proposal,
            model=request.model,
            cancellation=cancellation,
            glossary=glossary,
        )
        if not validation.accepted:
            return (
                OmissionRepairOutcome(
                    candidate_id,
                    Decision.REPAIR_REJECTED,
                    attempted=True,
                    patch_id=patch.patch_id,
                    reasons=validation.reasons,
                ),
                model,
            )

        try:
            self._engine.commit(preview, request.translated_path, self._store)
        except (ManualEditConflict, OSError) as error:
            return (
                OmissionRepairOutcome(
                    candidate_id,
                    Decision.REPAIR_REJECTED,
                    attempted=True,
                    patch_id=patch.patch_id,
                    reasons=(type(error).__name__,),
                ),
                model,
            )
        self._journal.append_repair(
            {
                "patch_id": patch.patch_id,
                "chapter_id": request.chapter_id,
                "candidate_id": candidate_id,
                "session_id": request.session_id,
                "fragment": proposal.translated_fragment,
            }
        )
        self._save_journal()
        return (
            OmissionRepairOutcome(
                candidate_id, Decision.FIXED, attempted=True, patch_id=patch.patch_id
            ),
            preview.document_model,
        )

    async def _detect_additions(
        self,
        coverage: CoverageAnalysis,
        request: ChapterQaRequest,
        options: QaOptions,
        cancellation: CancellationToken,
        warnings: list[str],
    ) -> tuple[AdditionCandidate, ...]:
        if self._additions is None or not options.detect_additions:
            return ()
        if coverage.mode != SEMANTIC_ALIGNMENT_MODE:
            return ()
        try:
            return await self._additions.detect(
                coverage,
                ChapterContext(
                    chapter_id=request.chapter_id,
                    model=request.model,
                    cancellation=cancellation,
                ),
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - additions are advisory
            warnings.append("addition_detection_failed")
            return ()

    async def _check_language(
        self,
        request: ChapterQaRequest,
        options: QaOptions,
        cancellation: CancellationToken,
        warnings: list[str],
    ) -> LanguageQaResult | None:
        if self._language is None or not options.check_language:
            return None
        try:
            html = request.translated_path.read_text(encoding="utf-8")
        except OSError:
            warnings.append("chapter_not_readable")
            return None
        model = build_html_document_model(html, document_id=request.chapter_id)
        language_request = LanguageQaRequest(
            chapter_id=request.chapter_id,
            document_model=model,
            source_language=request.source_language,
            target_language=request.target_language,
            model=request.model,
            cancellation=cancellation,
            source_text_by_block=request.source_text_by_block,
        )
        rule_candidates = await self._collect_rules(
            language_request, options, warnings
        )
        nlp_analysis = self._analyze_nlp(language_request, options, warnings)
        try:
            result = await self._language.check_chapter(
                language_request,
                rule_candidates=rule_candidates,
                nlp_analysis=nlp_analysis,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - language QA never breaks a chapter
            warnings.append("language_check_failed")
            return None
        if result.preview_model is not None and options.auto_repair_language:
            try:
                request.translated_path.write_text(
                    render_document_html(result.preview_model), encoding="utf-8"
                )
            except OSError:
                warnings.append("language_repair_not_written")
        return result

    async def _collect_rules(
        self, request: LanguageQaRequest, options: QaOptions, warnings: list[str]
    ) -> tuple[LanguageRuleIssue, ...]:
        if self._language_rules is None or not options.capabilities.language_tool_enabled:
            return ()
        try:
            return tuple(
                await self._language_rules.collect(request, options.capabilities)
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - an unavailable analyzer is a warning
            warnings.append("language_tool_unavailable")
            return ()

    def _analyze_nlp(
        self, request: LanguageQaRequest, options: QaOptions, warnings: list[str]
    ) -> RussianNlpAnalysis | None:
        if self._russian_nlp is None or not options.capabilities.slovnet_enabled:
            return None
        try:
            return self._russian_nlp.analyze(request, options.capabilities)
        except Exception:  # noqa: BLE001 - an unavailable analyzer is a warning
            warnings.append("slovnet_unavailable")
            return None

    def _record(self, result: ChapterQaResult) -> None:
        entries = [
            QaJournalEntry(
                entry_id=f"{result.chapter_id}:{repair.candidate_id}",
                chapter_id=result.chapter_id,
                decision=repair.decision,
            )
            for repair in result.repairs
        ]
        entries.extend(
            QaJournalEntry(
                entry_id=f"{result.chapter_id}:{addition.candidate_id}",
                chapter_id=result.chapter_id,
                decision=(
                    Decision.HALLUCINATED_ADDITION
                    if addition.blocks_gate
                    else Decision.WARNING
                ),
            )
            for addition in result.additions
        )
        self._journal.record_chapter_result(metrics=result.metrics, entries=entries)
        self._save_journal()

    def _save_journal(self) -> None:
        try:
            self._journal.save(self._journal_path)
        except OSError:
            # A journal that cannot be written must not undo an applied repair.
            return


def _patch_for(
    item: VerifiedCandidate,
    units: Mapping[str, SemanticUnit],
    model: dict,
    chapter_id: str,
    fragment: str,
) -> StructuralPatch | None:
    """Bind one verified candidate to an exact insertion point in the chapter."""
    left_anchor = item.candidate.left_anchor
    right_anchor = item.candidate.right_anchor
    if left_anchor is None or right_anchor is None:
        return None
    if not left_anchor.target_unit_ids or not right_anchor.target_unit_ids:
        return None
    left_unit = units.get(left_anchor.target_unit_ids[-1])
    right_unit = units.get(right_anchor.target_unit_ids[0])
    if left_unit is None or right_unit is None:
        return None
    identity = "\x1f".join((chapter_id, item.candidate.candidate_id, fragment))
    return StructuralPatch(
        patch_id="qa" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20],
        chapter_id=chapter_id,
        expected_fingerprint=document_fingerprint(model),
        left_anchor_unit_id=left_unit.unit_id,
        right_anchor_unit_id=right_unit.unit_id,
        parent_block_id=left_unit.block_id,
        translated_fragment=fragment,
    )


def _risk(
    verified: Sequence[VerifiedCandidate],
    repairs: Sequence[OmissionRepairOutcome],
    additions: Sequence[AdditionCandidate],
    coverage: CoverageAnalysis | None,
    warnings: Sequence[str],
) -> tuple[RiskLevel, bool]:
    """Classify the chapter and decide whether translation may continue."""
    fixed = {
        repair.candidate_id
        for repair in repairs
        if repair.decision is Decision.FIXED
    }
    unresolved = [
        item
        for item in verified
        if item.eligible_for_repair and item.candidate.candidate_id not in fixed
    ]
    blocking_additions = [addition for addition in additions if addition.blocks_gate]
    if unresolved or blocking_additions:
        return RiskLevel.HIGH, False
    if coverage is None:
        return RiskLevel.MEDIUM, True
    reported = [item for item in verified if item.verdict is not None]
    if coverage.mode != SEMANTIC_ALIGNMENT_MODE or warnings or reported or additions:
        return RiskLevel.MEDIUM, True
    return RiskLevel.LOW, True
