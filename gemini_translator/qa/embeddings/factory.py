"""Sequential, adapter-agnostic embedding provider fallback."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .base import (
    EmbeddingBatch,
    EmbeddingContractError,
    EmbeddingProvider,
    EmbeddingRequest,
    validate_and_normalize_batch,
)


@dataclass(frozen=True, slots=True)
class EmbeddingAttempt:
    """A public, secret-safe record of one failed provider attempt."""

    provider: str
    error_type: str
    message: str


class EmbeddingUnavailableError(RuntimeError):
    """Raised after every configured provider has failed exactly once."""

    def __init__(self, attempts: tuple[EmbeddingAttempt, ...]) -> None:
        self.attempts = attempts
        super().__init__("No configured embedding provider is available")


class FallbackEmbeddingProvider:
    """Try each configured provider in order, with no retry policy of its own."""

    name = "fallback"

    def __init__(self, providers: tuple[EmbeddingProvider, ...]) -> None:
        if not isinstance(providers, tuple) or not providers:
            raise EmbeddingContractError("providers must be a nonempty tuple")

        names: list[str] = []
        for provider in providers:
            name = getattr(provider, "name", None)
            if not isinstance(name, str) or not name.strip():
                raise EmbeddingContractError("each provider must have a nonempty name")
            names.append(name.strip())
        if len(set(names)) != len(names):
            raise EmbeddingContractError("provider names must be unique")

        self.providers = providers

    async def embed(self, request: EmbeddingRequest) -> EmbeddingBatch:
        """Return the first validated provider response in configuration order."""
        attempts: list[EmbeddingAttempt] = []
        for provider in self.providers:
            try:
                batch = await provider.embed(request)
                checked = validate_and_normalize_batch(batch, expected_rows=len(request.texts))
                if checked.model != request.model:
                    raise EmbeddingContractError("embedding batch model does not match request")
                if request.dimensions is not None and checked.dimensions != request.dimensions:
                    raise EmbeddingContractError("embedding batch dimensions do not match request")
                return checked
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as error:
                attempts.append(_sanitize_attempt(provider.name, error))

        raise EmbeddingUnavailableError(tuple(attempts))


def _sanitize_attempt(provider: str, error: Exception) -> EmbeddingAttempt:
    if isinstance(error, EmbeddingContractError):
        message = "embedding provider returned an invalid batch"
    else:
        message = "embedding provider failed"
    return EmbeddingAttempt(
        provider=provider,
        error_type=type(error).__name__,
        message=message,
    )
