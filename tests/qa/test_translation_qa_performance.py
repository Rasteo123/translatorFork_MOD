"""Budgets that keep quality control affordable on a real book."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

from gemini_translator.qa.alignment import AlignmentCapacityError, MonotonicAligner
from gemini_translator.qa.book_metrics import BookMetricsAnalyzer
from gemini_translator.qa.capabilities import QaCapabilitySettings
from gemini_translator.qa.embeddings.base import EmbeddingBatch, EmbeddingRequest
from gemini_translator.qa.embeddings.cache import CachedEmbeddingProvider, EmbeddingCache
from gemini_translator.qa.models import (
    AlignmentConfig,
    ChapterMetrics,
    EmbeddedUnits,
    SemanticInlineSpan,
    SemanticUnit,
)


def _units(count: int, prefix: str) -> tuple[SemanticUnit, ...]:
    units = []
    for index in range(count):
        text = f"{prefix} предложение номер {index}."
        units.append(
            SemanticUnit(
                unit_id=f"{prefix}-{index}",
                document_id=f"{prefix}-doc",
                block_id=f"b-{prefix}-{index // 5}",
                ordinal=index,
                text=text,
                normalized_text=text.casefold(),
                source_start=0,
                source_end=len(text),
                kind="paragraph",
                inline_spans=(SemanticInlineSpan(f"i-{prefix}-{index}", 0, len(text), 0, len(text)),),
            )
        )
    return tuple(units)


def _vectors(count: int, dimensions: int = 8) -> np.ndarray:
    rows = np.zeros((count, dimensions), dtype=np.float32)
    for index in range(count):
        rows[index, index % dimensions] = 1.0
    return rows


def test_alignment_refuses_a_chapter_it_cannot_afford():
    """A pathological chapter must be refused, never quietly allocated."""
    config = AlignmentConfig(max_cells=1000)
    aligner = MonotonicAligner(config)
    source = EmbeddedUnits("s-doc", _units(400, "s"), _vectors(400))
    target = EmbeddedUnits("t-doc", _units(400, "t"), _vectors(400))

    with pytest.raises(AlignmentCapacityError) as excinfo:
        aligner.align(source, target)

    assert excinfo.value.max_cells == 1000
    assert excinfo.value.required_cells > 1000


def test_alignment_stays_inside_its_declared_cell_budget():
    """The band, not the chapter length, must decide how much memory is used."""
    config = AlignmentConfig(max_cells=200000)
    aligner = MonotonicAligner(config)
    source = EmbeddedUnits("s-doc", _units(120, "s"), _vectors(120))
    target = EmbeddedUnits("t-doc", _units(120, "t"), _vectors(120))

    result = aligner.align(source, target)

    assert result.visited_cells <= config.max_cells
    assert result.spans


def test_embedding_vectors_stay_float32():
    """A float64 matrix doubles the memory of every chapter for no benefit."""
    from gemini_translator.qa.embeddings.base import validate_and_normalize_batch

    batch = validate_and_normalize_batch(
        EmbeddingBatch(
            vectors=np.ones((4, 8), dtype=np.float64),
            provider="test",
            model="m",
            dimensions=8,
        ),
        expected_rows=4,
    )

    assert batch.vectors.dtype == np.float32


def test_a_second_pass_over_an_unchanged_chapter_makes_no_network_call(tmp_path: Path):
    """Re-checking a book must not pay for its embeddings twice."""

    class _Counting:
        name = "counting"

        def __init__(self) -> None:
            self.calls = 0

        async def embed(self, request):
            self.calls += 1
            return EmbeddingBatch(
                _vectors(len(request.texts)), self.name, request.model, 8
            )

    upstream = _Counting()
    provider = CachedEmbeddingProvider(
        upstream,
        EmbeddingCache(tmp_path / "cache"),
        preprocessing_identity="semantic-units-v1",
    )
    request = EmbeddingRequest(
        texts=("Первое предложение.", "Второе предложение."),
        language="ru",
        model="embed-model",
        dimensions=8,
        task_type="semantic-similarity",
    )

    asyncio.run(provider.embed(request))
    asyncio.run(provider.embed(request))

    assert upstream.calls == 1


@pytest.mark.performance
def test_book_statistics_scale_to_a_long_book():
    """A ten-thousand-chapter book must aggregate in one vectorized pass."""
    metrics = [
        ChapterMetrics(
            chapter_id=f"chapter-{index}",
            source_language="zh",
            target_language="ru",
            source_chars=1000,
            translated_chars=2900 + (index % 40),
        )
        for index in range(10000)
    ]
    analyzer = BookMetricsAnalyzer()

    started = time.perf_counter()
    frame = analyzer.analyze(metrics)
    baseline = analyzer.ratio_baseline(frame, "chapter-5000")
    elapsed = time.perf_counter() - started

    assert len(frame) == 10000
    assert baseline.sample_size == 10000
    assert elapsed < 10.0


def test_a_disabled_analyzer_makes_no_call_at_all():
    """Every optional analyzer must cost exactly nothing while switched off."""
    from gemini_translator.qa.language_rules import LanguageRuleService
    from gemini_translator.qa.russian_nlp import RussianNlpService

    def forbidden(*_args, **_kwargs):
        raise AssertionError("a disabled analyzer must not be contacted")

    class _Provider:
        endpoint = "http://127.0.0.1:8081/v2/check"
        server_version = ""

        async def check(self, request):
            forbidden()

    rules = LanguageRuleService(provider=_Provider())
    nlp = RussianNlpService(provider_factory=forbidden)
    capabilities = QaCapabilitySettings()

    rule_result = asyncio.run(rules.collect(_units(3, "t"), capabilities))
    nlp_result = nlp.analyze(_units(3, "t"), capabilities)

    assert rule_result.status == "disabled"
    assert nlp_result.status == "disabled"


def test_the_report_table_updates_with_one_reset_not_one_signal_per_cell():
    """A per-cell signal storm freezes the window on a long book."""
    from PyQt6 import QtWidgets

    from gemini_translator.qa.journal import QaJournal
    from gemini_translator.ui.dialogs.validation_dialogs import (
        BookQaReportSnapshot,
        ChapterQaTableModel,
    )

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    journal = QaJournal.empty(book_id="book-1")
    for index in range(200):
        journal.upsert_metrics(
            ChapterMetrics(
                chapter_id=f"chapter-{index}",
                source_language="zh",
                target_language="ru",
                source_chars=1000,
                translated_chars=2900,
            )
        )
    model = ChapterQaTableModel()
    resets, changes = [], []
    model.modelReset.connect(lambda: resets.append(1))
    model.dataChanged.connect(lambda *_args: changes.append(1))

    model.set_snapshot(BookQaReportSnapshot.from_journal(journal))

    assert model.rowCount() == 200
    assert len(resets) == 1
    assert changes == []
