"""The final pass exists to close gaps, not to re-ask settled questions."""

from __future__ import annotations

from pathlib import Path

import pytest

from gemini_translator.core.chapter_qa_coordinator import (
    TranslationReadyEvent,
    select_final_pass_chapters,
)
from gemini_translator.qa.models import QaChapterState, RiskLevel
from gemini_translator.qa.service import chapter_fingerprint


_IDENTITY = "semantic-units-v1|razdel-0.5"


def _event(chapter_id: str, path: Path) -> TranslationReadyEvent:
    return TranslationReadyEvent(
        task_id="task-1",
        chapter_id=chapter_id,
        source_path=f"OEBPS/{chapter_id}",
        translated_path=str(path),
        source_language="zh",
        target_language="ru",
    )


def _state(chapter_id: str, **overrides) -> QaChapterState:
    values: dict[str, object] = {
        "chapter_id": chapter_id,
        "status": "checked",
        "analysis_identity": _IDENTITY,
        "risk_level": RiskLevel.MEDIUM,
        "book_sample_size": 10,
        "fingerprint": "",
    }
    values.update(overrides)
    return QaChapterState(**values)  # type: ignore[arg-type]


def _select(events, states, fingerprint_for=None):
    return select_final_pass_chapters(
        events,
        {state.chapter_id: state for state in states},
        analysis_identity=_IDENTITY,
        book_sample_size=10,
        fingerprint_for=fingerprint_for,
    )


@pytest.fixture()
def chapter(tmp_path: Path) -> Path:
    path = tmp_path / "chapter-1.html"
    path.write_text("<p>Он вышел наружу.</p>", encoding="utf-8")
    return path


def test_an_unchanged_medium_chapter_is_not_asked_again(chapter):
    """Тот же текст по тем же правилам даёт тот же ответ — это чистая трата книги."""
    event = _event("chapter-1", chapter)
    state = _state("chapter-1", fingerprint=chapter_fingerprint(chapter))

    assert _select([event], [state], lambda item: chapter_fingerprint(item.translated_path)) == ()


def test_a_medium_chapter_that_changed_is_asked_again(chapter):
    """Изменившийся текст — новый вопрос, даже если риск тот же."""
    event = _event("chapter-1", chapter)
    state = _state("chapter-1", fingerprint="sha256:" + "0" * 64)

    selected = _select([event], [state], lambda item: chapter_fingerprint(item.translated_path))

    assert [item.reason for item in selected] == ["unresolved_risk"]


def test_high_risk_is_always_asked_again(chapter):
    """Высокий риск — это незакрытый вопрос, сколько бы раз его ни задавали."""
    event = _event("chapter-1", chapter)
    state = _state(
        "chapter-1", risk_level=RiskLevel.HIGH, fingerprint=chapter_fingerprint(chapter)
    )

    selected = _select([event], [state], lambda item: chapter_fingerprint(item.translated_path))

    assert [item.reason for item in selected] == ["unresolved_risk"]


def test_a_state_without_a_fingerprint_is_asked_again(chapter):
    """Записи старых версий не знают отпечатка; неизвестность решается в пользу проверки."""
    event = _event("chapter-1", chapter)

    selected = _select(
        [event], [_state("chapter-1")], lambda item: chapter_fingerprint(item.translated_path)
    )

    assert [item.reason for item in selected] == ["unresolved_risk"]


def test_an_unreadable_chapter_is_asked_again(tmp_path: Path):
    """Отсутствующий файл — это не «не изменился»."""
    missing = tmp_path / "gone.html"
    event = _event("chapter-1", missing)
    state = _state("chapter-1", fingerprint="sha256:" + "0" * 64)

    selected = _select([event], [state], lambda item: chapter_fingerprint(item.translated_path))

    assert [item.reason for item in selected] == ["unresolved_risk"]


def test_a_broken_fingerprint_source_never_breaks_the_pass(chapter):
    """Сбой при чтении отпечатка не имеет права уронить итоговый проход."""

    def explode(_event):
        raise OSError("disk is on fire")

    selected = _select(
        [_event("chapter-1", chapter)],
        [_state("chapter-1", fingerprint="sha256:" + "0" * 64)],
        explode,
    )

    assert [item.reason for item in selected] == ["unresolved_risk"]


def test_the_other_reasons_still_win_over_an_unchanged_text(chapter):
    """Отложенная, заблокированная и проверенная по старым правилам — всё ещё вопросы."""
    fingerprint = chapter_fingerprint(chapter)
    events = [_event(f"chapter-{index}", chapter) for index in range(1, 5)]
    states = [
        _state("chapter-1", status="deferred", fingerprint=fingerprint),
        _state("chapter-2", status="blocked", fingerprint=fingerprint),
        _state("chapter-3", analysis_identity="older-rules", fingerprint=fingerprint),
        _state("chapter-4", risk_level=RiskLevel.LOW, book_sample_size=0,
               fingerprint=fingerprint),
    ]

    selected = _select(events, states, lambda item: chapter_fingerprint(item.translated_path))

    assert [item.reason for item in selected] == [
        "deferred",
        "unresolved_risk",
        "analysis_version_changed",
        "baseline_now_available",
    ]


def test_without_a_fingerprint_source_the_old_behaviour_stands(chapter):
    """Вызов без отпечатков ведёт себя ровно как раньше."""
    event = _event("chapter-1", chapter)
    state = _state("chapter-1", fingerprint=chapter_fingerprint(chapter))

    selected = _select([event], [state])

    assert [item.reason for item in selected] == ["unresolved_risk"]


def test_the_fingerprint_follows_the_text_and_nothing_else(tmp_path: Path):
    """Отпечаток обязан меняться от правки и не меняться от повторного чтения."""
    path = tmp_path / "chapter.html"
    path.write_text("<p>Первый вариант.</p>", encoding="utf-8")
    first = chapter_fingerprint(path)
    again = chapter_fingerprint(path)
    path.write_text("<p>Второй вариант.</p>", encoding="utf-8")

    assert first == again
    assert first.startswith("sha256:")
    assert chapter_fingerprint(path) != first
    assert chapter_fingerprint(tmp_path / "missing.html") == ""


# --- checking several chapters at once --------------------------------------


def test_a_batch_keeps_reading_order_and_reports_what_it_skipped():
    """Параллельность не имеет права перепутать главы местами."""
    import asyncio

    from gemini_translator.core.chapter_qa_coordinator import ChapterQaCoordinator
    from gemini_translator.qa.models import RiskLevel
    from gemini_translator.qa.service import ChapterQaResult, QaOptions

    class _Service:
        def __init__(self) -> None:
            self.live = 0
            self.peak = 0

        async def check_chapter(self, request, options, cancellation):
            self.live += 1
            self.peak = max(self.peak, self.live)
            # Every chapter yields, so a serial runner would never overlap.
            await asyncio.sleep(0)
            self.live -= 1
            if request == "chapter-2":
                raise RuntimeError("this chapter cannot be checked")
            return ChapterQaResult(
                chapter_id=request,
                risk_level=RiskLevel.LOW,
                may_continue_translation=True,
                coverage_mode="semantic_alignment",
            )

    service = _Service()
    coordinator = ChapterQaCoordinator(
        service=service,
        task_manager=None,
        request_builder=lambda event: event.chapter_id,
        max_concurrency=3,
    )
    events = tuple(
        _event(f"chapter-{index}", Path(f"/tmp/chapter-{index}")) for index in range(4)
    )

    outcome = asyncio.run(coordinator.check_all_now(events, QaOptions()))

    assert [item.chapter_id for item in outcome.results] == [
        "chapter-0",
        "chapter-1",
        "chapter-3",
    ]
    assert outcome.skipped == ("chapter-2",)
    assert service.peak > 1


def test_the_default_stays_one_chapter_at_a_time():
    """Поведение по умолчанию не меняется, пока о большем не попросили."""
    import asyncio

    from gemini_translator.core.chapter_qa_coordinator import ChapterQaCoordinator
    from gemini_translator.qa.models import RiskLevel
    from gemini_translator.qa.service import ChapterQaResult, QaOptions

    class _Service:
        def __init__(self) -> None:
            self.live = 0
            self.peak = 0

        async def check_chapter(self, request, options, cancellation):
            self.live += 1
            self.peak = max(self.peak, self.live)
            await asyncio.sleep(0)
            self.live -= 1
            return ChapterQaResult(
                chapter_id=request,
                risk_level=RiskLevel.LOW,
                may_continue_translation=True,
                coverage_mode="semantic_alignment",
            )

    service = _Service()
    coordinator = ChapterQaCoordinator(
        service=service,
        task_manager=None,
        request_builder=lambda event: event.chapter_id,
    )
    events = tuple(
        _event(f"chapter-{index}", Path(f"/tmp/chapter-{index}")) for index in range(4)
    )

    asyncio.run(coordinator.check_all_now(events, QaOptions()))

    assert service.peak == 1


def test_the_batch_size_is_bounded_by_the_settings():
    """Ручка должна быть достижима и не должна принимать что угодно."""
    from gemini_translator.qa.settings import QaSettings

    assert QaSettings().batch_concurrency == 1
    assert QaSettings(batch_concurrency=3).batch_concurrency == 3
    assert QaSettings(batch_concurrency=99).batch_concurrency == 4
    assert QaSettings(batch_concurrency=0).batch_concurrency == 1
    assert QaSettings.from_dict({"batch_concurrency": "два"}).batch_concurrency == 1


def test_the_pass_reports_each_finished_chapter():
    """Проход, который молчит минутами, неотличим от зависшего."""
    import asyncio

    from gemini_translator.core.chapter_qa_coordinator import ChapterQaCoordinator
    from gemini_translator.qa.models import RiskLevel
    from gemini_translator.qa.service import ChapterQaResult, QaOptions

    class _Service:
        async def check_chapter(self, request, options, cancellation):
            if request == "chapter-1":
                raise RuntimeError("unreadable")
            return ChapterQaResult(
                chapter_id=request,
                risk_level=RiskLevel.LOW,
                may_continue_translation=True,
                coverage_mode="semantic_alignment",
            )

    seen: list[tuple[int, int, str]] = []
    coordinator = ChapterQaCoordinator(
        service=_Service(),
        task_manager=None,
        request_builder=lambda event: event.chapter_id,
    )
    events = tuple(
        _event(f"chapter-{index}", Path(f"/tmp/chapter-{index}")) for index in range(3)
    )

    asyncio.run(
        coordinator.check_all_now(
            events, QaOptions(), on_progress=lambda *args: seen.append(args)
        )
    )

    # Counted even for the chapter that failed: the reader watches the pass,
    # not its success rate.
    assert [item[0] for item in seen] == [1, 2, 3]
    assert {item[1] for item in seen} == {3}
    assert seen[-1][2] == "chapter-2"


def test_a_broken_progress_callback_never_fails_the_pass():
    """Отчёт о ходе — украшение, а не условие проверки."""
    import asyncio

    from gemini_translator.core.chapter_qa_coordinator import ChapterQaCoordinator
    from gemini_translator.qa.models import RiskLevel
    from gemini_translator.qa.service import ChapterQaResult, QaOptions

    class _Service:
        async def check_chapter(self, request, options, cancellation):
            return ChapterQaResult(
                chapter_id=request,
                risk_level=RiskLevel.LOW,
                may_continue_translation=True,
                coverage_mode="semantic_alignment",
            )

    def explode(*_args):
        raise RuntimeError("the progress bar is on fire")

    coordinator = ChapterQaCoordinator(
        service=_Service(),
        task_manager=None,
        request_builder=lambda event: event.chapter_id,
    )
    events = (_event("chapter-0", Path("/tmp/chapter-0")),)

    outcome = asyncio.run(
        coordinator.check_all_now(events, QaOptions(), on_progress=explode)
    )

    assert [item.chapter_id for item in outcome.results] == ["chapter-0"]
