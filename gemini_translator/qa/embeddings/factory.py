"""Sequential, adapter-agnostic embedding provider fallback."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import re
from typing import Iterable

from .base import (
    EmbeddingBatch,
    EmbeddingContractError,
    EmbeddingProvider,
    EmbeddingRequest,
    _positive_finite_timeout,
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


class EmbeddingHttpError(RuntimeError):
    """A secret-safe HTTP failure reported by an online embedding adapter."""

    def __init__(
        self, status: int, provider: str, retryable: bool, *,
        retry_after_seconds: float = 60.0, quota_exhausted: bool = False,
    ) -> None:
        self.status = status
        self.provider = _provider_name_or_error(provider)
        self.retryable = bool(retryable)
        self.retry_after_seconds = retry_after_seconds
        self.quota_exhausted = quota_exhausted
        super().__init__(f"embedding HTTP request failed (status={status}, retryable={self.retryable})")


class EmbeddingTransportError(RuntimeError):
    """A secret-safe retryable transport failure reported by an online adapter."""

    def __init__(self, provider: str) -> None:
        self.provider = _provider_name_or_error(provider)
        self.retryable = True
        super().__init__("embedding transport request failed")


class EmbeddingResponseError(RuntimeError):
    """A secret-safe malformed JSON or schema failure from an online adapter."""

    def __init__(self, provider: str) -> None:
        self.provider = _provider_name_or_error(provider)
        self.retryable = False
        super().__init__("embedding provider returned an invalid response")


class UnsupportedEmbeddingProvider(EmbeddingContractError):
    """Raised when a provider kind cannot be instantiated."""

    def __init__(self) -> None:
        super().__init__("unsupported embedding provider")


def _optional_config_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise EmbeddingContractError(f"{field_name} must be a nonempty string when configured")
    return value.strip()


@dataclass(frozen=True, slots=True)
class EmbeddingProviderConfig:
    """Explicit, translation-worker-independent online embedding configuration.

    ``session_factory`` is deliberately supplied to the factory rather than stored
    here.  An ``auto`` configuration uses the ordered concrete entries in
    ``providers``; no provider is inferred from translation-worker settings.
    """

    kind: str
    api_key: str = field(default="", repr=False)
    api_keys: tuple[str, ...] = field(default=(), repr=False)
    base_url: str | None = None
    model: str | None = None
    timeout_seconds: float = 30.0
    providers: tuple["EmbeddingProviderConfig", ...] = ()
    model_dir: str | None = None
    intra_op_threads: int = 2
    # Who may say a key is out of embedding quota.  Deliberately not persisted
    # and not part of the provider identity: it is a callback, not a setting.
    key_health: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or self.kind not in {
            "gemini",
            "openai_compatible",
            "local_onnx",
            "auto",
        }:
            raise UnsupportedEmbeddingProvider()
        if not isinstance(self.api_key, str):
            raise EmbeddingContractError("api_key must be a string")
        object.__setattr__(self, "api_key", self.api_key.strip())
        if not isinstance(self.api_keys, tuple) or not all(
            isinstance(key, str) for key in self.api_keys
        ):
            raise EmbeddingContractError("api_keys must be a tuple of strings")
        object.__setattr__(
            self,
            "api_keys",
            tuple(dict.fromkeys(key.strip() for key in self.api_keys if key.strip())),
        )
        object.__setattr__(self, "base_url", _optional_config_string(self.base_url, "base_url"))
        object.__setattr__(self, "model", _optional_config_string(self.model, "model"))
        object.__setattr__(self, "timeout_seconds", _positive_finite_timeout(self.timeout_seconds))
        if not isinstance(self.providers, tuple):
            raise EmbeddingContractError("providers must be a tuple")
        if any(not isinstance(provider, EmbeddingProviderConfig) for provider in self.providers):
            raise EmbeddingContractError("providers must contain embedding provider configurations")
        if any(provider.kind == "auto" for provider in self.providers):
            raise EmbeddingContractError("auto configurations cannot contain nested auto configurations")
        if any(provider.kind == "local_onnx" for provider in self.providers):
            # An automatic setup must never start loading a local model the user
            # did not choose; the fallback chain stays online.
            raise EmbeddingContractError("auto configurations are online only")
        if self.kind != "auto" and self.providers:
            raise EmbeddingContractError("only auto configurations can contain providers")
        if self.kind == "openai_compatible" and self.base_url is None:
            raise EmbeddingContractError("openai-compatible provider requires a base URL")
        object.__setattr__(
            self, "model_dir", _optional_config_string(self.model_dir, "model_dir")
        )
        if self.kind == "local_onnx" and self.model_dir is None:
            raise EmbeddingContractError("local provider requires a model directory")
        if (
            isinstance(self.intra_op_threads, bool)
            or not isinstance(self.intra_op_threads, int)
            or self.intra_op_threads < 1
        ):
            raise EmbeddingContractError("intra_op_threads must be a positive integer")
        if self.kind == "auto" and (self.api_key or self.base_url is not None or self.model is not None):
            raise EmbeddingContractError("auto provider configuration must use explicit subconfigurations")


def create_embedding_provider(
    config: EmbeddingProviderConfig,
    session_factory,
) -> EmbeddingProvider:
    """Instantiate an explicit online adapter without importing translation code."""
    if not isinstance(config, EmbeddingProviderConfig):
        raise EmbeddingContractError("config must be an EmbeddingProviderConfig")
    if not callable(session_factory):
        raise EmbeddingContractError("session_factory must be callable")

    if config.kind == "gemini":
        from .gemini import GeminiEmbeddingProvider

        return GeminiEmbeddingProvider(
            config.api_keys or config.api_key,
            session_factory,
            config.timeout_seconds,
            key_health=config.key_health,
        )
    if config.kind == "openai_compatible":
        from .openai_compatible import OpenAICompatibleEmbeddingProvider

        return OpenAICompatibleEmbeddingProvider(
            config.base_url or "", config.api_key, session_factory, config.timeout_seconds
        )
    if config.kind == "local_onnx":
        from .local_onnx import LocalOnnxEmbeddingProvider, load_local_runtime

        model_dir = config.model_dir or ""
        return LocalOnnxEmbeddingProvider(
            model_dir,
            lambda: load_local_runtime(
                model_dir, intra_op_threads=config.intra_op_threads
            ),
        )
    if config.kind == "auto":
        concrete = tuple(create_embedding_provider(item, session_factory) for item in config.providers)
        if not concrete:
            raise EmbeddingUnavailableError(())
        return FallbackEmbeddingProvider(concrete)
    raise UnsupportedEmbeddingProvider()


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
