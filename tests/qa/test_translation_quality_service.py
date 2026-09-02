"""End-to-end contracts of the single chapter QA entry point."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from gemini_translator.qa.addition_detector import AdditionCandidate
from gemini_translator.qa.capabilities import QaCapabilitySettings
from gemini_translator.qa.coverage_service import (
    EMBEDDINGS_UNAVAILABLE_WARNING,
    STATISTICS_LLM_ONLY_MODE,
    CoverageRequest,
    DefaultCandidateFilter,
    DefaultCoverageMetricsCollector,
    SemanticCoverageService,
)
from gemini_translator.qa.embeddings.base import EmbeddingBatch
from gemini_translator.qa.embeddings.factory import EmbeddingUnavailableError
from gemini_translator.qa.glossary_context import GlossaryTerm
from gemini_translator.qa.journal import QaJournal
from gemini_translator.qa.llm import CancellationToken, QaModelSelection
from gemini_translator.qa.llm.omission_repairer import OmissionRepairError
from gemini_translator.qa.llm.schemas import OmissionVerdict, RepairProposal
from gemini_translator.qa.models import (
    AlignmentResult,
    AlignmentSpan,
    Decision,
    GapCandidate,
    RiskLevel,
    VerifiedCandidate,
)
from gemini_translator.qa.repair_store import RepairStore
from gemini_translator.qa.semantic_units import SemanticUnitExtractor
from gemini_translator.qa.service import (
    ChapterQaRequest,
    QaOptions,
    TranslationQualityService,
)
from gemini_translator.qa.structural_repair import (
    RepairValidation,
    StructuralRepairEngine,
)
from gemini_translator.utils.epub_json import (
    build_html_document_model,
    build_translation_payload,
)


_SOURCE = ("He opened the door.", "The room was empty.", "He left at once.")
_TARGET = ("Он открыл дверь.", "Он сразу ушёл.")
_MISSING = "Комната была пуста."
_FACT = "Потеряно: комната была пуста."
_CHAPTER_HTML = "".join(f"<p>{text}</p>" for text in _TARGET)


class _Provider:
    name = "recording"

    def __init__(self, *, available: bool = True) -> None:
        self.available = available

    async def embed(self, request):
        if not self.available:
            raise EmbeddingUnavailableError(())
        dimensions = request.dimensions or 3
        vectors = np.zeros((len(request.texts), dimensions), dtype=np.float32)
        for index in range(len(request.texts)):
            vectors[index, index % dimensions] = 1.0
        return EmbeddingBatch(vectors, self.name, request.model, dimensions)


class _GapAligner:
    """Leave the middle source sentence unaligned between two strong anchors."""

    def align(self, source, target):
        source_ids = tuple(unit.unit_id for unit in source.units)
        target_ids = tuple(unit.unit_id for unit in target.units)
        first = AlignmentSpan((source_ids[0],), (target_ids[0],), 0.99, "1:1")
        gap_span = AlignmentSpan((source_ids[1],), (), 0.0, "1:0")
        last = AlignmentSpan((source_ids[2],), (target_ids[1],), 0.98, "1:1")
        gap = GapCandidate(
            "gap-" + hashlib.sha256(b"service").hexdigest()[:20],
            "source",
            (source_ids[1],),
            (),
            first,
            last,
            True,
            ("missing_in_target",),
        )
        return AlignmentResult((first, gap_span, last), (gap,), 6)


class _CleanAligner:
    """Cover every unit, merging the last two source sentences into one."""

    def align(self, source, target):
        source_ids = tuple(unit.unit_id for unit in source.units)
        target_ids = tuple(unit.unit_id for unit in target.units)
        spans = (
            AlignmentSpan((source_ids[0],), (target_ids[0],), 0.99, "1:1"),
            AlignmentSpan(source_ids[1:], (target_ids[1],), 0.97, "2:1"),
        )
        return AlignmentResult(spans, (), 4)


class _Verifier:
    def __init__(self, *, decision: str = "missing_content", eligible: bool = True):
        self.decision = decision
        self.eligible = eligible
        self.calls = 0

    async def verify(self, candidate, context, glossary, model, cancellation):
        self.calls += 1
        self.last_glossary = glossary
        verdict = OmissionVerdict(
            decision=self.decision,
            confidence=0.97,
            source_unit_ids=candidate.source_unit_ids,
            missing_facts=(_FACT,) if self.decision == "missing_content" else (),
            explanation="Локальная проверка.",
        )
        from gemini_translator.qa.foreign_text_filter import ForeignTextFilter

        return VerifiedCandidate(
            candidate=candidate,
            context=context,
            verdict=verdict,
            foreign_text_decision=ForeignTextFilter().classify(candidate, context),
            eligible_for_repair=self.eligible and self.decision == "missing_content",
            status="verified",
        )


class _Repairer:
    def __init__(self, fragment: str = _MISSING, error: OmissionRepairError | None = None):
        self.fragment = fragment
        self.error = error
        self.calls = 0

    async def propose(self, verified, request):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return RepairProposal(
            candidate_id=verified.candidate.candidate_id,
            translated_fragment=self.fragment,
            glossary_terms_used=(),
        )


class _PostValidator:
    def __init__(self, accepted: bool = True) -> None:
        self.accepted = accepted
        self.calls = 0

    async def validate(self, before, preview, candidate, proposal, **kwargs):
        self.calls += 1
        return RepairValidation(self.accepted, () if self.accepted else ("post_check_rejected",))


def _payload(document_id: str, *texts: str) -> dict:
    return {
        "document_id": document_id,
        "blocks": [
            {
                "id": f"b-{document_id}-{index}",
                "tag": "p",
                "role": "paragraph",
                "inlines": [
                    {"id": f"i-{document_id}-{index}", "type": "text", "text": text}
                ],
            }
            for index, text in enumerate(texts)
        ],
    }


def _coverage_service(aligner, *, available: bool = True) -> SemanticCoverageService:
    return SemanticCoverageService(
        extractor=SemanticUnitExtractor(QaCapabilitySettings()),
        provider=_Provider(available=available),
        aligner=aligner,
        candidate_filter=DefaultCandidateFilter(),
        metrics_collector=DefaultCoverageMetricsCollector(),
    )


@pytest.fixture()
def chapter(tmp_path: Path) -> Path:
    path = tmp_path / "chapter-1.html"
    path.write_text(_CHAPTER_HTML, encoding="utf-8")
    return path


def _target_payload_from(path: Path) -> dict:
    model = build_html_document_model(
        path.read_text(encoding="utf-8"), document_id="target-doc"
    )
    return build_translation_payload(model, document_id="target-doc")


def _request(chapter: Path) -> ChapterQaRequest:
    return ChapterQaRequest(
        chapter_id="chapter-1",
        coverage_request=CoverageRequest(
            chapter_id="chapter-1",
            source_payload=_payload("source-doc", *_SOURCE),
            target_payload=_target_payload_from(chapter),
            source_language="en",
            target_language="ru",
            embedding_model="embed-model",
            embedding_dimensions=3,
        ),
        translated_path=chapter,
        model=QaModelSelection("gemini", "qa-model"),
        glossary=(GlossaryTerm("room", "комната"),),
        style_guide="Третье лицо.",
    )


def _service(
    tmp_path: Path,
    *,
    aligner=None,
    verifier=None,
    repairer=None,
    post_validator=None,
    additions=None,
    language=None,
    available: bool = True,
) -> tuple[TranslationQualityService, QaJournal, Path]:
    journal = QaJournal.empty(book_id="book-1")
    journal_path = tmp_path / "translation_qa.json"
    service = TranslationQualityService(
        coverage=_coverage_service(aligner or _GapAligner(), available=available),
        verifier=verifier or _Verifier(),
        repairer=repairer or _Repairer(),
        repair_engine=StructuralRepairEngine("ru", QaCapabilitySettings()),
        repair_validator=post_validator or _PostValidator(),
        store=RepairStore(tmp_path / "backups", session_id="session-1"),
        journal=journal,
        journal_path=journal_path,
        additions=additions,
        language=language,
    )
    return service, journal, journal_path


def _check(service, request, options: QaOptions | None = None):
    return asyncio.run(
        service.check_chapter(request, options or QaOptions(), CancellationToken())
    )


def test_clean_chapter_costs_no_requests_and_never_blocks(tmp_path, chapter):
    """A chapter with no candidates must not spend a single QA request."""
    verifier = _Verifier()
    repairer = _Repairer()
    service, journal, journal_path = _service(
        tmp_path, aligner=_CleanAligner(), verifier=verifier, repairer=repairer
    )

    result = _check(service, _request(chapter))

    assert verifier.calls == 0
    assert repairer.calls == 0
    assert result.risk_level is RiskLevel.LOW
    assert result.may_continue_translation is True
    assert result.repairs == ()
    assert chapter.read_text(encoding="utf-8") == _CHAPTER_HTML
    assert journal_path.exists()
    assert journal.metrics["chapter-1"].chapter_id == "chapter-1"


def test_confirmed_omission_is_repaired_once_and_recorded(tmp_path, chapter):
    """The whole cascade must be able to close a real gap end to end."""
    repairer = _Repairer()
    post_validator = _PostValidator()
    service, journal, journal_path = _service(
        tmp_path, repairer=repairer, post_validator=post_validator
    )

    result = _check(service, _request(chapter))

    assert repairer.calls == 1
    assert post_validator.calls == 1
    assert [repair.decision for repair in result.repairs] == [Decision.FIXED]
    assert result.may_continue_translation is True
    assert result.risk_level is not RiskLevel.HIGH
    assert _MISSING in chapter.read_text(encoding="utf-8")
    saved = json.loads(journal_path.read_text(encoding="utf-8"))
    assert len(saved["repairs"]) == 1
    assert saved["repairs"][0]["chapter_id"] == "chapter-1"
    assert any(entry["decision"] == "fixed" for entry in saved["candidates"])


def test_ambiguous_omission_is_reported_and_never_repaired(tmp_path, chapter):
    """An unconfirmed candidate must not spend a repair request or edit a file."""
    repairer = _Repairer()
    service, _journal, _path = _service(
        tmp_path, verifier=_Verifier(decision="ambiguous"), repairer=repairer
    )

    result = _check(service, _request(chapter))

    assert repairer.calls == 0
    assert result.repairs == ()
    assert result.risk_level is RiskLevel.MEDIUM
    assert result.may_continue_translation is True
    assert chapter.read_text(encoding="utf-8") == _CHAPTER_HTML


def test_unrepaired_confirmed_omission_blocks_the_next_chapter(tmp_path, chapter):
    """A confirmed loss with no successful repair is exactly what a gate is for."""
    service, _journal, _path = _service(
        tmp_path, post_validator=_PostValidator(accepted=False)
    )

    result = _check(service, _request(chapter))

    assert [repair.decision for repair in result.repairs] == [Decision.REPAIR_REJECTED]
    assert result.repairs[0].attempted is True
    assert result.risk_level is RiskLevel.HIGH
    assert result.may_continue_translation is False
    assert chapter.read_text(encoding="utf-8") == _CHAPTER_HTML


def test_rejected_proposal_is_attempted_exactly_once(tmp_path, chapter):
    """A repairer refusal must not be retried inside the same chapter pass."""
    repairer = _Repairer(error=OmissionRepairError("fragment_rejected", "anchor_echo"))
    service, _journal, _path = _service(tmp_path, repairer=repairer)

    result = _check(service, _request(chapter))

    assert repairer.calls == 1
    assert result.repairs[0].decision is Decision.REPAIR_REJECTED
    assert result.repairs[0].reasons == ("fragment_rejected", "anchor_echo")
    assert result.may_continue_translation is False


def test_disabled_auto_repair_reports_without_touching_the_chapter(tmp_path, chapter):
    """With auto-repair off, a confirmed gap is a report, not an edit."""
    repairer = _Repairer()
    service, _journal, _path = _service(tmp_path, repairer=repairer)

    result = _check(
        service, _request(chapter), QaOptions(auto_repair_omissions=False)
    )

    assert repairer.calls == 0
    assert [repair.decision for repair in result.repairs] == [Decision.WARNING]
    assert "omission_auto_repair_disabled" in result.warnings
    assert chapter.read_text(encoding="utf-8") == _CHAPTER_HTML


def test_embedding_outage_defers_without_blocking(tmp_path, chapter):
    """A provider outage is a deferral, never a reason to stop translating."""
    verifier = _Verifier()
    service, _journal, _path = _service(tmp_path, verifier=verifier, available=False)

    result = _check(service, _request(chapter))

    assert result.coverage_mode == STATISTICS_LLM_ONLY_MODE
    assert EMBEDDINGS_UNAVAILABLE_WARNING in result.warnings
    assert verifier.calls == 0
    assert result.may_continue_translation is True
    assert result.risk_level is RiskLevel.MEDIUM


def test_coverage_failure_never_breaks_the_chapter(tmp_path, chapter):
    """An internal QA error must degrade to a warning, not an exception."""

    class _BrokenCoverage:
        async def analyze(self, request):
            raise RuntimeError("coverage exploded")

    service, _journal, _path = _service(tmp_path)
    service._coverage = _BrokenCoverage()  # noqa: SLF001 - exercising the failure path

    result = _check(service, _request(chapter))

    assert "coverage_failed" in result.warnings
    assert result.may_continue_translation is True
    assert result.coverage_mode == "unavailable"


def test_confirmed_addition_blocks_the_gate(tmp_path, chapter):
    """An invented fact must stop the queue even when nothing was omitted."""

    class _Additions:
        async def detect(self, coverage, context):
            gap = GapCandidate(
                "gap-" + hashlib.sha256(b"addition").hexdigest()[:20],
                "target",
                (),
                ("unit-1",),
                AlignmentSpan(("s0",), ("t0",), 0.9, "1:1"),
                AlignmentSpan(("s2",), ("t2",), 0.9, "1:1"),
                False,
                ("addition",),
            )
            from gemini_translator.qa.llm.schemas import AdditionVerdict
            from gemini_translator.qa.models import CandidateContext

            context_value = CandidateContext(
                candidate_id=gap.candidate_id,
                source_text="",
                target_text="Он вспомнил о матери.",
                source_before="a",
                source_after="b",
                target_before="в",
                target_after="г",
                source_language="en",
                target_language="ru",
                candidate_language="ru",
            )
            return (
                AdditionCandidate(
                    candidate_id=gap.candidate_id,
                    candidate=gap,
                    context=context_value,
                    verdict=AdditionVerdict(
                        candidate_id=gap.candidate_id,
                        decision="hallucinated_addition",
                        confidence=0.95,
                        target_unit_ids=("unit-1",),
                        added_facts=("Мать в оригинале не упоминается.",),
                        explanation="Факт отсутствует в исходнике.",
                    ),
                    status="verified",
                    blocks_gate=True,
                ),
            )

    service, _journal, journal_path = _service(
        tmp_path, aligner=_CleanAligner(), additions=_Additions()
    )

    result = _check(service, _request(chapter))

    assert len(result.additions) == 1
    assert result.risk_level is RiskLevel.HIGH
    assert result.may_continue_translation is False
    saved = json.loads(journal_path.read_text(encoding="utf-8"))
    assert any(
        entry["decision"] == "hallucinated_addition" for entry in saved["candidates"]
    )


def test_language_pass_runs_after_completeness_and_writes_its_preview(
    tmp_path, chapter
):
    """Language QA follows completeness and persists only what it confirmed."""
    calls: list[str] = []

    class _Language:
        async def check_chapter(self, request, *, rule_candidates=(), nlp_analysis=None):
            calls.append(request.chapter_id)
            from gemini_translator.qa.language_validation import (
                LanguageQaResult,
                LanguageReplacement,
                apply_language_replacements,
            )

            blocks = build_translation_payload(request.document_model)["blocks"]
            replacement = LanguageReplacement(
                "issue-1", blocks[-1].get("id"), "сразу ушёл", "тут же ушёл"
            )
            preview = apply_language_replacements(
                request.document_model, (replacement,)
            )
            return LanguageQaResult(
                chapter_id=request.chapter_id,
                applied=(replacement,),
                preview_model=preview,
            )

    service, _journal, _path = _service(
        tmp_path, aligner=_CleanAligner(), language=_Language()
    )

    result = _check(service, _request(chapter))

    assert calls == ["chapter-1"]
    assert result.language is not None
    assert "тут же ушёл" in chapter.read_text(encoding="utf-8")


def test_undo_restores_every_automatic_repair(tmp_path, chapter):
    """Undo must reverse the service's own edits without any model call."""
    original = chapter.read_bytes()
    service, _journal, _path = _service(tmp_path)

    _check(service, _request(chapter))
    assert chapter.read_bytes() != original

    result = asyncio.run(service.undo_chapter("chapter-1"))

    assert result.status == "restored"
    assert chapter.read_bytes() == original


def test_repeated_pass_never_applies_the_same_repair_twice(tmp_path, chapter):
    """A restart or a manual re-check must not duplicate an applied fragment."""
    service, _journal, _path = _service(tmp_path)

    _check(service, _request(chapter))
    first = chapter.read_text(encoding="utf-8")
    _check(service, _request(chapter))

    assert chapter.read_text(encoding="utf-8").count(_MISSING) == 1
    assert first.count(_MISSING) == 1


def test_repair_budget_limits_automatic_edits(tmp_path, chapter):
    """A chapter full of candidates must not turn into an unbounded edit run."""
    service, _journal, _path = _service(tmp_path)

    result = _check(service, _request(chapter), QaOptions(max_repairs_per_chapter=0))

    assert [repair.decision for repair in result.repairs] == [Decision.WARNING]
    assert result.repairs[0].reasons == ("repair_budget_exhausted",)
    assert chapter.read_text(encoding="utf-8") == _CHAPTER_HTML


def test_only_candidate_local_glossary_terms_reach_the_verifier(tmp_path, chapter):
    """The whole book glossary must never be pushed into a candidate request."""
    verifier = _Verifier()
    service, _journal, _path = _service(tmp_path, verifier=verifier)
    request = replace(
        _request(chapter),
        glossary=(
            GlossaryTerm("room", "комната"),
            GlossaryTerm("unrelated-secret", "несвязанное"),
        ),
    )

    _check(service, request)

    assert [term.original_term for term in verifier.last_glossary] == ["room"]


def test_cancellation_stops_the_pass_immediately(tmp_path, chapter):
    """A cancelled session must not start a new QA stage."""
    verifier = _Verifier()
    service, _journal, _path = _service(tmp_path, verifier=verifier)
    token = CancellationToken()
    token.cancel()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service.check_chapter(_request(chapter), QaOptions(), token))

    assert verifier.calls == 0
    assert chapter.read_text(encoding="utf-8") == _CHAPTER_HTML


def test_language_fixes_are_backed_up_and_undoable(tmp_path, chapter):
    """A language fix edits the book, so undo must reach it like any repair."""
    from gemini_translator.qa.language_validation import (
        LanguageQaResult,
        LanguageReplacement,
        apply_language_replacements,
    )

    class _Language:
        async def check_chapter(self, request, *, rule_candidates=(), nlp_analysis=None):
            blocks = build_translation_payload(request.document_model)["blocks"]
            replacement = LanguageReplacement(
                "issue-1", blocks[-1]["id"], "сразу ушёл", "тут же ушёл"
            )
            return LanguageQaResult(
                chapter_id=request.chapter_id,
                applied=(replacement,),
                preview_model=apply_language_replacements(
                    request.document_model, (replacement,)
                ),
            )

    original = chapter.read_bytes()
    service, journal, journal_path = _service(
        tmp_path, aligner=_CleanAligner(), language=_Language()
    )

    result = _check(service, _request(chapter))

    assert "тут же ушёл" in chapter.read_text(encoding="utf-8")
    assert result.language is not None
    saved = json.loads(journal_path.read_text(encoding="utf-8"))
    assert any(entry["candidate_id"] == "language" for entry in saved["repairs"])

    undo = asyncio.run(service.undo_chapter("chapter-1"))

    assert undo.status == "restored"
    assert chapter.read_bytes() == original


def test_a_language_fix_that_cannot_be_recorded_is_rolled_back(tmp_path, chapter):
    """An edit with no way back is worse than no edit at all."""
    from gemini_translator.qa.language_validation import (
        LanguageQaResult,
        LanguageReplacement,
        apply_language_replacements,
    )

    class _Language:
        async def check_chapter(self, request, *, rule_candidates=(), nlp_analysis=None):
            blocks = build_translation_payload(request.document_model)["blocks"]
            replacement = LanguageReplacement(
                "issue-1", blocks[-1]["id"], "сразу ушёл", "тут же ушёл"
            )
            return LanguageQaResult(
                chapter_id=request.chapter_id,
                applied=(replacement,),
                preview_model=apply_language_replacements(
                    request.document_model, (replacement,)
                ),
            )

    original = chapter.read_bytes()
    service, _journal, _path = _service(
        tmp_path, aligner=_CleanAligner(), language=_Language()
    )

    def broken(applied):
        raise OSError("journal is not writable")

    service._store.record_applied = broken  # noqa: SLF001 - exercising the failure

    result = _check(service, _request(chapter))

    assert chapter.read_bytes() == original
    assert "language_repair_not_recorded" in result.warnings


def test_the_pass_size_reaches_the_language_request(tmp_path, chapter):
    """Настройка размера бесполезна, пока она не доезжает до самого запроса."""
    from gemini_translator.qa.language_validation import LanguageQaResult

    seen: list[int] = []

    class _Language:
        async def check_chapter(self, request, *, rule_candidates=(), nlp_analysis=None):
            seen.append(request.max_chunk_chars)
            return LanguageQaResult(chapter_id=request.chapter_id)

    service, _journal, _path = _service(
        tmp_path, aligner=_CleanAligner(), language=_Language()
    )

    _check(service, _request(chapter), QaOptions(language_chunk_chars=25000))

    assert seen == [25000]
