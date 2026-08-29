"""Embedding contracts and safe sequential provider fallback for QA."""

from .base import (
    EmbeddingBatch,
    EmbeddingContractError,
    EmbeddingProvider,
    EmbeddingRequest,
    validate_and_normalize_batch,
)
from .factory import EmbeddingAttempt, EmbeddingUnavailableError, FallbackEmbeddingProvider

__all__ = (
    "EmbeddingAttempt",
    "EmbeddingBatch",
    "EmbeddingContractError",
    "EmbeddingProvider",
    "EmbeddingRequest",
    "EmbeddingUnavailableError",
    "FallbackEmbeddingProvider",
    "validate_and_normalize_batch",
)
