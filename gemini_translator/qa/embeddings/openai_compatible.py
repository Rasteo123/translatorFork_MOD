"""OpenAI-compatible REST adapter for the Qt-free semantic embedding contract.

``session_factory`` follows the same aiohttp-like contract as the Gemini
adapter: it returns an async session context manager; ``post`` returns an async
response context manager with integer ``status`` and async ``json()``.
"""

from __future__ import annotations

import asyncio
import math
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

import numpy as np

from .base import EmbeddingBatch, EmbeddingContractError, EmbeddingRequest, validate_and_normalize_batch
from .factory import EmbeddingHttpError, EmbeddingResponseError, EmbeddingTransportError


def _positive_finite_timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EmbeddingContractError("timeout_seconds must be a positive finite number")
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0:
        raise EmbeddingContractError("timeout_seconds must be a positive finite number")
    return timeout


def _embeddings_url(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EmbeddingContractError("OpenAI-compatible base URL must be a valid HTTP URL")
    try:
        parsed = urlsplit(value.strip())
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise EmbeddingContractError("OpenAI-compatible base URL must be a valid HTTP URL") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise EmbeddingContractError("OpenAI-compatible base URL must be a valid HTTP URL")
    netloc = hostname
    if ":" in hostname and not hostname.startswith("["):
        netloc = f"[{hostname}]"
    if port is not None:
        netloc = f"{netloc}:{port}"
    parts = [part for part in parsed.path.split("/") if part]
    if any(unquote(part) in {".", ".."} or "/" in unquote(part) for part in parts):
        raise EmbeddingContractError("OpenAI-compatible base URL must be a valid HTTP URL")
    if "v1" in parts:
        prefix = parts[: parts.index("v1")]
    else:
        prefix = parts
    path = "/" + "/".join((*prefix, "v1", "embeddings"))
    return urlunsplit((parsed.scheme, netloc, path, "", ""))


def _response_matrix(payload: object, expected_rows: int) -> np.ndarray:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise EmbeddingResponseError("openai_compatible")
    data = payload["data"]
    if len(data) != expected_rows:
        raise EmbeddingResponseError("openai_compatible")
    ordered: dict[int, list[float | int]] = {}
    for item in data:
        if not isinstance(item, dict):
            raise EmbeddingResponseError("openai_compatible")
        index = item.get("index")
        values = item.get("embedding")
        if isinstance(index, bool) or not isinstance(index, int) or index in ordered:
            raise EmbeddingResponseError("openai_compatible")
        if not isinstance(values, list) or not values:
            raise EmbeddingResponseError("openai_compatible")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
            raise EmbeddingResponseError("openai_compatible")
        ordered[index] = values
    if set(ordered) != set(range(expected_rows)):
        raise EmbeddingResponseError("openai_compatible")
    try:
        matrix = np.asarray([ordered[index] for index in range(expected_rows)], dtype=np.float32)
    except (TypeError, ValueError, OverflowError):
        raise EmbeddingResponseError("openai_compatible") from None
    if matrix.ndim != 2 or matrix.shape != (expected_rows, matrix.shape[1]) or matrix.shape[1] <= 0:
        raise EmbeddingResponseError("openai_compatible")
    return matrix


class OpenAICompatibleEmbeddingProvider:
    """Send one ordered ``/v1/embeddings`` request to a configured endpoint."""

    name = "openai_compatible"

    def __init__(self, base_url: str, api_key: str, session_factory, timeout_seconds: float):
        if not callable(session_factory):
            raise EmbeddingContractError("session_factory must be callable")
        self._url = _embeddings_url(base_url)
        self._api_key = str(api_key or "").strip()
        self._session_factory = session_factory
        self._timeout_seconds = _positive_finite_timeout(timeout_seconds)

    async def embed(self, request: EmbeddingRequest) -> EmbeddingBatch:
        if not isinstance(request, EmbeddingRequest):
            raise EmbeddingContractError("request must be an EmbeddingRequest")
        payload: dict[str, Any] = {
            "model": request.model,
            "input": list(request.texts),
            "encoding_format": "float",
        }
        if request.dimensions is not None:
            payload["dimensions"] = request.dimensions
        headers = {"Content-Type": "application/json"}
        if self._api_key and not self._api_key.startswith("__"):
            headers["Authorization"] = f"Bearer {self._api_key}"
        response_payload = await self._post_json(headers, payload)
        matrix = _response_matrix(response_payload, len(request.texts))
        try:
            batch = validate_and_normalize_batch(
                EmbeddingBatch(
                    vectors=matrix,
                    provider=self.name,
                    model=request.model,
                    dimensions=int(matrix.shape[1]),
                ),
                expected_rows=len(request.texts),
            )
        except EmbeddingContractError:
            raise EmbeddingResponseError(self.name) from None
        if request.dimensions is not None and batch.dimensions != request.dimensions:
            raise EmbeddingResponseError(self.name)
        return batch

    async def _post_json(self, headers: dict[str, str], payload: dict) -> object:
        try:
            async with self._session_factory() as session:
                async with session.post(self._url, headers=headers, json=payload, timeout=self._timeout_seconds) as response:
                    status = getattr(response, "status", None)
                    if isinstance(status, bool) or not isinstance(status, int):
                        raise EmbeddingResponseError(self.name)
                    if not 200 <= status < 300:
                        raise EmbeddingHttpError(status, self.name, status == 408 or status == 429 or status >= 500)
                    try:
                        return await response.json()
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        raise EmbeddingResponseError(self.name) from None
        except asyncio.CancelledError:
            raise
        except (EmbeddingHttpError, EmbeddingResponseError, EmbeddingTransportError):
            raise
        except Exception:
            raise EmbeddingTransportError(self.name) from None
