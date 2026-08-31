"""A chapter nobody could check must never read as a chapter with nothing wrong.

Measured on a live book: one 429 on the diagnosis request left 33 of 40 chapters
reporting «найдено 0», which is what a clean chapter reports.  Two behaviours
close that: a busy service is asked again, and what still could not be checked
says so out loud and comes back.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from gemini_translator.qa.language_validation import (
    LanguageQaRequest,
    LanguageQaResult,
    LanguageQualityPipeline,
    LanguageReviewError,
)
from gemini_translator.qa.llm import CancellationToken, QaModelSelection
from gemini_translator.qa.llm.language_reviewer import (
    RETRY_ATTEMPTS,
    is_transient,
    request_qa_json,
    retry_delay,
)
from gemini_translator.qa.models import RiskLevel
from gemini_translator.qa.service import DEFERRED_WARNINGS, ChapterQaResult
from gemini_translator.utils.epub_json import build_html_document_model


_CHAPTER_HTML = "<p>Он взял себе решение уйти.</p><p>Она дала ему знать о приезде.</p>"


class _Busy(Exception):
    """What a handler raises when the service is merely busy: it names a delay."""

    def __init__(self, delay_seconds: float = 30) -> None:
        super().__init__("Временный лимит запросов (429).")
        self.delay_seconds = delay_seconds


class _Refused(Exception):
    """What a handler raises when asking again cannot help."""


class _Client:
    def __init__(self, *outcomes: object) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    async def complete_json(self, prompt, *, model, max_output_tokens, cancellation, purpose=""):
        self.calls += 1
        outcome = self.outcomes[min(self.calls - 1, len(self.outcomes) - 1)]
        if isinstance(outcome, BaseException):
            raise outcome
        return deepcopy(outcome)


def _request(**overrides) -> LanguageQaRequest:
    values: dict[str, object] = {
        "chapter_id": "chapter-1",
        "document_model": build_html_document_model(_CHAPTER_HTML, document_id="chapter-1"),
        "source_language": "en",
        "target_language": "ru",
        "model": QaModelSelection("gemini", "qa-model"),
        "cancellation": CancellationToken(),
    }
    values.update(overrides)
    return LanguageQaRequest(**values)  # type: ignore[arg-type]


def _ask(client: _Client, request: LanguageQaRequest | None = None) -> tuple[object, list[float]]:
    """Run one request, recording the pauses instead of living through them."""
    pauses: list[float] = []

    async def sleep(seconds: float) -> None:
        pauses.append(seconds)

    payload = asyncio.run(
        request_qa_json(
            client, "prompt", request or _request(), 1024, "language_diagnosis", sleep=sleep
        )
    )
    return payload, pauses


# --- asking again ------------------------------------------------------------


def test_a_busy_service_is_asked_again():
    """429 — это «спроси позже», а не «в главе всё хорошо»."""
    client = _Client(_Busy(), _Busy(), {"issues": []})

    payload, pauses = _ask(client)

    assert payload == {"issues": []}
    assert client.calls == 3
    assert len(pauses) == 2


def test_a_service_that_stays_busy_ends_as_one_honest_refusal():
    """Попытки не бесконечны: отказ должен наступить и быть назван."""
    client = _Client(_Busy())

    with pytest.raises(LanguageReviewError) as error:
        _ask(client)

    assert error.value.reason == "language_diagnosis_failed"
    assert client.calls == RETRY_ATTEMPTS


def test_a_refusal_that_asking_again_cannot_fix_is_not_repeated():
    """Исчерпанная квота или запрещённый промпт от повтора не починятся."""
    client = _Client(_Refused("quota"))

    with pytest.raises(LanguageReviewError):
        _ask(client)

    assert client.calls == 1


def test_a_timeout_is_retried_and_keeps_its_own_reason():
    """Медленный ответ — тоже временный сбой, но в логе он остаётся таймаутом."""
    client = _Client(TimeoutError("slow"))

    with pytest.raises(LanguageReviewError) as error:
        _ask(client)

    assert error.value.reason == "language_diagnosis_timeout"
    assert client.calls == RETRY_ATTEMPTS


def test_only_an_error_that_names_a_delay_is_worth_repeating():
    """Обработчики говорят «попробуй позже» именно так — задержкой в самой ошибке."""
    assert is_transient(_Busy()) is True
    assert is_transient(TimeoutError()) is True
    assert is_transient(_Refused()) is False
    assert is_transient(_Busy(delay_seconds=0)) is False
    assert is_transient(_Busy(delay_seconds=True)) is False


def test_the_pauses_grow_and_then_stop_growing():
    """Пауза растёт, но проверка главы не должна превращаться в ожидание."""
    delays = [retry_delay(attempt) for attempt in range(8)]

    assert delays == sorted(delays)
    assert delays[0] < delays[1]
    assert max(delays) <= 20.0


def test_cancelling_during_the_pause_stops_the_check():
    """Отменённая проверка не имеет права досыпать свои паузы."""
    cancellation = CancellationToken()
    client = _Client(_Busy())

    async def sleep(seconds: float) -> None:  # pragma: no cover - never reached
        raise AssertionError("отменённая проверка не должна спать")

    async def run() -> None:
        cancellation.cancel()
        await request_qa_json(
            client,
            "prompt",
            _request(cancellation=cancellation),
            1024,
            "language_diagnosis",
            sleep=sleep,
        )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run())


# --- saying what was never checked -------------------------------------------


def _run(client: _Client) -> LanguageQaResult:
    return asyncio.run(LanguageQualityPipeline(client).check_chapter(_request()))


def test_a_chapter_the_model_never_saw_says_so():
    """Ради этого всё и делалось: «не проверено» отличимо от «дефектов нет»."""
    result = _run(_Client(_Refused("quota")))

    assert result.issues == ()
    assert result.blocks_total == 2
    assert result.unchecked_blocks == 2
    assert result.fully_checked is False
    warning = result.warnings[0]
    assert warning.startswith("language_diagnosis_failed")
    # The cause is the whole point: an exhausted key, a busy service and a dead
    # proxy all read as «сбой запроса» and need different answers.
    assert "quota" in warning


def test_a_chapter_that_was_checked_and_is_clean_says_that_instead():
    """Пустой результат проверенной главы — это ноль непроверенных абзацев."""
    result = _run(_Client({"issues": []}))

    assert result.issues == ()
    assert result.blocks_total == 2
    assert result.unchecked_blocks == 0
    assert result.fully_checked is True


def test_the_log_shows_how_much_of_the_chapter_was_missed():
    """Число в логе — единственное, что отличает эти два случая для человека."""
    result = ChapterQaResult(
        chapter_id="chapter-1",
        risk_level=RiskLevel.MEDIUM,
        may_continue_translation=True,
        coverage_mode="semantic_alignment",
        language=LanguageQaResult(
            chapter_id="chapter-1",
            warnings=("language_diagnosis_failed",),
            blocks_total=40,
            unchecked_blocks=12,
        ),
    )

    text = result.change_details()
    html = result.change_details_html()

    # The line names its own stage: someone who enabled only the typo check
    # must not read this as a paragraph check they never asked for.
    assert "ЯЗЫКОВАЯ ПРОВЕРКА НЕ ПРОШЛА: не проверено 12 из 40 абзацев" in text
    assert "глава вернётся на проверку" in text
    assert "ЯЗЫКОВАЯ ПРОВЕРКА НЕ ПРОШЛА: не проверено 12 из 40 абзацев" in html


def test_an_incompletely_checked_chapter_comes_back():
    """Непроверенная глава должна вернуться в очередь, а не считаться закрытой."""
    from gemini_translator.qa.service import _chapter_status

    assert "language_check_incomplete" in DEFERRED_WARNINGS

    result = ChapterQaResult(
        chapter_id="chapter-1",
        risk_level=RiskLevel.MEDIUM,
        may_continue_translation=True,
        coverage_mode="semantic_alignment",
        warnings=("language_check_incomplete",),
    )

    assert _chapter_status(result) == "deferred"


def test_an_old_result_without_the_new_numbers_still_renders():
    """Записанный раньше результат не должен ломать окно изменений."""
    result = ChapterQaResult(
        chapter_id="chapter-1",
        risk_level=RiskLevel.LOW,
        may_continue_translation=True,
        coverage_mode="semantic_alignment",
        language=LanguageQaResult(chapter_id="chapter-1"),
    )

    assert "НЕ ПРОВЕРЕНО" not in result.change_details()
    assert "НЕ ПРОВЕРЕНО" not in result.change_details_html()
