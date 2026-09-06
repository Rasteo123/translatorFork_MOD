"""Gemini REST adapter for the Qt-free semantic embedding contract.

``session_factory`` must return an aiohttp-like asynchronous context manager.
Its session must expose ``post(url, *, headers, json, timeout)`` returning an
asynchronous response context manager whose response exposes integer ``status``
and async ``json()``.  The adapter owns both contexts for every request.
"""

from __future__ import annotations

import asyncio
from email.utils import parsedate_to_datetime
import json
import math
import re
import time
from typing import Any

import numpy as np

from .base import (
    EmbeddingBatch,
    EmbeddingContractError,
    EmbeddingRequest,
    _positive_finite_timeout,
    validate_and_normalize_batch,
)
from .factory import (
    EmbeddingHttpError,
    EmbeddingResponseError,
    EmbeddingTransportError,
    EmbeddingUnavailableError,
)
from .retry import DEFAULT_ATTEMPTS, DEFAULT_BASE_DELAY_SECONDS, exponential_delay
from ..key_pool import QaKeyPool


_GEMINI_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_GEMINI_TASK = re.compile(r"[A-Z][A-Z0-9_]*\Z")
_GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta"
MAX_KEY_ROTATIONS = 12
# The service refuses more than this many items in one batch request.
MAX_BATCH_REQUESTS = 100


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
        clock=time.monotonic,
        max_wait_seconds: float = 120.0,
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
        self._api_keys = cleaned
        self._api_key = cleaned[0]
        self._session_factory = session_factory
        self._timeout_seconds = _positive_finite_timeout(timeout_seconds)
        self._retry_attempts = retry_attempts
        self._retry_sleep = retry_sleep
        self._key_health = key_health
        # Match QA/translation rotation: keep a working key, wait on the first
        # temporary refusal, and only then consider a replacement. Preserve the
        # embedding pool's historical first-key order.
        self._pool = QaKeyPool(reversed(cleaned), clock=clock)
        self._max_wait = max(0.0, float(max_wait_seconds))

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
        """Use the persistent pool, including its pauses and request budget."""
        attempts = max(self._retry_attempts, min(len(self._api_keys), MAX_KEY_ROTATIONS))
        waited = 0.0
        last_error = None
        for attempt in range(max(1, attempts)):
            usable = set(self._usable_keys())
            for item in self._api_keys:
                if item not in usable:
                    self._pool.mark_exhausted(item)
            while True:
                if self._pool.blocked_reason is not None:
                    raise EmbeddingHttpError(int(self._pool.blocked_reason), self.name, False)
                key = self._pool.acquire()
                if key is not None:
                    break
                wait = self._pool.seconds_until_available()
                if wait is None or waited >= self._max_wait:
                    if last_error is not None:
                        raise last_error
                    raise EmbeddingUnavailableError(())
                pause = min(max(wait, 0.5), self._max_wait - waited)
                waited += pause
                await self._retry_sleep(pause)
            headers = {"Content-Type": "application/json", "x-goog-api-key": key}
            try:
                result = await self._post_json_once(url, headers, payload, api_key=key)
                self._pool.note_success(key)
                return result
            except EmbeddingHttpError as error:
                last_error = error
                if error.status in (401, 403):
                    self._pool.block(str(error.status))
                    raise
                if not error.retryable:
                    raise
                if error.status == 429:
                    if error.quota_exhausted:
                        self._pool.mark_exhausted(key)
                    else:
                        self._pool.note_throttled(key, error.retry_after_seconds)
                    continue
            except EmbeddingTransportError as error:
                last_error = error
            if attempt < attempts - 1:
                await self._retry_sleep(exponential_delay(attempt, DEFAULT_BASE_DELAY_SECONDS))
        raise last_error

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
                        delay, reason = (60.0, "")
                        if status == 429:
                            delay, reason = await _rate_limit_details(response)
                            if reason:
                                self._report_exhausted(api_key, reason)
                        raise EmbeddingHttpError(
                            status, self.name, status == 408 or status == 429 or status >= 500,
                            retry_after_seconds=delay, quota_exhausted=bool(reason),
                        )
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


async def _rate_limit_details(response) -> tuple[float, str]:
    """Read server deadlines without exposing response text or credentials.

    RESOURCE_EXHAUSTED and 'quota' also describe minute limits; only explicit
    daily quota evidence may remove a key for the day.
    """
    delays = []

    def add_delay(value):
        if isinstance(value, bool):
            return
        try:
            seconds = float(str(value).removesuffix("s"))
        except (TypeError, ValueError):
            return
        if math.isfinite(seconds) and seconds > 0:
            delays.append(seconds)

    headers = getattr(response, "headers", {})
    retry_after = headers.get("Retry-After") if headers is not None else None
    if retry_after:
        add_delay(retry_after)
        if not delays:
            try:
                add_delay(parsedate_to_datetime(retry_after).timestamp() - time.time())
            except (TypeError, ValueError, OverflowError):
                pass

    body = ""
    reader = getattr(response, "text", None)
    if callable(reader):
        try:
            body = await reader()
        except Exception:  # noqa: BLE001 - retain a readable header's deadline
            pass
    body = body if isinstance(body, str) else ""

    def visit(value):
        if isinstance(value, dict):
            for name, item in value.items():
                if name == "retryDelay":
                    if isinstance(item, dict):
                        add_delay(item.get("seconds"))
                    else:
                        add_delay(item)
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    try:
        visit(json.loads(body))
    except (ValueError, RecursionError):
        pass
    for value in re.findall(r"retry in ([\d.]+)s", body.lower()):
        add_delay(value)
    daily = "perday" in re.sub(r"[\s_-]+", "", body.lower())
    return max(delays, default=60.0), "per day" if daily else ""
