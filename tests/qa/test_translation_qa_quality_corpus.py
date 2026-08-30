"""Acceptance for the promises the design makes about what QA may change."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from gemini_translator.qa.capabilities import QaCapabilitySettings
from gemini_translator.qa.coverage_service import (
    CoverageRequest,
    DefaultCandidateFilter,
    DefaultCoverageMetricsCollector,
    SemanticCoverageService,
)
from gemini_translator.qa.embeddings.base import EmbeddingBatch
from gemini_translator.qa.foreign_text_filter import ForeignTextFilter
from gemini_translator.qa.journal import QaJournal
from gemini_translator.qa.llm import CancellationToken, QaModelSelection
from gemini_translator.qa.llm.schemas import OmissionVerdict, RepairProposal
from gemini_translator.qa.models import (
    AlignmentResult,
    AlignmentSpan,
    ChapterMetrics,
    Decision,
    GapCandidate,
    RiskLevel,
    VerifiedCandidate,
)
from gemini_translator.qa.book_metrics import BookMetricsAnalyzer
from gemini_translator.qa.ratio_profiles import get_ratio_profile
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


# --- absolute language profiles -------------------------------------------


@pytest.mark.parametrize("ratio", [2.80, 3.05, 3.30])
def test_cjk_ratios_inside_the_profile_are_normal(ratio):
    """A Chinese chapter three times longer in Russian is ordinary, not a defect."""
    assert get_ratio_profile("zh", "ru").contains(ratio) is True


@pytest.mark.parametrize("ratio", [2.79, 3.31])
def test_cjk_ratios_outside_the_profile_are_signals(ratio):
    """The profile edges are the whole point of having a profile."""
    assert get_ratio_profile("zh", "ru").contains(ratio) is False


@pytest.mark.parametrize("ratio", [0.92, 1.00, 1.20])
def test_alphabetic_ratios_inside_the_profile_are_normal(ratio):
    """An English chapter of roughly equal length must not be flagged."""
    assert get_ratio_profile("en", "ru").contains(ratio) is True


@pytest.mark.parametrize("ratio", [0.91, 1.21])
def test_alphabetic_ratios_outside_the_profile_are_signals(ratio):
    assert get_ratio_profile("en", "ru").contains(ratio) is False


def test_a_relative_deviation_inside_the_profile_never_authorizes_a_change():
    """Being unusual for this book is not the same as being wrong."""
    metrics = [
        ChapterMetrics(
            chapter_id=f"chapter-{index}",
            source_language="zh",
            target_language="ru",
            source_chars=1000,
            translated_chars=2900,
        )
        for index in range(6)
    ]
    # One chapter is unusual for this book but still inside the language profile.
    metrics.append(
        ChapterMetrics(
            chapter_id="chapter-6",
            source_language="zh",
            target_language="ru",
            source_chars=1000,
            translated_chars=3280,
        )
    )
    analyzer = BookMetricsAnalyzer()
    frame = analyzer.analyze(metrics)

    risk = analyzer.classify_ratio_risk(frame, "chapter-6")

    assert risk.within_absolute_profile is True
    assert risk.relative_risk in {"medium", "high"}
    assert risk.requires_deep_check is True


# --- omission corpus -------------------------------------------------------


_SOURCE = (
    "He opened the door.",
    "The room was empty.",
    "He left at once.",
)
_TARGET = ("Он открыл дверь.", "Он сразу ушёл.")
_MISSING = "Комната была пуста."
_CHAPTER_HTML = "".join(f"<p>{text}</p>" for text in _TARGET)


class _Provider:
    name = "recording"

    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, request):
        self.calls += 1
        dimensions = request.dimensions or 3
        vectors = np.zeros((len(request.texts), dimensions), dtype=np.float32)
        for index in range(len(request.texts)):
            vectors[index, index % dimensions] = 1.0
        return EmbeddingBatch(vectors, self.name, request.model, dimensions)


class _Aligner:
    def align(self, source, target):
        source_ids = tuple(unit.unit_id for unit in source.units)
        target_ids = tuple(unit.unit_id for unit in target.units)
        first = AlignmentSpan((source_ids[0],), (target_ids[0],), 0.99, "1:1")
        gap_span = AlignmentSpan((source_ids[1],), (), 0.0, "1:0")
        last = AlignmentSpan((source_ids[2],), (target_ids[1],), 0.98, "1:1")
        gap = GapCandidate(
            "gap-" + hashlib.sha256(b"corpus").hexdigest()[:20],
            "source",
            (source_ids[1],),
            (),
            first,
            last,
            True,
            ("missing_in_target",),
        )
        return AlignmentResult((first, gap_span, last), (gap,), 6)


class _Verifier:
    def __init__(self, decision: str = "missing_content") -> None:
        self.decision = decision

    async def verify(self, candidate, context, glossary, model, cancellation):
        verdict = OmissionVerdict(
            decision=self.decision,
            confidence=0.97,
            source_unit_ids=candidate.source_unit_ids,
            missing_facts=(
                ("Потеряно: комната была пуста.",)
                if self.decision == "missing_content"
                else ()
            ),
            explanation="Корпусный случай.",
        )
        return VerifiedCandidate(
            candidate=candidate,
            context=context,
            verdict=verdict,
            foreign_text_decision=ForeignTextFilter().classify(candidate, context),
            eligible_for_repair=self.decision == "missing_content",
            status="verified",
        )


class _Repairer:
    async def propose(self, verified, request):
        return RepairProposal(
            candidate_id=verified.candidate.candidate_id,
            translated_fragment=_MISSING,
            glossary_terms_used=(),
        )


class _PostValidator:
    def __init__(self, accepted: bool = True) -> None:
        self.accepted = accepted
        self.seen = []

    async def validate(self, before, preview, candidate, proposal, **kwargs):
        self.seen.append(candidate)
        return RepairValidation(
            self.accepted, () if self.accepted else ("post_check_rejected",)
        )


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


def _service(tmp_path: Path, *, verifier=None, post_validator=None, provider=None):
    journal = QaJournal.empty(book_id="book-1")
    return TranslationQualityService(
        coverage=SemanticCoverageService(
            extractor=SemanticUnitExtractor(QaCapabilitySettings()),
            provider=provider or _Provider(),
            aligner=_Aligner(),
            candidate_filter=DefaultCandidateFilter(),
            metrics_collector=DefaultCoverageMetricsCollector(),
        ),
        verifier=verifier or _Verifier(),
        repairer=_Repairer(),
        repair_engine=StructuralRepairEngine("ru", QaCapabilitySettings()),
        repair_validator=post_validator or _PostValidator(),
        store=RepairStore(tmp_path / "backups", session_id="session-1"),
        journal=journal,
        journal_path=tmp_path / "translation_qa.json",
    )


def _request(chapter: Path) -> ChapterQaRequest:
    model = build_html_document_model(
        chapter.read_text(encoding="utf-8"), document_id="target-doc"
    )
    return ChapterQaRequest(
        chapter_id="chapter-1",
        coverage_request=CoverageRequest(
            chapter_id="chapter-1",
            source_payload=_payload("source-doc", *_SOURCE),
            target_payload=build_translation_payload(model, document_id="target-doc"),
            source_language="en",
            target_language="ru",
            embedding_model="embed-model",
            embedding_dimensions=3,
        ),
        translated_path=chapter,
        model=QaModelSelection("gemini", "qa-model"),
    )


@pytest.fixture()
def chapter(tmp_path: Path) -> Path:
    path = tmp_path / "chapter-1.html"
    path.write_text(_CHAPTER_HTML, encoding="utf-8")
    return path


def test_a_significant_omission_is_detected_and_repaired_with_two_anchors(
    tmp_path, chapter
):
    """Every promise about an applied repair, asserted on one real repair."""
    post_validator = _PostValidator()
    service = _service(tmp_path, post_validator=post_validator)

    result = asyncio.run(
        service.check_chapter(_request(chapter), QaOptions(), CancellationToken())
    )

    detected = {item.candidate.candidate_id for item in result.verified}
    applied = [repair for repair in result.repairs if repair.decision is Decision.FIXED]

    assert detected
    assert len(applied) == 1
    assert all(item.candidate.left_anchor is not None for item in result.verified)
    assert all(item.candidate.right_anchor is not None for item in result.verified)
    assert post_validator.seen, "an applied repair must pass the post-check"
    assert chapter.read_text(encoding="utf-8").count(_MISSING) == 1


def test_negative_controls_are_never_modified(tmp_path, chapter):
    """A covered or ambiguous case must leave the chapter byte-identical."""
    before = chapter.read_bytes()

    for decision in ("covered", "ambiguous", "intentional_foreign"):
        service = _service(tmp_path, verifier=_Verifier(decision))
        result = asyncio.run(
            service.check_chapter(_request(chapter), QaOptions(), CancellationToken())
        )

        assert result.repairs == ()
        assert chapter.read_bytes() == before


def test_a_repair_is_never_applied_twice(tmp_path, chapter):
    """Re-running the whole cascade must not duplicate an applied fragment."""
    service = _service(tmp_path)

    asyncio.run(
        service.check_chapter(_request(chapter), QaOptions(), CancellationToken())
    )
    first = chapter.read_text(encoding="utf-8")
    asyncio.run(
        service.check_chapter(_request(chapter), QaOptions(), CancellationToken())
    )

    assert first.count(_MISSING) == 1
    assert chapter.read_text(encoding="utf-8").count(_MISSING) == 1


def test_a_failed_post_check_leaves_the_chapter_untouched_and_blocks(tmp_path, chapter):
    """An unconfirmed repair is exactly the case the gate exists for."""
    before = chapter.read_bytes()
    service = _service(tmp_path, post_validator=_PostValidator(accepted=False))

    result = asyncio.run(
        service.check_chapter(_request(chapter), QaOptions(), CancellationToken())
    )

    assert chapter.read_bytes() == before
    assert result.may_continue_translation is False
    assert result.risk_level is RiskLevel.HIGH


def test_undo_restores_the_chapter_exactly(tmp_path, chapter):
    """Everything QA writes must be reversible to the byte."""
    original = chapter.read_bytes()
    service = _service(tmp_path)

    asyncio.run(
        service.check_chapter(_request(chapter), QaOptions(), CancellationToken())
    )
    undo = asyncio.run(service.undo_chapter("chapter-1"))

    assert undo.status == "restored"
    assert chapter.read_bytes() == original


def test_the_journal_records_every_decision_it_made(tmp_path, chapter):
    """A repair nobody can find in the history is not auditable."""
    service = _service(tmp_path)

    asyncio.run(
        service.check_chapter(_request(chapter), QaOptions(), CancellationToken())
    )
    saved = json.loads((tmp_path / "translation_qa.json").read_text(encoding="utf-8"))

    assert saved["schema_version"] == 2
    assert len(saved["repairs"]) == 1
    assert any(entry["decision"] == "fixed" for entry in saved["candidates"])
    assert saved["chapter_states"][0]["chapter_id"] == "chapter-1"
