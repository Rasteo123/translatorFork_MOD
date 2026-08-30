"""Behavioral tests for the existing-handler QA completion adapter."""

import asyncio
import inspect

import pytest

from gemini_translator.qa.llm import (
    CancellationToken,
    ExistingHandlerCompletionClient,
    QaModelSelection,
    QaResponseSchemaError,
)


class TypedApiError(RuntimeError):
    """Representative typed provider failure that must pass through unchanged."""


class _Handler:
    def __init__(self, outcome: object, calls: list[dict[str, object]]) -> None:
        self._outcome = outcome
        self._calls = calls

    def execute_api_call(self, prompt, log_prefix, **kwargs):
        self._calls.append(
            {"prompt": prompt, "log_prefix": log_prefix, "kwargs": kwargs}
        )
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        if callable(self._outcome):
            return self._outcome()
        return self._outcome


def test_adapter_uses_selected_handler_and_existing_execution_lifecycle():
    """Bypassing execute_api_call would duplicate or omit handler retry behavior."""
    factory_calls: list[QaModelSelection] = []
    execute_calls: list[dict[str, object]] = []
    events: list[dict[str, object]] = []

    def factory(selection):
        factory_calls.append(selection)
        return _Handler('{"decision":"covered"}', execute_calls)

    client = ExistingHandlerCompletionClient(
        handler_factory=factory,
        event_sink=events.append,
    )
    selection = QaModelSelection(provider="google", model="gemini-test")

    result = asyncio.run(
        client.complete_json(
            "private prompt",
            model=selection,
            max_output_tokens=321,
            cancellation=CancellationToken(),
        )
    )

    assert result == {"decision": "covered"}
    assert factory_calls == [selection]
    assert len(execute_calls) == 1
    call = execute_calls[0]
    assert call["prompt"] == "private prompt"
    assert str(call["log_prefix"]).startswith("[QA:")
    assert call["kwargs"] == {
        "allow_incomplete": False,
        "use_stream": False,
        "max_output_tokens": 321,
    }
    assert [event["status"] for event in events] == [
        "creating_handler",
        "request_started",
        "completed",
    ]
    assert all(
        set(event) == {"qa_request_id", "provider", "model", "status"}
        for event in events
    )
    assert all("private prompt" not in repr(event) for event in events)
    assert len({event["qa_request_id"] for event in events}) == 1


def test_adapter_supports_async_factory_and_async_execute_result():
    """Async-native handlers must use the same adapter contract as sync handlers."""
    execute_calls: list[dict[str, object]] = []

    async def result():
        return '```json\n{"ok":true}\n```'

    async def factory(selection):
        assert selection == QaModelSelection(provider="openai", model="model-1")
        return _Handler(result, execute_calls)

    client = ExistingHandlerCompletionClient(factory, event_sink=None)
    completed = asyncio.run(
        client.complete_json(
            "prompt",
            model=QaModelSelection(provider="openai", model="model-1"),
            max_output_tokens=10,
            cancellation=CancellationToken(),
        )
    )

    assert completed == {"ok": True}
    assert len(execute_calls) == 1


def test_typed_api_error_passes_through_and_raw_response_is_not_emitted():
    """Turning API failures into empty JSON would weaken fail-closed decisions."""
    error = TypedApiError("secret provider response")
    events: list[dict[str, object]] = []
    client = ExistingHandlerCompletionClient(
        lambda selection: _Handler(error, []),
        event_sink=events.append,
    )

    with pytest.raises(TypedApiError) as raised:
        asyncio.run(
            client.complete_json(
                "secret prompt",
                model=QaModelSelection(provider="google", model="model-1"),
                max_output_tokens=100,
                cancellation=CancellationToken(),
            )
        )

    assert raised.value is error
    assert events[-1]["status"] == "failed"
    assert "secret provider response" not in repr(events)
    assert "secret prompt" not in repr(events)


def test_invalid_handler_json_is_removed_from_adapter_error_traceback():
    """The adapter frame must not retain a rejected raw provider response."""
    marker = "raw-handler-secret"
    client = ExistingHandlerCompletionClient(
        lambda selection: _Handler(f'{{"secret":"{marker}", invalid}}', []),
        event_sink=None,
    )

    with pytest.raises(QaResponseSchemaError) as raised:
        asyncio.run(
            client.complete_json(
                "prompt",
                model=QaModelSelection(provider="google", model="model-1"),
                max_output_tokens=100,
                cancellation=CancellationToken(),
            )
        )

    error = raised.value
    assert error.__cause__ is None
    assert error.__context__ is None
    assert marker not in str(error)
    traceback = error.__traceback__
    while traceback is not None:
        if traceback.tb_frame.f_code.co_filename.endswith("completion.py"):
            assert marker not in repr(traceback.tb_frame.f_locals)
        traceback = traceback.tb_next


def test_precancelled_request_never_creates_handler():
    """Creating a handler after cancellation could allocate network resources."""
    factory_calls = 0

    def factory(selection):
        nonlocal factory_calls
        factory_calls += 1
        return _Handler("{}", [])

    token = CancellationToken()
    token.cancel()
    client = ExistingHandlerCompletionClient(factory, event_sink=None)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            client.complete_json(
                "prompt",
                model=QaModelSelection(provider="google", model="model-1"),
                max_output_tokens=100,
                cancellation=token,
            )
        )

    assert factory_calls == 0


def test_callback_backed_cancellation_never_creates_handler():
    """A worker-backed callback must bridge existing cancellation into QA."""
    factory_calls = 0

    def factory(selection):
        nonlocal factory_calls
        factory_calls += 1
        return _Handler("{}", [])

    client = ExistingHandlerCompletionClient(factory, event_sink=None)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            client.complete_json(
                "prompt",
                model=QaModelSelection(provider="google", model="model-1"),
                max_output_tokens=100,
                cancellation=CancellationToken(is_cancelled=lambda: True),
            )
        )

    assert factory_calls == 0


def test_cancellation_is_rechecked_immediately_before_network_call():
    """Cancellation during handler creation must stop before execute_api_call."""
    execute_calls: list[dict[str, object]] = []
    token = CancellationToken()

    async def factory(selection):
        token.cancel()
        return _Handler("{}", execute_calls)

    client = ExistingHandlerCompletionClient(factory, event_sink=None)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            client.complete_json(
                "prompt",
                model=QaModelSelection(provider="google", model="model-1"),
                max_output_tokens=100,
                cancellation=token,
            )
        )

    assert execute_calls == []


def test_cancellation_after_creating_event_stops_before_factory():
    """Cancellation during observability must be checked directly before factory."""
    factory_calls = 0
    token = CancellationToken()

    async def event_sink(event):
        if event["status"] == "creating_handler":
            token.cancel()

    def factory(selection):
        nonlocal factory_calls
        factory_calls += 1
        return _Handler("{}", [])

    client = ExistingHandlerCompletionClient(factory, event_sink=event_sink)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            client.complete_json(
                "prompt",
                model=QaModelSelection(provider="google", model="model-1"),
                max_output_tokens=100,
                cancellation=token,
            )
        )

    assert factory_calls == 0


def test_cancellation_after_lifecycle_event_still_stops_network_call():
    """The final cancellation check must be adjacent to execute_api_call."""
    execute_calls: list[dict[str, object]] = []
    token = CancellationToken()

    async def event_sink(event):
        if event["status"] == "request_started":
            token.cancel()

    client = ExistingHandlerCompletionClient(
        lambda selection: _Handler("{}", execute_calls),
        event_sink=event_sink,
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            client.complete_json(
                "prompt",
                model=QaModelSelection(provider="google", model="model-1"),
                max_output_tokens=100,
                cancellation=token,
            )
        )

    assert execute_calls == []


def test_local_cancel_interrupts_inflight_async_handler():
    """Local cancellation must interrupt, not merely post-check, an in-flight call."""
    async def scenario():
        started = asyncio.Event()
        handler_cancelled = asyncio.Event()

        async def blocked_result():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                handler_cancelled.set()

        token = CancellationToken()
        client = ExistingHandlerCompletionClient(
            lambda selection: _Handler(blocked_result, []),
            event_sink=None,
        )
        request = asyncio.create_task(
            client.complete_json(
                "prompt",
                model=QaModelSelection(provider="google", model="model-1"),
                max_output_tokens=100,
                cancellation=token,
            )
        )
        await started.wait()
        token.cancel()

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(request, timeout=0.2)
        assert handler_cancelled.is_set()

    asyncio.run(scenario())


def test_sync_result_cannot_succeed_after_token_is_cancelled():
    """A synchronous handler result must be post-checked before parsing."""
    token = CancellationToken()

    def result():
        token.cancel()
        return '{"ok":true}'

    client = ExistingHandlerCompletionClient(
        lambda selection: _Handler(result, []),
        event_sink=None,
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            client.complete_json(
                "prompt",
                model=QaModelSelection(provider="google", model="model-1"),
                max_output_tokens=100,
                cancellation=token,
            )
        )


def test_cancellation_during_completed_event_prevents_success_return():
    """The last lifecycle await must not reopen a success race."""
    token = CancellationToken()

    async def event_sink(event):
        if event["status"] == "completed":
            token.cancel()

    client = ExistingHandlerCompletionClient(
        lambda selection: _Handler('{"ok":true}', []),
        event_sink=event_sink,
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            client.complete_json(
                "prompt",
                model=QaModelSelection(provider="google", model="model-1"),
                max_output_tokens=100,
                cancellation=token,
            )
        )


def test_asyncio_cancellation_from_handler_is_not_swallowed():
    """Native task cancellation must retain asyncio cancellation semantics."""
    async def cancelled():
        raise asyncio.CancelledError

    client = ExistingHandlerCompletionClient(
        lambda selection: _Handler(cancelled, []),
        event_sink=None,
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            client.complete_json(
                "prompt",
                model=QaModelSelection(provider="google", model="model-1"),
                max_output_tokens=100,
                cancellation=CancellationToken(),
            )
        )


@pytest.mark.parametrize("result", [None, {}, b"{}", 3])
def test_handler_result_must_be_a_json_string(result):
    """Coercing non-text handler output could accept an invalid provider contract."""
    client = ExistingHandlerCompletionClient(
        lambda selection: _Handler(result, []),
        event_sink=None,
    )

    with pytest.raises(QaResponseSchemaError):
        asyncio.run(
            client.complete_json(
                "prompt",
                model=QaModelSelection(provider="google", model="model-1"),
                max_output_tokens=100,
                cancellation=CancellationToken(),
            )
        )


@pytest.mark.parametrize(
    ("provider", "model"),
    [("", "model"), ("google", " "), (1, "model"), ("google", None)],
)
def test_model_selection_requires_nonempty_provider_and_model(provider, model):
    """An invalid selection must fail before handler routing."""
    with pytest.raises(ValueError):
        QaModelSelection(provider=provider, model=model)


def test_event_sink_may_be_async_without_receiving_sensitive_payloads():
    """Async observability must remain metadata-only."""
    events: list[dict[str, object]] = []

    async def sink(event):
        assert not inspect.isawaitable(event)
        events.append(event)

    client = ExistingHandlerCompletionClient(
        lambda selection: _Handler('{"answer":"raw-secret"}', []),
        event_sink=sink,
    )

    asyncio.run(
        client.complete_json(
            "prompt-secret",
            model=QaModelSelection(provider="google", model="model-1"),
            max_output_tokens=100,
            cancellation=CancellationToken(),
        )
    )

    assert events
    assert "prompt-secret" not in repr(events)
    assert "raw-secret" not in repr(events)
