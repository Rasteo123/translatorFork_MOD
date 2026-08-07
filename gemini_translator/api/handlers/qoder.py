import asyncio
import math
import tempfile
import time
from collections import deque
from urllib.parse import quote

from ..base import BaseApiHandler
from ..errors import (
    ModelNotFoundError,
    NetworkError,
    PartialGenerationError,
    RateLimitExceededError,
    TemporaryRateLimitError,
    ValidationFailedError,
)

# Ленивый импорт qoder_agent_sdk: он тянет пакеты mcp и aiohttp (~250 мс),
# поэтому загружается при первом обращении к Qoder-провайдеру, а не при
# импорте модуля (модуль импортируется при старте приложения через фабрику).
_SDK_NAMES = (
    "AssistantMessage",
    "AuthAccessTokenEnvVarError",
    "AuthNotConfiguredError",
    "CLIConnectionError",
    "CLINotFoundError",
    "ProcessError",
    "QoderAgentOptions",
    "QoderSDKError",
    "RateLimitEvent",
    "RateLimitInfo",
    "ResultMessage",
    "TextBlock",
    "access_token",
    "query",
)

AssistantMessage = None
AuthAccessTokenEnvVarError = None
AuthNotConfiguredError = None
CLIConnectionError = None
CLINotFoundError = None
ProcessError = None
QoderAgentOptions = None
QoderSDKError = None
RateLimitEvent = None
RateLimitInfo = None
ResultMessage = None
TextBlock = None
access_token = None
query = None
_AUTH_ERRORS = ()
_CLI_NOT_FOUND_ERRORS = ()
_SDK_TRANSPORT_ERRORS = ()

_SDK_IMPORT_ATTEMPTED = False


def _ensure_sdk_imported():
    """Загружает qoder_agent_sdk при первом использовании.

    Имена, уже установленные извне (например, подменённые в тестах),
    не перезаписываются. Неполная/сломанная установка SDK оставляет имена
    равными None — setup_client сообщит об этом пользователю.
    """
    global _SDK_IMPORT_ATTEMPTED, _AUTH_ERRORS, _CLI_NOT_FOUND_ERRORS, _SDK_TRANSPORT_ERRORS
    if _SDK_IMPORT_ATTEMPTED:
        return
    _SDK_IMPORT_ATTEMPTED = True
    try:
        import qoder_agent_sdk as _sdk
        resolved = {name: getattr(_sdk, name) for name in _SDK_NAMES}
    except (ImportError, AttributeError):  # pragma: no cover - broken installations
        return

    module_globals = globals()
    for name, value in resolved.items():
        if module_globals[name] is None:
            module_globals[name] = value
    _AUTH_ERRORS = tuple(
        exc for exc in (AuthNotConfiguredError, AuthAccessTokenEnvVarError) if exc)
    _CLI_NOT_FOUND_ERRORS = tuple(exc for exc in (CLINotFoundError,) if exc)
    _SDK_TRANSPORT_ERRORS = tuple(
        exc for exc in (CLIConnectionError, ProcessError, QoderSDKError) if exc)


class QoderApiHandler(BaseApiHandler):
    """Qoder PAT provider backed by the official Qoder Agent Python SDK."""

    DEFAULT_SYSTEM_PROMPT = (
        "Follow the user's translation instructions exactly. Return only the "
        "requested translated or processed text without commentary."
    )

    def __init__(self, worker):
        super().__init__(worker)
        self._last_usage = None
        self._auth_expired = False
        self._stderr_tail = deque(maxlen=20)

    def setup_client(self, client_override=None, proxy_settings=None):
        super().setup_client(client_override, proxy_settings)
        if not client_override or not getattr(client_override, "api_key", None):
            return False
        _ensure_sdk_imported()
        if QoderAgentOptions is None or query is None or access_token is None:
            raise ModelNotFoundError(
                "Не установлен официальный пакет qoder-agent-sdk. "
                "Переустановите зависимости приложения."
            )

        self.worker.api_key = str(client_override.api_key).strip()
        self.worker.model_id = str(self.worker.model_config.get("id") or "auto").strip()
        self._last_usage = None
        self._auth_expired = False
        self._stderr_tail.clear()
        return True

    def _proxy_url(self):
        settings = self.proxy_settings or {}
        if not settings.get("enabled"):
            return None

        host = str(settings.get("host") or "").strip()
        port = str(settings.get("port") or "").strip()
        if not host or not port:
            return None

        proxy_type = str(settings.get("type") or "socks5").strip().lower()
        if proxy_type == "socks":
            proxy_type = "socks5"
        user = str(settings.get("user") or "")
        password = str(settings.get("pass") or "")
        auth = ""
        if user:
            auth = quote(user, safe="")
            if password:
                auth += f":{quote(password, safe='')}"
            auth += "@"
        return f"{proxy_type}://{auth}{host}:{port}"

    def _system_prompt(self):
        prompt_builder = getattr(self.worker, "prompt_builder", None)
        system_instruction = getattr(prompt_builder, "system_instruction", None)
        return str(system_instruction or self.DEFAULT_SYSTEM_PROMPT).strip()

    @staticmethod
    def _positive_int(value):
        if isinstance(value, bool) or value is None:
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    def _options(self, max_output_tokens=None):
        configured_max = self._positive_int(max_output_tokens)
        if configured_max is None:
            configured_max = self._positive_int(
                self.worker.model_config.get("max_output_tokens")
            )

        extra_args = {}
        if configured_max is not None:
            extra_args["max-output-tokens"] = str(configured_max)

        return QoderAgentOptions(
            auth=access_token(self.worker.api_key),
            model=self.worker.model_id,
            system_prompt=self._system_prompt(),
            tools=[],
            permission_mode="dontAsk",
            max_turns=1,
            cwd=tempfile.gettempdir(),
            setting_sources=[],
            skills=[],
            plugins=[],
            mcp_servers={},
            proxy=self._proxy_url(),
            extra_args=extra_args,
            on_auth_expired=self._mark_auth_expired,
            stderr=self._capture_stderr,
        )

    def _mark_auth_expired(self):
        self._auth_expired = True

    def _capture_stderr(self, line):
        text = str(line or "").strip()
        if text:
            self._stderr_tail.append(text)

    @staticmethod
    def _assistant_text(message):
        parts = []
        for block in getattr(message, "content", None) or []:
            if TextBlock is not None and isinstance(block, TextBlock):
                parts.append(str(block.text or ""))
        return "".join(parts)

    @staticmethod
    def _retry_after_seconds(rate_limit_info):
        resets_at = getattr(rate_limit_info, "resets_at", None)
        if not resets_at:
            return 60
        try:
            return max(1, int(math.ceil(float(resets_at) - time.time())))
        except (TypeError, ValueError):
            return 60

    def _raise_rate_limit(self, rate_limit_info):
        delay = self._retry_after_seconds(rate_limit_info)
        window = str(getattr(rate_limit_info, "rate_limit_type", None) or "credits")
        error = RateLimitExceededError(
            f"Исчерпан лимит Qoder ({window}). Сброс примерно через {delay} сек."
        )
        error.retry_after_seconds = delay
        error.qoder_rate_limit_type = window
        raise error

    def _raise_result_error(self, result, assistant_error=None):
        errors = [
            str(item)
            for item in (getattr(result, "errors", None) or [])
            if item
        ]
        detail = "\n".join(errors).strip()
        subtype = str(getattr(result, "subtype", None) or "error_during_execution")
        combined = f"{assistant_error or ''} {subtype} {detail}".strip()
        lowered = combined.lower()

        auth_markers = (
            "authentication",
            "unauthorized",
            "access token",
            "invalid token",
            "expired",
        )
        if self._auth_expired or any(marker in lowered for marker in auth_markers):
            raise RateLimitExceededError(
                "Qoder отклонил PAT-ключ. Проверьте срок действия и права токена."
            )
        if any(
            marker in lowered
            for marker in ("rate_limit", "rate limit", "quota", "credit")
        ):
            raise TemporaryRateLimitError(
                f"Временный лимит Qoder: {detail or subtype}",
                delay_seconds=60,
            )
        if "billing" in lowered or "payment" in lowered:
            raise RateLimitExceededError(
                f"Qoder: недоступен баланс или тариф: {detail or subtype}"
            )
        if "model" in lowered and (
            "not found" in lowered or "not available" in lowered
        ):
            raise ModelNotFoundError(f"Модель Qoder недоступна: {detail or subtype}")
        if "max_turns" in lowered:
            raise PartialGenerationError(
                "Qoder остановил ответ по лимиту шагов.",
                partial_text="",
                reason="MAX_TURNS",
            )
        if "invalid_request" in lowered or "invalid request" in lowered:
            raise ValidationFailedError(f"Qoder отклонил запрос: {detail or subtype}")
        raise NetworkError(
            f"Ошибка Qoder ({subtype}): "
            f"{detail or assistant_error or 'нет описания'}"
        )

    async def call_api(
        self,
        prompt,
        log_prefix,
        allow_incomplete=False,
        use_stream=True,
        debug=False,
        max_output_tokens=None,
    ):
        self._auth_expired = False
        self._last_usage = None
        options = self._options(max_output_tokens=max_output_tokens)
        self._debug_record_request(
            {
                "provider": "qoder",
                "model": self.worker.model_id,
                "prompt": prompt,
                "system_prompt": options.system_prompt,
                "tools": [],
            },
            extra={"use_stream": bool(use_stream), "max_turns": 1},
        )

        stream = None
        assistant_text = ""
        assistant_error = None
        terminal_result = None
        try:
            stream = query(prompt=prompt, options=options)
            async for message in stream:
                if RateLimitEvent is not None and isinstance(message, RateLimitEvent):
                    info = message.rate_limit_info
                    if getattr(info, "status", None) == "rejected":
                        self._raise_rate_limit(info)
                    continue

                if AssistantMessage is not None and isinstance(message, AssistantMessage):
                    text = self._assistant_text(message)
                    if text:
                        assistant_text = text
                    if getattr(message, "error", None):
                        assistant_error = str(message.error)
                    if getattr(message, "usage", None):
                        self._last_usage = dict(message.usage)
                    continue

                if ResultMessage is not None and isinstance(message, ResultMessage):
                    terminal_result = message
                    if getattr(message, "usage", None):
                        self._last_usage = dict(message.usage)
        except asyncio.CancelledError:
            raise
        except (
            RateLimitExceededError,
            TemporaryRateLimitError,
            ValidationFailedError,
            PartialGenerationError,
        ):
            raise
        except _AUTH_ERRORS as exc:
            raise RateLimitExceededError("Qoder PAT-ключ не настроен или отклонён.") from exc
        except _CLI_NOT_FOUND_ERRORS as exc:
            raise ModelNotFoundError(
                "Встроенный Qoder CLI не найден. Переустановите qoder-agent-sdk."
            ) from exc
        except _SDK_TRANSPORT_ERRORS as exc:
            raise NetworkError(
                f"Не удалось выполнить запрос Qoder: {exc}", delay_seconds=30
            ) from exc
        except Exception as exc:
            raise NetworkError(
                f"Сбой Qoder SDK ({type(exc).__name__}): {exc}",
                delay_seconds=30,
            ) from exc
        finally:
            if stream is not None:
                close = getattr(stream, "aclose", None)
                if callable(close):
                    await close()

        if terminal_result is not None:
            if (
                getattr(terminal_result, "subtype", None) != "success"
                or getattr(terminal_result, "is_error", False)
            ):
                self._raise_result_error(terminal_result, assistant_error=assistant_error)
            result_text = str(
                getattr(terminal_result, "result", None) or assistant_text or ""
            )
        elif assistant_error:
            self._raise_result_error(
                SimpleResultError(assistant_error), assistant_error=assistant_error
            )
        else:
            result_text = assistant_text

        result_text = result_text.strip()
        self._debug_record_response(
            {
                "subtype": getattr(terminal_result, "subtype", None),
                "text": result_text,
                "usage": self._last_usage,
            },
            status="ok" if result_text else "empty",
        )
        if not result_text:
            raise ValidationFailedError("Qoder вернул пустой ответ.")
        return result_text

    def _estimate_token_usage(self, prompt, response_text):
        usage = self._last_usage or {}
        input_tokens = self._positive_int(
            usage.get("input_tokens") or usage.get("inputTokens")
        )
        output_tokens = self._positive_int(
            usage.get("output_tokens") or usage.get("outputTokens")
        )
        if input_tokens is None and output_tokens is None:
            return super()._estimate_token_usage(prompt, response_text)

        input_tokens = input_tokens or 0
        output_tokens = output_tokens or 0
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "estimated": False,
            "model_id": self.worker.model_id,
            "provider": "qoder",
        }


class SimpleResultError:
    def __init__(self, error):
        self.subtype = "error_during_execution"
        self.errors = [error]
