import asyncio
import time
from types import SimpleNamespace

import pytest

from gemini_translator.api import config as api_config
from gemini_translator.api.errors import (
    ModelNotFoundError,
    NetworkError,
    RateLimitExceededError,
    ValidationFailedError,
)
from gemini_translator.api.factory import get_api_handler_class
from gemini_translator.api.handlers import qoder as _qoder_module
from gemini_translator.api.handlers.qoder import QoderApiHandler


def _install_sdk_stubs_if_needed():
    """Тесты проверяют обработчик, а не SDK: если qoder_agent_sdk недоступен
    или неполон (CI, машины без пакета), подставляем минимальные заглушки
    в модуль обработчика. При рабочем SDK ничего не подменяется."""
    class _StubRecord:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class RateLimitInfo(_StubRecord):
        pass

    class RateLimitEvent(_StubRecord):
        pass

    _qoder_module._ensure_sdk_imported()
    if _qoder_module.RateLimitInfo is None:
        _qoder_module.RateLimitInfo = RateLimitInfo
    if _qoder_module.RateLimitEvent is None:
        _qoder_module.RateLimitEvent = RateLimitEvent
    if _qoder_module.QoderAgentOptions is not None:
        return

    class QoderAgentOptions(_StubRecord):
        pass

    class ResultMessage(_StubRecord):
        pass

    class AssistantMessage(_StubRecord):
        pass

    class TextBlock(_StubRecord):
        pass

    class QoderSDKError(Exception):
        pass

    class CLIConnectionError(QoderSDKError):
        pass

    class CLINotFoundError(QoderSDKError):
        pass

    class ProcessError(QoderSDKError):
        pass

    class AuthNotConfiguredError(QoderSDKError):
        pass

    class AuthAccessTokenEnvVarError(QoderSDKError):
        pass

    def _unpatched_query(**_kwargs):
        raise AssertionError("query stub must be monkeypatched by the test")

    stubs = {
        "QoderAgentOptions": QoderAgentOptions,
        "ResultMessage": ResultMessage,
        "RateLimitInfo": RateLimitInfo,
        "RateLimitEvent": RateLimitEvent,
        "AssistantMessage": AssistantMessage,
        "TextBlock": TextBlock,
        "QoderSDKError": QoderSDKError,
        "CLIConnectionError": CLIConnectionError,
        "CLINotFoundError": CLINotFoundError,
        "ProcessError": ProcessError,
        "AuthNotConfiguredError": AuthNotConfiguredError,
        "AuthAccessTokenEnvVarError": AuthAccessTokenEnvVarError,
        "access_token": lambda token: token,
        "query": _unpatched_query,
    }
    for name, value in stubs.items():
        setattr(_qoder_module, name, value)
    _qoder_module._AUTH_ERRORS = (AuthNotConfiguredError, AuthAccessTokenEnvVarError)
    _qoder_module._CLI_NOT_FOUND_ERRORS = (CLINotFoundError,)
    _qoder_module._SDK_TRANSPORT_ERRORS = (CLIConnectionError, ProcessError, QoderSDKError)


_install_sdk_stubs_if_needed()


class FakeSettingsManager:
    def increment_request_count(self, api_key, model_id):
        pass

    def decrement_request_count(self, api_key, model_id):
        pass


class FakeWorker:
    def __init__(self):
        self.provider_config = {"is_async": True, "base_timeout": 30}
        self.model_config = {
            "id": "auto",
            "provider": "qoder",
            "max_output_tokens": 8192,
        }
        self.settings_manager = FakeSettingsManager()
        self.api_key = "pat-before-setup"
        self.model_id = "auto"
        self.prompt_builder = SimpleNamespace(system_instruction="Translate faithfully.")
        self.temperature_override_enabled = False
        self.temperature = None
        self.debug_logging_enabled = False
        self.debug_operation_filters = None
        self.debug_max_log_mb = 128
        self.is_cancelled = False
        self.is_shutting_down = False
        self.sync_executor = None
        self.events = []

    def _post_event(self, event_name, payload):
        self.events.append((event_name, payload))

    def get_debug_operation_context(self):
        return {"surface": "unit-test"}


def _result_message(qoder_module, *, text="перевод", subtype="success", errors=None):
    return qoder_module.ResultMessage(
        subtype=subtype,
        duration_ms=10,
        duration_api_ms=8,
        is_error=subtype != "success",
        num_turns=1,
        session_id="session-1",
        result=text if subtype == "success" else None,
        errors=errors,
        usage={"input_tokens": 11, "output_tokens": 7},
    )


def test_qoder_handler_uses_pat_and_builds_tool_free_one_shot_request(monkeypatch):
    from gemini_translator.api.handlers import qoder as qoder_module

    worker = FakeWorker()
    handler = QoderApiHandler(worker)
    captured = {}

    async def fake_query(*, prompt, options):
        captured["prompt"] = prompt
        captured["options"] = options
        yield _result_message(qoder_module)

    monkeypatch.setattr(qoder_module, "query", fake_query)
    monkeypatch.setattr(qoder_module, "access_token", lambda token: ("pat", token))

    assert handler.setup_client(SimpleNamespace(api_key="qoder-pat")) is True
    result = asyncio.run(
        handler.call_api(
            "SOURCE TEXT",
            "[TEST]",
            use_stream=False,
            max_output_tokens=4096,
        )
    )

    assert result == "перевод"
    assert worker.api_key == "qoder-pat"
    assert worker.model_id == "auto"
    assert captured["prompt"] == "SOURCE TEXT"
    options = captured["options"]
    assert options.auth == ("pat", "qoder-pat")
    assert options.model == "auto"
    assert options.tools == []
    assert options.permission_mode == "dontAsk"
    assert options.max_turns == 1
    assert options.setting_sources == []
    assert options.system_prompt == "Translate faithfully."
    assert options.extra_args["max-output-tokens"] == "4096"


def test_qoder_handler_passes_configured_proxy(monkeypatch):
    from gemini_translator.api.handlers import qoder as qoder_module

    worker = FakeWorker()
    handler = QoderApiHandler(worker)
    captured = {}

    async def fake_query(*, prompt, options):
        captured["options"] = options
        yield _result_message(qoder_module)

    monkeypatch.setattr(qoder_module, "query", fake_query)
    monkeypatch.setattr(qoder_module, "access_token", lambda token: token)
    handler.setup_client(
        SimpleNamespace(api_key="qoder-pat"),
        proxy_settings={
            "enabled": True,
            "type": "SOCKS5",
            "host": "127.0.0.1",
            "port": 1080,
            "user": "name",
            "pass": "secret",
        },
    )

    asyncio.run(handler.call_api("text", "[TEST]"))

    assert captured["options"].proxy == "socks5://name:secret@127.0.0.1:1080"


def test_qoder_handler_rejects_empty_success_response(monkeypatch):
    from gemini_translator.api.handlers import qoder as qoder_module

    worker = FakeWorker()
    handler = QoderApiHandler(worker)

    async def fake_query(*, prompt, options):
        yield _result_message(qoder_module, text="   ")

    monkeypatch.setattr(qoder_module, "query", fake_query)
    monkeypatch.setattr(qoder_module, "access_token", lambda token: token)
    handler.setup_client(SimpleNamespace(api_key="qoder-pat"))

    with pytest.raises(ValidationFailedError, match="пустой ответ"):
        asyncio.run(handler.call_api("text", "[TEST]"))


def test_qoder_handler_maps_auth_failure_to_exhausted_key(monkeypatch):
    from gemini_translator.api.handlers import qoder as qoder_module

    worker = FakeWorker()
    handler = QoderApiHandler(worker)

    async def fake_query(*, prompt, options):
        yield _result_message(
            qoder_module,
            subtype="error_during_execution",
            errors=["authentication_failed: access token expired"],
        )

    monkeypatch.setattr(qoder_module, "query", fake_query)
    monkeypatch.setattr(qoder_module, "access_token", lambda token: token)
    handler.setup_client(SimpleNamespace(api_key="qoder-pat"))

    with pytest.raises(RateLimitExceededError, match="PAT"):
        asyncio.run(handler.call_api("text", "[TEST]"))


def test_qoder_handler_maps_sdk_transport_failure_to_network_error(monkeypatch):
    from gemini_translator.api.handlers import qoder as qoder_module

    worker = FakeWorker()
    handler = QoderApiHandler(worker)

    async def fake_query(*, prompt, options):
        if False:
            yield None
        raise qoder_module.CLIConnectionError("runtime disconnected")

    monkeypatch.setattr(qoder_module, "query", fake_query)
    monkeypatch.setattr(qoder_module, "access_token", lambda token: token)
    handler.setup_client(SimpleNamespace(api_key="qoder-pat"))

    with pytest.raises(NetworkError, match="Qoder"):
        asyncio.run(handler.call_api("text", "[TEST]"))


def test_qoder_handler_reports_missing_sdk_dependency(monkeypatch):
    from gemini_translator.api.handlers import qoder as qoder_module

    worker = FakeWorker()
    handler = QoderApiHandler(worker)
    monkeypatch.setattr(qoder_module, "QoderAgentOptions", None)

    with pytest.raises(ModelNotFoundError, match="qoder-agent-sdk"):
        handler.setup_client(SimpleNamespace(api_key="qoder-pat"))


def test_qoder_handler_maps_rejected_rate_limit_event(monkeypatch):
    from gemini_translator.api.handlers import qoder as qoder_module

    worker = FakeWorker()
    handler = QoderApiHandler(worker)

    async def fake_query(*, prompt, options):
        yield qoder_module.RateLimitEvent(
            rate_limit_info=qoder_module.RateLimitInfo(
                status="rejected",
                resets_at=int(time.time()) + 3600,
                rate_limit_type="five_hour",
            ),
            uuid="rate-1",
            session_id="session-1",
        )

    monkeypatch.setattr(qoder_module, "query", fake_query)
    monkeypatch.setattr(qoder_module, "access_token", lambda token: token)
    handler.setup_client(SimpleNamespace(api_key="qoder-pat"))

    with pytest.raises(RateLimitExceededError, match="лимит Qoder") as raised:
        asyncio.run(handler.call_api("text", "[TEST]"))

    assert getattr(raised.value, "retry_after_seconds") > 0


def test_qoder_handler_closes_query_when_cancelled(monkeypatch):
    from gemini_translator.api.handlers import qoder as qoder_module

    worker = FakeWorker()
    handler = QoderApiHandler(worker)
    started = asyncio.Event()
    closed = asyncio.Event()

    async def fake_query(*, prompt, options):
        try:
            started.set()
            await asyncio.Event().wait()
            if False:
                yield None
        finally:
            closed.set()

    monkeypatch.setattr(qoder_module, "query", fake_query)
    monkeypatch.setattr(qoder_module, "access_token", lambda token: token)
    handler.setup_client(SimpleNamespace(api_key="qoder-pat"))

    async def run_and_cancel():
        task = asyncio.create_task(handler.call_api("text", "[TEST]"))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(closed.wait(), timeout=1)

    asyncio.run(run_and_cancel())


def test_qoder_provider_config_and_factory_registration():
    api_config.initialize_configs()

    provider = api_config.api_providers()["qoder"]
    assert provider["visible"] is True
    assert provider["requires_api_key"] is True
    assert provider["is_async"] is True
    assert api_config.uses_legacy_worker_thread(provider) is True
    assert set(model["id"] for model in provider["models"].values()) == {
        "lite",
        "efficient",
        "auto",
        "performance",
        "ultimate",
        "Qwen3.7-Max",
        "Qwen3.8-Max",
        "Qwen3.7-Plus",
        "DeepSeek-V4-Pro",
        "DeepSeek-V4-Flash",
        "GLM-5.2",
        "Kimi-K2.7-Code",
        "MiniMax-M3",
    }
    assert get_api_handler_class(provider["handler_class"]) is QoderApiHandler
