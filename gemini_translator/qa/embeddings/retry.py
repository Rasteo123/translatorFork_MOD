"""Bounded retries for the online embedding adapters.

An embedding provider that gives up on the first 429 turns every busy minute
into a chapter checked in limited mode. Retrying is therefore part of the
contract — but strictly bounded, and only for failures the service itself
called retryable.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from .factory import EmbeddingHttpError, EmbeddingTransportError


DEFAULT_ATTEMPTS = 4
DEFAULT_BASE_DELAY_SECONDS = 1.5
MAX_DELAY_SECONDS = 20.0


def exponential_delay(attempt: int, base_delay: float) -> float:
    """Grow the pause with each attempt, up to a fixed ceiling."""
    return min(base_delay * (2**attempt), MAX_DELAY_SECONDS)


async def with_retries(
    operation: Callable[[], Awaitable[object]],
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY_SECONDS,
    sleep=asyncio.sleep,
    delay_for: Callable[[int, float], float] | None = None,
) -> object:
    """Run one request again after a retryable failure, with growing pauses.

    ``delay_for`` lets a caller that has somewhere else to try — another key,
    say — move on immediately before it starts waiting.
    """

    total = max(1, int(attempts))
    delay_for = delay_for or exponential_delay
    for attempt in range(total):
        try:
            return await operation()
        except asyncio.CancelledError:
            raise
        except EmbeddingHttpError as error:
            if not error.retryable or attempt == total - 1:
                raise
        except EmbeddingTransportError:
            if attempt == total - 1:
                raise
        await sleep(max(0.0, delay_for(attempt, base_delay)))
    raise AssertionError("unreachable")  # pragma: no cover - loop always returns
