"""Run translation QA between chapters without blocking the translation itself."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import threading

from ..qa.book_metrics import MIN_BASELINE_SAMPLE_SIZE
from ..qa.coverage_service import SEMANTIC_ALIGNMENT_MODE
from ..qa.estimators.base import (
    QualityEstimateRequest,
    SourceTranslationWindow,
)
from ..qa.llm.completion import CancellationToken
from ..qa.models import Decision, QaChapterState, RiskLevel
from ..qa.service import (
    DEFERRED_WARNINGS,
    ChapterQaResult,
    QaOptions,
    TranslationQualityService,
    chapter_fingerprint,
)
from .task_manager import QaQueueOutcome


# How many chapters in a row must lose semantic comparison before the session
# is told.  One is noise; a run of three is a broken setup.
LIMITED_MODE_ALERT_STREAK = 3


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
        self._limited_streak = 0
        self._limited_reported = False
        self._log = log
        self._max_concurrency = max(1, max_concurrency)
        self._cancellation = CancellationToken()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._pending: set[asyncio.Future] = set()
        self._pending_lock = threading.Lock()

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

    def reset_cancellation(self) -> None:
        """Allow a new manual pass after the previous one was cancelled."""
        self._cancellation = CancellationToken()

    def drain(self, timeout: float | None = None) -> None:
        """Wait for every scheduled check to finish."""
        with self._pending_lock:
            pending = tuple(self._pending)
        for future in pending:
            try:
                future.result(timeout=timeout)
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
            loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self._thread = None
        self._loop = None

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

    async def run_final_book_pass(
        self, session_id: str, on_progress=None
    ) -> BookQaResult:
        """Re-check only the chapters the book's own history says are unsettled."""
        events = self._book_events()
        if not events:
            return BookQaResult()
        journal = self._journal()
        states = dict(getattr(journal, "chapter_states", {}) or {})
        selected = select_final_pass_chapters(
            events,
            states,
            analysis_identity=self._analysis_identity(),
            book_sample_size=len(getattr(journal, "metrics", {}) or {}),
            fingerprint_for=lambda item: chapter_fingerprint(item.translated_path),
        )
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

        def report_progress(chapter_id: str) -> None:
            if not callable(on_progress):
                return
            try:
                on_progress(done, total, chapter_id)
            except Exception:  # noqa: BLE001 - a display never fails a check
                return

        async def check(index: int, event: TranslationReadyEvent) -> None:
            nonlocal done
            if self._cancellation.is_cancelled:
                return
            async with limit:
                if self._cancellation.is_cancelled:
                    return
                result = await self._check_one(event, resolved)
                if result is not None:
                    outcomes[index] = result
            # Counted whether the chapter produced a result or not: the reader
            # is watching how much of the pass is left, not how much of it
            # succeeded.
            done += 1
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
        return BookQaResult(results, tuple(dict.fromkeys(skipped)))

    async def undo_chapter(self, chapter_id: str):
        """Revert one chapter's automatic repairs through the same service."""
        return await self._service.undo_chapter(chapter_id)

    async def undo_all(self):
        """Revert every automatic repair this service recorded."""
        return await self._service.undo_session(self._service.session_id)

    async def _check_one(
        self, event: TranslationReadyEvent, options: QaOptions
    ) -> ChapterQaResult | None:
        try:
            request = self._request_builder(event)
        except Exception as error:  # noqa: BLE001 - QA never breaks translation
            self._report(f"[QA] Не удалось собрать запрос для '{event.chapter_id}': {error}")
            return None
        if request is None:
            return None
        try:
            result = await self._service.check_chapter(
                request, options, self._cancellation
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - QA never breaks translation
            self._report(f"[QA] Проверка главы '{event.chapter_id}' не удалась: {error}")
            return None
        result = await self._estimate_quality(event, result)
        self._note_limited_mode(result)
        self._report_chapter(event, result)
        return result

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
        """Score the windows still in dispute, and only those.

        A chapter the checks agreed on is never worth a heavy model: the
        estimator process is not started at all unless something is unresolved,
        and whatever it answers is evidence only — risk and repairs are already
        decided by the alignment and the model that read the text.
        """
        estimator = self._quality_estimator
        if estimator is None:
            return result
        windows = _disputed_windows(result)
        if not windows:
            return result
        attach = getattr(self._service, "attach_quality_estimate", None)
        if not callable(attach):
            return result
        request = QualityEstimateRequest(
            chapter_id=result.chapter_id,
            windows=windows,
            source_language=event.source_language or "auto",
            target_language=event.target_language or "ru",
        )
        try:
            estimate = await estimator.estimate(request, self._cancellation)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - an estimate never breaks QA
            self._report(
                f"[QA] Оценка качества главы '{event.chapter_id}' недоступна: {error}"
            )
            return result
        try:
            return attach(result, estimate)
        except Exception as error:  # noqa: BLE001 - nor does recording one
            self._report(f"[QA] Оценку качества не удалось записать: {error}")
            return result

    def _report_chapter(self, event: TranslationReadyEvent, result) -> None:
        """Log what the check changed, with the text before and after each edit."""
        applied = len(getattr(result, "applied_repair_ids", ()) or ())
        language = len(getattr(getattr(result, "language", None), "applied", ()) or ())
        if not getattr(result, "changed_anything", False):
            if not result.may_continue_translation:
                self._report(
                    f"[QA] Глава '{event.chapter_id}': перевод остановлен, "
                    "нужно решение.",
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


def _disputed_windows(
    result: ChapterQaResult,
) -> tuple[SourceTranslationWindow, ...]:
    """Build one window per candidate the checks could not settle.

    A settled candidate — covered by the model, or fixed and confirmed by the
    post-check — is not in dispute and costs nothing to skip.
    """
    fixed = {
        repair.candidate_id
        for repair in result.repairs
        if repair.decision is Decision.FIXED
    }
    windows: list[SourceTranslationWindow] = []
    for item in result.verified:
        verdict = item.verdict
        if verdict is None or verdict.decision == "covered":
            continue
        if item.candidate.candidate_id in fixed:
            continue
        context = item.context
        source = " ".join(
            part.strip()
            for part in (context.source_before, context.source_text, context.source_after)
            if part.strip()
        )
        translation = " ".join(
            part.strip()
            for part in (context.target_before, context.target_text, context.target_after)
            if part.strip()
        )
        visible = sum(1 for character in translation if not character.isspace())
        if not source or not translation or visible <= 0:
            continue
        windows.append(
            SourceTranslationWindow(
                window_id=item.candidate.candidate_id,
                source=source,
                translation=translation,
                visible_chars=visible,
            )
        )
    return tuple(windows)


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
