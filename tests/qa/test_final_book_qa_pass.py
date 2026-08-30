"""The final pass closes what the per-chapter checks left open, and no more."""

from __future__ import annotations

import asyncio

import pytest

from gemini_translator.core.chapter_qa_coordinator import (
    ChapterQaCoordinator,
    ResumeResult,
    TranslationReadyEvent,
    select_final_pass_chapters,
)
from gemini_translator.qa.journal import QaJournal
from gemini_translator.qa.models import ChapterMetrics, QaChapterState, RiskLevel
from gemini_translator.qa.service import ChapterQaResult


_IDENTITY = "semantic-units-v1|razdel-0.5"


class _QueueStub:
    def __init__(self) -> None:
        self.outcomes: list[tuple[str, str]] = []

    def mark_task_qa_pending(self, task_id, chapter_ids):
        return None

    def resolve_task_qa(self, task_id, outcome):
        self.outcomes.append((str(task_id), outcome.kind))


class _ServiceStub:
    def __init__(self, blocking=()) -> None:
        self.checked: list[str] = []
        self.blocking = set(blocking)

    async def check_chapter(self, request, options, cancellation):
        self.checked.append(request)
        return ChapterQaResult(
            chapter_id=request,
            risk_level=RiskLevel.HIGH if request in self.blocking else RiskLevel.LOW,
            may_continue_translation=request not in self.blocking,
            coverage_mode="semantic_alignment",
        )


def _event(chapter_id: str) -> TranslationReadyEvent:
    return TranslationReadyEvent(
        task_id="manual",
        chapter_id=chapter_id,
        source_path=chapter_id,
        translated_path=f"/tmp/{chapter_id}.html",
        source_language="auto",
        target_language="ru",
    )


def _state(chapter_id: str, **overrides) -> QaChapterState:
    values = {
        "chapter_id": chapter_id,
        "status": "checked",
        "analysis_identity": _IDENTITY,
        "risk_level": RiskLevel.LOW,
        "book_sample_size": 8,
    }
    values.update(overrides)
    return QaChapterState(**values)  # type: ignore[arg-type]


def _select(states, *, sample_size=8, identity=_IDENTITY, chapters=("chapter-1",)):
    return select_final_pass_chapters(
        tuple(_event(chapter_id) for chapter_id in chapters),
        states,
        analysis_identity=identity,
        book_sample_size=sample_size,
    )


def test_a_settled_chapter_is_not_checked_again():
    """Re-checking a clean chapter would spend the book's budget for nothing."""
    assert _select({"chapter-1": _state("chapter-1")}) == ()


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        (None, "never_checked"),
        ({"status": "deferred"}, "deferred"),
        ({"status": "blocked"}, "unresolved_risk"),
        ({"risk_level": RiskLevel.MEDIUM}, "unresolved_risk"),
        ({"risk_level": RiskLevel.HIGH}, "unresolved_risk"),
        ({"analysis_identity": "semantic-units-v0"}, "analysis_version_changed"),
        ({"book_sample_size": 2}, "baseline_now_available"),
    ],
)
def test_every_unsettled_chapter_is_selected_with_its_reason(state, reason):
    """The journal must be able to explain why the final pass touched a chapter."""
    states = {} if state is None else {"chapter-1": _state("chapter-1", **state)}

    selected = _select(states)

    assert [item.reason for item in selected] == [reason]
    assert [item.event.chapter_id for item in selected] == ["chapter-1"]


def test_early_chapters_wait_for_a_real_baseline():
    """Reclassifying against a baseline that does not exist yet proves nothing."""
    states = {"chapter-1": _state("chapter-1", book_sample_size=2)}

    assert _select(states, sample_size=3) == ()
    assert len(_select(states, sample_size=5)) == 1


def test_selection_keeps_book_order():
    """A book must be re-checked in its own order, so statistics stay comparable."""
    chapters = ("chapter-1", "chapter-2", "chapter-3")

    selected = _select({}, chapters=chapters)

    assert [item.event.chapter_id for item in selected] == list(chapters)


def _coordinator(service, *, journal=None, events=(), pending=None, queue=None):
    return ChapterQaCoordinator(
        service=service,
        task_manager=queue,
        request_builder=lambda event: event.chapter_id,
        book_events_provider=lambda: tuple(_event(chapter) for chapter in events),
        journal_provider=(lambda: journal) if journal is not None else None,
        pending_tasks_provider=(lambda: pending) if pending is not None else None,
        analysis_identity=_IDENTITY,
    )


def _journal(states=(), metrics_count=8) -> QaJournal:
    journal = QaJournal.empty(book_id="book-1")
    for index in range(metrics_count):
        journal.upsert_metrics(
            ChapterMetrics(
                chapter_id=f"chapter-{index + 1}",
                source_language="zh",
                target_language="ru",
                source_chars=1000,
                translated_chars=2900,
            )
        )
    for state in states:
        journal.record_chapter_state(state)
    return journal


def test_final_pass_checks_only_the_unsettled_chapters():
    """The pass must be a closing sweep, not a second full run of the book."""
    service = _ServiceStub()
    coordinator = _coordinator(
        service,
        journal=_journal(
            (
                _state("chapter-1"),
                _state("chapter-2", status="deferred"),
                _state("chapter-3"),
            )
        ),
        events=("chapter-1", "chapter-2", "chapter-3"),
    )

    result = asyncio.run(coordinator.run_final_book_pass("session-1"))

    assert service.checked == ["chapter-2"]
    assert [item.chapter_id for item in result.results] == ["chapter-2"]


def test_final_pass_does_nothing_when_the_book_is_settled():
    """A finished book must not pay for a pass that has nothing to do."""
    service = _ServiceStub()
    coordinator = _coordinator(
        service,
        journal=_journal((_state("chapter-1"),)),
        events=("chapter-1",),
    )

    result = asyncio.run(coordinator.run_final_book_pass("session-1"))

    assert service.checked == []
    assert result.results == ()


def test_final_pass_reports_the_chapters_that_still_block():
    """After the last pass the user must know exactly what is left to decide."""
    service = _ServiceStub(blocking={"chapter-2"})
    coordinator = _coordinator(
        service,
        journal=_journal((_state("chapter-2", status="deferred"),)),
        events=("chapter-1", "chapter-2"),
    )

    result = asyncio.run(coordinator.run_final_book_pass("session-1"))

    assert "chapter-2" in result.blocking_chapters


def test_a_missing_journal_makes_the_pass_check_everything():
    """Without a history the safe assumption is that nothing has been checked."""
    service = _ServiceStub()
    coordinator = _coordinator(service, events=("chapter-1", "chapter-2"))

    asyncio.run(coordinator.run_final_book_pass("session-1"))

    assert service.checked == ["chapter-1", "chapter-2"]


def test_restart_finishes_the_checks_the_previous_run_owed():
    """A chapter left in qa_pending must be finished, not silently completed."""
    service = _ServiceStub()
    queue = _QueueStub()
    coordinator = _coordinator(
        service,
        journal=_journal(),
        events=("chapter-1", "chapter-2"),
        pending=[("task-7", ["chapter-2"])],
        queue=queue,
    )

    result = asyncio.run(coordinator.resume_pending_qa())

    assert isinstance(result, ResumeResult)
    assert result.status == "resumed"
    assert result.task_ids == ("task-7",)
    assert service.checked == ["chapter-2"]
    assert queue.outcomes == [("task-7", "completed")]


def test_restart_defers_a_task_whose_translation_disappeared():
    """A task whose chapter is gone must be released, not held forever."""
    service = _ServiceStub()
    queue = _QueueStub()
    coordinator = _coordinator(
        service,
        journal=_journal(),
        events=("chapter-1",),
        pending=[("task-9", ["chapter-404"])],
        queue=queue,
    )

    result = asyncio.run(coordinator.resume_pending_qa())

    assert service.checked == []
    assert queue.outcomes == [("task-9", "deferred")]
    assert result.task_ids == ("task-9",)


def test_nothing_to_resume_is_a_normal_outcome():
    """A clean restart must not invent work or report a failure."""
    coordinator = _coordinator(_ServiceStub(), journal=_journal(), pending=[])

    result = asyncio.run(coordinator.resume_pending_qa())

    assert result.status == "nothing_to_resume"
    assert result.task_ids == ()


def test_a_broken_queue_read_is_reported_not_raised():
    """A damaged queue must not stop the application from starting."""

    def broken():
        raise RuntimeError("queue is unreadable")

    coordinator = ChapterQaCoordinator(
        service=_ServiceStub(),
        task_manager=None,
        request_builder=lambda event: event.chapter_id,
        pending_tasks_provider=broken,
    )

    result = asyncio.run(coordinator.resume_pending_qa())

    assert result.status == "failed"
    assert "unreadable" in result.detail
