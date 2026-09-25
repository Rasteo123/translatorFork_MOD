"""Run translation QA between chapters without blocking the translation itself."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
import threading
import time

from ..qa.book_metrics import MIN_BASELINE_SAMPLE_SIZE, eligible_baseline_size
from ..qa.coverage_service import SEMANTIC_ALIGNMENT_MODE
from ..qa.estimators.base import (
    QualityEstimate,
    QualityEstimateRequest,
    aggregate,
)
from ..qa.llm.completion import CancellationToken
from ..qa.models import QaChapterState, RiskLevel
from ..qa.service import (
    DEFERRED_WARNINGS,
    ChapterQaResult,
    QaOptions,
    SuggestionOutcome,
    TranslationQualityService,
    chapter_fingerprint,
    describe_deferral,
)
from ..utils.callbacks import safe_call
from .task_manager import QaQueueOutcome


# How many chapters in a row must lose semantic comparison before the session
# is told.  One is noise; a run of three is a broken setup.
LIMITED_MODE_ALERT_STREAK = 3
_REQUEST_NOT_BUILT = object()
# The PC server and the local runner refuse a request of more passages
# than this (MAX_SEGMENTS in tools/translation_qa_cometkiwi_runner.py).
MAX_WINDOWS_PER_ESTIMATE = 512


@dataclass(frozen=True, slots=True)
class TranslationReadyEvent:
    """One saved chapter translation, described without reading any widget."""

    task_id: str
    chapter_id: str
    source_path: str
    translated_path: str
    source_language: str
    target_language: str
    fingerprint: str = ""
    epub_path: str = ""
    retries: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    duration_seconds: float = 0.0

    def __post_init__(self) -> None:
        for field_name in (
            "task_id",
            "chapter_id",
            "translated_path",
            "source_language",
            "target_language",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a nonempty string")


@dataclass(frozen=True, slots=True)
class TaskQaOutcome:
    """What one finished translation task means for the queue."""

    task_id: str
    outcome: QaQueueOutcome
    results: tuple[ChapterQaResult, ...] = ()


@dataclass(frozen=True, slots=True)
class SelectedChapter:
    """One chapter the final pass will re-check, and the reason it was picked."""

    event: "TranslationReadyEvent"
    reason: str


@dataclass(frozen=True, slots=True)
class ResumeResult:
    """What a restart recovered: which tasks were owed a check, and how it went."""

    status: str = "nothing_to_resume"
    task_ids: tuple[str, ...] = ()
    results: tuple[ChapterQaResult, ...] = ()
    detail: str = ""


@dataclass(frozen=True, slots=True)
class BookQaResult:
    """Everything one manual or final multi-chapter pass produced."""

    results: tuple[ChapterQaResult, ...] = ()
    skipped: tuple[str, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)
    # The skipped chapters the pass never offered to the service because no QA
    # key would ever be available again; the rest of ``skipped`` could not be
    # read. Kept apart so a report can say which of the two happened.
    stopped: tuple[str, ...] = ()

    @property
    def blocking_chapters(self) -> tuple[str, ...]:
        return tuple(
            result.chapter_id
            for result in self.results
            if not result.may_continue_translation
        )


class ChapterQaCoordinator:
    """Own one bounded QA runtime shared by the automatic and manual paths.

    The coordinator never edits the queue directly beyond the two calls the
    queue itself defines: it holds a finished task in ``qa_pending`` while a
    chapter is checked and resolves it with one typed outcome afterwards.
    """

    def __init__(
        self,
        *,
        service: TranslationQualityService,
        task_manager,
        request_builder,
        options_provider=None,
        book_events_provider=None,
        journal_provider=None,
        pending_tasks_provider=None,
        analysis_identity="",
        log=None,
        max_concurrency: int = 1,
        quality_estimator=None,
        stop_requested: Callable[[], bool] | None = None,
    ) -> None:
        if not callable(getattr(service, "check_chapter", None)):
            raise TypeError("service must provide check_chapter")
        if not callable(request_builder):
            raise TypeError("request_builder must be callable")
        if isinstance(max_concurrency, bool) or not isinstance(max_concurrency, int):
            raise TypeError("max_concurrency must be an integer")
        self._service = service
        self._task_manager = task_manager
        self._request_builder = request_builder
        self._options_provider = options_provider or (lambda: QaOptions())
        self._book_events_provider = book_events_provider
        self._journal_provider = journal_provider
        self._pending_tasks_provider = pending_tasks_provider
        self._analysis_identity_value = analysis_identity
        self._quality_estimator = quality_estimator
        self._stop_requested = stop_requested if callable(stop_requested) else None
        self._limited_streak = 0
        self._limited_reported = False
        self._log = log
        self._max_concurrency = max(1, max_concurrency)
        # One check at a time unless the user allowed more.  Measured on a live
        # book: the translation outran the check, dozens of chapters were
        # checked at once, and together they asked every key of the pool
        # within two minutes whenever the service throttled.
        self._check_limit: asyncio.Semaphore | None = None
        self._check_limit_loop: asyncio.AbstractEventLoop | None = None
        self._cancellation = CancellationToken()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._pending: set[asyncio.Future] = set()
        self._pending_lock = threading.Lock()
        # Tasks handed over for a check that has not ended, with how many
        # chapters each carries; guarded by _pending_lock.
        self._unchecked: dict[str, int] = {}
        # Chapters a check is reading or writing right now, with how many checks
        # hold each: a fix the user applies must not land in one of them.
        self._checking: dict[str, int] = {}
        self._checking_lock = threading.Lock()
        self._checking_listener: Callable[[frozenset[str]], None] | None = None

    # -- runtime -----------------------------------------------------------

    def start(self) -> None:
        """Start the dedicated QA loop; safe to call more than once."""
        if self._thread is not None and self._thread.is_alive():
            return
        ready = threading.Event()

        def run() -> None:
            loop = asyncio.new_event_loop()
            self._loop = loop
            asyncio.set_event_loop(loop)
            ready.set()
            try:
                loop.run_forever()
            finally:
                loop.close()

        self._thread = threading.Thread(target=run, name="qa-coordinator", daemon=True)
        self._thread.start()
        ready.wait(timeout=5)

    def submit(self, task_id: str, events: Sequence[TranslationReadyEvent]) -> None:
        """Hold the task in QA and schedule its check on the QA loop."""
        events = tuple(events)
        if not events:
            return
        self._mark_pending(task_id, events)
        self._owe_check(task_id, len(events))
        self.start()
        loop = self._loop
        if loop is None:  # pragma: no cover - start() guarantees a loop
            return
        future = asyncio.run_coroutine_threadsafe(
            self._inspect_and_resolve(task_id, events), loop
        )
        with self._pending_lock:
            self._pending.add(future)
        future.add_done_callback(self._discard_future)

    def run_background(self, coroutine_factory, on_done=None) -> None:
        """Run one coroutine on the QA loop and report its outcome exactly once.

        ``on_done(result, error)`` is called from the QA thread; a caller that
        touches the interface must marshal it back itself.
        """

        self.start()
        loop = self._loop
        if loop is None:  # pragma: no cover - start() guarantees a loop
            if callable(on_done):
                on_done(None, RuntimeError("QA runtime is not available"))
            return
        future = asyncio.run_coroutine_threadsafe(coroutine_factory(), loop)
        with self._pending_lock:
            self._pending.add(future)

        def finished(completed) -> None:
            self._discard_future(completed)
            if not callable(on_done):
                return
            try:
                on_done(completed.result(), None)
            except Exception as error:  # noqa: BLE001 - the caller decides what to show
                on_done(None, error)

        future.add_done_callback(finished)

    def unchecked_chapter_count(self) -> int:
        """Chapters handed over for a check that has not ended yet.

        A check stopped halfway still counts: its task waits in qa_pending for
        the next session or for «Продолжить проверку».
        """
        with self._pending_lock:
            return sum(self._unchecked.values())

    def _owe_check(self, task_id, chapters: int) -> None:
        with self._pending_lock:
            self._unchecked[str(task_id)] = chapters

    def _settle_check(self, task_id, outcome: QaQueueOutcome) -> None:
        if outcome.kind == "cancelled":
            return
        with self._pending_lock:
            self._unchecked.pop(str(task_id), None)

    def checking_chapters(self) -> frozenset[str]:
        """The chapters a check holds at this moment."""
        with self._checking_lock:
            return frozenset(self._checking)

    def set_checking_listener(self, listener) -> None:
        """Hear every change of the chapters being checked, from the QA thread.

        The listener hears the current set at once: a window opened in the
        middle of a pass must not wait for the next chapter to learn it.
        """
        self._checking_listener = listener if callable(listener) else None
        safe_call(self._checking_listener, self.checking_chapters())

    def reset_cancellation(self) -> None:
        """Allow a new manual pass after the previous one was cancelled."""
        self._cancellation = CancellationToken()

    def drain(self, timeout: float | None = None) -> None:
        """Wait for every scheduled check to finish, within one shared deadline.

        ``timeout`` bounds the whole call, not each future in turn: a caller
        that says "wait up to 5 seconds" means five seconds total, not five
        per pending check.  Handing each future its own full timeout let a
        handful of stuck checks (a network call with no cancellation of its
        own) turn a 5 second shutdown into N*5 seconds of a frozen interface.
        """
        with self._pending_lock:
            pending = tuple(self._pending)
        deadline = None if timeout is None else time.monotonic() + timeout
        for future in pending:
            remaining = timeout
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
            try:
                future.result(timeout=remaining)
            except Exception:  # noqa: BLE001 - a failed check is already recorded
                continue

    def cancel(self) -> None:
        """Stop scheduling new work and ask running checks to stop safely."""
        self._cancellation.cancel()

    def shutdown(self, timeout: float | None = 5.0) -> None:
        """Stop the QA loop after the work in flight reaches a safe point."""
        self.cancel()
        self.drain(timeout=timeout)
        loop = self._loop
        if loop is not None:
            cleanup = asyncio.run_coroutine_threadsafe(
                self._cancel_loop_tasks(), loop
            )

            def stop_loop(_completed) -> None:
                loop.call_soon_threadsafe(loop.stop)

            # A slow check may have exhausted the cooperative drain deadline.
            # Let its task process CancelledError before closing the loop;
            # otherwise the coroutine is destroyed while still running.
            cleanup.add_done_callback(stop_loop)
            try:
                cleanup.result(timeout=None if timeout is None else max(timeout, 0.5))
            except Exception:  # noqa: BLE001 - the callback stops the loop after cleanup
                pass
        if self._thread is not None:
            self._thread.join(timeout=None if timeout is None else max(timeout, 0.5))
            if not self._thread.is_alive():
                self._thread = None
                self._loop = None

    @staticmethod
    async def _cancel_loop_tasks() -> None:
        """Finish task cancellation on the loop that owns the QA coroutines."""
        current = asyncio.current_task()
        pending = [task for task in asyncio.all_tasks() if task is not current]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    # -- checks ------------------------------------------------------------

    async def inspect_completed_task(
        self, task_id: str, events: Sequence[TranslationReadyEvent]
    ) -> TaskQaOutcome:
        """Check every chapter of one task in book order and classify the task."""
        options = self._options()
        results: list[ChapterQaResult] = []
        chapter_ids: list[str] = []
        for event in events:
            if self._cancellation.is_cancelled:
                return TaskQaOutcome(
                    task_id,
                    QaQueueOutcome.cancelled(chapter_ids),
                    tuple(results),
                )
            chapter_ids.append(event.chapter_id)
            result = await self._check_one(event, options)
            if result is not None:
                results.append(result)
        return TaskQaOutcome(
            task_id, _outcome_for(chapter_ids, results), tuple(results)
        )

    def select_unsettled_chapters(
        self, events: Sequence[TranslationReadyEvent]
    ) -> tuple[SelectedChapter, ...]:
        """Return the chapters whose last check no longer answers for them.

        A chapter checked under the current rules, carrying no unresolved risk
        and with its text unchanged, is left alone.  This is what lets a pass
        that was closed halfway be continued instead of paid for twice.

        The pass begins after the chapter checked last and comes back to the
        earlier ones at the end.  In book order it went back to chapter one
        whenever an early chapter was never checked or was deferred, and a
        continued pass looked like one started over.
        """
        journal = self._journal()
        states = dict(getattr(journal, "chapter_states", {}) or {})
        selected = select_final_pass_chapters(
            events,
            states,
            analysis_identity=self._analysis_identity(),
            book_sample_size=eligible_baseline_size(
                (getattr(journal, "metrics", {}) or {}).values()
            ),
            fingerprint_for=lambda item: chapter_fingerprint(item.translated_path),
        )
        return _after_last_check(selected, events, states)

    async def run_final_book_pass(
        self, session_id: str, on_progress=None
    ) -> BookQaResult:
        """Re-check only the chapters the book's own history says are unsettled."""
        events = self._book_events()
        if not events:
            return BookQaResult()
        selected = self.select_unsettled_chapters(events)
        if not selected:
            self._report(
                f"[QA] Итоговый проход: перепроверять нечего (сессия {session_id})."
            )
            return BookQaResult()
        self._report(
            "[QA] Итоговый проход по книге: "
            + ", ".join(
                f"{item.event.chapter_id} ({item.reason})" for item in selected[:10]
            )
            + ("…" if len(selected) > 10 else "")
        )
        return await self.check_all_now(
            tuple(item.event for item in selected), on_progress=on_progress
        )

    async def resume_pending_qa(self) -> ResumeResult:
        """Finish the checks a previous run owed, without repeating applied repairs."""
        if not callable(self._pending_tasks_provider):
            return ResumeResult()
        try:
            pending = tuple(self._pending_tasks_provider() or ())
        except Exception as error:  # noqa: BLE001 - a broken queue read is reportable
            return ResumeResult("failed", detail=str(error)[:200])
        if not pending:
            return ResumeResult()
        for task_id, chapter_ids in pending:
            self._owe_check(task_id, len(tuple(chapter_ids)))

        by_chapter = {event.chapter_id: event for event in self._book_events()}
        task_ids: list[str] = []
        results: list[ChapterQaResult] = []
        for task_id, chapter_ids in pending:
            events = tuple(
                by_chapter[chapter_id]
                for chapter_id in chapter_ids
                if chapter_id in by_chapter
            )
            if not events:
                self._resolve(
                    task_id,
                    QaQueueOutcome.deferred(
                        tuple(chapter_ids), reason="chapter_translation_missing"
                    ),
                )
                task_ids.append(str(task_id))
                continue
            outcome = await self.inspect_completed_task(str(task_id), events)
            self._resolve(task_id, outcome.outcome)
            task_ids.append(str(task_id))
            results.extend(outcome.results)
        return ResumeResult("resumed", tuple(task_ids), tuple(results))

    async def check_chapter_now(
        self, event: TranslationReadyEvent, options: QaOptions | None = None
    ) -> ChapterQaResult | None:
        """Run the same cascade the automatic path uses, for one chapter."""
        return await self._check_one(event, options or self._options())

    async def check_all_now(
        self,
        events: Sequence[TranslationReadyEvent],
        options: QaOptions | None = None,
        on_progress=None,
        on_chapter=None,
    ) -> BookQaResult:
        """Run the cascade over many chapters, stopping cleanly on cancellation.

        Chapters are independent — separate files, separate backups — and every
        check spends most of its time waiting on the network, so a few may run
        at once.  They share one event loop, which is what makes it safe: the
        journal is only ever mutated inside synchronous sections, and a
        cooperative loop cannot interrupt one.  The default of one keeps the
        old behaviour for anyone who does not ask for more.
        """
        resolved = options or self._options()
        limit = asyncio.Semaphore(self._max_concurrency)
        outcomes: dict[int, ChapterQaResult] = {}
        total = len(events)
        done = 0
        attempted = False
        stopped: set[int] = set()

        def report_progress(chapter_id: str) -> None:
            safe_call(on_progress, done, total, chapter_id)

        def report_chapter(result: ChapterQaResult | None) -> None:
            # A pass over a whole book runs for hours; whoever started it should
            # see each chapter's edits as they happen, not a report that stays
            # empty until the last one.
            if result is None:
                return
            safe_call(on_chapter, result)

        async def check(index: int, event: TranslationReadyEvent) -> None:
            nonlocal attempted, done
            if self._cancellation.is_cancelled:
                return
            result: ChapterQaResult | None = None
            async with limit:
                if self._cancellation.is_cancelled:
                    return
                # Held from the moment the file is read: a fix written after
                # that would be checked against text the check never saw.
                with self._checking_chapter(event.chapter_id):
                    request = self._build_request(event)
                    # A pool can already be terminal when the user starts a new
                    # pass in the same application session.  Always let the first
                    # buildable chapter reach the service so the report explains
                    # that state; silently skipping the entire book looks like a
                    # frozen UI.  An unreadable chapter is not an API attempt, so
                    # it must not prevent the next valid chapter from explaining
                    # why the provider is unavailable.
                    if request is not None:
                        if attempted and self._should_stop():
                            stopped.add(index)
                            return
                        attempted = True
                        result = await self._check_one(
                            event, resolved, request=request
                        )
                if result is not None:
                    outcomes[index] = result
            # Counted whether the chapter produced a result or not: the reader
            # is watching how much of the pass is left, not how much of it
            # succeeded.
            done += 1
            report_chapter(result)
            report_progress(event.chapter_id)

        await asyncio.gather(
            *(check(index, event) for index, event in enumerate(events))
        )
        results = tuple(outcomes[index] for index in sorted(outcomes))
        skipped = tuple(
            event.chapter_id
            for index, event in enumerate(events)
            if index not in outcomes
        )
        stopped_ids = tuple(
            event.chapter_id for index, event in enumerate(events) if index in stopped
        )
        return BookQaResult(
            results,
            tuple(dict.fromkeys(skipped)),
            stopped=tuple(dict.fromkeys(stopped_ids)),
        )

    def _should_stop(self) -> bool:
        stop_requested = getattr(self, "_stop_requested", None)
        if stop_requested is None:
            return False
        try:
            return bool(stop_requested())
        except Exception:  # noqa: BLE001 - a broken probe must not cancel QA
            return False

    async def undo_chapter(self, chapter_id: str):
        """Revert one chapter's automatic repairs through the same service."""
        return await self._service.undo_chapter(chapter_id)

    async def undo_all(self):
        """Revert every automatic repair this service recorded."""
        return await self._service.undo_session(self._service.session_id)

    async def apply_suggestion(self, suggestion_id: str):
        """Write one suggestion the user accepted into its chapter's translation."""
        suggestion = self._service.suggestion(suggestion_id)
        chapter_id = str(getattr(suggestion, "chapter_id", "") or "")
        if chapter_id and chapter_id in self.checking_chapters():
            # The check would write its own result over this fix, or judge a
            # file that changed under it.  The fix waits; nothing is touched.
            return SuggestionOutcome(
                "busy", suggestion_id, chapter_id, "глава сейчас проверяется"
            )
        translated_path = (
            next(
                (
                    event.translated_path
                    for event in self._book_events()
                    if event.chapter_id == chapter_id
                ),
                None,
            )
            if chapter_id
            else None
        )
        return await self._service.apply_suggestion(suggestion_id, translated_path)

    async def dismiss_suggestion(self, suggestion_id: str):
        """Take one suggestion off the list without touching the chapter."""
        return await self._service.dismiss_suggestion(suggestion_id)

    async def _check_one(
        self,
        event: TranslationReadyEvent,
        options: QaOptions,
        *,
        request=_REQUEST_NOT_BUILT,
    ) -> ChapterQaResult | None:
        with self._checking_chapter(event.chapter_id):
            if request is _REQUEST_NOT_BUILT:
                request = self._build_request(event)
            if request is None:
                return None
            try:
                async with self._one_check_at_a_time():
                    result = await self._service.check_chapter(
                        request, options, self._cancellation
                    )
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - QA never breaks translation
                self._report(f"[QA] Проверка главы '{event.chapter_id}' не удалась: {error}")
                return None
            result = await self._estimate_quality(event, result)
            if getattr(result, "quality_windows", ()) or getattr(
                result, "suggestion_windows", ()
            ):
                # The windows hold the chapter's whole text, and a book
                # pass keeps every result until it ends.
                result = replace(result, quality_windows=(), suggestion_windows=())
            self._note_limited_mode(result)
            self._report_chapter(event, result)
            return result

    @contextmanager
    def _checking_chapter(self, chapter_id: str):
        self._mark_checking(chapter_id, 1)
        try:
            yield
        finally:
            self._mark_checking(chapter_id, -1)

    def _mark_checking(self, chapter_id: str, step: int) -> None:
        with self._checking_lock:
            before = frozenset(self._checking)
            held = self._checking.get(chapter_id, 0) + step
            if held > 0:
                self._checking[chapter_id] = held
            else:
                self._checking.pop(chapter_id, None)
            current = frozenset(self._checking)
        if current != before:
            safe_call(self._checking_listener, current)

    def _build_request(self, event: TranslationReadyEvent):
        """Build one request and report project-state failures consistently."""
        try:
            request = self._request_builder(event)
        except Exception as error:  # noqa: BLE001 - QA never breaks translation
            self._report(f"[QA] Не удалось собрать запрос для '{event.chapter_id}': {error}")
            return None
        if request is None:
            # Measured on a live book: sixty chapters vanished this way, with
            # no trace in the journal and none in the log.
            self._report(
                f"[QA] Глава '{event.chapter_id}' пропущена: не удалось прочитать "
                "оригинал или перевод."
            )
            return None
        return request

    def _one_check_at_a_time(self) -> asyncio.Semaphore:
        """The semaphore every check passes through, bound to the running loop.

        The QA loop is stopped and started again between sessions, and a
        semaphore belongs to the loop it was first used on.
        """
        loop = asyncio.get_running_loop()
        if self._check_limit is None or self._check_limit_loop is not loop:
            self._check_limit = asyncio.Semaphore(self._max_concurrency)
            self._check_limit_loop = loop
        return self._check_limit

    def _note_limited_mode(self, result: ChapterQaResult) -> None:
        """Say once when semantic comparison has stopped working for the book.

        A single chapter in limited mode is ordinary — a slow service, one bad
        response.  A run of them means the embeddings are gone (a key, a quota,
        a blocked network), and the whole book is being checked with half the
        cascade while every chapter reports itself as merely deferred.
        """
        if result.coverage_mode == SEMANTIC_ALIGNMENT_MODE:
            self._limited_streak = 0
            self._limited_reported = False
            return
        if "completeness_check_disabled" in (getattr(result, "warnings", ()) or ()):
            # Nothing stopped working: the user switched the comparison off.
            return
        self._limited_streak += 1
        if self._limited_streak < LIMITED_MODE_ALERT_STREAK or self._limited_reported:
            return
        self._limited_reported = True
        self._report(
            f"[QA] Смысловое сравнение недоступно уже {self._limited_streak} глав подряд: "
            "проверяется только язык и статистика. Проверьте ключ, лимиты и сеть "
            "для эмбеддингов — эти главы попадут в итоговый проход."
        )

    async def _estimate_quality(
        self, event: TranslationReadyEvent, result: ChapterQaResult
    ) -> ChapterQaResult:
        """Score every passage the completeness check aligned, and each refused fix.

        Scoring only what the checks disputed left CometKiwi idle through a
        whole pass of clean chapters. The fixes ride in the same request and
        stay out of the chapter's score. Whatever comes back is evidence only —
        risk and repairs are already decided by the alignment and the model
        that read the text.
        """
        estimator = self._quality_estimator
        if estimator is None:
            return result
        chapter_windows = tuple(getattr(result, "quality_windows", ()) or ())
        fixes = tuple(getattr(result, "suggestion_windows", ()) or ())
        if not callable(getattr(self._service, "attach_quality_estimate", None)):
            chapter_windows = ()
        if not callable(getattr(self._service, "attach_suggestion_scores", None)):
            fixes = ()
        if not chapter_windows and not fixes:
            return result
        request = QualityEstimateRequest(
            chapter_id=result.chapter_id,
            windows=chapter_windows
            + tuple(window for fix in fixes for window in (fix.before, fix.after)),
            source_language=event.source_language or "auto",
            target_language=event.target_language or "ru",
        )
        try:
            estimate = await self._estimate_in_parts(estimator, request)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - an estimate never breaks QA
            self._report(
                f"[QA] Оценка качества главы '{event.chapter_id}' недоступна: {error}"
            )
            return result
        if chapter_windows:
            result = self._record_chapter_estimate(
                result, estimate, replace(request, windows=chapter_windows)
            )
        if fixes and estimate.status == "completed":
            self._record_fix_scores(
                result.chapter_id, fixes, estimate.window_scores[len(chapter_windows) :]
            )
        return result

    def _record_chapter_estimate(
        self,
        result: ChapterQaResult,
        estimate: QualityEstimate,
        chapter_request: QualityEstimateRequest,
    ) -> ChapterQaResult:
        """Store the chapter's own score: an answer over its windows alone."""
        try:
            if estimate.status == "completed":
                estimate = aggregate(
                    estimate.estimator,
                    estimate.model,
                    chapter_request,
                    estimate.window_scores[: len(chapter_request.windows)],
                    estimate.metadata,
                )
            return self._service.attach_quality_estimate(result, estimate)
        except Exception as error:  # noqa: BLE001 - nor does recording one
            self._report(f"[QA] Оценку качества не удалось записать: {error}")
            return result

    def _record_fix_scores(self, chapter_id: str, fixes, scores) -> None:
        """Give each refused fix its pair: the paragraph as it is, and with the fix."""
        if len(scores) != 2 * len(fixes):
            return
        pairs = {
            fix.suggestion_id: (scores[2 * index], scores[2 * index + 1])
            for index, fix in enumerate(fixes)
        }
        try:
            self._service.attach_suggestion_scores(chapter_id, pairs)
        except Exception as error:  # noqa: BLE001 - a fix's score never breaks QA
            self._report(
                f"[QA] Оценки правок главы '{chapter_id}' не удалось записать: {error}"
            )

    async def _estimate_in_parts(
        self, estimator, request: QualityEstimateRequest
    ) -> QualityEstimate:
        """Send a long chapter in requests the estimator accepts, and score it once.

        The first part that fails answers for the whole chapter: a score over
        some of its passages would read as the score of all of them.
        """
        windows = request.windows
        if len(windows) <= MAX_WINDOWS_PER_ESTIMATE:
            return await estimator.estimate(request, self._cancellation)
        scores: list[float] = []
        for start in range(0, len(windows), MAX_WINDOWS_PER_ESTIMATE):
            part = replace(
                request, windows=windows[start : start + MAX_WINDOWS_PER_ESTIMATE]
            )
            estimate = await estimator.estimate(part, self._cancellation)
            if estimate.status != "completed":
                return estimate
            scores.extend(estimate.window_scores)
        return aggregate(
            estimate.estimator, estimate.model, request, scores, estimate.metadata
        )

    def _report_chapter(self, event: TranslationReadyEvent, result) -> None:
        """Log what the check changed, with the text before and after each edit."""
        applied = len(getattr(result, "applied_repair_ids", ()) or ())
        language = len(getattr(getattr(result, "language", None), "applied", ()) or ())
        if not getattr(result, "changed_anything", False):
            if not result.may_continue_translation:
                self._report(
                    f"[QA] Глава '{event.chapter_id}': высокий риск, "
                    "требуется проверка.",
                    details_title=f"Проверка главы '{event.chapter_id}'",
                    details_text=result.change_details(),
                    details_html=result.change_details_html(),
                )
                return
            deferred = tuple(
                warning
                for warning in (getattr(result, "warnings", ()) or ())
                if warning in DEFERRED_WARNINGS
            )
            if deferred:
                # A deferred chapter used to pass in silence, and a whole night
                # of them read as "the check is not running at all".
                self._report(
                    f"[QA] Глава '{event.chapter_id}' отложена: "
                    f"{_deferral_summary(result, deferred)}",
                    details_title=f"Проверка главы '{event.chapter_id}'",
                    details_text=result.change_details(),
                    details_html=result.change_details_html(),
                )
            return
        self._report(
            f"[QA] Глава '{event.chapter_id}': исправлено пропусков — {applied}, "
            f"языковых дефектов — {language}.",
            details_title=f"Исправления в главе '{event.chapter_id}'",
            details_text=result.change_details(),
            details_html=result.change_details_html(),
        )

    async def _inspect_and_resolve(
        self, task_id: str, events: Sequence[TranslationReadyEvent]
    ) -> TaskQaOutcome:
        try:
            outcome = await self.inspect_completed_task(task_id, events)
        except asyncio.CancelledError:
            self._resolve(
                task_id,
                QaQueueOutcome.cancelled([event.chapter_id for event in events]),
            )
            raise
        except Exception as error:  # noqa: BLE001 - a QA crash must not lose a task
            self._report(f"[QA] Сбой проверки задачи: {error}")
            outcome = TaskQaOutcome(
                task_id,
                QaQueueOutcome.deferred(
                    [event.chapter_id for event in events], reason=str(error)[:200]
                ),
            )
        self._resolve(task_id, outcome.outcome)
        return outcome

    # -- queue and reporting ----------------------------------------------

    def _mark_pending(
        self, task_id: str, events: Sequence[TranslationReadyEvent]
    ) -> None:
        if self._task_manager is None:
            return
        try:
            self._task_manager.mark_task_qa_pending(
                task_id, [event.chapter_id for event in events]
            )
        except Exception as error:  # noqa: BLE001 - the queue is not QA's to break
            self._report(f"[QA] Не удалось перевести задачу в проверку: {error}")

    def _resolve(self, task_id: str, outcome: QaQueueOutcome) -> None:
        self._settle_check(task_id, outcome)
        if self._task_manager is None:
            return
        try:
            self._task_manager.resolve_task_qa(task_id, outcome)
        except Exception as error:  # noqa: BLE001 - the queue is not QA's to break
            self._report(f"[QA] Не удалось закрыть проверку задачи: {error}")

    def _book_events(self) -> tuple[TranslationReadyEvent, ...]:
        if not callable(self._book_events_provider):
            return ()
        try:
            return tuple(self._book_events_provider() or ())
        except Exception as error:  # noqa: BLE001 - a broken project map is reportable
            self._report(f"[QA] Не удалось собрать список глав книги: {error}")
            return ()

    def _journal(self):
        if callable(self._journal_provider):
            try:
                return self._journal_provider()
            except Exception as error:  # noqa: BLE001 - a damaged journal is reportable
                self._report(f"[QA] Журнал проверок недоступен: {error}")
        return _EmptyJournal()

    def _analysis_identity(self) -> str:
        value = self._analysis_identity_value
        if callable(value):
            try:
                return str(value() or "")
            except Exception:  # noqa: BLE001 - identity is advisory for selection
                return ""
        return str(value or "")

    def _options(self) -> QaOptions:
        try:
            options = self._options_provider()
        except Exception:  # noqa: BLE001 - unreadable settings fall back to defaults
            return QaOptions()
        return options if isinstance(options, QaOptions) else QaOptions()

    def _report(
        self,
        message: str,
        details_title: str = "",
        details_text: str = "",
        details_html: str = "",
    ) -> None:
        """Log one line, with an expandable detail block when there is one."""
        if not callable(self._log):
            return
        try:
            if details_text:
                self._log(
                    message,
                    details_title=details_title,
                    details_text=details_text,
                    details_html=details_html,
                )
            else:
                self._log(message)
        except TypeError:
            try:
                self._log(message)
            except Exception:  # noqa: BLE001 - logging must never raise
                return
        except Exception:  # noqa: BLE001 - logging must never raise
            return

    def _discard_future(self, future: asyncio.Future) -> None:
        with self._pending_lock:
            self._pending.discard(future)


def _text_unchanged(
    event: TranslationReadyEvent, state: QaChapterState, fingerprint_for
) -> bool:
    """Report whether the chapter still holds the text its last check answered for.

    Without a fingerprint on either side the answer is no: an unknown chapter is
    re-checked, which is the safe direction.
    """
    recorded = str(getattr(state, "fingerprint", "") or "")
    if not recorded or not callable(fingerprint_for):
        return False
    try:
        current = str(fingerprint_for(event) or "")
    except Exception:  # noqa: BLE001 - an unreadable chapter is simply unknown
        return False
    return bool(current) and current == recorded


def _deferral_summary(result, deferred: Sequence[str]) -> str:
    """One line saying why a chapter was deferred, with the service's own words.

    The deferral code names the stage; the language stage's first warning
    names the cause — an exhausted key, a busy service, a refused prompt — and
    that is what decides what to do about it.
    """
    from ..qa.language_validation import describe_refusal

    reasons = [describe_deferral(code) for code in deferred]
    language = getattr(result, "language", None)
    causes = tuple(getattr(language, "warnings", ()) or ()) if language else ()
    summary = "; ".join(dict.fromkeys(reasons))
    if causes:
        summary += f" — {describe_refusal(causes[0])}"
    return summary


def _outcome_for(
    chapter_ids: Sequence[str], results: Sequence[ChapterQaResult]
) -> QaQueueOutcome:
    """Classify one task from the results of all its chapters."""
    blocking = [result for result in results if not result.may_continue_translation]
    if blocking:
        reasons = ", ".join(sorted({result.chapter_id for result in blocking}))
        return QaQueueOutcome.high_unresolved(chapter_ids, reason=reasons)
    if len(results) != len(chapter_ids):
        return QaQueueOutcome.deferred(chapter_ids, reason="chapter_check_unavailable")
    deferred = [
        warning
        for result in results
        for warning in result.warnings
        if warning in DEFERRED_WARNINGS
    ]
    if deferred:
        return QaQueueOutcome.deferred(
            chapter_ids, reason=", ".join(sorted(set(deferred)))
        )
    return QaQueueOutcome.completed(chapter_ids)


def _after_last_check(
    selected: tuple[SelectedChapter, ...],
    events: Sequence[TranslationReadyEvent],
    states: Mapping[str, QaChapterState],
) -> tuple[SelectedChapter, ...]:
    """Put the chapters after the one checked last first, and the rest after them."""
    position = {event.chapter_id: index for index, event in enumerate(events)}
    checked = [
        (state.updated_at, position[chapter_id])
        for chapter_id, state in states.items()
        if state.updated_at and chapter_id in position
    ]
    if not checked:
        return selected
    last = max(checked)[1]
    later = tuple(item for item in selected if position[item.event.chapter_id] > last)
    return later + tuple(
        item for item in selected if position[item.event.chapter_id] <= last
    )


class _EmptyJournal:
    """Stand-in used when no journal is available: nothing is known, so recheck."""

    metrics: dict = {}
    chapter_states: dict = {}


def select_final_pass_chapters(
    events: Sequence[TranslationReadyEvent],
    states: Mapping[str, QaChapterState],
    *,
    analysis_identity: str,
    book_sample_size: int,
    minimum_baseline_sample: int = MIN_BASELINE_SAMPLE_SIZE,
    fingerprint_for=None,
) -> tuple[SelectedChapter, ...]:
    """Pick the chapters whose last check no longer answers for them.

    A chapter that was fully checked under the current rules, carries no
    unresolved risk, and already had the book's statistics available is left
    alone: the final pass exists to close gaps, not to spend the book again.

    A chapter that only reached MEDIUM is left alone too, provided its text has
    not changed since that check: the same text under the same rules produces
    the same answer, so re-asking costs the whole cascade to learn nothing.  A
    reader who wants it asked again has "Проверить все главы", which never
    consults this function.
    """

    selected: list[SelectedChapter] = []
    for event in events:
        state = states.get(event.chapter_id)
        reason = ""
        if state is None:
            reason = "never_checked"
        elif state.status == "deferred":
            reason = "deferred"
        elif state.status == "blocked":
            reason = "unresolved_risk"
        elif analysis_identity and state.analysis_identity != analysis_identity:
            reason = "analysis_version_changed"
        elif RiskLevel(state.risk_level) is RiskLevel.HIGH:
            reason = "unresolved_risk"
        elif RiskLevel(state.risk_level) is RiskLevel.MEDIUM and not _text_unchanged(
            event, state, fingerprint_for
        ):
            reason = "unresolved_risk"
        elif (
            state.book_sample_size < minimum_baseline_sample
            <= book_sample_size
        ):
            reason = "baseline_now_available"
        if reason:
            selected.append(SelectedChapter(event, reason))
    return tuple(selected)
