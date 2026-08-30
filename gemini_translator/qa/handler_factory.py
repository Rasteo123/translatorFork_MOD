"""Create API handlers for QA requests without borrowing a translation worker."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy

from .llm.completion import QaModelSelection


class QaHandlerError(RuntimeError):
    """Raised when no API handler can be created for a QA request."""


class _QaKeyHolder:
    """The minimal client identity an API handler asks for during setup."""

    def __init__(self, key: str) -> None:
        self.api_key = key
        self.worker_id = key


class _QaPromptBuilder:
    """Handlers read a system instruction off the worker; QA never sets one."""

    def __init__(self) -> None:
        self.system_instruction = None


class QaHandlerWorker:
    """A worker-shaped object an API handler can be constructed around.

    Handlers were written against the translation worker. QA is not a worker and
    must not own a translation queue, so this exposes only the attributes a
    handler reads, with conservative single-request defaults.
    """

    def __init__(
        self,
        *,
        settings_manager,
        provider_config: Mapping[str, object],
        model_config: Mapping[str, object],
        api_key: str,
        session_settings: Mapping[str, object] | None = None,
        cancellation=None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        settings = dict(session_settings or {})
        self.settings_manager = settings_manager
        self.session_id = "translation_qa"
        self.provider_config = dict(provider_config or {})
        self.model_config = dict(model_config or {})
        self.api_key = api_key
        self.worker_id = api_key
        self.model_id = str(self.model_config.get("id") or "")
        self.temperature = settings.get("temperature", 0.3)
        self.temperature_override_enabled = bool(
            settings.get("temperature_override_enabled", True)
        )
        self.thinking_enabled = bool(settings.get("thinking_enabled", False))
        self.thinking_budget = settings.get("thinking_budget", 0)
        self.thinking_level = settings.get("thinking_level", "minimal")
        self.max_concurrent_requests = 1
        self.proxy_settings = settings.get("proxy_settings")
        self.workascii_workspace_name = str(
            settings.get("workascii_workspace_name", "") or ""
        ).strip()
        self.workascii_workspace_index = _safe_int(
            settings.get("workascii_workspace_index", 1), 1, 1
        )
        self.workascii_timeout_sec = _safe_int(
            settings.get("workascii_timeout_sec", 1800), 1800, 60
        )
        self.workascii_headless = bool(settings.get("workascii_headless", False))
        self.workascii_profile_template_dir = str(
            settings.get("workascii_profile_template_dir", "") or ""
        ).strip()
        self.workascii_refresh_every_requests = _safe_int(
            settings.get("workascii_refresh_every_requests", 0), 0, 0
        )
        self.debug_logging_enabled = bool(settings.get("debug_logging_enabled", False))
        self.debug_operation_filters = str(
            settings.get("debug_operation_filters", "") or ""
        ).strip()
        self.debug_max_log_mb = _safe_int(settings.get("debug_max_log_mb", 128), 128, 1)
        self.prompt_builder = _QaPromptBuilder()
        self._cancellation = cancellation
        self._log = log

    @property
    def is_cancelled(self) -> bool:
        cancellation = self._cancellation
        return bool(cancellation is not None and cancellation.is_cancelled)

    @is_cancelled.setter
    def is_cancelled(self, value) -> None:
        # Handlers assign this on their own paths; QA owns cancellation itself.
        return

    def check_cancellation(self) -> None:
        if self.is_cancelled:
            from ..api.errors import OperationCancelledError

            raise OperationCancelledError("Cancelled by user")

    def _post_event(self, name: str, data: dict | None = None) -> None:
        if name != "log_message" or not callable(self._log):
            return
        message = (data or {}).get("message", "")
        if message:
            self._log(str(message))


def build_qa_handler_factory(
    *,
    settings_manager,
    api_key_for,
    session_settings: Mapping[str, object] | None = None,
    cancellation=None,
    log: Callable[[str], None] | None = None,
) -> Callable[[QaModelSelection], object]:
    """Return a factory that creates one configured handler per QA model.

    ``api_key_for(provider_id)`` supplies the key; QA never reads or stores keys
    of its own.
    """

    if not callable(api_key_for):
        raise TypeError("api_key_for must be callable")

    def factory(model: QaModelSelection):
        from ..api import config as api_config
        from ..api.factory import get_api_handler_class

        if not isinstance(model, QaModelSelection):
            raise QaHandlerError("model must be a QaModelSelection")
        providers = api_config.api_providers_view()
        provider_config = providers.get(model.provider)
        if not isinstance(provider_config, Mapping):
            raise QaHandlerError(f"Unknown QA provider: {model.provider}")
        provider_config = deepcopy(dict(provider_config))
        model_config = _model_config(provider_config, model.model)
        api_key = str(api_key_for(model.provider) or "")
        if not api_key:
            raise QaHandlerError(f"No API key configured for {model.provider}")

        handler_class = get_api_handler_class(provider_config.get("handler_class"))
        worker = QaHandlerWorker(
            settings_manager=settings_manager,
            provider_config=provider_config,
            model_config=model_config,
            api_key=api_key,
            session_settings=session_settings,
            cancellation=cancellation,
            log=log,
        )
        handler = handler_class(worker)
        proxy_settings = (session_settings or {}).get("proxy_settings")
        if not handler.setup_client(_QaKeyHolder(api_key), proxy_settings=proxy_settings):
            raise QaHandlerError(
                f"Failed to initialize the QA API handler for {model.provider}"
            )
        return handler

    return factory


def _model_config(provider_config: Mapping[str, object], model_name: str) -> dict:
    """Find one model by its display name or by its API id, in that order."""
    models = provider_config.get("models")
    if isinstance(models, Mapping):
        for name, config in models.items():
            if not isinstance(config, Mapping):
                continue
            if name == model_name or str(config.get("id", "")) == model_name:
                resolved = deepcopy(dict(config))
                resolved.setdefault("id", model_name)
                return resolved
    return {"id": model_name}


def _safe_int(value: object, default: int, minimum: int) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, parsed)
