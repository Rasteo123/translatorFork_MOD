"""Adapter from QA completion requests to existing configured API handlers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import inspect
from threading import Event, Lock
from typing import Awaitable, Callable, Mapping, Protocol
from uuid import uuid4

from .json_response import QaResponseSchemaError, parse_single_json_object


_CALLBACK_POLL_INTERVAL_SECONDS = 0.05


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

    __slots__ = (
        "_cancelled",
        "_is_cancelled_callback",
        "_waiters",
        "_waiters_lock",
    )

    def __init__(
        self,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> None:
        if is_cancelled is not None and not callable(is_cancelled):
            raise TypeError("is_cancelled must be callable")
        self._cancelled = Event()
        self._is_cancelled_callback = is_cancelled
        self._waiters: set[tuple[asyncio.AbstractEventLoop, asyncio.Future]] = set()
        self._waiters_lock = Lock()

    @property
    def is_cancelled(self) -> bool:
        callback_cancelled = bool(
            self._is_cancelled_callback and self._is_cancelled_callback()
        )
        return self._cancelled.is_set() or callback_cancelled

    def cancel(self) -> None:
        self._cancelled.set()
        with self._waiters_lock:
            waiters = tuple(self._waiters)
        for loop, future in waiters:
            try:
                loop.call_soon_threadsafe(self._resolve_waiter, future)
            except RuntimeError:
                continue

    @staticmethod
    def _resolve_waiter(future: asyncio.Future) -> None:
        if not future.done():
            future.set_result(None)

    async def wait_cancelled(self) -> None:
        """Wait for cancellation without executors or unbounded busy polling.

        Local ``cancel()`` wakes the registered future immediately. A legacy
        worker callback has no push subscription, so it is checked at a bounded
        50 ms interval while an async factory, sink, or handler is in flight.
        """
        if self.is_cancelled:
            return
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        waiter = (loop, future)
        with self._waiters_lock:
            self._waiters.add(waiter)
            cancelled = self._cancelled.is_set()
        if cancelled:
            self._resolve_waiter(future)
        try:
            if self._is_cancelled_callback is None:
                await future
                return
            while not self.is_cancelled:
                done, _ = await asyncio.wait(
                    {future},
                    timeout=_CALLBACK_POLL_INTERVAL_SECONDS,
                )
                if future in done:
                    return
        finally:
            with self._waiters_lock:
                self._waiters.discard(waiter)
            if not future.done():
                future.cancel()

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
        event_sink: EventSink | None = None,
    ) -> None:
        if not callable(handler_factory):
            raise TypeError("handler_factory must be callable")
        if event_sink is not None and not callable(event_sink):
            raise TypeError("event_sink must be callable")
        self._handler_factory = handler_factory
        self._event_sink = event_sink

    @staticmethod
    async def _drain_cancelled_task(task: asyncio.Future) -> None:
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return
        except Exception:
            return

    async def _await_with_cancellation(
        self,
        awaitable: Awaitable[object],
        cancellation: CancellationToken,
    ) -> object:
        handler_task = asyncio.ensure_future(awaitable)
        cancellation_task = asyncio.create_task(cancellation.wait_cancelled())
        try:
            done, _ = await asyncio.wait(
                {handler_task, cancellation_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancellation_task in done or cancellation.is_cancelled:
                await self._drain_cancelled_task(handler_task)
                raise asyncio.CancelledError

            await self._drain_cancelled_task(cancellation_task)
            result = await handler_task
            cancellation.raise_if_cancelled()
            return result
        except asyncio.CancelledError:
            await self._drain_cancelled_task(handler_task)
            await self._drain_cancelled_task(cancellation_task)
            raise

    @staticmethod
    def _raise_if_cancelling(cancellation: CancellationToken) -> None:
        cancellation.raise_if_cancelled()
        current_task = asyncio.current_task()
        if current_task is not None and current_task.cancelling():
            raise asyncio.CancelledError

    async def _emit(
        self,
        *,
        qa_request_id: str,
        model: QaModelSelection,
        status: str,
        cancellation: CancellationToken,
    ) -> None:
        self._raise_if_cancelling(cancellation)
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
                await self._await_with_cancellation(result, cancellation)
            self._raise_if_cancelling(cancellation)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Observability is best-effort and must not mask typed API failures.
            self._raise_if_cancelling(cancellation)
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
                cancellation=cancellation,
            )
            cancellation.raise_if_cancelled()

            handler = self._handler_factory(model)
            if inspect.isawaitable(handler):
                handler = await self._await_with_cancellation(handler, cancellation)

            cancellation.raise_if_cancelled()
            await self._emit(
                qa_request_id=qa_request_id,
                model=model,
                status="request_started",
                cancellation=cancellation,
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
                raw_response = await self._await_with_cancellation(
                    raw_response, cancellation
                )
            cancellation.raise_if_cancelled()
            if not isinstance(raw_response, str):
                raw_response = None
                raise QaResponseSchemaError("QA handler result must be text")

            parse_failed = False
            try:
                parsed = parse_single_json_object(raw_response)
            except QaResponseSchemaError:
                parse_failed = True
            if parse_failed:
                raw_response = None
                raise QaResponseSchemaError(
                    "QA handler response failed strict JSON validation"
                ) from None
            cancellation.raise_if_cancelled()
            await self._emit(
                qa_request_id=qa_request_id,
                model=model,
                status="completed",
                cancellation=cancellation,
            )
            cancellation.raise_if_cancelled()
            return parsed
        except asyncio.CancelledError:
            await self._emit(
                qa_request_id=qa_request_id,
                model=model,
                status="cancelled",
                cancellation=cancellation,
            )
            raise
        except Exception:
            await self._emit(
                qa_request_id=qa_request_id,
                model=model,
                status="failed",
                cancellation=cancellation,
            )
            raise
