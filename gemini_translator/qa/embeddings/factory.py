"""Sequential, adapter-agnostic embedding provider fallback."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import re
from typing import Iterable

from .base import (
    EmbeddingBatch,
    EmbeddingContractError,
    EmbeddingProvider,
    EmbeddingRequest,
    validate_and_normalize_batch,
)


_SAFE_PROVIDER_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_SAFE_ERROR_TYPES = frozenset(
    {
        "EmbeddingContractError",
        "RuntimeError",
        "ValueError",
        "TypeError",
        "TimeoutError",
        "ConnectionError",
        "OSError",
    }
)


def _public_provider_name(value: object) -> str:
    if isinstance(value, str) and _SAFE_PROVIDER_NAME.fullmatch(value):
        return value
    return "unknown"


def _provider_name_or_error(value: object) -> str:
    name = _public_provider_name(value)
    if name == "unknown":
        raise EmbeddingContractError("provider name must be a safe canonical identifier")
    return name


def _public_error_type(value: object) -> str:
    if isinstance(value, str) and value in _SAFE_ERROR_TYPES:
        return value
    return "EmbeddingProviderError"


def _public_message(error_type: str) -> str:
    if error_type == "EmbeddingContractError":
        return "embedding provider returned an invalid batch"
    return "embedding provider failed"


@dataclass(frozen=True, slots=True)
class EmbeddingAttempt:
    """A public, secret-safe record of one failed provider attempt."""

    provider: str
    error_type: str
    message: str

    def __post_init__(self) -> None:
        provider = _public_provider_name(self.provider)
        error_type = _public_error_type(self.error_type)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "error_type", error_type)
        object.__setattr__(self, "message", _public_message(error_type))


class EmbeddingUnavailableError(RuntimeError):
    """Raised after every configured provider has failed exactly once."""

    def __init__(self, attempts: Iterable[EmbeddingAttempt]) -> None:
        try:
            frozen_attempts = tuple(
                attempt
                if isinstance(attempt, EmbeddingAttempt)
                else EmbeddingAttempt("unknown", "EmbeddingProviderError", "")
                for attempt in attempts
            )
        except TypeError:
            frozen_attempts = ()
        self._attempts = frozen_attempts
        super().__init__("No configured embedding provider is available")

    @property
    def attempts(self) -> tuple[EmbeddingAttempt, ...]:
        """Return fixed public records without exposing provider exception details."""
        return self._attempts


class FallbackEmbeddingProvider:
    """Try each configured provider in order, with no retry policy of its own."""

    name = "fallback"

    def __init__(self, providers: tuple[EmbeddingProvider, ...]) -> None:
        if not isinstance(providers, tuple) or not providers:
            raise EmbeddingContractError("providers must be a nonempty tuple")

        names: list[str] = []
        for provider in providers:
            names.append(_provider_name_or_error(getattr(provider, "name", None)))
        if len(set(names)) != len(names):
            raise EmbeddingContractError("provider names must be unique")

        self.providers = providers
        self._provider_names = tuple(names)

    async def embed(self, request: EmbeddingRequest) -> EmbeddingBatch:
        """Return the first validated provider response in configuration order."""
        attempts: list[EmbeddingAttempt] = []
        for provider, provider_name in zip(self.providers, self._provider_names, strict=True):
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
                attempts.append(_sanitize_attempt(provider_name, error))

        raise EmbeddingUnavailableError(tuple(attempts))


def _sanitize_attempt(provider: str, error: Exception) -> EmbeddingAttempt:
    error_type = "EmbeddingContractError" if isinstance(error, EmbeddingContractError) else type(error).__name__
    return EmbeddingAttempt(
        provider=provider,
        error_type=error_type,
        message="",
    )
