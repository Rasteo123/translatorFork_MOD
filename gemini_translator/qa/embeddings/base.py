"""Qt-free contracts and validation for semantic embedding providers."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Protocol

import numpy as np


class EmbeddingContractError(ValueError):
    """Raised when an embedding request or provider response is invalid."""


def _nonempty_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EmbeddingContractError(f"{field_name} must be a nonempty string")
    return value.strip()


def _positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise EmbeddingContractError(f"{field_name} must be a positive integer")
    return value


def _positive_finite_timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EmbeddingContractError("timeout_seconds must be a positive finite number")
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0:
        raise EmbeddingContractError("timeout_seconds must be a positive finite number")
    return timeout


def _base_language(value: object) -> str:
    language = _nonempty_string(value, "language").replace("_", "-")
    base = language.split("-", 1)[0].casefold()
    if not base:
        raise EmbeddingContractError("language must have a nonempty base language")
    return base


@dataclass(frozen=True, slots=True)
class EmbeddingRequest:
    """One ordered embedding request for a single model and language."""

    texts: tuple[str, ...]
    language: str
    model: str
    dimensions: int | None = None
    task_type: str = "semantic-similarity"

    def __post_init__(self) -> None:
        if not isinstance(self.texts, tuple) or not self.texts:
            raise EmbeddingContractError("texts must be a nonempty tuple")
        for text in self.texts:
            _nonempty_string(text, "text")
        object.__setattr__(self, "language", _base_language(self.language))
        object.__setattr__(self, "model", _nonempty_string(self.model, "model"))
        object.__setattr__(self, "task_type", _nonempty_string(self.task_type, "task_type"))
        if self.dimensions is not None:
            _positive_int(self.dimensions, "dimensions")


@dataclass(frozen=True, slots=True)
class EmbeddingBatch:
    """Provider-owned raw embedding data before defensive normalization."""

    vectors: np.ndarray
    provider: str
    model: str
    dimensions: int

    def __post_init__(self) -> None:
        if not isinstance(self.vectors, np.ndarray):
            raise EmbeddingContractError("vectors must be an ndarray")
        object.__setattr__(self, "provider", _nonempty_string(self.provider, "provider"))
        object.__setattr__(self, "model", _nonempty_string(self.model, "model"))
        _positive_int(self.dimensions, "dimensions")
        if self.vectors.dtype.kind == "O":
            raise EmbeddingContractError("vectors must not use object dtype")
        if self.vectors.ndim != 2 or self.vectors.shape[0] <= 0 or self.vectors.shape[1] <= 0:
            raise EmbeddingContractError("vectors must be a nonempty two-dimensional matrix")
        if self.vectors.shape[1] != self.dimensions:
            raise EmbeddingContractError("vectors columns must match dimensions")


class EmbeddingProvider(Protocol):
    """Minimal async interface implemented by each embedding adapter."""

    name: str

    async def embed(self, request: EmbeddingRequest) -> EmbeddingBatch:
        """Embed each request text in order."""


def _invalid_batch(message: str) -> EmbeddingContractError:
    return EmbeddingContractError(message)


def validate_and_normalize_batch(batch: EmbeddingBatch, expected_rows: int) -> EmbeddingBatch:
    """Copy, validate, and L2-normalize a provider batch without mutating its array."""
    if not isinstance(batch, EmbeddingBatch):
        raise _invalid_batch("embedding batch must be an EmbeddingBatch")
    if isinstance(expected_rows, bool) or not isinstance(expected_rows, int) or expected_rows <= 0:
        raise _invalid_batch("expected_rows must be a positive integer")
    if not isinstance(batch.vectors, np.ndarray):
        raise _invalid_batch("embedding vectors must be an ndarray")
    if batch.vectors.dtype.kind in {"O", "b", "c"}:
        raise _invalid_batch("embedding vectors must be real numeric data")

    try:
        with np.errstate(all="ignore"):
            vectors = np.array(batch.vectors, dtype=np.float32, order="C", copy=True)
    except (TypeError, ValueError, OverflowError, FloatingPointError):
        raise _invalid_batch("embedding vectors cannot be converted to float32") from None

    if vectors.ndim != 2 or vectors.shape[0] != expected_rows or vectors.shape[1] <= 0:
        raise _invalid_batch("unexpected embedding batch shape")
    if batch.dimensions != vectors.shape[1]:
        raise _invalid_batch("embedding batch dimensions do not match its vectors")

    try:
        with np.errstate(all="ignore"):
            if not np.isfinite(vectors).all():
                raise _invalid_batch("embedding batch contains NaN or Inf")
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            if not np.isfinite(norms).all():
                raise _invalid_batch("embedding batch norms are not finite")
            if np.any(norms <= np.finfo(np.float32).eps):
                raise _invalid_batch("embedding batch contains a zero or near-zero vector")
            normalized = np.ascontiguousarray(vectors / norms, dtype=np.float32)
            normalized_norms = np.linalg.norm(normalized, axis=1)
    except (TypeError, ValueError, OverflowError, FloatingPointError):
        raise _invalid_batch("embedding batch cannot be normalized") from None

    if not np.isfinite(normalized).all() or not np.isfinite(normalized_norms).all():
        raise _invalid_batch("normalized embedding batch contains NaN or Inf")
    if not np.allclose(normalized_norms, 1.0, rtol=1e-5, atol=1e-5):
        raise _invalid_batch("normalized embedding batch does not have unit norms")

    normalized.setflags(write=False)
    return EmbeddingBatch(
        vectors=normalized,
        provider=batch.provider,
        model=batch.model,
        dimensions=batch.dimensions,
    )


class EmbeddingKeyHealth(Protocol):
    """Who decides whether a key may still be used for embeddings.

    Key limits are tracked per model, so exhausting an embedding quota must not
    take the same key out of translation, and the other way round.  Providers
    ask before using a key and report back only when the service itself says the
    quota is gone — a transient 429 is a reason to rotate, not to condemn.
    """

    def is_active(self, api_key: str) -> bool:
        raise NotImplementedError

    def mark_exhausted(self, api_key: str, reason: str = "") -> None:
        raise NotImplementedError
