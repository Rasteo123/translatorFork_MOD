"""A finished chapter must be checked before the next one is dispatched."""

from __future__ import annotations

import asyncio

import pytest

from gemini_translator.core.chapter_qa_coordinator import (
    ChapterQaCoordinator,
    TranslationReadyEvent,
)
from gemini_translator.core.task_manager import QaQueueOutcome
from gemini_translator.qa.models import RiskLevel
from gemini_translator.qa.service import ChapterQaResult, QaOptions


class _QueueStub:
    def __init__(self) -> None:
        self.status: dict[str, str] = {}
        self.outcomes: list[tuple[str, QaQueueOutcome]] = []

    def mark_task_qa_pending(self, task_id, chapter_ids):
        self.status[str(task_id)] = "qa_pending"

    def resolve_task_qa(self, task_id, outcome):
        self.outcomes.append((str(task_id), outcome))
        self.status[str(task_id)] = {
            "completed": "completed",
            "deferred": "completed",
            "high_unresolved": "qa_blocked",
            "cancelled": "qa_pending",
        }[outcome.kind]


class _ServiceStub:
    def __init__(self, results=None, error: Exception | None = None) -> None:
        self.results = dict(results or {})
        self.error = error
        self.calls: list[str] = []
        self.options: list[QaOptions] = []

    async def check_chapter(self, request, options, cancellation):
        self.calls.append(request)
        self.options.append(options)
        if self.error is not None:
            raise self.error
        return self.results.get(request, _result(request))


def _result(chapter_id: str, *, may_continue: bool = True, warnings=()) -> ChapterQaResult:
    return ChapterQaResult(
        chapter_id=chapter_id,
        risk_level=RiskLevel.LOW if may_continue else RiskLevel.HIGH,
        may_continue_translation=may_continue,
        coverage_mode="semantic_alignment",
        warnings=tuple(warnings),
    )


def _event(chapter_id: str, task_id: str = "task-1") -> TranslationReadyEvent:
    return TranslationReadyEvent(
        task_id=task_id,
        chapter_id=chapter_id,
        source_path=f"OEBPS/{chapter_id}",
        translated_path=f"/tmp/{chapter_id}",
        source_language="en",
        target_language="ru",
    )


def _coordinator(service, queue=None, *, options=None, builder=None):
    return ChapterQaCoordinator(
        service=service,
        task_manager=queue,
        request_builder=builder or (lambda event: event.chapter_id),
        options_provider=(lambda: options) if options is not None else None,
    )


def test_a_clean_chapter_completes_its_task(tmp_path):
    """A chapter that passes QA must release the queue immediately."""
    queue = _QueueStub()
    service = _ServiceStub()
    coordinator = _coordinator(service, queue)

    outcome = asyncio.run(
        coordinator.inspect_completed_task("task-1", (_event("chapter-1"),))
    )

    assert outcome.outcome.kind == "completed"
    assert outcome.outcome.chapter_ids == ("chapter-1",)
    assert service.calls == ["chapter-1"]


def test_unresolved_high_risk_blocks_the_task(tmp_path):
    """A chapter QA could not repair must leave the task blocked."""
    service = _ServiceStub({"chapter-1": _result("chapter-1", may_continue=False)})
    coordinator = _coordinator(service)

    outcome = asyncio.run(
        coordinator.inspect_completed_task("task-1", (_event("chapter-1"),))
    )

    assert outcome.outcome.kind == "high_unresolved"
    assert "chapter-1" in outcome.outcome.reason


def test_infrastructure_warnings_defer_instead_of_blocking():
    """An embedding outage must never stop a translation session."""
    service = _ServiceStub(
        {"chapter-1": _result("chapter-1", warnings=("embeddings_unavailable",))}
    )
    coordinator = _coordinator(service)

    outcome = asyncio.run(
        coordinator.inspect_completed_task("task-1", (_event("chapter-1"),))
    )

    assert outcome.outcome.kind == "deferred"
    assert "embeddings_unavailable" in outcome.outcome.reason


def test_a_batch_task_resolves_only_after_every_chapter():
    """A batch task must not release the queue halfway through its chapters."""
    service = _ServiceStub(
        {
            "chapter-1": _result("chapter-1"),
            "chapter-2": _result("chapter-2", may_continue=False),
            "chapter-3": _result("chapter-3"),
        }
    )
    queue = _QueueStub()
    coordinator = _coordinator(service, queue)
    events = tuple(_event(f"chapter-{index}") for index in (1, 2, 3))

    outcome = asyncio.run(coordinator.inspect_completed_task("task-1", events))

    assert service.calls == ["chapter-1", "chapter-2", "chapter-3"]
    assert outcome.outcome.kind == "high_unresolved"
    assert outcome.outcome.chapter_ids == ("chapter-1", "chapter-2", "chapter-3")


def test_a_qa_crash_defers_the_task_instead_of_losing_it():
    """A failing check must not leave a task stuck in qa_pending forever."""
    queue = _QueueStub()
    service = _ServiceStub(error=RuntimeError("qa exploded"))
    coordinator = _coordinator(service, queue)

    coordinator.submit("task-1", (_event("chapter-1"),))
    coordinator.drain(timeout=5)
    coordinator.shutdown(timeout=5)

    assert queue.status["task-1"] == "completed"
    assert queue.outcomes[-1][1].kind == "deferred"


def test_submitting_holds_the_task_before_any_check_runs():
    """The queue must see qa_pending before the first request is made."""
    queue = _QueueStub()
    seen: list[str] = []

    class _SlowService:
        async def check_chapter(self, request, options, cancellation):
            seen.append(queue.status.get("task-1", ""))
            return _result(request)

    coordinator = _coordinator(_SlowService(), queue)
    coordinator.submit("task-1", (_event("chapter-1"),))
    coordinator.drain(timeout=5)
    coordinator.shutdown(timeout=5)

    assert seen == ["qa_pending"]
    assert queue.status["task-1"] == "completed"


def test_cancellation_leaves_the_task_resumable():
    """A cancelled session must leave QA to resume, not mark work as done."""
    queue = _QueueStub()
    service = _ServiceStub()
    coordinator = _coordinator(service, queue)
    coordinator.cancel()

    outcome = asyncio.run(
        coordinator.inspect_completed_task("task-1", (_event("chapter-1"),))
    )

    assert outcome.outcome.kind == "cancelled"
    assert service.calls == []


def test_an_unbuildable_request_defers_the_chapter():
    """A chapter whose project state is missing must defer, not block."""
    service = _ServiceStub()
    coordinator = _coordinator(service, builder=lambda event: None)

    outcome = asyncio.run(
        coordinator.inspect_completed_task("task-1", (_event("chapter-1"),))
    )

    assert outcome.outcome.kind == "deferred"
    assert service.calls == []


def test_options_are_read_per_check_so_a_setting_change_applies_next_time():
    """A capability toggled mid-session must apply to the next check only."""
    provided = [QaOptions(check_language=False), QaOptions(check_language=True)]
    service = _ServiceStub()
    coordinator = ChapterQaCoordinator(
        service=service,
        task_manager=None,
        request_builder=lambda event: event.chapter_id,
        options_provider=lambda: provided.pop(0),
    )

    asyncio.run(coordinator.inspect_completed_task("task-1", (_event("chapter-1"),)))
    asyncio.run(coordinator.inspect_completed_task("task-2", (_event("chapter-2"),)))

    assert [options.check_language for options in service.options] == [False, True]


def test_manual_check_all_stops_cleanly_on_cancellation():
    """A manual whole-book pass must stop at a safe point when cancelled."""
    service = _ServiceStub()
    coordinator = _coordinator(service)
    events = tuple(_event(f"chapter-{index}") for index in (1, 2, 3))
    coordinator.cancel()

    result = asyncio.run(coordinator.check_all_now(events))

    assert result.results == ()
    assert set(result.skipped) == {"chapter-1", "chapter-2", "chapter-3"}


def test_book_result_lists_the_chapters_that_block_the_queue():
    """A whole-book pass must name what the user has to resolve."""
    service = _ServiceStub(
        {
            "chapter-1": _result("chapter-1"),
            "chapter-2": _result("chapter-2", may_continue=False),
        }
    )
    coordinator = _coordinator(service)

    result = asyncio.run(
        coordinator.check_all_now((_event("chapter-1"), _event("chapter-2")))
    )

    assert result.blocking_chapters == ("chapter-2",)


def test_translation_ready_event_rejects_incomplete_identity():
    """An event without identity could resolve the wrong task."""
    with pytest.raises(ValueError):
        TranslationReadyEvent(
            task_id="",
            chapter_id="chapter-1",
            source_path="a",
            translated_path="b",
            source_language="en",
            target_language="ru",
        )


def _repair(candidate_id="gap-1", source="He never told her.", inserted="Он не сказал ей."):
    from gemini_translator.qa.models import Decision
    from gemini_translator.qa.service import OmissionRepairOutcome

    return OmissionRepairOutcome(
        candidate_id=candidate_id,
        decision=Decision.FIXED,
        attempted=True,
        patch_id="qa-1",
        source_text=source,
        inserted_text=inserted,
    )


def test_a_chapter_that_changed_is_logged_with_before_and_after():
    """A reader must be able to judge an automatic edit from the log alone."""
    from gemini_translator.qa.language_validation import (
        LanguageQaResult,
        LanguageReplacement,
    )
    from gemini_translator.qa.llm.schemas import LanguageIssue

    result = ChapterQaResult(
        chapter_id="chapter-1",
        risk_level=RiskLevel.MEDIUM,
        may_continue_translation=True,
        coverage_mode="semantic_alignment",
        repairs=(_repair(),),
        language=LanguageQaResult(
            chapter_id="chapter-1",
            issues=(
                LanguageIssue(
                    issue_id="issue-1",
                    category="calque",
                    block_id="b-1",
                    original_text="сделало его чувствовать",
                    replacement_text="заставило его почувствовать",
                    objective=True,
                    confidence=0.93,
                    explanation="Калька.",
                ),
            ),
            applied=(
                LanguageReplacement(
                    "issue-1",
                    "b-1",
                    "сделало его чувствовать",
                    "заставило его почувствовать",
                ),
            ),
        ),
    )
    logged: list[tuple] = []

    class _Service:
        async def check_chapter(self, request, options, cancellation):
            return result

    coordinator = ChapterQaCoordinator(
        service=_Service(),
        task_manager=None,
        request_builder=lambda event: event.chapter_id,
        log=lambda message, details_title="", details_text="", details_html="": (
            logged.append((message, details_title, details_text))
        ),
    )

    asyncio.run(coordinator.inspect_completed_task("task-1", (_event("chapter-1"),)))

    assert logged
    message, title, details = logged[-1]
    assert "исправлено пропусков — 1" in message
    assert "языковых дефектов — 1" in message
    assert "chapter-1" in title
    assert "было (оригинал): He never told her." in details
    assert "стало (перевод): Он не сказал ей." in details
    assert "было:  сделало его чувствовать" in details
    assert "стало: заставило его почувствовать" in details


def test_a_chapter_that_changed_nothing_is_not_logged_with_details():
    """A clean chapter must not fill the log with empty detail blocks."""
    logged: list[tuple] = []

    class _Service:
        async def check_chapter(self, request, options, cancellation):
            return _result(request.chapter_id if hasattr(request, "chapter_id") else request)

    coordinator = ChapterQaCoordinator(
        service=_Service(),
        task_manager=None,
        request_builder=lambda event: event.chapter_id,
        log=lambda message, details_title="", details_text="", details_html="": (
            logged.append((message, details_title, details_text))
        ),
    )

    asyncio.run(coordinator.inspect_completed_task("task-1", (_event("chapter-1"),)))

    assert logged == []


def test_a_blocked_chapter_is_logged_with_the_reason():
    """A stopped translation must say in the log exactly what stopped it."""
    logged: list[tuple] = []
    from gemini_translator.qa.models import Decision
    from gemini_translator.qa.service import OmissionRepairOutcome

    result = ChapterQaResult(
        chapter_id="chapter-1",
        risk_level=RiskLevel.HIGH,
        may_continue_translation=False,
        coverage_mode="semantic_alignment",
        repairs=(
            OmissionRepairOutcome(
                candidate_id="gap-1",
                decision=Decision.REPAIR_REJECTED,
                attempted=True,
                reasons=("post_check_rejected",),
                source_text="He never told her.",
            ),
        ),
    )

    class _Service:
        async def check_chapter(self, request, options, cancellation):
            return result

    coordinator = ChapterQaCoordinator(
        service=_Service(),
        task_manager=None,
        request_builder=lambda event: event.chapter_id,
        log=lambda message, details_title="", details_text="", details_html="": (
            logged.append((message, details_title, details_text))
        ),
    )

    asyncio.run(coordinator.inspect_completed_task("task-1", (_event("chapter-1"),)))

    message, _title, details = logged[-1]
    assert "перевод остановлен" in message
    assert "post_check_rejected" in details
    assert "He never told her." in details


def _peak_concurrency(max_concurrency: int, tasks: int = 4) -> int:
    running = 0
    peak = 0

    class _Slow:
        async def check_chapter(self, request, options, cancellation):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.01)
            running -= 1
            return _result(request)

    coordinator = ChapterQaCoordinator(
        service=_Slow(),
        task_manager=_QueueStub(),
        request_builder=lambda event: event.chapter_id,
        max_concurrency=max_concurrency,
    )

    async def run() -> None:
        await asyncio.gather(
            *(
                coordinator._inspect_and_resolve(
                    f"task-{index}", (_event(f"chapter-{index}", f"task-{index}"),)
                )
                for index in range(tasks)
            )
        )

    asyncio.run(run())
    return peak


def test_automatic_checks_run_one_at_a_time_by_default():
    """Очередь из десятков готовых глав не должна крутить пул ключей разом.

    Measured on a live book: the translation outran the check, dozens of
    chapters were checked at once, and together they asked every key of the
    pool within two minutes whenever the service throttled.
    """
    assert _peak_concurrency(1) == 1


def test_the_user_may_allow_more_checks_at_once():
    assert _peak_concurrency(3) <= 3


def test_a_logger_without_details_support_still_works():
    """An older log callback must keep working, not swallow the message."""
    logged: list[str] = []

    class _Service:
        async def check_chapter(self, request, options, cancellation):
            return ChapterQaResult(
                chapter_id="chapter-1",
                risk_level=RiskLevel.LOW,
                may_continue_translation=True,
                coverage_mode="semantic_alignment",
                repairs=(_repair(),),
            )

    coordinator = ChapterQaCoordinator(
        service=_Service(),
        task_manager=None,
        request_builder=lambda event: event.chapter_id,
        log=logged.append,
    )

    asyncio.run(coordinator.inspect_completed_task("task-1", (_event("chapter-1"),)))

    assert logged and "исправлено пропусков — 1" in logged[-1]


def test_the_details_mark_exactly_the_words_that_changed():
    """A reader must see what was replaced, not just two similar sentences."""
    from gemini_translator.qa.language_validation import (
        LanguageQaResult,
        LanguageReplacement,
    )
    from gemini_translator.qa.llm.schemas import LanguageIssue
    from gemini_translator.qa.text_diff import ADDED_STYLE, REMOVED_STYLE

    result = ChapterQaResult(
        chapter_id="chapter-1",
        risk_level=RiskLevel.LOW,
        may_continue_translation=True,
        coverage_mode="semantic_alignment",
        language=LanguageQaResult(
            chapter_id="chapter-1",
            issues=(
                LanguageIssue(
                    issue_id="issue-1",
                    category="calque",
                    block_id="b-1",
                    original_text="сделало его чувствовать себя одиноким",
                    replacement_text="заставило его почувствовать себя одиноким",
                    objective=True,
                    confidence=0.93,
                    explanation="Калька.",
                ),
            ),
            applied=(
                LanguageReplacement(
                    "issue-1",
                    "b-1",
                    "сделало его чувствовать себя одиноким",
                    "заставило его почувствовать себя одиноким",
                ),
            ),
        ),
    )

    html = result.change_details_html()

    assert REMOVED_STYLE in html
    assert ADDED_STYLE in html
    assert f'<span style="{REMOVED_STYLE}">сделало</span>' in html
    assert f'<span style="{ADDED_STYLE}">заставило</span>' in html
    # Unchanged words must stay unmarked, or the highlight means nothing.
    assert "себя одиноким" in html
    assert f'<span style="{REMOVED_STYLE}">себя</span>' not in html
    assert "было" in html and "стало" in html


def test_a_restored_omission_is_marked_entirely_as_added():
    """A recovered fragment is new text; every word of it is an addition."""
    from gemini_translator.qa.text_diff import ADDED_STYLE

    result = ChapterQaResult(
        chapter_id="chapter-1",
        risk_level=RiskLevel.LOW,
        may_continue_translation=True,
        coverage_mode="semantic_alignment",
        repairs=(_repair(source="", inserted="Комната была пуста."),),
    )

    html = result.change_details_html()

    assert f'<span style="{ADDED_STYLE}">Комната была пуста.</span>' in html


def test_details_html_escapes_the_book_text():
    """Book text is data: a stray tag must never become markup in the log."""
    result = ChapterQaResult(
        chapter_id="chapter-1",
        risk_level=RiskLevel.LOW,
        may_continue_translation=True,
        coverage_mode="semantic_alignment",
        repairs=(
            _repair(source="<b>жирный</b> текст", inserted="<i>курсив</i> текст"),
        ),
    )

    html = result.change_details_html()

    assert "&lt;" in html
    assert "<b>жирный</b>" not in html
    assert "<i>курсив</i>" not in html
