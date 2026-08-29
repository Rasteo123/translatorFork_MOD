"""Gemini REST adapter for the Qt-free semantic embedding contract.

``session_factory`` must return an aiohttp-like asynchronous context manager.
Its session must expose ``post(url, *, headers, json, timeout)`` returning an
asynchronous response context manager whose response exposes integer ``status``
and async ``json()``.  The adapter owns both contexts for every request.
"""

from __future__ import annotations

import asyncio
import math
import re
from typing import Any

import numpy as np

from .base import EmbeddingBatch, EmbeddingContractError, EmbeddingRequest, validate_and_normalize_batch
from .factory import EmbeddingHttpError, EmbeddingResponseError, EmbeddingTransportError


_GEMINI_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_GEMINI_TASK = re.compile(r"[A-Z][A-Z0-9_]*\Z")
_GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta"


def _positive_finite_timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EmbeddingContractError("timeout_seconds must be a positive finite number")
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0:
        raise EmbeddingContractError("timeout_seconds must be a positive finite number")
    return timeout


def _model_resource(value: object) -> str:
    if not isinstance(value, str):
        raise EmbeddingContractError("Gemini model must be a valid resource name")
    model = value.strip()
    if model.startswith("models/"):
        model = model[len("models/") :]
    if not _GEMINI_MODEL.fullmatch(model):
        raise EmbeddingContractError("Gemini model must be a valid resource name")
    return f"models/{model}"


def _task_type(value: object) -> str:
    if not isinstance(value, str):
        raise EmbeddingContractError("Gemini task type must be valid")
    normalized = value.strip().replace("-", "_").replace(" ", "_").upper()
    if not _GEMINI_TASK.fullmatch(normalized):
        raise EmbeddingContractError("Gemini task type must be valid")
    return normalized


def _response_matrix(payload: object, expected_rows: int) -> np.ndarray:
    if not isinstance(payload, dict) or not isinstance(payload.get("embeddings"), list):
        raise EmbeddingResponseError("gemini")
    embeddings = payload["embeddings"]
    if len(embeddings) != expected_rows:
        raise EmbeddingResponseError("gemini")
    rows: list[list[float | int]] = []
    for embedding in embeddings:
        values = embedding.get("values") if isinstance(embedding, dict) else None
        if not isinstance(values, list) or not values:
            raise EmbeddingResponseError("gemini")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
            raise EmbeddingResponseError("gemini")
        rows.append(values)
    try:
        matrix = np.asarray(rows, dtype=np.float32)
    except (TypeError, ValueError, OverflowError):
        raise EmbeddingResponseError("gemini") from None
    if matrix.ndim != 2 or matrix.shape[0] != expected_rows or matrix.shape[1] <= 0:
        raise EmbeddingResponseError("gemini")
    return matrix


class GeminiEmbeddingProvider:
    """Send one ordered Gemini ``batchEmbedContents`` request per batch."""

    name = "gemini"

    def __init__(self, api_key: str, session_factory, timeout_seconds: float):
        if not isinstance(api_key, str) or not api_key.strip() or api_key.strip().startswith("__"):
            raise EmbeddingContractError("Gemini API key must be configured")
        if not callable(session_factory):
            raise EmbeddingContractError("session_factory must be callable")
        self._api_key = api_key.strip()
        self._session_factory = session_factory
        self._timeout_seconds = _positive_finite_timeout(timeout_seconds)

    async def embed(self, request: EmbeddingRequest) -> EmbeddingBatch:
        if not isinstance(request, EmbeddingRequest):
            raise EmbeddingContractError("request must be an EmbeddingRequest")
        model = _model_resource(request.model)
        config: dict[str, Any] = {"taskType": _task_type(request.task_type)}
        if request.dimensions is not None:
            config["outputDimensionality"] = request.dimensions
        payload = {
            "requests": [
                {
                    "model": model,
                    "content": {"parts": [{"text": text}]},
                    "embedContentConfig": dict(config),
                }
                for text in request.texts
            ]
        }
        url = f"{_GEMINI_ENDPOINT}/{model}:batchEmbedContents"
        headers = {"Content-Type": "application/json", "x-goog-api-key": self._api_key}
        response_payload = await self._post_json(url, headers, payload)
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

    async def _post_json(self, url: str, headers: dict[str, str], payload: dict) -> object:
        try:
            async with self._session_factory() as session:
                async with session.post(url, headers=headers, json=payload, timeout=self._timeout_seconds) as response:
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
