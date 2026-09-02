"""A chapter the check could not finish must say so in the log, once, with why.

Measured on a live book: 110 of 112 checked chapters were deferred because no
request reached the model, and the log showed nothing about any of them.  The
user watched chapters translate one after another and concluded the check was
not running at all.
"""

from __future__ import annotations

import asyncio

from gemini_translator.core.chapter_qa_coordinator import (
    LIMITED_MODE_ALERT_STREAK,
    ChapterQaCoordinator,
    TranslationReadyEvent,
)
from gemini_translator.qa.language_validation import LanguageQaResult
from gemini_translator.qa.models import RiskLevel
from gemini_translator.qa.service import ChapterQaResult, QaOptions, describe_deferral


def _event(chapter_id: str) -> TranslationReadyEvent:
    return TranslationReadyEvent(
        task_id="task-1",
        chapter_id=chapter_id,
        source_path=f"OEBPS/{chapter_id}",
        translated_path=f"/tmp/{chapter_id}",
        source_language="zh",
        target_language="ru",
    )


def _deferred(chapter_id: str, *, warnings, language=None) -> ChapterQaResult:
    return ChapterQaResult(
        chapter_id=chapter_id,
        risk_level=RiskLevel.MEDIUM,
        may_continue_translation=True,
        coverage_mode="unavailable",
        language=language,
        warnings=tuple(warnings),
    )


def _logged(result_for, *, builder=None) -> list[tuple[str, str, str]]:
    logged: list[tuple[str, str, str]] = []

    class _Service:
        async def check_chapter(self, request, options, cancellation):
            return result_for(request)

    coordinator = ChapterQaCoordinator(
        service=_Service(),
        task_manager=None,
        request_builder=builder or (lambda event: event.chapter_id),
        log=lambda message, details_title="", details_text="", details_html="": (
            logged.append((message, details_title, details_text))
        ),
    )
    asyncio.run(coordinator.inspect_completed_task("task-1", (_event("chapter-1"),)))
    return logged


def test_a_chapter_the_model_never_saw_is_logged_with_the_cause():
    """Именно эта строка отсутствовала всю ночь."""
    language = LanguageQaResult(
        chapter_id="chapter-1",
        warnings=(
            "language_diagnosis_failed (RateLimitExceededError: "
            "Суточный лимит для ключа …aaaa исчерпан)",
        ),
        blocks_total=12,
        unchecked_blocks=12,
    )
    logged = _logged(
        lambda chapter_id: _deferred(
            chapter_id,
            warnings=("completeness_check_disabled", "language_check_incomplete"),
            language=language,
        )
    )

    assert len(logged) == 1
    message, title, details = logged[0]
    assert "chapter-1" in message
    assert "отложена" in message
    assert "Суточный лимит" in message
    assert "chapter-1" in title
    assert "не проверено 12 из 12" in details


def test_a_chapter_deferred_without_the_language_stage_still_names_a_reason():
    logged = _logged(
        lambda chapter_id: _deferred(chapter_id, warnings=("embeddings_unavailable",))
    )

    assert len(logged) == 1
    message = logged[0][0]
    assert "отложена" in message
    assert describe_deferral("embeddings_unavailable") in message


def test_the_reason_is_a_sentence_not_a_code():
    assert describe_deferral("language_check_incomplete") != "language_check_incomplete"
    assert describe_deferral("no_such_code") == "no_such_code"


def test_a_chapter_with_no_readable_request_is_logged_as_skipped():
    """Раньше такая глава исчезала без следа: ни в журнале, ни в логе."""
    logged = _logged(lambda chapter_id: None, builder=lambda event: None)

    assert len(logged) == 1
    assert "chapter-1" in logged[0][0]
    assert "пропущена" in logged[0][0]


def test_a_settled_chapter_still_says_nothing():
    """Чистая глава по-прежнему не засоряет лог."""
    logged = _logged(
        lambda chapter_id: ChapterQaResult(
            chapter_id=chapter_id,
            risk_level=RiskLevel.LOW,
            may_continue_translation=True,
            coverage_mode="semantic_alignment",
            warnings=("completeness_check_disabled",),
        )
    )

    assert logged == []


def test_no_embedding_alarm_when_the_user_switched_completeness_off():
    """«Смысловое сравнение недоступно» — не новость, когда его выключили сами."""
    messages: list[str] = []

    class _Service:
        async def check_chapter(self, request, options, cancellation):
            return _deferred(str(request), warnings=("completeness_check_disabled",))

    coordinator = ChapterQaCoordinator(
        service=_Service(),
        task_manager=None,
        request_builder=lambda event: event.chapter_id,
        log=lambda message, **kwargs: messages.append(message),
    )
    for index in range(LIMITED_MODE_ALERT_STREAK + 2):
        asyncio.run(coordinator._check_one(_event(f"chapter-{index}"), QaOptions()))

    assert not [m for m in messages if "Смысловое сравнение" in m]
