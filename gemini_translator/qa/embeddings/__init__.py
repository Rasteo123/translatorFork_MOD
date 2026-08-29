"""Embedding contracts and safe sequential provider fallback for QA."""

from .base import (
    EmbeddingBatch,
    EmbeddingContractError,
    EmbeddingProvider,
    EmbeddingRequest,
    validate_and_normalize_batch,
)
from .cache import CachedEmbeddingProvider, EmbeddingCache, EmbeddingCacheKey
from .factory import (
    EmbeddingAttempt,
    EmbeddingHttpError,
    EmbeddingProviderConfig,
    EmbeddingResponseError,
    EmbeddingTransportError,
    EmbeddingUnavailableError,
    FallbackEmbeddingProvider,
    UnsupportedEmbeddingProvider,
    create_embedding_provider,
)

__all__ = (
    "EmbeddingAttempt",
    "EmbeddingBatch",
    "EmbeddingCache",
    "EmbeddingCacheKey",
    "EmbeddingContractError",
    "EmbeddingHttpError",
    "EmbeddingProvider",
    "EmbeddingProviderConfig",
    "EmbeddingRequest",
    "EmbeddingResponseError",
    "EmbeddingTransportError",
    "EmbeddingUnavailableError",
    "CachedEmbeddingProvider",
    "FallbackEmbeddingProvider",
    "UnsupportedEmbeddingProvider",
    "create_embedding_provider",
    "validate_and_normalize_batch",
)
