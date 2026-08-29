"""Behavioral contract for the Gemini online embedding adapter."""

import asyncio
from dataclasses import dataclass

import numpy as np
import pytest

from gemini_translator.qa.embeddings import EmbeddingRequest
from gemini_translator.qa.embeddings.factory import (
    EmbeddingHttpError,
    EmbeddingResponseError,
    EmbeddingTransportError,
)
from gemini_translator.qa.embeddings.gemini import GeminiEmbeddingProvider


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
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
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
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
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


def _request(*texts, model="models/gemini-embedding-001", dimensions=2, task_type="semantic-similarity"):
    return EmbeddingRequest(
        texts=tuple(texts), language="ru-RU", model=model, dimensions=dimensions, task_type=task_type
    )


def _embed(provider, request):
    return asyncio.run(provider.embed(request))


def test_gemini_posts_documented_batch_shape_and_preserves_text_order():
    """Dropping per-item config or changing the order corrupts source/translation alignment."""
    session = _Session(
        _Response(payload={"embeddings": [{"values": [1.0, 0.0]}, {"values": [0.0, 2.0]}]})
    )
    secret = "gemini-secret-never-in-url"
    result = _embed(GeminiEmbeddingProvider(secret, _factory(session), 30), _request("源文", "перевод"))

    assert session.request.url == "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:batchEmbedContents"
    assert "?" not in session.request.url
    assert session.request.headers == {"Content-Type": "application/json", "x-goog-api-key": secret}
    assert session.request.timeout == 30.0
    assert [row["content"]["parts"][0]["text"] for row in session.request.json["requests"]] == ["源文", "перевод"]
    assert all(row["model"] == "models/gemini-embedding-001" for row in session.request.json["requests"])
    assert all(
        row["embedContentConfig"] == {"taskType": "SEMANTIC_SIMILARITY", "outputDimensionality": 2}
        for row in session.request.json["requests"]
    )
    assert result.vectors.shape == (2, 2)
    np.testing.assert_allclose(result.vectors, [[1.0, 0.0], [0.0, 1.0]])


def test_gemini_accepts_bare_model_and_omits_unrequested_dimension():
    """Failing to normalize resource names produces invalid Gemini RPC paths."""
    session = _Session(_Response(payload={"embeddings": [{"values": [3.0, 4.0]}]}))
    _embed(
        GeminiEmbeddingProvider("key", _factory(session), 2.5),
        _request("text", model="gemini-embedding-001", dimensions=None, task_type="retrieval-query"),
    )

    assert session.request.url.endswith("/models/gemini-embedding-001:batchEmbedContents")
    assert session.request.json["requests"][0]["model"] == "models/gemini-embedding-001"
    assert session.request.json["requests"][0]["embedContentConfig"] == {"taskType": "RETRIEVAL_QUERY"}


@pytest.mark.parametrize("model", ["", "models/", "models/a/b", "../model", "model?key=x", "model#fragment"])
def test_gemini_rejects_unsafe_model_without_echoing_it(model):
    """Permitting a configured path/query could alter the request endpoint or expose secrets."""
    with pytest.raises(Exception) as captured:
        _embed(GeminiEmbeddingProvider("key", _factory(_Session()), 1), _request("x", model=model))

    if model:
        assert model not in str(captured.value)
        assert model not in repr(captured.value)


@pytest.mark.parametrize("key", ["", "  ", "__unset__"])
def test_gemini_rejects_missing_or_placeholder_api_key_without_echoing_it(key):
    """Sending a placeholder as a credential masks a misconfigured provider."""
    with pytest.raises(Exception) as captured:
        GeminiEmbeddingProvider(key, _factory(_Session()), 1)

    if key:
        assert key not in str(captured.value)
        assert key not in repr(captured.value)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True, "30"])
def test_gemini_rejects_nonpositive_or_nonfinite_timeout(timeout):
    """Invalid timeouts otherwise fail later as opaque transport errors."""
    with pytest.raises(Exception):
        GeminiEmbeddingProvider("key", _factory(_Session()), timeout)


@pytest.mark.parametrize(
    ("status", "retryable"),
    [(400, False), (401, False), (403, False), (404, False), (408, True), (429, True), (500, True)],
)
def test_gemini_maps_http_status_before_parsing_json(status, retryable):
    """Parsing an error body first can leak provider diagnostics and misclassify retryability."""
    response = _Response(status=status, payload={"error": "secret body"})
    session = _Session(response)
    secret = "gemini-api-key-123"

    with pytest.raises(EmbeddingHttpError) as captured:
        _embed(GeminiEmbeddingProvider(secret, _factory(session), 1), _request("text"))

    error = captured.value
    assert (error.status, error.provider, error.retryable) == (status, "gemini", retryable)
    assert response.json_calls == 0
    assert session.exited and response.exited
    assert secret not in str(error)
    assert secret not in repr(error)
    assert "secret body" not in str(error)
    assert "secret body" not in repr(error)


def test_gemini_maps_timeout_transport_and_keeps_contexts_closed():
    """Letting timeout details escape could disclose request data and leave a session open."""
    session = _Session(post_error=TimeoutError("request text: sensitive"))
    with pytest.raises(EmbeddingTransportError) as captured:
        _embed(GeminiEmbeddingProvider("private", _factory(session), 1), _request("sensitive"))

    assert captured.value.provider == "gemini"
    assert captured.value.retryable is True
    assert session.exited
    assert "private" not in str(captured.value)
    assert "private" not in repr(captured.value)
    assert "sensitive" not in str(captured.value)
    assert "sensitive" not in repr(captured.value)


def test_gemini_maps_invalid_json_and_schema_to_secret_safe_response_errors():
    """Accepting malformed provider JSON could create partial or ragged embeddings."""
    for response in (
        _Response(json_error=ValueError("raw secret response")),
        _Response(payload={"embeddings": [{"values": [1.0, 0.0]}]}),
        _Response(payload={"embeddings": [{"values": [1.0]}, {"values": [0.0, 1.0]}]}),
    ):
        with pytest.raises(EmbeddingResponseError) as captured:
            _embed(GeminiEmbeddingProvider("private", _factory(_Session(response)), 1), _request("a", "b"))

        assert "private" not in str(captured.value)
        assert "raw secret response" not in str(captured.value)


def test_gemini_propagates_cancellation():
    """Wrapping cancellation as transport failure would make QA impossible to stop promptly."""
    session = _Session(post_error=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        _embed(GeminiEmbeddingProvider("key", _factory(session), 1), _request("x"))
