"""Run translation QA between chapters without blocking the translation itself."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
import threading

from ..qa.llm.completion import CancellationToken
from ..qa.service import ChapterQaResult, QaOptions, TranslationQualityService
from .task_manager import QaQueueOutcome


DEFERRED_WARNINGS = frozenset(
    {
        "coverage_failed",
        "embeddings_unavailable",
        "invalid_embedding_response",
        "alignment_capacity_exceeded",
        "verification_failed",
        "language_check_failed",
        "addition_detection_failed",
        "chapter_not_readable",
        "language_tool_unavailable",
        "slovnet_unavailable",
    }
)


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
        log=None,
        max_concurrency: int = 1,
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

    async def check_chapter_now(
        self, event: TranslationReadyEvent, options: QaOptions | None = None
    ) -> ChapterQaResult | None:
        """Run the same cascade the automatic path uses, for one chapter."""
        return await self._check_one(event, options or self._options())

    async def check_all_now(
        self,
        events: Sequence[TranslationReadyEvent],
        options: QaOptions | None = None,
    ) -> BookQaResult:
        """Run the cascade over many chapters, stopping cleanly on cancellation."""
        resolved = options or self._options()
        results: list[ChapterQaResult] = []
        skipped: list[str] = []
        for event in events:
            if self._cancellation.is_cancelled:
                skipped.extend(item.chapter_id for item in events[len(results) :])
                break
            result = await self._check_one(event, resolved)
            if result is None:
                skipped.append(event.chapter_id)
            else:
                results.append(result)
        return BookQaResult(tuple(results), tuple(dict.fromkeys(skipped)))

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
            return await self._service.check_chapter(request, options, self._cancellation)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - QA never breaks translation
            self._report(f"[QA] Проверка главы '{event.chapter_id}' не удалась: {error}")
            return None

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

    def _options(self) -> QaOptions:
        try:
            options = self._options_provider()
        except Exception:  # noqa: BLE001 - unreadable settings fall back to defaults
            return QaOptions()
        return options if isinstance(options, QaOptions) else QaOptions()

    def _report(self, message: str) -> None:
        if callable(self._log):
            try:
                self._log(message)
            except Exception:  # noqa: BLE001 - logging must never raise
                return

    def _discard_future(self, future: asyncio.Future) -> None:
        with self._pending_lock:
            self._pending.discard(future)


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
