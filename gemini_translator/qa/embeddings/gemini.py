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
from .factory import (
    EmbeddingHttpError,
    EmbeddingResponseError,
    EmbeddingTransportError,
    EmbeddingUnavailableError,
)
from .retry import DEFAULT_ATTEMPTS, exponential_delay, with_retries


_GEMINI_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_GEMINI_TASK = re.compile(r"[A-Z][A-Z0-9_]*\Z")
_GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta"
MAX_KEY_ROTATIONS = 12
# The service refuses more than this many items in one batch request.
MAX_BATCH_REQUESTS = 100
# What the service says when a key is out of quota rather than merely busy.  A
# plain 429 is a reason to rotate and come back; these are a reason to stop
# using the key for embeddings until its limit window resets.
_QUOTA_MARKERS = (
    "resource_exhausted",
    "quota",
    "billing",
    "exceeded your current",
    "per day",
)


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

    def __init__(
        self,
        api_key,
        session_factory,
        timeout_seconds: float,
        *,
        retry_attempts: int = DEFAULT_ATTEMPTS,
        retry_sleep=asyncio.sleep,
        key_health=None,
    ):
        keys = (api_key,) if isinstance(api_key, str) else tuple(api_key or ())
        cleaned = tuple(
            key.strip()
            for key in keys
            if isinstance(key, str) and key.strip() and not key.strip().startswith("__")
        )
        if not cleaned:
            raise EmbeddingContractError("Gemini API key must be configured")
        if not callable(session_factory):
            raise EmbeddingContractError("session_factory must be callable")
        # Several keys are one provider with somewhere else to go when a key is
        # rate limited: the cache and the provider identity stay shared.
        self._api_keys = cleaned
        self._api_key = cleaned[0]
        self._session_factory = session_factory
        self._timeout_seconds = _positive_finite_timeout(timeout_seconds)
        self._retry_attempts = retry_attempts
        self._retry_sleep = retry_sleep
        self._key_health = key_health

    async def embed(self, request: EmbeddingRequest) -> EmbeddingBatch:
        if not isinstance(request, EmbeddingRequest):
            raise EmbeddingContractError("request must be an EmbeddingRequest")
        model = _model_resource(request.model)
        # batchEmbedContents takes these as fields of each request. Nesting them
        # the way the Python SDK does is accepted and then silently ignored: the
        # service answers with its default task type and dimensionality.
        config: dict[str, Any] = {"taskType": _task_type(request.task_type)}
        if request.dimensions is not None:
            config["outputDimensionality"] = request.dimensions
        url = f"{_GEMINI_ENDPOINT}/{model}:batchEmbedContents"
        # A chapter has more sentences than one batch may carry, so the texts
        # travel in order-preserving chunks and are stitched back together.
        chunks = [
            request.texts[start : start + MAX_BATCH_REQUESTS]
            for start in range(0, len(request.texts), MAX_BATCH_REQUESTS)
        ]
        matrices = []
        for chunk in chunks:
            payload = {
                "requests": [
                    {
                        "model": model,
                        "content": {"parts": [{"text": text}]},
                        **config,
                    }
                    for text in chunk
                ]
            }
            response_payload = await self._post_json(url, payload)
            matrices.append(_response_matrix(response_payload, len(chunk)))
        if len({item.shape[1] for item in matrices}) > 1:
            raise EmbeddingResponseError(self.name)
        matrix = np.vstack(matrices) if len(matrices) > 1 else matrices[0]
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

    def _usable_keys(self) -> tuple[str, ...]:
        """Skip keys already known to be out of embedding quota."""
        health = self._key_health
        if health is None:
            return self._api_keys
        usable = []
        for key in self._api_keys:
            try:
                if health.is_active(key):
                    usable.append(key)
            except Exception:  # noqa: BLE001 - unreadable health is not a red key
                usable.append(key)
        return tuple(usable)

    def _report_exhausted(self, api_key: str, reason: str) -> None:
        health = self._key_health
        if health is None:
            return
        try:
            health.mark_exhausted(api_key, reason)
        except Exception:  # noqa: BLE001 - bookkeeping never fails a check
            return

    async def _post_json(self, url: str, payload: dict) -> object:
        """Send one request, moving to the next key before it starts waiting."""
        available = self._usable_keys()
        if not available:
            raise EmbeddingUnavailableError(
                "every Gemini key is out of embedding quota"
            )
        state = {"attempt": 0}

        async def once():
            key = available[state["attempt"] % len(available)]
            state["attempt"] += 1
            headers = {"Content-Type": "application/json", "x-goog-api-key": key}
            return await self._post_json_once(url, headers, payload, api_key=key)

        keys = len(available)
        return await with_retries(
            once,
            attempts=max(self._retry_attempts, min(keys, MAX_KEY_ROTATIONS)),
            sleep=self._retry_sleep,
            delay_for=lambda attempt, base: (
                0.0 if attempt < keys - 1 else exponential_delay(attempt - keys + 1, base)
            ),
        )

    async def _post_json_once(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict,
        api_key: str = "",
    ) -> object:
        try:
            async with self._session_factory() as session:
                async with session.post(url, headers=headers, json=payload, timeout=self._timeout_seconds) as response:
                    status = getattr(response, "status", None)
                    if isinstance(status, bool) or not isinstance(status, int):
                        raise EmbeddingResponseError(self.name)
                    if not 200 <= status < 300:
                        if api_key and status in (403, 429):
                            reason = await _quota_reason(response)
                            if reason:
                                self._report_exhausted(api_key, reason)
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


async def _quota_reason(response) -> str:
    """Name the quota the service refused on, or nothing for a transient refusal.

    Reading the body is what separates "this key is busy right now" from "this
    key has nothing left today"; only the second one may take a key out of the
    embedding pool.
    """
    reader = getattr(response, "text", None)
    if not callable(reader):
        return ""
    try:
        body = await reader()
    except Exception:  # noqa: BLE001 - an unreadable body is not evidence
        return ""
    if not isinstance(body, str):
        return ""
    lowered = body.lower()
    for marker in _QUOTA_MARKERS:
        if marker in lowered:
            return marker
    return ""
