"""Behavioral contracts for local embedding-provider validation and fallback."""

import asyncio
from dataclasses import FrozenInstanceError
from pathlib import Path

import numpy as np
import pytest

from gemini_translator.qa.embeddings import (
    EmbeddingBatch,
    EmbeddingContractError,
    EmbeddingRequest,
    EmbeddingUnavailableError,
    FallbackEmbeddingProvider,
    validate_and_normalize_batch,
)
from gemini_translator.qa.embeddings import base as embedding_base
from gemini_translator.qa.embeddings import factory as embedding_factory


def _request(*texts: str, model: str = "embedding-v1", dimensions: int | None = None):
    return EmbeddingRequest(
        texts=texts,
        language="ru-RU",
        model=model,
        dimensions=dimensions,
    )


def _batch(
    vectors: np.ndarray,
    *,
    provider: str = "fake",
    model: str = "embedding-v1",
    dimensions: int | None = None,
) -> EmbeddingBatch:
    width = int(vectors.shape[1]) if dimensions is None and vectors.ndim == 2 else dimensions
    return EmbeddingBatch(vectors=vectors, provider=provider, model=model, dimensions=width)


class _Provider:
    def __init__(self, name: str, outcome):
        self.name = name
        self._outcome = outcome
        self.calls = 0

    async def embed(self, request: EmbeddingRequest) -> EmbeddingBatch:
        self.calls += 1
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        if callable(self._outcome):
            return self._outcome(request)
        return self._outcome


def test_request_normalizes_language_and_rejects_empty_or_invalid_inputs():
    """Removing constructor checks would send invalid network embedding requests."""
    request = _request("один", "два", dimensions=4)

    assert request.language == "ru"
    assert request.texts == ("один", "два")

    invalid_arguments = [
        {"texts": ()},
        {"texts": ["one"]},
        {"texts": ("",)},
        {"texts": ("   ",)},
        {"language": "-RU"},
        {"language": "  "},
        {"model": ""},
        {"dimensions": 0},
        {"dimensions": -1},
        {"dimensions": True},
        {"dimensions": 1.5},
        {"task_type": "  "},
    ]

    for overrides in invalid_arguments:
        values = {
            "texts": ("one",),
            "language": "en-US",
            "model": "embedding-v1",
            "task_type": "semantic-similarity",
        }
        values.update(overrides)
        with pytest.raises(EmbeddingContractError):
            EmbeddingRequest(**values)


def test_batch_requires_explicit_ndarray_and_valid_metadata():
    """Coercing provider data in the batch constructor hides malformed adapter output."""
    vectors = np.ones((1, 2), dtype=np.float64)
    batch = _batch(vectors)

    assert batch.vectors is vectors
    with pytest.raises(EmbeddingContractError):
        EmbeddingBatch(vectors=[[1.0, 0.0]], provider="fake", model="m", dimensions=2)

    for field, value in (("provider", " "), ("model", ""), ("dimensions", 0), ("dimensions", True)):
        values = dict(vectors=vectors, provider="fake", model="m", dimensions=2)
        values[field] = value
        with pytest.raises(EmbeddingContractError):
            EmbeddingBatch(**values)


@pytest.mark.parametrize(
    ("vectors", "dimensions"),
    [
        (np.array([1.0, 0.0]), 2),
        (np.empty((0, 2)), 2),
        (np.empty((1, 0)), 1),
        (np.ones((2, 3)), 2),
        (np.array([[1, 2], [3]], dtype=object), 2),
    ],
)
def test_batch_rejects_malformed_matrix_shape_before_validation(vectors, dimensions):
    """Accepting malformed matrices defers adapter bugs past the batch boundary."""
    with pytest.raises(EmbeddingContractError):
        EmbeddingBatch(vectors=vectors, provider="fake", model="m", dimensions=dimensions)


def test_validation_returns_immutable_normalized_float32_copy_without_mutating_input():
    """Returning a view or skipping normalization would leak mutable, non-unit vectors."""
    provider_vectors = np.asfortranarray(np.array([[3.0, 4.0], [0.0, 2.0]], dtype=np.float64))
    before = provider_vectors.copy()
    batch = _batch(provider_vectors)

    checked = validate_and_normalize_batch(batch, expected_rows=2)

    assert checked is not batch
    assert checked.vectors.dtype == np.float32
    assert checked.vectors.flags.c_contiguous
    assert not checked.vectors.flags.writeable
    np.testing.assert_allclose(checked.vectors, [[0.6, 0.8], [0.0, 1.0]], atol=1e-6)
    np.testing.assert_allclose(np.linalg.norm(checked.vectors, axis=1), 1.0, atol=1e-6)
    np.testing.assert_array_equal(provider_vectors, before)
    with pytest.raises(ValueError):
        checked.vectors[0, 0] = 0.0
    with pytest.raises(FrozenInstanceError):
        checked.provider = "other"


@pytest.mark.parametrize(
    "vectors",
    [
        np.ones((1, 2)),
        np.array([[np.nan, 0.0], [1.0, 0.0]]),
        np.zeros((2, 2)),
        np.array([[True, False], [False, True]]),
        np.array([[1 + 0j, 0j], [0j, 1 + 0j]]),
        np.array([["3.0", "4.0"], ["0.0", "2.0"]], dtype="U"),
        np.array([[np.finfo(np.float64).max, np.finfo(np.float64).max]], dtype=np.float64),
    ],
)
def test_validation_rejects_invalid_batches_and_accepts_coercible_numeric_arrays(vectors):
    """Weak matrix validation would admit partial, unsafe, or unnormalizable vectors."""
    batch = _batch(vectors, dimensions=2)

    if vectors.dtype.kind == "U":
        checked = validate_and_normalize_batch(batch, expected_rows=2)
        assert checked.vectors.dtype == np.float32
        return

    with pytest.raises(EmbeddingContractError):
        validate_and_normalize_batch(batch, expected_rows=2)


@pytest.mark.parametrize("expected_rows", [-1, 0, True, 1.5])
def test_validation_rejects_invalid_expected_row_count(expected_rows):
    """Accepting invalid row expectations weakens the adapter output boundary."""
    with pytest.raises(EmbeddingContractError):
        validate_and_normalize_batch(_batch(np.ones((1, 2))), expected_rows=expected_rows)


def test_fallback_calls_providers_once_in_order_and_returns_first_valid_batch():
    """Reordering or retrying providers changes latency and the configured failover policy."""
    calls: list[str] = []

    def valid(name):
        def outcome(request):
            calls.append(name)
            return _batch(
                np.array([[3.0, 4.0], [0.0, 2.0]]), provider=name, dimensions=2
            )

        return outcome

    first = _Provider("first", valid("first"))
    second = _Provider("second", valid("second"))
    provider = FallbackEmbeddingProvider((first, second))

    result = asyncio.run(provider.embed(_request("one", "two", dimensions=2)))

    assert result.provider == "first"
    assert calls == ["first"]
    assert first.calls == 1
    assert second.calls == 0
    assert not result.vectors.flags.writeable


def test_fallback_skips_invalid_batches_and_model_or_dimension_mismatches():
    """Trusting malformed metadata would align vectors from a different embedding space."""
    request = _request("one", "two", dimensions=2)
    invalid = _Provider("invalid", _batch(np.ones((1, 2)), provider="invalid", dimensions=2))
    wrong_model = _Provider(
        "wrong_model", _batch(np.ones((2, 2)), provider="wrong_model", model="other", dimensions=2)
    )
    wrong_dimensions = _Provider(
        "wrong_dimensions", _batch(np.ones((2, 3)), provider="wrong_dimensions", dimensions=3)
    )
    valid = _Provider(
        "valid", _batch(np.array([[1.0, 0.0], [0.0, 1.0]]), provider="valid", dimensions=2)
    )

    result = asyncio.run(
        FallbackEmbeddingProvider((invalid, wrong_model, wrong_dimensions, valid)).embed(request)
    )

    assert result.provider == "valid"
    assert [invalid.calls, wrong_model.calls, wrong_dimensions.calls, valid.calls] == [1, 1, 1, 1]


def test_fallback_records_sanitized_immutable_attempts_when_all_providers_fail():
    """Exposing provider reprs can leak API keys when degraded mode is reported."""
    secret = "api-key=do-not-disclose"
    error = RuntimeError(secret)
    invalid = _Provider("invalid", _batch(np.zeros((1, 2)), dimensions=2))
    unavailable = _Provider("unavailable", error)
    malformed = _Provider("malformed", EmbeddingContractError(secret))

    with pytest.raises(EmbeddingUnavailableError) as captured:
        asyncio.run(
            FallbackEmbeddingProvider((invalid, unavailable, malformed)).embed(
                _request("one", dimensions=2)
            )
        )

    attempts = captured.value.attempts
    assert [(attempt.provider, attempt.error_type) for attempt in attempts] == [
        ("invalid", "EmbeddingContractError"),
        ("unavailable", "RuntimeError"),
        ("malformed", "EmbeddingContractError"),
    ]
    assert all(secret not in attempt.message for attempt in attempts)
    with pytest.raises(FrozenInstanceError):
        attempts[0].provider = "changed"
    assert invalid.calls == unavailable.calls == malformed.calls == 1


def test_unavailable_error_defensively_sanitizes_and_freezes_attempt_records():
    """Keeping caller-provided attempt strings would expose secrets in degraded-mode reporting."""
    secret = "api-key=SECRET"
    error = EmbeddingUnavailableError(
        [
            embedding_factory.EmbeddingAttempt(
                provider=secret,
                error_type=secret,
                message=secret,
            )
        ]
    )

    assert isinstance(error.attempts, tuple)
    assert secret not in repr(error)
    assert secret not in repr(error.attempts)
    assert secret not in str(error)
    with pytest.raises((AttributeError, FrozenInstanceError)):
        error.attempts = ()


def test_fallback_propagates_cancellation_without_trying_the_next_provider():
    """Swallowing cancellation prevents callers from stopping quality analysis promptly."""
    cancelled = _Provider("cancelled", asyncio.CancelledError())
    later = _Provider("later", _batch(np.ones((1, 2)), dimensions=2))

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(FallbackEmbeddingProvider((cancelled, later)).embed(_request("one", dimensions=2)))

    assert cancelled.calls == 1
    assert later.calls == 0


@pytest.mark.parametrize("interruption", [KeyboardInterrupt(), SystemExit(2)])
def test_fallback_propagates_process_interrupts_without_trying_the_next_provider(interruption):
    """Catching process interrupts would prevent a caller from stopping the application."""
    interrupted = _Provider("interrupted", interruption)
    later = _Provider("later", _batch(np.ones((1, 2)), dimensions=2))

    with pytest.raises(type(interruption)):
        asyncio.run(FallbackEmbeddingProvider((interrupted, later)).embed(_request("one", dimensions=2)))

    assert interrupted.calls == 1
    assert later.calls == 0


def test_fallback_requires_unique_named_providers():
    """Ambiguous provider identities make failure reports and fallback selection unreliable."""
    provider = _Provider("same", _batch(np.ones((1, 2)), dimensions=2))

    for providers in ((), (provider, provider), (_Provider(" ", provider._outcome),)):
        with pytest.raises(EmbeddingContractError):
            FallbackEmbeddingProvider(providers)


def test_fallback_accepts_canonical_provider_names_and_rejects_secret_bearing_names():
    """Provider names must be safe report identifiers, not URLs, keys, or free-form labels."""
    gemini = _Provider("gemini", _batch(np.ones((1, 2)), dimensions=2))
    openai = _Provider("openai_compatible", _batch(np.ones((1, 2)), dimensions=2))

    FallbackEmbeddingProvider((gemini, openai))
    with pytest.raises(EmbeddingContractError):
        FallbackEmbeddingProvider((gemini, _Provider("gemini", openai._outcome)))

    for unsafe_name in (
        "api-key=SECRET",
        "https://embeddings.example.test/v1?key=SECRET",
        "openai_compatible?token=SECRET",
    ):
        with pytest.raises(EmbeddingContractError) as captured:
            FallbackEmbeddingProvider((_Provider(unsafe_name, gemini._outcome),))

        assert unsafe_name not in str(captured.value)
        assert unsafe_name not in repr(captured.value)


def test_fallback_attempts_keep_the_constructor_validated_provider_identity():
    """Reading a mutable provider name during failure handling could leak a later secret value."""
    secret = "https://embeddings.example.test/v1?key=SECRET"
    provider = _Provider("gemini", RuntimeError("failed"))
    fallback = FallbackEmbeddingProvider((provider,))
    provider.name = secret

    with pytest.raises(EmbeddingUnavailableError) as captured:
        asyncio.run(fallback.embed(_request("one", dimensions=2)))

    assert captured.value.attempts[0].provider == "gemini"
    assert secret not in str(captured.value)
    assert secret not in repr(captured.value)
    assert secret not in repr(captured.value.attempts)


def test_embedding_contract_boundary_has_no_qt_or_translation_engine_imports():
    """A UI or translation-engine dependency would make offline QA contracts unsafe to import."""
    source = "\n".join(
        Path(module.__file__).read_text(encoding="utf-8")
        for module in (embedding_base, embedding_factory)
    )

    assert "PyQt" not in source
    assert "TranslationEngine" not in source
    assert "UniversalWorker" not in source
