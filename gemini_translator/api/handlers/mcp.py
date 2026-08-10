import asyncio
from datetime import datetime, timedelta
import re
import threading
import time
import uuid

from ..base import BaseApiHandler, _current_debug_trace
from ..errors import NetworkError, RateLimitExceededError, TemporaryRateLimitError, ValidationFailedError
from ...mcp import inflight
from ...mcp.client import DaemonClientError, load_client
from ...utils.async_helpers import run_sync

MCP_LONG_LIMIT_THRESHOLD_SECONDS = 4 * 60 * 60
MCP_DEFAULT_LIMIT_DELAY_SECONDS = 120


class McpApiHandler(BaseApiHandler):
    """API handler that delegates prompts to the connected MCP AI client."""

    def setup_client(self, client_override=None, proxy_settings=None):
        self.proxy_settings = proxy_settings
        if client_override is not None:
            self.worker.api_key = getattr(client_override, "api_key", self.worker.api_key)
        self.worker.model_id = self.worker.model_config.get("id", "mcp-client")
        return True

    async def execute_api_call(
        self,
        prompt,
        log_prefix,
        allow_incomplete=False,
        debug=False,
        use_stream=True,
        max_output_tokens=None,
    ):
        trace = self._create_debug_trace(log_prefix)
        trace_token = _current_debug_trace.set(trace)
        started_at = time.perf_counter()
        payload = self._build_completion_payload(prompt, log_prefix, max_output_tokens=max_output_tokens)
        self._debug_record_request({"mode": "mcp", **payload})

        # Пока запрос висит, его id должен быть виден снаружи: кнопка «стоп»
        # снимает такие запросы напрямую, не дожидаясь движка (см. mcp.inflight).
        inflight.register(payload.get("request_id"))
        try:
            timeout_sec = payload["timeout_sec"]
            response = await run_sync(
                self._request_completion,
                payload,
                timeout=timeout_sec + 5,
                executor=getattr(self.worker, "sync_executor", None),
            )
            text = self._extract_response_text(response)
            self._debug_record_response(
                {"mode": "mcp", "transport": response.get("transport"), "text": text},
                status="ok",
            )
            self._finalize_debug_trace(trace, started_at=started_at, status="success")
            self._post_token_usage(prompt, text)
            return text
        except asyncio.CancelledError:
            self._cancel_completion(payload.get("request_id"))
            self._finalize_debug_trace(
                trace,
                started_at=started_at,
                status="cancelled",
            )
            raise
        except Exception as exc:
            self._finalize_debug_trace(
                trace,
                started_at=started_at,
                status=self._debug_status_from_exception(exc),
                error=exc,
            )
            raise
        finally:
            inflight.unregister(payload.get("request_id"))
            _current_debug_trace.reset(trace_token)

    def _build_completion_payload(self, prompt, log_prefix, *, max_output_tokens=None):
        payload = {
            "request_id": uuid.uuid4().hex,
            "prompt": str(prompt or ""),
            "system_instruction": str(getattr(self.worker.prompt_builder, "system_instruction", "") or ""),
            "timeout_sec": self._base_timeout(),
            "metadata": {
                "log_prefix": str(log_prefix or ""),
                "model_id": getattr(self.worker, "model_id", None),
                "operation_context": self._operation_context(),
            },
        }
        resolved_max_output_tokens = self._max_output_tokens(max_output_tokens)
        if resolved_max_output_tokens is not None:
            payload["max_output_tokens"] = resolved_max_output_tokens
        temperature = self._temperature_payload_value()
        if temperature is not None:
            payload["temperature"] = temperature
        return payload

    def _base_timeout(self) -> float:
        try:
            value = float(self.worker.provider_config.get("base_timeout", 1800))
        except (TypeError, ValueError, OverflowError):
            value = 1800.0
        return max(1.0, min(value, 7200.0))

    def _max_output_tokens(self, explicit_value):
        value = explicit_value
        if value is None:
            value = getattr(self.worker, "model_config", {}).get("max_output_tokens")
        try:
            resolved = int(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return resolved if resolved > 0 else None

    def _operation_context(self):
        context_getter = getattr(self.worker, "get_debug_operation_context", None)
        if not callable(context_getter):
            return {}
        context = context_getter()
        return dict(context) if isinstance(context, dict) else {}

    def _request_completion(self, payload):
        try:
            client = load_client()
            return client.request_ai_completion(payload, timeout=payload["timeout_sec"] + 5)
        except DaemonClientError as exc:
            self._raise_mcp_error(str(exc), getattr(exc, "payload", None) or {"error": str(exc)})

    def _cancel_completion(self, request_id):
        """Просит демон отменить запрос. Выполняется в фоновом daemon-потоке:
        вызов происходит из обработчика CancelledError на event-loop-потоке,
        синхронный HTTP здесь блокировал бы весь loop. Повторяем дважды —
        одиночный проглоченный сбой оставлял запрос висеть до таймаута."""
        if not request_id:
            return

        def _send_cancel():
            for attempt in range(2):
                try:
                    client = load_client()
                    cancel = getattr(client, "cancel_ai_completion", None)
                    if callable(cancel):
                        cancel(str(request_id))
                    return
                except DaemonClientError:
                    if attempt == 0:
                        time.sleep(0.5)

        threading.Thread(
            target=_send_cancel, name="mcp-cancel", daemon=True
        ).start()

    def _extract_response_text(self, response):
        if not isinstance(response, dict):
            raise NetworkError("MCP сервер вернул некорректный ответ", delay_seconds=10)
        if response.get("ok") is False:
            self._raise_mcp_error(
                self._error_message(response) or "MCP запрос завершился ошибкой",
                response,
            )
        text = str(response.get("text") or "")
        if not text.strip():
            raise ValidationFailedError("MCP клиент вернул пустой ответ.")
        return text

    def _raise_mcp_error(self, message: str, payload: dict | None = None):
        payload = payload if isinstance(payload, dict) else {}
        message = str(message or "MCP запрос завершился ошибкой")
        if not self._looks_like_limit_error(message, payload):
            raise NetworkError(message, delay_seconds=10)

        reset_delay = self._extract_reset_delay_seconds(message, payload)
        window_seconds = self._extract_window_seconds(payload)
        long_window_kind = self._long_window_kind(message, payload, window_seconds)
        reset_hint = self._reset_hint(reset_delay)

        if long_window_kind or (window_seconds is not None and window_seconds >= MCP_LONG_LIMIT_THRESHOLD_SECONDS):
            window_label = long_window_kind or "длинное окно лимита"
            details = f"MCP AI-клиент упёрся в {window_label}."
            if reset_hint:
                details = f"{details} {reset_hint}."
            if message:
                details = f"{details} Исходная ошибка: {message}"
            error = RateLimitExceededError(details)
            error.mcp_reset_delay_seconds = reset_delay
            error.mcp_reset_hint = reset_hint
            error.mcp_limit_window = window_label
            error.mcp_is_long_window = True
            raise error

        delay = reset_delay if reset_delay is not None else MCP_DEFAULT_LIMIT_DELAY_SECONDS
        details = "MCP AI-клиент временно упёрся в лимит."
        if reset_hint:
            details = f"{details} {reset_hint}."
        if message:
            details = f"{details} Исходная ошибка: {message}"
        error = TemporaryRateLimitError(details, delay_seconds=max(1, int(delay)))
        error.mcp_reset_delay_seconds = delay
        error.mcp_reset_hint = reset_hint
        error.mcp_is_long_window = False
        raise error

    def _error_message(self, payload) -> str:
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                return str(error.get("message") or error.get("code") or error)
            if error is not None:
                return str(error)
            if payload.get("message") is not None:
                return str(payload.get("message"))
        return str(payload or "")

    def _looks_like_limit_error(self, message: str, payload: dict) -> bool:
        text = self._limit_text(message, payload)
        markers = (
            "429",
            "rate limit",
            "ratelimit",
            "too many requests",
            "quota",
            "resource_exhausted",
            "usage limit",
            "limit exceeded",
            "лимит",
            "квот",
            "слишком много запрос",
        )
        return any(marker in text for marker in markers)

    def _long_window_kind(self, message: str, payload: dict, window_seconds: int | None) -> str:
        text = self._limit_text(message, payload)
        if window_seconds is not None and window_seconds >= 7 * 24 * 60 * 60:
            return "недельное окно лимита"
        if window_seconds is not None and window_seconds >= MCP_LONG_LIMIT_THRESHOLD_SECONDS:
            return "длинное окно лимита"
        if re.search(r"\b(?:5|five)\s*[- ]?(?:h|hr|hour|hours)\b", text):
            return "5-часовое окно лимита"
        if re.search(r"\b5\s*[- ]?час", text):
            return "5-часовое окно лимита"
        if "weekly" in text or re.search(r"\bweek(?:ly)?\b", text) or "недель" in text:
            return "недельное окно лимита"
        return ""

    def _limit_text(self, message: str, payload: dict) -> str:
        parts = [str(message or "")]
        if isinstance(payload, dict):
            for key in ("error", "message", "code", "status", "reason", "type"):
                value = payload.get(key)
                if value is not None:
                    parts.append(str(value))
            data = payload.get("data")
            if isinstance(data, dict):
                for value in data.values():
                    if value is not None:
                        parts.append(str(value))
        return " ".join(parts).lower()

    def _extract_reset_delay_seconds(self, message: str, payload: dict) -> int | None:
        for key in (
            "retry_after_seconds",
            "reset_after_seconds",
            "reset_in_seconds",
            "delay_seconds",
            "retry_after",
            "reset_after",
        ):
            seconds = self._seconds_from_value(payload.get(key))
            if seconds is not None:
                return seconds
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict):
            for key in (
                "retry_after_seconds",
                "reset_after_seconds",
                "reset_in_seconds",
                "retry_after",
                "reset_after",
            ):
                seconds = self._seconds_from_value(data.get(key))
                if seconds is not None:
                    return seconds
        return self._seconds_from_text(str(message or ""))

    def _extract_window_seconds(self, payload: dict) -> int | None:
        for key in (
            "window_seconds",
            "limit_window_seconds",
            "quota_window_seconds",
            "window_duration_seconds",
        ):
            seconds = self._seconds_from_value(payload.get(key))
            if seconds is not None:
                return seconds
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict):
            for key in (
                "window_seconds",
                "limit_window_seconds",
                "quota_window_seconds",
                "window_duration_seconds",
            ):
                seconds = self._seconds_from_value(data.get(key))
                if seconds is not None:
                    return seconds
        return None

    def _seconds_from_value(self, value) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            seconds = int(float(value))
        except (TypeError, ValueError, OverflowError):
            return self._seconds_from_text(str(value))
        return seconds if seconds >= 0 else None

    def _seconds_from_text(self, text: str) -> int | None:
        lowered = str(text or "").lower()
        for pattern in (
            r"(?:retry|try again|reset|resets|сброс|повтор)[^\d]{0,40}(\d+(?:[.,]\d+)?)\s*(seconds?|secs?|sec|s|секунд[а-я]*)",
            r"(?:retry|try again|reset|resets|сброс|повтор)[^\d]{0,40}(\d+(?:[.,]\d+)?)\s*(minutes?|mins?|min|m|минут[а-я]*)",
            r"(?:retry|try again|reset|resets|сброс|повтор)[^\d]{0,40}(\d+(?:[.,]\d+)?)\s*(hours?|hrs?|hr|h|час[а-я]*)",
        ):
            seconds = self._duration_match_seconds(pattern, lowered)
            if seconds is not None:
                return seconds
        for pattern in (
            r"(\d+(?:[.,]\d+)?)\s*(seconds?|secs?|sec|s|секунд[а-я]*)",
            r"(\d+(?:[.,]\d+)?)\s*(minutes?|mins?|min|m|минут[а-я]*)",
            r"(\d+(?:[.,]\d+)?)\s*(hours?|hrs?|hr|h|час[а-я]*)",
        ):
            seconds = self._duration_match_seconds(pattern, lowered)
            if seconds is not None:
                return seconds
        return None

    def _duration_match_seconds(self, pattern: str, text: str) -> int | None:
        match = re.search(pattern, text)
        if not match:
            return None
        try:
            value = float(match.group(1).replace(",", "."))
        except (TypeError, ValueError, OverflowError):
            return None
        unit = match.group(2).lower()
        if unit.startswith(("second", "sec", "s", "секунд")):
            multiplier = 1
        elif unit.startswith(("minute", "min", "m", "минут")):
            multiplier = 60
        else:
            multiplier = 3600
        return max(0, int(value * multiplier))

    def _reset_hint(self, delay_seconds: int | None) -> str:
        if delay_seconds is None:
            return ""
        reset_at = datetime.now().astimezone() + timedelta(seconds=max(0, int(delay_seconds)))
        return f"Сброс примерно {reset_at:%Y-%m-%d %H:%M:%S %Z} ({self._human_delay(delay_seconds)})"

    def _human_delay(self, delay_seconds: int) -> str:
        seconds = max(0, int(delay_seconds))
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        parts = []
        if hours:
            parts.append(f"{hours} ч.")
        if minutes:
            parts.append(f"{minutes} мин.")
        if not parts:
            parts.append(f"{seconds} сек.")
        return "через " + " ".join(parts)
