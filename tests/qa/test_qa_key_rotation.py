"""QA must spend the book's keys the way the workers do, not die on the first.

Measured on a live book: the check was pinned to the first session key, the
same one the first worker takes.  The model allows 20 requests a day per key;
the key was gone within a minute, every later request failed on the spot, and
110 chapters were quietly deferred while the translation went on.
"""

from __future__ import annotations

import asyncio

import pytest

from gemini_translator.api.errors import RateLimitExceededError, TemporaryRateLimitError
from gemini_translator.qa.handler_factory import (
    QaHandlerError,
    RotatingQaHandler,
    build_qa_handler_factory,
)
from gemini_translator.qa.key_pool import QaKeyPool
from gemini_translator.qa.llm import QaModelSelection


# --- the pool ----------------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _Settings:
    """The slice of the settings manager the pool consults."""

    def __init__(self, limited=()) -> None:
        self.limited = set(limited)
        self.exhausted: list[tuple[str, str]] = []

    def get_key_info(self, key):
        return {"key": key, "provider": "gemini"}

    def is_key_limit_active(self, key_info, model_id):
        return key_info["key"] in self.limited

    def mark_key_as_exhausted(self, key, model_id):
        self.exhausted.append((key, model_id))
        return True


def test_qa_takes_the_keys_from_the_end_where_the_workers_are_not():
    """Воркеры берут ключи с начала списка; проверка должна начинать с конца."""
    pool = QaKeyPool(["a", "b", "c"])

    assert pool.acquire() == "c"


def test_a_key_is_kept_until_the_service_turns_it_away():
    """Как воркер перевода: один ключ, пока он работает. Смена — исключение."""
    pool = QaKeyPool(["a", "b", "c"])

    assert [pool.acquire() for _ in range(4)] == ["c", "c", "c", "c"]
    pool.mark_exhausted("c")
    assert [pool.acquire() for _ in range(2)] == ["b", "b"]


def test_an_exhausted_key_is_never_offered_again():
    pool = QaKeyPool(["a", "b"])

    pool.mark_exhausted("b")

    assert [pool.acquire() for _ in range(3)] == ["a", "a", "a"]
    assert pool.remaining == 1


def test_a_pool_with_nothing_left_says_so():
    pool = QaKeyPool(["a"])
    pool.mark_exhausted("a")

    assert pool.acquire() is None
    assert pool.seconds_until_available() is None


def test_a_paused_key_comes_back_when_its_time_is_up():
    """Пауза по 429 — на столько, на сколько попросил сервис, и ни секундой дольше."""
    clock = _Clock()
    pool = QaKeyPool(["a", "b"], clock=clock)
    pool.pause("b", 30)

    assert pool.acquire() == "a"
    pool.pause("a", 10)
    assert pool.acquire() is None
    assert pool.seconds_until_available() == pytest.approx(10)

    clock.now += 10
    assert pool.acquire() == "a"
    clock.now += 20
    # "b" is back, but "a" works and a working key is kept.
    assert pool.acquire() == "a"
    pool.pause("a", 5)
    assert pool.acquire() == "b"


def test_a_key_the_workers_already_burnt_today_is_skipped():
    """Что воркер отметил исчерпанным в настройках, проверка не трогает."""
    settings = _Settings(limited={"b"})
    pool = QaKeyPool(["a", "b"], model_id="m", settings_manager=settings)

    assert [pool.acquire() for _ in range(2)] == ["a", "a"]


def test_an_exhausted_key_is_reported_to_the_settings_for_the_workers():
    settings = _Settings()
    pool = QaKeyPool(["a", "b"], model_id="gemini-3", settings_manager=settings)

    pool.mark_exhausted("b")

    assert settings.exhausted == [("b", "gemini-3")]


def test_keys_a_worker_is_using_right_now_are_the_last_resort():
    """Общий ключ с воркером делит с ним 5 запросов в минуту; лучше взять свободный."""
    busy = {"c"}
    pool = QaKeyPool(["a", "b", "c"], busy=lambda key: key in busy)

    assert pool.acquire() == "b"
    pool.mark_exhausted("b")
    assert pool.acquire() == "a"
    pool.mark_exhausted("a")
    assert pool.acquire() == "c"


def test_blank_and_duplicate_keys_are_dropped():
    pool = QaKeyPool(["a", " ", "a", "", "b"])

    assert len(pool) == 2


# --- the rotating handler ----------------------------------------------------


class _Handler:
    def __init__(self, key: str, outcome: object) -> None:
        self.key = key
        self.outcome = outcome
        self.closed = False
        self.calls = 0

    async def execute_api_call(self, prompt, log_prefix, **kwargs):
        self.calls += 1
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome

    async def _close_thread_session_internal(self) -> None:
        self.closed = True


def _rotating(keys, outcomes, *, sleep=None, clock=None, log=None, **options):
    """A handler over ``keys`` whose real handlers answer per ``outcomes[key]``."""
    pool = QaKeyPool(keys, clock=clock) if clock else QaKeyPool(keys)
    made: list[_Handler] = []

    def make_handler(key: str):
        handler = _Handler(key, outcomes[key])
        made.append(handler)
        return handler

    async def no_sleep(seconds: float) -> None:
        return None

    handler = RotatingQaHandler(
        pool, make_handler, log=log, sleep=sleep or no_sleep, **options
    )
    return handler, made, pool


def _ask(handler) -> object:
    return asyncio.run(handler.execute_api_call("prompt", "[QA]"))


def test_a_request_uses_one_key_and_closes_its_session_afterwards():
    """877 «Unclosed client session» за ночь — это по одной на каждый запрос."""
    handler, made, _ = _rotating(["a"], {"a": '{"issues": []}'})

    assert _ask(handler) == '{"issues": []}'
    assert [item.key for item in made] == ["a"]
    assert made[0].closed is True


def test_an_exhausted_key_is_replaced_within_the_same_request():
    """Суточный лимит одного ключа не должен стоить главе проверки."""
    messages: list[str] = []
    exhausted = RateLimitExceededError("Суточный лимит для ключа …aaaa исчерпан")
    handler, made, pool = _rotating(
        ["a", "b"], {"b": exhausted, "a": '{"issues": []}'}, log=messages.append
    )

    assert _ask(handler) == '{"issues": []}'
    assert [item.key for item in made] == ["b", "a"]
    assert all(item.closed for item in made)
    assert pool.remaining == 1
    assert any("исчерпан" in message for message in messages)


def test_a_busy_key_is_asked_again_and_only_a_second_refusal_rests_it():
    """Как у воркера: первое 429 — ждём на том же ключе, второе — ключ отдыхает."""
    clock = _Clock()
    pauses: list[float] = []

    async def sleep(seconds: float) -> None:
        pauses.append(seconds)
        clock.now += seconds

    busy = TemporaryRateLimitError("API запросил паузу", delay_seconds=42)
    handler, made, pool = _rotating(
        ["a", "b"], {"b": busy, "a": '{"issues": []}'}, clock=clock, sleep=sleep
    )

    assert _ask(handler) == '{"issues": []}'
    assert [item.key for item in made] == ["b", "b", "a"]
    assert pauses == [pytest.approx(42)]
    assert pool.acquire() == "a"
    pool.mark_exhausted("a")
    assert pool.acquire() is None
    clock.now += 42
    assert pool.acquire() == "b"


def test_when_every_key_is_paused_the_request_waits_for_the_first_to_return():
    """Ждать лучше, чем отказывать: пауза короткая, а глава — навсегда."""
    clock = _Clock()
    pauses: list[float] = []
    answers = [TemporaryRateLimitError("busy", delay_seconds=7), '{"issues": []}']

    class _Flaky(_Handler):
        async def execute_api_call(self, prompt, log_prefix, **kwargs):
            outcome = answers.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

    pool = QaKeyPool(["a"], clock=clock)

    async def sleep(seconds: float) -> None:
        pauses.append(seconds)
        clock.now += seconds

    handler = RotatingQaHandler(pool, lambda key: _Flaky(key, None), sleep=sleep)

    assert _ask(handler) == '{"issues": []}'
    assert pauses == [pytest.approx(7)]


def test_with_no_key_left_the_refusal_names_the_last_cause():
    """Отказ должен быть понятным и окончательным: ретраи тут не помогут."""
    exhausted = RateLimitExceededError("Суточный лимит для ключа …bbbb исчерпан")
    handler, made, _ = _rotating(["a", "b"], {"a": exhausted, "b": exhausted})

    with pytest.raises(QaHandlerError) as error:
        _ask(handler)

    assert "Суточный лимит" in str(error.value)
    assert not hasattr(error.value, "delay_seconds")
    assert all(item.closed for item in made)


def test_waiting_has_a_ceiling():
    """Ключ, поставленный на паузу на час, не должен держать проверку час."""
    clock = _Clock()
    pauses: list[float] = []

    async def sleep(seconds: float) -> None:
        pauses.append(seconds)
        clock.now += seconds

    handler, made, _ = _rotating(
        ["a"],
        {"a": TemporaryRateLimitError("busy", delay_seconds=3600)},
        clock=clock,
        sleep=sleep,
        max_wait_seconds=60,
    )

    with pytest.raises(QaHandlerError):
        _ask(handler)

    assert sum(pauses) <= 60


def test_other_errors_pass_through_untouched_and_still_close_the_session():
    """Сеть, отказ модели, отмена — это дело того, кто спросил, не пула."""
    handler, made, pool = _rotating(["a"], {"a": ValueError("bad json")})

    with pytest.raises(ValueError):
        _ask(handler)

    assert made[0].closed is True
    assert pool.remaining == 1


# --- the storm ----------------------------------------------------------------
#
# Measured on a live book: when the service began answering 429 to everyone,
# the check hopped to the next key on every refusal and asked all 150 keys
# within two minutes, then again a minute later when their pauses ran out.
# Dozens of chapters were being checked at once, each hopping on its own.
# An hour later the service accounts behind the keys were disabled.


def test_when_several_keys_are_throttled_at_once_the_whole_pool_rests():
    """Три ключа подряд с 429 за минуту — лимит общий, перебор бесполезен и опасен."""
    clock = _Clock()
    pool = QaKeyPool(["a", "b", "c", "d", "e"], clock=clock)

    pool.pause("e", 60)
    pool.pause("d", 60)
    assert pool.acquire() == "c"
    pool.pause("c", 60)

    assert pool.acquire() is None
    assert pool.seconds_until_available() == pytest.approx(60)
    clock.now += 60
    assert pool.acquire() is not None


def test_the_pool_never_hands_out_more_than_its_minute_budget():
    """Потолок на все запросы проверки в минуту, сколько бы ключей ни было."""
    clock = _Clock()
    pool = QaKeyPool(["a", "b", "c", "d"], clock=clock, max_requests_per_minute=3)

    assert [pool.acquire() for _ in range(3)] == ["d", "d", "d"]
    assert pool.acquire() is None
    wait = pool.seconds_until_available()
    assert 0 < wait <= 60
    clock.now += wait
    assert pool.acquire() == "d"


def test_a_throttled_key_is_waited_on_not_hopped_away_from():
    """Перебрать все ключи за минуту — это и есть шторм, за который банят."""
    clock = _Clock()
    pauses: list[float] = []
    calls: dict[str, int] = {}

    class _Throttling(_Handler):
        async def execute_api_call(self, prompt, log_prefix, **kwargs):
            calls[self.key] = calls.get(self.key, 0) + 1
            if calls[self.key] == 1:
                raise TemporaryRateLimitError("busy", delay_seconds=30)
            return '{"issues": []}'

    async def sleep(seconds: float) -> None:
        pauses.append(seconds)
        clock.now += seconds

    pool = QaKeyPool(["a", "b", "c", "d"], clock=clock)
    handler = RotatingQaHandler(pool, lambda key: _Throttling(key, None), sleep=sleep)

    assert _ask(handler) == '{"issues": []}'
    assert calls == {"d": 2}
    assert pauses == [pytest.approx(30)]


# --- wiring into the factory -------------------------------------------------


class _FactoryHandler:
    created: list["_FactoryHandler"] = []

    def __init__(self, worker):
        self.worker = worker
        _FactoryHandler.created.append(self)

    def setup_client(self, client_override=None, proxy_settings=None):
        return True

    async def execute_api_call(self, prompt, log_prefix, **kwargs):
        if self.worker.api_key == "second":
            raise RateLimitExceededError("Суточный лимит")
        return '{"issues": []}'

    async def _close_thread_session_internal(self) -> None:
        return None


_PROVIDERS = {
    "gemini": {
        "handler_class": "GeminiHandler",
        "models": {"Gemini Flash": {"id": "gemini-flash-001"}},
    }
}


def test_the_factory_hands_every_request_a_rotating_handler(monkeypatch):
    from gemini_translator.api import config as api_config
    from gemini_translator.api import factory as api_factory

    monkeypatch.setattr(api_config, "api_providers_view", lambda: _PROVIDERS)
    monkeypatch.setattr(api_factory, "get_api_handler_class", lambda name: _FactoryHandler)
    _FactoryHandler.created.clear()

    factory = build_qa_handler_factory(
        settings_manager=object(),
        key_pool=QaKeyPool(["first", "second"]),
        session_settings={},
    )
    handler = factory(QaModelSelection("gemini", "Gemini Flash"))

    assert _ask(handler) == '{"issues": []}'
    assert [item.worker.api_key for item in _FactoryHandler.created] == ["second", "first"]


def test_the_factory_needs_either_a_pool_or_a_key_source():
    with pytest.raises(TypeError):
        build_qa_handler_factory(settings_manager=object())
