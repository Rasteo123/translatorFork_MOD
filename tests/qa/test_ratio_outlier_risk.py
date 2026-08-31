"""A chapter whose volume is far off is worth a second look, and only that."""

from __future__ import annotations

import asyncio
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
from gemini_translator.qa.journal import QaJournal
from gemini_translator.qa.llm import CancellationToken, QaModelSelection
from gemini_translator.qa.models import (
    AlignmentResult,
    AlignmentSpan,
    ChapterMetrics,
    RiskLevel,
)
from gemini_translator.qa.repair_store import RepairStore
from gemini_translator.qa.semantic_units import SemanticUnitExtractor
from gemini_translator.qa.service import (
    RATIO_OUTLIER_WARNING,
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


# Equal sentence counts on both sides, so a clean 1:1 alignment is valid and the
# only thing that differs is how much text each side carries.
_FULL = "Он открыл тяжёлую дверь и вышел наружу, оставив башню за спиной навсегда."
_TRUNCATED = "Он вышел."
_SENTENCES = 20


class _Provider:
    name = "recording"

    async def embed(self, request):
        dimensions = request.dimensions or 3
        vectors = np.zeros((len(request.texts), dimensions), dtype=np.float32)
        for index in range(len(request.texts)):
            vectors[index, index % dimensions] = 1.0
        return EmbeddingBatch(vectors, self.name, request.model, dimensions)


class _CleanAligner:
    def align(self, source, target):
        spans = tuple(
            AlignmentSpan((unit.unit_id,), (other.unit_id,), 0.99, "1:1")
            for unit, other in zip(source.units, target.units, strict=False)
        )
        return AlignmentResult(spans or (), (), max(1, len(spans)))


class _Verifier:
    async def verify(self, candidate, context, glossary, model, cancellation):
        raise AssertionError("a clean chapter must not reach the verifier")


class _Repairer:
    async def propose(self, verified, request):
        raise AssertionError("a clean chapter must not reach the repairer")


class _PostValidator:
    async def validate(self, before, preview, candidate, proposal, **kwargs):
        return RepairValidation(True, ())


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


def _service(tmp_path: Path, journal: QaJournal):
    return TranslationQualityService(
        coverage=SemanticCoverageService(
            extractor=SemanticUnitExtractor(QaCapabilitySettings()),
            provider=_Provider(),
            aligner=_CleanAligner(),
            candidate_filter=DefaultCandidateFilter(),
            metrics_collector=DefaultCoverageMetricsCollector(),
        ),
        verifier=_Verifier(),
        repairer=_Repairer(),
        repair_engine=StructuralRepairEngine("ru", QaCapabilitySettings()),
        repair_validator=_PostValidator(),
        store=RepairStore(tmp_path / "backups", session_id="session-1"),
        journal=journal,
        journal_path=tmp_path / "translation_qa.json",
    )


def _request(chapter: Path, source_sentences, target_sentences) -> ChapterQaRequest:
    model = build_html_document_model(
        chapter.read_text(encoding="utf-8"), document_id="target-doc"
    )
    return ChapterQaRequest(
        chapter_id="chapter-1",
        coverage_request=CoverageRequest(
            chapter_id="chapter-1",
            source_payload=_payload("source-doc", *source_sentences),
            target_payload=build_translation_payload(model, document_id="target-doc"),
            source_language="en",
            target_language="ru",
            embedding_model="embed-model",
            embedding_dimensions=3,
        ),
        translated_path=chapter,
        model=QaModelSelection("gemini", "qa-model"),
    )


def _run(tmp_path: Path, source_sentence: str, target_sentence: str, journal=None, count=_SENTENCES):
    # No numbering: the English segmenter would read "1." as a sentence of its
    # own and the two sides would stop having the same number of units.
    source_sentences = [source_sentence] * count
    target_sentences = [target_sentence] * count
    chapter = tmp_path / "chapter-1.html"
    chapter.write_text(
        "".join(f"<p>{text}</p>" for text in target_sentences), encoding="utf-8"
    )
    service = _service(tmp_path, journal or QaJournal.empty(book_id="book-1"))
    return asyncio.run(
        service.check_chapter(
            _request(chapter, source_sentences, target_sentences),
            QaOptions(),
            CancellationToken(),
        )
    )


def test_a_chapter_far_outside_its_language_profile_asks_for_a_second_look(tmp_path):
    """Глава, потерявшая треть текста, занижает собственные ожидания — это ловит статистика."""
    result = _run(tmp_path, _FULL, _TRUNCATED)

    assert RATIO_OUTLIER_WARNING in result.warnings
    assert result.risk_level is RiskLevel.MEDIUM
    assert result.may_continue_translation is True
    assert result.repairs == ()


def test_a_normal_chapter_is_not_raised_by_its_length(tmp_path):
    """Длина сама по себе ничего не доказывает — и не должна поднимать риск."""
    result = _run(tmp_path, _FULL, _FULL)

    assert RATIO_OUTLIER_WARNING not in result.warnings
    assert result.risk_level is RiskLevel.LOW


def test_a_short_chapter_is_never_judged_by_its_ratio(tmp_path):
    """У титульной страницы отношение есть, и оно ничего не значит."""
    result = _run(tmp_path, "Front matter.", "Титул.", count=2)

    assert RATIO_OUTLIER_WARNING not in result.warnings
    assert result.risk_level is RiskLevel.LOW


def test_the_outlier_never_blocks_the_queue_or_defers_the_chapter(tmp_path):
    """Отклонение — повод присмотреться, а не остановить перевод и не переспросить."""
    from gemini_translator.qa.service import DEFERRED_WARNINGS

    result = _run(tmp_path, _FULL, _TRUNCATED)

    assert result.may_continue_translation is True
    assert RATIO_OUTLIER_WARNING not in DEFERRED_WARNINGS


def test_statistics_never_break_a_check(tmp_path, monkeypatch):
    """Сломанная статистика не имеет права уронить проверку главы."""
    import gemini_translator.qa.service as service_module

    class _Broken:
        def analyze(self, metrics):
            raise RuntimeError("frame is unusable")

    monkeypatch.setattr(service_module, "BookMetricsAnalyzer", _Broken)

    result = _run(tmp_path, _FULL, _TRUNCATED)

    assert RATIO_OUTLIER_WARNING not in result.warnings
    assert result.risk_level is RiskLevel.LOW


def test_the_book_norm_is_consulted_once_five_chapters_exist(tmp_path):
    """Отклонение от нормы самой книги — второй повод, кроме языкового профиля."""
    journal = QaJournal.empty(book_id="book-1")
    for index in range(6):
        journal.upsert_metrics(
            ChapterMetrics(
                chapter_id=f"prior-{index}",
                source_language="en",
                target_language="ru",
                source_chars=4000,
                translated_chars=4000,
                content_kind="narrative",
            )
        )
    result = _run(tmp_path, _FULL, _FULL, journal=journal)

    assert result.metrics is not None
    assert result.metrics.source_chars >= 500
    # The chapter matches both the profile and the book, so nothing is raised.
    assert RATIO_OUTLIER_WARNING not in result.warnings


@pytest.mark.parametrize("kind", ["front_matter", "toc"])
def test_only_narrative_chapters_are_judged_by_volume(tmp_path, kind, monkeypatch):
    """Оглавление и выходные данные живут по своим правилам длины."""
    import gemini_translator.qa.coverage_service as coverage_module

    original = coverage_module.DefaultCoverageMetricsCollector.collect

    def collect(self, request, source_units, target_units, alignment, **kwargs):
        metrics = original(self, request, source_units, target_units, alignment, **kwargs)
        from dataclasses import replace

        return replace(metrics, content_kind=kind)

    monkeypatch.setattr(
        coverage_module.DefaultCoverageMetricsCollector, "collect", collect
    )

    result = _run(tmp_path, _FULL, _TRUNCATED)

    assert RATIO_OUTLIER_WARNING not in result.warnings


# --- the chapter's own kind -------------------------------------------------


def test_a_prose_chapter_is_narrative_so_the_book_baseline_can_use_it():
    """Раньше сюда попадал вид первого блока — и книжная норма не набиралась никогда."""
    from gemini_translator.qa.coverage_service import _content_kind
    from gemini_translator.qa.models import SemanticInlineSpan, SemanticUnit

    def unit(index: int, kind: str, text: str) -> SemanticUnit:
        return SemanticUnit(
            unit_id=f"u{index}",
            document_id="d",
            block_id=f"b{index}",
            ordinal=index,
            text=text,
            normalized_text=text,
            source_start=0,
            source_end=len(text),
            kind=kind,
            inline_spans=(SemanticInlineSpan(f"i{index}", 0, len(text), 0, len(text)),),
        )

    prose = [unit(0, "heading", "Глава первая"), unit(1, "paragraph", "Проза. " * 40)]
    front_matter = [unit(0, "heading", "Оглавление"), unit(1, "link", "Глава " * 40)]

    assert _content_kind(prose) == "narrative"
    assert _content_kind(front_matter) == "heading"
    assert _content_kind(()) == "narrative"
