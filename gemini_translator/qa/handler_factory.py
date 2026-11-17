"""Create API handlers for QA requests without borrowing a translation worker."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from copy import deepcopy
import inspect
import threading
import time

from ..utils.helpers import safe_int
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
        # QA also runs inside translation sessions; its tokens are not translation.
        self.token_usage_operation = "quality_check"
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
        self.workascii_workspace_index = safe_int(
            settings.get("workascii_workspace_index", 1), 1, 1
        )
        self.workascii_timeout_sec = safe_int(
            settings.get("workascii_timeout_sec", 1800), 1800, 60
        )
        self.workascii_headless = bool(settings.get("workascii_headless", False))
        self.workascii_profile_template_dir = str(
            settings.get("workascii_profile_template_dir", "") or ""
        ).strip()
        self.workascii_refresh_every_requests = safe_int(
            settings.get("workascii_refresh_every_requests", 0), 0, 0
        )
        self.debug_logging_enabled = bool(settings.get("debug_logging_enabled", False))
        self.debug_operation_filters = str(
            settings.get("debug_operation_filters", "") or ""
        ).strip()
        self.debug_max_log_mb = safe_int(settings.get("debug_max_log_mb", 128), 128, 1)
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


# How long one request may wait for a paused key before it is refused.  A key
# paused for a minute is worth waiting for; one paused for an hour is not, and
# the chapter is better deferred than held for that long.
MAX_KEY_WAIT_SECONDS = 120.0

# How long the same failure of the service stays unrepeated in the log.  An
# overloaded server answers every twenty seconds for hours, and a line each
# time would push the chapters themselves out of the quality window's journal.
SERVER_ERROR_REPEAT_SECONDS = 300.0
# Where a check that keeps failing can be moved to another model.
CHANGE_MODEL_HINT = (
    "Сменить модель можно во вкладке «Настройки» → «Модель проверки», "
    "затем «Остановить» и «Продолжить проверку»."
)
# The first line of a run of failures, and the line that reminds of it.
_FAILURE_HEADLINES = {
    "server": ("Сервер отвечает ошибкой", "Сервер всё ещё отвечает ошибкой"),
    "keys": ("Ключи проверки на паузе", "Ключи проверки всё ещё на паузе"),
}
_FAILURE_TEXT_LIMIT = 300


class ServerErrorNotices:
    """Say that the service keeps failing: at once, now and then, and when it is back.

    A request the retry loop keeps asking used to fail in silence: on 14.09 two
    windows sat on their first chapter for half an hour with nothing to show
    why.  One instance serves every request of a check, so a failure is not
    told again by each request that meets it.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = 0
        self._last_said: tuple[str, float] | None = None

    def failure(self, kind: str, detail: object, model: str) -> str | None:
        """The line for one more failed request, or None when it was said just now."""
        first, again = _FAILURE_HEADLINES[kind]
        text = _sentence(detail)
        with self._lock:
            self._failures += 1
            failures = self._failures
            now = self._clock()
            said = f"{kind}:{text}"
            previous = self._last_said
            if (
                previous is not None
                and previous[0] == said
                and now - previous[1] < SERVER_ERROR_REPEAT_SECONDS
            ):
                return None
            self._last_said = (said, now)
        if previous is None:
            named = f" Модель: {model}." if model else ""
            return f"{first}: {text}{named} {CHANGE_MODEL_HINT}"
        return f"{again}: {text} Неудачных попыток подряд: {failures}."

    def success(self, model: str) -> str | None:
        """The line for an answer after failures, or None when nothing had failed."""
        with self._lock:
            failures = self._failures
            self._failures = 0
            self._last_said = None
        if not failures:
            return None
        named = f" (модель {model})" if model else ""
        return f"Сервер снова отвечает{named}: неудачных попыток подряд было {failures}."


def _sentence(detail: object) -> str:
    """An error's words on one line, short enough for the log, ending as a sentence."""
    text = " ".join(str(detail or "").split())
    if len(text) > _FAILURE_TEXT_LIMIT:
        text = text[: _FAILURE_TEXT_LIMIT - 1].rstrip() + "…"
    if text and text[-1] not in ".!?…":
        text += "."
    return text


def _is_service_trouble(error: BaseException) -> bool:
    """A failure the service may get over by itself: a timeout, or one naming a pause."""
    return isinstance(error, TimeoutError) or _requested_delay(error, default=0.0) > 0


class RotatingQaHandler:
    """Answer one request with whichever key of the pool is ready for it.

    A real handler is built per attempt around one key and closed after it,
    success or failure: the handlers were written for a worker that keeps its
    session for the whole run, and a check that builds one per request and
    never closes it leaked a session and a connector on every question.

    A key the service declares spent is dropped and the next one asked the
    same question.  A key the service asks to rest is asked again after the
    pause it named — the way a translation worker waits on its own key — and
    only a second refusal in a row rests it and moves on.  Measured on a live
    book: hopping to the next key on every refusal asked all 150 keys within
    two minutes, and an hour later the accounts behind them were disabled.
    One request never waits longer than :data:`MAX_KEY_WAIT_SECONDS` in total.
    """

    def __init__(
        self,
        pool,
        make_handler: Callable[[str], object],
        *,
        log: Callable[[str], None] | None = None,
        sleep=None,
        max_wait_seconds: float = MAX_KEY_WAIT_SECONDS,
        close_handler: Callable[[object], object] | None = None,
        notices: ServerErrorNotices | None = None,
        model_name: str = "",
    ) -> None:
        if not callable(make_handler):
            raise TypeError("make_handler must be callable")
        self._pool = pool
        self._make_handler = make_handler
        self._log = log if callable(log) else None
        self._sleep = sleep if callable(sleep) else asyncio.sleep
        self._max_wait = max(0.0, float(max_wait_seconds))
        self._close_handler = close_handler
        self._notices = notices if notices is not None else ServerErrorNotices()
        self._model_name = str(model_name or "")

    async def execute_api_call(self, prompt, log_prefix, **kwargs):
        from ..api.errors import ApiAccessError, RateLimitExceededError, TemporaryRateLimitError

        waited = 0.0
        last_error: BaseException | None = None
        while True:
            key = self._pool.acquire()
            if key is None:
                if self._pool.blocked_reason is not None:
                    raise QaHandlerError(self._pool.blocked_reason) from last_error
                wait = self._pool.seconds_until_available()
                if wait is None or waited >= self._max_wait:
                    refusal = self._refusal(last_error)
                    if getattr(last_error, "delay_seconds", None):
                        self._say_line(
                            self._notices.failure("keys", refusal, self._model_name)
                        )
                    raise QaHandlerError(refusal) from last_error
                pause = min(max(float(wait), 0.5), self._max_wait - waited)
                waited += pause
                await self._sleep(pause)
                continue
            handler = self._make_handler(key)
            try:
                result = handler.execute_api_call(prompt, log_prefix, **kwargs)
                if inspect.isawaitable(result):
                    result = await result
                self._pool.note_success(key)
                self._say_line(self._notices.success(self._model_name))
                return result
            except ApiAccessError as error:
                reason = f"QA остановлена: сервис отказал в доступе. {error}"
                self._pool.block(reason)
                self._say(f"[QA] {reason}")
                raise QaHandlerError(reason) from error
            except RateLimitExceededError as error:
                last_error = error
                self._pool.mark_exhausted(key)
                self._say(
                    f"[QA] Ключ …{key[-4:]} исчерпан, проверка переходит на "
                    f"следующий: {error}"
                )
            except TemporaryRateLimitError as error:
                last_error = error
                delay = _requested_delay(error, default=60.0)
                if self._pool.note_throttled(key, delay):
                    self._say(
                        f"[QA] Ключ …{key[-4:]} отдыхает {delay:.0f} с по просьбе "
                        "сервиса, проверка берёт следующий."
                    )
                    continue
                self._say(
                    f"[QA] Ключ …{key[-4:]} просит подождать {delay:.0f} с, "
                    "проверка ждёт на нём."
                )
                # The pool owns the full deadline for every caller. The next
                # loop waits within this request's budget; expiry never makes
                # the key available before the server's deadline.
            except Exception as error:
                # What happens next is the retry loop's decision; this only
                # makes sure a service that keeps failing does not fail in silence.
                if _is_service_trouble(error):
                    self._say_line(
                        self._notices.failure("server", error, self._model_name)
                    )
                raise
            finally:
                await self._close(handler)

    @staticmethod
    def _refusal(error: BaseException | None) -> str:
        if error is None:
            return "У проверки не осталось рабочих ключей."
        if getattr(error, "delay_seconds", None):
            return f"Сервис просит ждать дольше, чем может одна проверка: {error}"
        return f"У проверки не осталось рабочих ключей: {error}"

    def _say_line(self, line: str | None) -> None:
        if line:
            self._say(f"[QA] {line}")

    def _say(self, message: str) -> None:
        if self._log is None:
            return
        try:
            self._log(message)
        except Exception:  # noqa: BLE001 - logging must never fail a request
            return

    async def _close(self, handler) -> None:
        closer = self._close_handler or _close_handler_session
        try:
            result = closer(handler)
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001 - a session that will not close is not an error
            return


def _requested_delay(error: BaseException, *, default: float) -> float:
    value = getattr(error, "delay_seconds", None)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return default
    return float(value)


def _close_handler_session(handler):
    """Close whatever session a handler opened, by whichever name it uses."""
    for name in ("_close_thread_session_internal", "aclose", "close"):
        closer = getattr(handler, name, None)
        if callable(closer):
            return closer()
    return None


def build_qa_handler_factory(
    *,
    settings_manager,
    api_key_for: Callable[[str], str] | None = None,
    key_pool=None,
    session_settings: Mapping[str, object] | None = None,
    cancellation=None,
    log: Callable[[str], None] | None = None,
) -> Callable[[QaModelSelection], object]:
    """Return a factory that creates one configured handler per QA model.

    With ``key_pool`` every request is answered by a :class:`RotatingQaHandler`
    that spends the pool's keys in turn.  Without one, ``api_key_for(provider)``
    supplies the single key; QA never reads or stores keys of its own.
    """

    if key_pool is None and not callable(api_key_for):
        raise TypeError("api_key_for or key_pool is required")

    def resolve(model: QaModelSelection) -> tuple[dict, dict]:
        from ..api import config as api_config

        if not isinstance(model, QaModelSelection):
            raise QaHandlerError("model must be a QaModelSelection")
        providers = api_config.api_providers_view()
        provider_config = providers.get(model.provider)
        if not isinstance(provider_config, Mapping):
            raise QaHandlerError(f"Unknown QA provider: {model.provider}")
        provider_config = deepcopy(dict(provider_config))
        model_name = api_config.canonical_model_name(model.provider, model.model)
        model_config = _model_config(provider_config, model_name)
        # A provider view keeps the provider only as its key. Translation models get
        # it from all_models_view; QA must name it too, or its token usage is
        # published with no provider.
        model_config.setdefault("provider", model.provider)
        return provider_config, model_config

    def build(model: QaModelSelection, provider_config, model_config, api_key: str):
        from ..api.factory import get_api_handler_class

        api_key = str(api_key or "")
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

    # One for the whole check: every request builds its own handler, and the
    # same failure must not be told again by each request that meets it.
    notices = ServerErrorNotices()

    def factory(model: QaModelSelection):
        provider_config, model_config = resolve(model)
        if key_pool is not None:
            return RotatingQaHandler(
                key_pool,
                lambda key: build(model, provider_config, model_config, key),
                log=log,
                notices=notices,
                model_name=model.model,
            )
        return build(model, provider_config, model_config, api_key_for(model.provider))

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
