"""A book quietly checked with half the cascade is the failure nobody notices."""

from __future__ import annotations

import asyncio

from gemini_translator.core.chapter_qa_coordinator import (
    LIMITED_MODE_ALERT_STREAK,
    ChapterQaCoordinator,
    TranslationReadyEvent,
)
from gemini_translator.qa.models import ChapterMetrics, RiskLevel
from gemini_translator.qa.report_snapshot import BookQaReportSnapshot
from gemini_translator.qa.service import ChapterQaResult, QaOptions


class _Service:
    def __init__(self, modes) -> None:
        self.modes = list(modes)
        self.calls = 0

    async def check_chapter(self, request, options, cancellation):
        mode = self.modes[min(self.calls, len(self.modes) - 1)]
        self.calls += 1
        return ChapterQaResult(
            chapter_id=str(request),
            risk_level=RiskLevel.LOW,
            may_continue_translation=True,
            coverage_mode=mode,
        )


def _event(chapter_id: str) -> TranslationReadyEvent:
    return TranslationReadyEvent(
        task_id="task-1",
        chapter_id=chapter_id,
        source_path=f"OEBPS/{chapter_id}",
        translated_path=f"/tmp/{chapter_id}",
        source_language="zh",
        target_language="ru",
    )


def _run(modes) -> list[str]:
    messages: list[str] = []
    service = _Service(modes)
    coordinator = ChapterQaCoordinator(
        service=service,
        task_manager=None,
        request_builder=lambda event: event.chapter_id,
        log=lambda message, **kwargs: messages.append(message),
    )
    for index in range(len(modes)):
        asyncio.run(coordinator._check_one(_event(f"chapter-{index}"), QaOptions()))
    return [message for message in messages if "Смысловое сравнение недоступно" in message]


def test_a_run_of_limited_chapters_is_reported_once():
    """Каждая глава по отдельности выглядит просто отложенной."""
    alerts = _run(["statistics_llm_only"] * (LIMITED_MODE_ALERT_STREAK + 4))

    assert len(alerts) == 1
    assert str(LIMITED_MODE_ALERT_STREAK) in alerts[0]


def test_a_single_limited_chapter_says_nothing():
    """Одна медленная глава — это шум, а не поломка."""
    modes = ["semantic_alignment", "statistics_llm_only", "semantic_alignment"]

    assert _run(modes) == []


def test_the_alert_returns_after_the_check_recovers_and_breaks_again():
    """Починили и снова сломалось — это новая новость."""
    streak = ["statistics_llm_only"] * LIMITED_MODE_ALERT_STREAK
    modes = [*streak, "semantic_alignment", *streak]

    assert len(_run(modes)) == 2


def test_a_run_shorter_than_the_threshold_stays_silent():
    modes = ["statistics_llm_only"] * (LIMITED_MODE_ALERT_STREAK - 1)

    assert _run(modes) == []


# --- the report counter -----------------------------------------------------


class _Journal:
    def __init__(self, metrics) -> None:
        self.metrics = {item.chapter_id: item for item in metrics}
        self.candidates = ()
        self.repairs = ()


def _metrics(chapter_id: str, *, source_units: int, aligned_units: int) -> ChapterMetrics:
    return ChapterMetrics(
        chapter_id=chapter_id,
        source_language="zh",
        target_language="ru",
        source_chars=3000,
        translated_chars=9000,
        source_units=source_units,
        aligned_units=aligned_units,
    )


def test_the_report_counts_the_chapters_checked_without_alignment():
    """Счётчик «без смыслового сравнения» в отчёте всегда показывал ноль."""
    journal = _Journal(
        [
            _metrics("chapter-1", source_units=40, aligned_units=40),
            _metrics("chapter-2", source_units=40, aligned_units=0),
            _metrics("chapter-3", source_units=0, aligned_units=0),
        ]
    )

    snapshot = BookQaReportSnapshot.from_journal(journal)

    assert snapshot.limited_mode_chapters == ("chapter-2",)


def test_an_empty_book_has_nothing_to_report():
    assert BookQaReportSnapshot.from_journal(_Journal([])).limited_mode_chapters == ()
