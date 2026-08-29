"""Embedding contracts and safe sequential provider fallback for QA."""

from .base import (
    EmbeddingBatch,
    EmbeddingContractError,
    EmbeddingProvider,
    EmbeddingRequest,
    validate_and_normalize_batch,
)
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
    "EmbeddingContractError",
    "EmbeddingHttpError",
    "EmbeddingProvider",
    "EmbeddingProviderConfig",
    "EmbeddingRequest",
    "EmbeddingResponseError",
    "EmbeddingTransportError",
    "EmbeddingUnavailableError",
    "FallbackEmbeddingProvider",
    "UnsupportedEmbeddingProvider",
    "create_embedding_provider",
    "validate_and_normalize_batch",
)
