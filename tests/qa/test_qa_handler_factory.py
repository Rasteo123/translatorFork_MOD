"""QA builds its own API handlers and never borrows a translation worker."""

from __future__ import annotations

import pytest

from gemini_translator.qa.handler_factory import (
    QaHandlerError,
    QaHandlerWorker,
    build_qa_handler_factory,
)
from gemini_translator.qa.llm import CancellationToken, QaModelSelection


class _Handler:
    created: list["_Handler"] = []

    def __init__(self, worker):
        self.worker = worker
        self.setup_calls = []
        _Handler.created.append(self)

    def setup_client(self, client_override=None, proxy_settings=None):
        self.setup_calls.append((client_override, proxy_settings))
        return True


class _FailingHandler(_Handler):
    def setup_client(self, client_override=None, proxy_settings=None):
        return False


_PROVIDERS = {
    "gemini": {
        "handler_class": "GeminiHandler",
        "file_suffix": "_ru",
        "models": {"Gemini Flash": {"id": "gemini-flash-001"}},
    }
}


@pytest.fixture(autouse=True)
def _patched_api(monkeypatch):
    from gemini_translator.api import config as api_config
    from gemini_translator.api import factory as api_factory

    monkeypatch.setattr(api_config, "api_providers_view", lambda: _PROVIDERS)
    monkeypatch.setattr(api_factory, "get_api_handler_class", lambda name: _Handler)
    _Handler.created.clear()


def _factory(**overrides):
    values = {
        "settings_manager": object(),
        "api_key_for": lambda provider: "secret-key",
        "session_settings": {"proxy_settings": {"http": "proxy"}},
    }
    values.update(overrides)
    return build_qa_handler_factory(**values)


def test_handler_is_created_and_configured_for_the_selected_model():
    """A QA request must reach the provider the user chose for corrections."""
    handler = _factory()(QaModelSelection("gemini", "Gemini Flash"))

    assert isinstance(handler, _Handler)
    assert handler.worker.model_id == "gemini-flash-001"
    assert handler.worker.provider_config["handler_class"] == "GeminiHandler"
    assert handler.setup_calls[0][1] == {"http": "proxy"}
    assert handler.setup_calls[0][0].api_key == "secret-key"


def test_a_model_may_be_selected_by_its_api_id():
    """Settings store an id; the picker stores a display name. Both must work."""
    handler = _factory()(QaModelSelection("gemini", "gemini-flash-001"))

    assert handler.worker.model_id == "gemini-flash-001"


def test_missing_provider_or_key_is_refused_clearly():
    """QA must fail with a typed error instead of half-configuring a handler."""
    with pytest.raises(QaHandlerError):
        _factory()(QaModelSelection("unknown", "model"))
    with pytest.raises(QaHandlerError):
        _factory(api_key_for=lambda provider: "")(
            QaModelSelection("gemini", "Gemini Flash")
        )


def test_failed_client_setup_is_refused(monkeypatch):
    """A handler that could not connect must never be handed to QA."""
    from gemini_translator.api import factory as api_factory

    monkeypatch.setattr(api_factory, "get_api_handler_class", lambda name: _FailingHandler)

    with pytest.raises(QaHandlerError):
        _factory()(QaModelSelection("gemini", "Gemini Flash"))


def test_worker_shim_reports_cancellation_and_stays_single_request():
    """QA must not inherit a translation worker's concurrency or queue."""
    token = CancellationToken()
    worker = QaHandlerWorker(
        settings_manager=object(),
        provider_config={"handler_class": "H"},
        model_config={"id": "m"},
        api_key="key",
        cancellation=token,
    )

    assert worker.max_concurrent_requests == 1
    assert worker.is_cancelled is False
    token.cancel()
    assert worker.is_cancelled is True
    with pytest.raises(Exception):
        worker.check_cancellation()
