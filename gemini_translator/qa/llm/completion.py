"""Adapter from QA completion requests to existing configured API handlers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import inspect
from threading import Event
from typing import Awaitable, Callable, Mapping, Protocol
from uuid import uuid4

from .json_response import QaResponseSchemaError, parse_single_json_object


@dataclass(frozen=True, slots=True)
class QaModelSelection:
    """Qt-free routing identity sufficient for a handler factory."""

    provider: str
    model: str

    def __post_init__(self) -> None:
        for field_name in ("provider", "model"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a nonempty string")
            object.__setattr__(self, field_name, value.strip())


class CancellationToken:
    """Small thread-safe cancellation contract independent of Qt workers."""

    __slots__ = ("_cancelled", "_is_cancelled_callback")

    def __init__(
        self,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> None:
        if is_cancelled is not None and not callable(is_cancelled):
            raise TypeError("is_cancelled must be callable")
        self._cancelled = Event()
        self._is_cancelled_callback = is_cancelled

    @property
    def is_cancelled(self) -> bool:
        callback_cancelled = bool(
            self._is_cancelled_callback and self._is_cancelled_callback()
        )
        return self._cancelled.is_set() or callback_cancelled

    def cancel(self) -> None:
        self._cancelled.set()

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled:
            raise asyncio.CancelledError


class QaCompletionClient(Protocol):
    async def complete_json(
        self,
        prompt: str,
        *,
        model: QaModelSelection,
        max_output_tokens: int,
        cancellation: CancellationToken,
    ) -> dict[str, object]:
        """Return exactly one parsed JSON object from a QA request."""


HandlerFactory = Callable[[QaModelSelection], object | Awaitable[object]]
EventSink = Callable[
    [Mapping[str, str]],
    object | Awaitable[object],
]


class ExistingHandlerCompletionClient:
    """Route QA prompts through an injected handler's existing lifecycle."""

    def __init__(
        self,
        handler_factory: HandlerFactory,
        retry_policy: object,
        event_sink: EventSink | None,
    ) -> None:
        if not callable(handler_factory):
            raise TypeError("handler_factory must be callable")
        if event_sink is not None and not callable(event_sink):
            raise TypeError("event_sink must be callable")
        self._handler_factory = handler_factory
        self._retry_policy = retry_policy
        self._event_sink = event_sink

    async def _emit(
        self,
        *,
        qa_request_id: str,
        model: QaModelSelection,
        status: str,
    ) -> None:
        if self._event_sink is None:
            return
        event = {
            "qa_request_id": qa_request_id,
            "provider": model.provider,
            "model": model.model,
            "status": status,
        }
        try:
            result = self._event_sink(event)
            if inspect.isawaitable(result):
                await result
        except Exception:
            # Observability is best-effort and must not mask typed API failures.
            return

    async def complete_json(
        self,
        prompt: str,
        *,
        model: QaModelSelection,
        max_output_tokens: int,
        cancellation: CancellationToken,
    ) -> dict[str, object]:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be nonempty text")
        if not isinstance(model, QaModelSelection):
            raise TypeError("model must be a QaModelSelection")
        if (
            isinstance(max_output_tokens, bool)
            or not isinstance(max_output_tokens, int)
            or max_output_tokens <= 0
        ):
            raise ValueError("max_output_tokens must be a positive integer")
        if not hasattr(cancellation, "raise_if_cancelled"):
            raise TypeError("cancellation must implement raise_if_cancelled")

        qa_request_id = f"qa-{uuid4().hex}"
        try:
            cancellation.raise_if_cancelled()
            await self._emit(
                qa_request_id=qa_request_id,
                model=model,
                status="creating_handler",
            )

            handler = self._handler_factory(model)
            if inspect.isawaitable(handler):
                handler = await handler

            cancellation.raise_if_cancelled()
            await self._emit(
                qa_request_id=qa_request_id,
                model=model,
                status="request_started",
            )
            cancellation.raise_if_cancelled()

            execute_api_call = getattr(handler, "execute_api_call", None)
            if not callable(execute_api_call):
                raise TypeError("handler must provide execute_api_call")
            raw_response = execute_api_call(
                prompt,
                f"[QA:{qa_request_id}]",
                allow_incomplete=False,
                use_stream=False,
                max_output_tokens=max_output_tokens,
            )
            if inspect.isawaitable(raw_response):
                raw_response = await raw_response
            if not isinstance(raw_response, str):
                raise QaResponseSchemaError("QA handler result must be text")

            parsed = parse_single_json_object(raw_response)
            await self._emit(
                qa_request_id=qa_request_id,
                model=model,
                status="completed",
            )
            return parsed
        except asyncio.CancelledError:
            await self._emit(
                qa_request_id=qa_request_id,
                model=model,
                status="cancelled",
            )
            raise
        except Exception:
            await self._emit(
                qa_request_id=qa_request_id,
                model=model,
                status="failed",
            )
            raise
