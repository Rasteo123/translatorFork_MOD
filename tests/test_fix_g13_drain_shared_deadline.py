"""g13 / core-a/bugs/2-qa-coordinator-shutdown-abando.

drain(timeout) должен ограничивать ОБЩЕЕ время ожидания всех незавершённых
проверок одним бюджетом, а не тратить ``timeout`` секунд на каждую из них по
отдельности. Раньше при N зависших проверках (например, сетевой embed-вызов
в CoverageService.analyze() без поддержки отмены) drain()/shutdown() держали
поток (в реальном приложении — поток GUI, вызывающий shutdown() из таймера
сессии) до N*timeout секунд вместо заявленного timeout.
"""

from __future__ import annotations

import asyncio
import time

from gemini_translator.core.chapter_qa_coordinator import (
    ChapterQaCoordinator,
    TranslationReadyEvent,
)
from gemini_translator.core.task_manager import QaQueueOutcome


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


class _StuckService:
    """Имитирует сетевой вызов без поддержки отмены (как CoverageService.analyze).

    Кооперативный CancellationToken игнорируется намеренно — именно так ведёт
    себя реальный embed-запрос, у которого нет собственной гонки с отменой.
    """

    async def check_chapter(self, request, options, cancellation):
        await asyncio.sleep(999)
        raise AssertionError("не должно завершиться в рамках теста")


def _event(chapter_id: str, task_id: str) -> TranslationReadyEvent:
    return TranslationReadyEvent(
        task_id=task_id,
        chapter_id=chapter_id,
        source_path=f"OEBPS/{chapter_id}",
        translated_path=f"/tmp/{chapter_id}",
        source_language="en",
        target_language="ru",
    )


def _coordinator(service, queue) -> ChapterQaCoordinator:
    return ChapterQaCoordinator(
        service=service,
        task_manager=queue,
        request_builder=lambda event: event.chapter_id,
    )


def test_drain_bounds_total_wait_by_one_shared_timeout():
    """drain(timeout=T) с N зависшими проверками не должен стоить N*T."""
    queue = _QueueStub()
    coordinator = _coordinator(_StuckService(), queue)
    task_count = 3
    per_task_timeout = 0.2
    for index in range(task_count):
        task_id = f"task-{index}"
        coordinator.submit(task_id, (_event(f"chapter-{index}", task_id),))

    started = time.monotonic()
    coordinator.drain(timeout=per_task_timeout)
    elapsed = time.monotonic() - started

    # До фикса: drain ждал per_task_timeout НА КАЖДЫЙ future -> ~task_count*per_task_timeout (0.6с+).
    # После фикса: один общий бюджет на весь вызов -> заметно меньше N*timeout.
    assert elapsed < per_task_timeout * task_count * 0.75, (
        f"drain() потратил {elapsed:.3f}с на {task_count} проверок при "
        f"timeout={per_task_timeout}с — похоже, бюджет расходуется на каждый "
        "future по отдельности, а не один общий"
    )

    coordinator.shutdown(timeout=0.2)
