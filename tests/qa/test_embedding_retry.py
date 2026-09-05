"""A retryable rate limit must cost a pause, not a chapter checked blind."""

from __future__ import annotations

import asyncio

import pytest

from gemini_translator.qa.embeddings.base import EmbeddingRequest
from gemini_translator.qa.embeddings.factory import (
    EmbeddingHttpError,
    EmbeddingResponseError,
    EmbeddingTransportError,
)
from gemini_translator.qa.embeddings.gemini import GeminiEmbeddingProvider
from gemini_translator.qa.embeddings.retry import with_retries


class _Response:
    def __init__(self, status=200, payload=None):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def json(self):
        return self._payload


class _Session:
    def __init__(self, statuses) -> None:
        self.statuses = list(statuses)
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    def post(self, url, *, headers, json, timeout):
        self.calls += 1
        status = self.statuses.pop(0) if self.statuses else 200
        if status == 200:
            return _Response(
                200,
                {"embeddings": [{"values": [1.0, 0.0]} for _ in json["requests"]]},
            )
        return _Response(status, {})


def _request(*texts):
    return EmbeddingRequest(
        texts=tuple(texts),
        language="ru",
        model="gemini-embedding-001",
        dimensions=2,
        task_type="semantic-similarity",
    )


async def _no_sleep(_delay: float) -> None:
    return None


def _provider(session, attempts=4):
    now = [1000.0]

    async def sleep(delay):
        now[0] += delay

    return GeminiEmbeddingProvider(
        "key",
        lambda: session,
        30,
        retry_attempts=attempts,
        retry_sleep=sleep,
        clock=lambda: now[0],
    )


def test_a_rate_limit_is_retried_and_then_succeeds():
    """A busy minute must not drop the chapter into limited mode."""
    session = _Session([429, 429, 200])

    batch = asyncio.run(_provider(session).embed(_request("первое", "второе")))

    assert session.calls == 3
    assert batch.vectors.shape == (2, 2)


def test_retries_are_bounded():
    """An endpoint that is down must fail in a bounded time, not forever."""
    session = _Session([429] * 10)

    with pytest.raises(EmbeddingHttpError):
        asyncio.run(_provider(session, attempts=3).embed(_request("текст")))

    assert session.calls == 3


def test_a_permanent_failure_is_never_retried():
    """A wrong key is not going to become right by asking again."""
    session = _Session([400, 200])

    with pytest.raises(EmbeddingHttpError):
        asyncio.run(_provider(session).embed(_request("текст")))

    assert session.calls == 1


def test_backoff_grows_and_stays_capped():
    """Retrying instantly would only deepen the rate limit."""
    delays: list[float] = []

    async def failing():
        raise EmbeddingHttpError(429, "gemini", True)

    async def record(delay):
        delays.append(delay)

    with pytest.raises(EmbeddingHttpError):
        asyncio.run(
            with_retries(failing, attempts=6, base_delay=1.5, sleep=record)
        )

    assert delays == sorted(delays)
    assert delays[0] < delays[-1]
    assert max(delays) <= 20.0


def test_an_invalid_answer_is_not_a_retry():
    """A malformed response is a contract failure, not a transient one."""
    calls = {"count": 0}

    async def invalid():
        calls["count"] += 1
        raise EmbeddingResponseError("gemini")

    with pytest.raises(EmbeddingResponseError):
        asyncio.run(with_retries(invalid, attempts=4, sleep=_no_sleep))

    assert calls["count"] == 1


def test_a_transport_failure_is_retried():
    """A dropped connection in the middle of a book is worth one more try."""
    calls = {"count": 0}

    async def flaky():
        calls["count"] += 1
        if calls["count"] < 3:
            raise EmbeddingTransportError("gemini")
        return "ok"

    assert asyncio.run(with_retries(flaky, attempts=4, sleep=_no_sleep)) == "ok"
    assert calls["count"] == 3
