"""Behavioral contract for an OpenAI-compatible embedding adapter."""

import asyncio
from dataclasses import dataclass
from dataclasses import FrozenInstanceError

import numpy as np
import pytest

from gemini_translator.qa.embeddings import EmbeddingRequest, EmbeddingUnavailableError
from gemini_translator.qa.embeddings.factory import (
    EmbeddingHttpError,
    EmbeddingProviderConfig,
    EmbeddingResponseError,
    EmbeddingTransportError,
    create_embedding_provider,
)
from gemini_translator.qa.embeddings.openai_compatible import OpenAICompatibleEmbeddingProvider


async def _no_sleep(_delay: float) -> None:
    """Retries are exercised without spending the wall clock on them."""
    return None


def OpenAICompatibleEmbeddingProvider_fast(*args, **kwargs):
    """Build the adapter with the retry policy tests want to control."""
    kwargs.setdefault("retry_sleep", _no_sleep)
    return OpenAICompatibleEmbeddingProvider(*args, **kwargs)


@dataclass
class _RecordedRequest:
    url: str
    headers: dict[str, str]
    json: dict
    timeout: float


class _Response:
    def __init__(self, status=200, payload=None, json_error=None):
        self.status = status
        self._payload = payload
        self._json_error = json_error
        self.json_calls = 0
        self.exited = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.exited = True

    async def json(self):
        self.json_calls += 1
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class _Session:
    def __init__(self, response=None, post_error=None):
        self.response = response or _Response()
        self.post_error = post_error
        self.request = None
        self.exited = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.exited = True

    def post(self, url, *, headers, json, timeout):
        self.request = _RecordedRequest(url, headers, json, timeout)
        if self.post_error is not None:
            raise self.post_error
        return self.response


def _factory(session):
    return lambda: session


def _request(*texts, dimensions=2):
    return EmbeddingRequest(texts=tuple(texts), language="en", model="embedding-v1", dimensions=dimensions)


def _embed(provider, request):
    return asyncio.run(provider.embed(request))


def test_openai_posts_one_ordered_batch_and_restores_index_order():
    """Ignoring response indices pairs embeddings with the wrong semantic units."""
    session = _Session(
        _Response(payload={"data": [{"index": 1, "embedding": [0.0, 1.0]}, {"index": 0, "embedding": [2.0, 0.0]}]})
    )
    result = _embed(
        OpenAICompatibleEmbeddingProvider_fast("https://example.test/v1/chat/completions", "secret", _factory(session), 30),
        _request("a", "b"),
    )

    assert session.request.url == "https://example.test/v1/embeddings"
    assert session.request.headers == {"Content-Type": "application/json", "Authorization": "Bearer secret"}
    assert session.request.json == {
        "model": "embedding-v1", "input": ["a", "b"], "encoding_format": "float", "dimensions": 2
    }
    np.testing.assert_allclose(result.vectors, [[1.0, 0.0], [0.0, 1.0]])
    assert result.model == "embedding-v1"


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://example.test", "https://example.test/v1/embeddings"),
        ("https://example.test/v1", "https://example.test/v1/embeddings"),
        ("https://example.test/v1/", "https://example.test/v1/embeddings"),
        ("https://example.test/deployment/v1/chat/completions", "https://example.test/deployment/v1/embeddings"),
    ],
)
def test_openai_normalizes_root_and_preserves_safe_deployment_prefix(base_url, expected):
    """Discarding a deployment prefix sends requests to the wrong hosted endpoint."""
    session = _Session(_Response(payload={"data": [{"index": 0, "embedding": [1.0, 0.0]}]}))
    _embed(OpenAICompatibleEmbeddingProvider_fast(base_url, "", _factory(session), 1), _request("x"))
    assert session.request.url == expected
    assert session.request.headers == {"Content-Type": "application/json"}


@pytest.mark.parametrize("base_url", ["example.test", "ftp://example.test", "https:///v1", "https://user:pass@example.test/v1", "https://example.test/v1?key=x", "https://example.test/v1#secret"])
def test_openai_rejects_unsafe_endpoint_without_echoing_it(base_url):
    """Credentials or query strings in an endpoint can leak through logs and exceptions."""
    with pytest.raises(Exception) as captured:
        OpenAICompatibleEmbeddingProvider_fast(base_url, "key", _factory(_Session()), 1)
    assert base_url not in str(captured.value)
    assert base_url not in repr(captured.value)


@pytest.mark.parametrize(
    ("status", "retryable"),
    [(400, False), (401, False), (403, False), (404, False), (408, True), (429, True), (500, True)],
)
def test_openai_maps_http_status_before_json_and_closes_contexts(status, retryable):
    """Reading raw error payloads could expose credentials and server diagnostics."""
    response = _Response(status=status, payload={"error": "do not disclose"})
    session = _Session(response)
    with pytest.raises(EmbeddingHttpError) as captured:
        _embed(OpenAICompatibleEmbeddingProvider_fast("https://example.test", "secret", _factory(session), 1), _request("text"))

    error = captured.value
    assert (error.status, error.provider, error.retryable) == (status, "openai_compatible", retryable)
    assert response.json_calls == 0
    assert response.exited and session.exited
    assert "secret" not in str(error)
    assert "secret" not in repr(error)
    assert "do not disclose" not in str(error)
    assert "do not disclose" not in repr(error)


def test_openai_treats_sentinel_key_as_no_authentication():
    """Sending a local sentinel as Bearer auth breaks unauthenticated local endpoints."""
    session = _Session(_Response(payload={"data": [{"index": 0, "embedding": [1.0, 0.0]}]}))
    _embed(OpenAICompatibleEmbeddingProvider_fast("http://localhost:1234", "  __local__ ", _factory(session), 1), _request("x"))
    assert session.request.headers == {"Content-Type": "application/json"}


def test_openai_omits_dimensions_when_the_embedding_request_does_not_set_them():
    """Sending a null dimension changes the documented request shape for compatible endpoints."""
    session = _Session(_Response(payload={"data": [{"index": 0, "embedding": [1.0, 0.0]}]}))
    _embed(OpenAICompatibleEmbeddingProvider_fast("https://example.test", "", _factory(session), 1), _request("x", dimensions=None))
    assert session.request.json == {"model": "embedding-v1", "input": ["x"], "encoding_format": "float"}


def test_openai_maps_timeout_and_invalid_json_to_typed_safe_errors():
    """Unwrapped transport or decoder exceptions may contain request text and keys."""
    session = _Session(post_error=TimeoutError("secret request"))
    with pytest.raises(EmbeddingTransportError) as transport:
        _embed(OpenAICompatibleEmbeddingProvider_fast("https://example.test", "key", _factory(session), 1), _request("secret request"))
    assert "secret request" not in str(transport.value)
    assert "secret request" not in repr(transport.value)

    with pytest.raises(EmbeddingResponseError) as response:
        invalid_json_session = _Session(_Response(json_error=ValueError("raw body")))
        _embed(
            OpenAICompatibleEmbeddingProvider_fast(
                "https://example.test", "key", _factory(invalid_json_session), 1
            ),
            _request("x"),
        )
    assert "raw body" not in str(response.value)
    assert "raw body" not in repr(response.value)


@pytest.mark.parametrize(
    "payload",
    [
        {"data": [{"index": 0, "embedding": [1.0, 0.0]}, {"index": 0, "embedding": [0.0, 1.0]}]},
        {"data": [{"index": 1, "embedding": [1.0, 0.0]}, {"index": 2, "embedding": [0.0, 1.0]}]},
        {"data": [{"index": True, "embedding": [1.0, 0.0]}, {"index": 1, "embedding": [0.0, 1.0]}]},
        {"data": [{"index": 0, "embedding": [1.0]}, {"index": 1, "embedding": [0.0, 1.0]}]},
    ],
)
def test_openai_rejects_duplicate_missing_boolean_or_ragged_response_rows(payload):
    """Partial, duplicate, or ragged OpenAI data invalidates positional semantic alignment."""
    with pytest.raises(EmbeddingResponseError):
        _embed(
            OpenAICompatibleEmbeddingProvider_fast("https://example.test", "key", _factory(_Session(_Response(payload=payload))), 1),
            _request("a", "b"),
        )


def test_openai_propagates_cancellation():
    """Treating cancellation as a retryable network failure delays user cancellation."""
    with pytest.raises(asyncio.CancelledError):
        _embed(
            OpenAICompatibleEmbeddingProvider_fast("https://example.test", "key", _factory(_Session(post_error=asyncio.CancelledError())), 1),
            _request("x"),
        )


def test_factory_selects_explicit_online_configurations_without_translation_worker_state():
    """Reading a translation worker model would couple embedding model selection to translation."""
    config = EmbeddingProviderConfig(
        kind="auto",
        providers=(
            EmbeddingProviderConfig(kind="gemini", api_key="gemini-key", model="gemini-embedding-001"),
            EmbeddingProviderConfig(kind="openai_compatible", base_url="https://example.test/v1", model="embedding-v1"),
        ),
    )
    provider = create_embedding_provider(config, _factory(_Session()))

    assert provider.name == "fallback"
    assert [item.name for item in provider.providers] == ["gemini", "openai_compatible"]
    assert getattr(provider.providers[0], "model", None) is None


def test_factory_rejects_empty_auto_configuration_as_unavailable():
    """Silently creating an empty fallback would postpone a configuration error until runtime."""
    with pytest.raises(EmbeddingUnavailableError):
        create_embedding_provider(EmbeddingProviderConfig(kind="auto"), _factory(_Session()))


def test_factory_configuration_is_frozen_secret_safe_and_auto_only_accepts_concrete_providers():
    """Nested fallbacks and key-bearing config reprs hide invalid setup and disclose credentials."""
    secret = "factory-api-key-must-not-appear"
    config = EmbeddingProviderConfig(kind="gemini", api_key=secret, model="embedding-v1")

    assert secret not in repr(config)
    with pytest.raises(FrozenInstanceError):
        config.api_key = "changed"
    with pytest.raises(Exception):
        EmbeddingProviderConfig(kind="auto", providers=(EmbeddingProviderConfig(kind="auto"),))
