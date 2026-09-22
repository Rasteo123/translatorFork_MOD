import asyncio
import json
import traceback
import os
import platform
from ..base import BaseApiHandler, TRANSPORT_ERRORS
from ..errors import (
    ContentFilterError, NetworkError, LocationBlockedError,
    RateLimitExceededError, ModelNotFoundError, ValidationFailedError,
    TemporaryRateLimitError, PartialGenerationError
)
from ._sse_stream import SSEStreamInterrupted, parse_openai_compatible_sse_stream
from ..retry_hints import retry_after_seconds

# A 429 is a pause, never a spent key. OmniRoute answers one for a closed
# 5-hour Antigravity window with the word «quota» in the body, and while the
# quota-text check ran before the status check, that pause marked the only
# OmniRoute key exhausted for 24 hours and ended the session mid-book.
RATE_LIMIT_DEFAULT_DELAY_SECONDS = 20
# Per 429 the worker sleeps at most this long and then asks again, so a window
# that reopens earlier than the hint said is not waited out to the end.
RATE_LIMIT_WAIT_CAP_SECONDS = 3600.0
# A reset further away than a working day (weekly Antigravity caps answer with
# 166h) is a spent key: the session ends with the time in the message instead
# of sleeping for a week one hour at a time.
QUOTA_RESET_FAR_SECONDS = 6 * 3600.0
# The reason an OpenAI-compatible ``finish_reason: "content_filter"`` carries
# into PartialGenerationError; the analyzer and the fallback treat it like
# Gemini's SAFETY / PROHIBITED_CONTENT.
CONTENT_BLOCK_REASON = "CONTENT_FILTER"


def _format_wait(seconds):
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours} ч {minutes} мин" if minutes else f"{hours} ч"
    if minutes:
        return f"{minutes} мин"
    return f"{secs} с"

def get_dynamic_server_url(endpoint_filename: str, default_port: int = 8000) -> str:
    """
    Универсальная читалка URL.
    Ищет указанный файл в папке данных приложения.
    """
    if platform.system() == "Windows":
        app_data_dir = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "GeminiTranslator")
    else:
        app_data_dir = os.path.join(os.path.expanduser("~"), ".gemini_translator")

    # Если имя файла не передано, возвращаем дефолт
    if not endpoint_filename:
        return f"http://127.0.0.1:{default_port}/v1/chat/completions"

    endpoint_file = os.path.join(app_data_dir, endpoint_filename)

    if os.path.exists(endpoint_file):
        try:
            with open(endpoint_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                url = data.get("v1_chat_url")
                if url: return url
        except Exception: pass
    
    return f"http://127.0.0.1:{default_port}/v1/chat/completions"

class OpenRouterApiHandler(BaseApiHandler):
    def _build_request_headers(self):
        headers = {
            "Authorization": f"Bearer {self.worker.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/gemini-translator",
            "X-Title": "Gemini Epub Translator",
        }

        provider_config = (
            self.worker.provider_config
            if isinstance(self.worker.provider_config, dict)
            else {}
        )
        extra_headers = provider_config.get("extra_headers")
        if isinstance(extra_headers, dict):
            headers.update(
                {
                    str(name): str(value)
                    for name, value in extra_headers.items()
                    if str(name).strip() and value is not None
                }
            )
        return headers

    def _apply_openai_reasoning_options(self, payload):
        model_config = self.worker.model_config if isinstance(self.worker.model_config, dict) else {}
        provider_config = self.worker.provider_config if isinstance(self.worker.provider_config, dict) else {}
        raw_effort = (
            model_config.get("reasoning_effort")
            or model_config.get("default_reasoning_effort")
            or provider_config.get("reasoning_effort")
            or provider_config.get("default_reasoning_effort")
        )
        if isinstance(raw_effort, bool) or raw_effort is None:
            return

        effort = str(raw_effort).strip().lower()
        if effort:
            payload["reasoning_effort"] = effort

    @staticmethod
    def _is_model_access_denied_error(status, response_text):
        if status != 403:
            return False

        text = str(response_text or "").lower()
        if "model" not in text:
            return False

        access_markers = (
            "not allowed",
            "not permitted",
            "not available",
            "no access",
            "access denied",
            "forbidden",
        )
        key_markers = ("api key", "key")
        return any(marker in text for marker in access_markers) and any(
            marker in text for marker in key_markers
        )

    @classmethod
    def _should_retry_without_stream_for_model_access(
        cls, status, response_text, use_stream, already_retried
    ):
        return (
            bool(use_stream)
            and not already_retried
            and cls._is_model_access_denied_error(status, response_text)
        )

    @staticmethod
    def _raise_rate_limited(response, response_text):
        """Turn a 429 into the pause the service asked for, or a spent key.

        The hint comes from Retry-After, from OmniRoute's ``retryAfterMs`` or
        Google's ``retryDelay`` in the body, or from the message text («Your
        quota will reset after 4h32m10s»). No hint keeps the old short pause.
        """
        hint = retry_after_seconds(getattr(response, "headers", None), response_text)
        if hint is None:
            raise TemporaryRateLimitError(
                "Лимит запросов (429).", delay_seconds=RATE_LIMIT_DEFAULT_DELAY_SECONDS
            )
        if hint > QUOTA_RESET_FAR_SECONDS:
            error = RateLimitExceededError(
                f"Квота исчерпана (429), сброс примерно через {_format_wait(hint)}: "
                f"{response_text[:150]}"
            )
            error.retry_after_seconds = hint
            raise error
        raise TemporaryRateLimitError(
            f"Лимит запросов (429), сервис просит подождать {_format_wait(hint)}.",
            delay_seconds=min(max(hint, 1.0), RATE_LIMIT_WAIT_CAP_SECONDS),
        )

    async def _read_success_response(self, response, use_stream, allow_incomplete, debug):
        if use_stream:
            try:
                collected_text, finish_reason, raw_stream_lines = await parse_openai_compatible_sse_stream(
                    response,
                    capture_raw=(self._has_debug_trace() or debug),
                    on_usage=self._remember_openai_usage,
                )
            except SSEStreamInterrupted as interrupted:
                raise PartialGenerationError(
                    f"Обрыв стрима: {interrupted.original_error}",
                    partial_text=interrupted.partial_text,
                    reason="NETWORK_ERROR"
                ) from interrupted.original_error

            if raw_stream_lines is not None:
                self._debug_record_response(
                    "\n".join(raw_stream_lines),
                    status=finish_reason or "stream",
                    extra={"mode": "stream", "http_status": response.status},
                )

            if finish_reason == "length" and not allow_incomplete:
                raise PartialGenerationError("Превышен лимит токенов", partial_text=collected_text, reason="LENGTH")
            if finish_reason == "content_filter":
                self._raise_content_block(collected_text)
            return collected_text

        result = await response.json()
        self._debug_record_response(
            result,
            status="http_200",
            extra={"mode": "full", "http_status": response.status},
        )
        if 'choices' in result and result['choices']:
            self._remember_openai_usage(result.get("usage"))
            choice = result['choices'][0]
            content = choice['message']['content']
            if isinstance(choice, dict) and choice.get('finish_reason') == "content_filter":
                self._raise_content_block(content or "")
            return content
        raise Exception(f"Пустой ответ: {result}")

    @staticmethod
    def _raise_content_block(text):
        """A ``content_filter`` finish reason is a block, not a finished answer.

        OmniRoute folds Gemini's SAFETY, PROHIBITED_CONTENT, RECITATION and
        BLOCKLIST into it. With a tail the block is reported the way the Gemini
        handler reports its own (a partial with a block reason), so the
        content-filter fallback and the «Не повторять блокировки» option see it.
        """
        if text:
            raise PartialGenerationError(
                "Генерация прервана фильтром контента (finish_reason: content_filter)",
                partial_text=text,
                reason=CONTENT_BLOCK_REASON,
            )
        raise ContentFilterError("Ответ заблокирован фильтром контента (finish_reason: content_filter)")

    def setup_client(self, client_override=None, proxy_settings=None):
        super().setup_client(client_override, proxy_settings)
        if not client_override: return False

        self.worker.api_key = client_override.api_key
        self.worker.model_id = self.worker.model_config.get("id", "deepseek/deepseek-chat-v3-0324:free")

        # --- ЛОГИКА ДИНАМИЧЕСКОГО ПОДКЛЮЧЕНИЯ ---
        # 1. Читаем имя файла эндпоинта из конфига (например: "perplexity_server_endpoint.json")
        self.endpoint_filename = self.worker.provider_config.get("server_endpoint")
        
        # 2. Проверяем, есть ли класс сервера (значит это локальный провайдер)
        self.server_class_name = self.worker.provider_config.get("server_class")
        
        if self.server_class_name and self.endpoint_filename:
            self.is_dynamic_local = True
        else:
            self.is_dynamic_local = False
            # Если это не динамика, берем статический URL
            self.base_url = self.worker.model_config.get("base_url") or self.worker.provider_config.get("base_url") or "https://openrouter.ai/api/v1/chat/completions"

        self._proactive_session_init()
        return True

    async def call_api(self, prompt, log_prefix, allow_incomplete=False, use_stream=True, debug=False, max_output_tokens=None):
        session = await self._get_or_create_session_internal()

        # --- DYNAMIC URL FETCHING ---
        if self.is_dynamic_local:
            # Читаем URL из файла ПРЯМО ПЕРЕД ЗАПРОСОМ.
            # Если сервер перезагрузился и сменил порт, мы это увидим тут.
            self.base_url = get_dynamic_server_url(self.endpoint_filename)
        # ----------------------------

        headers = self._build_request_headers()

        messages = ([{"role": "system", "content": self.worker.prompt_builder.system_instruction}] if self.worker.prompt_builder.system_instruction else []) + [{"role": "user", "content": prompt}]

        payload = {
            "model": self.worker.model_id,
            "messages": messages,
            "stream": use_stream
        }
        temperature = self._temperature_payload_value()
        if temperature is not None:
            payload["temperature"] = temperature
        self._apply_openai_reasoning_options(payload)
        if max_output_tokens is not None: payload["max_tokens"] = max_output_tokens
        elif allow_incomplete:
             payload["max_tokens"] = int(self.worker.model_config.get("max_output_tokens", 8192) * 0.98)

        try:
            retried_without_stream = False
            while True:
                request_uses_stream = bool(payload.get("stream"))
                self._debug_record_request(
                    {
                        "method": "POST",
                        "url": self.base_url,
                        "headers": headers,
                        "payload": payload,
                    },
                    extra={
                        "use_stream": request_uses_stream,
                        "allow_incomplete": allow_incomplete,
                        "retried_without_stream": retried_without_stream,
                    },
                )

                async with session.post(self.base_url, headers=headers, json=payload) as response:
                    if response.status == 200:
                        return await self._read_success_response(
                            response,
                            request_uses_stream,
                            allow_incomplete,
                            debug,
                        )

                    response_text = await response.text()
                    self._debug_record_response(
                        response_text,
                        status=f"http_{response.status}",
                        extra={"http_status": response.status, "mode": "error"},
                    )

                    if self._should_retry_without_stream_for_model_access(
                        response.status,
                        response_text,
                        request_uses_stream,
                        retried_without_stream,
                    ):
                        retried_without_stream = True
                        payload["stream"] = False
                        continue

                    txt_low = response_text.lower()
                    
                    if response.status == 429:
                        self._raise_rate_limited(response, response_text)
                    if self._is_model_access_denied_error(response.status, response_text):
                        raise ModelNotFoundError(
                            f"Model {self.worker.model_id} is not allowed for this API key: {response_text[:150]}"
                        )
                    if response.status in [401, 403]: raise RateLimitExceededError(f"Ошибка доступа ({response.status}): {response_text[:150]}")
                    if response.status == 402 or "quota" in txt_low: raise RateLimitExceededError("Недостаточно средств/Квота (402).")
                    if response.status == 404: raise ModelNotFoundError(f"Модель {self.worker.model_id} не найдена (404).")
                    
                    raise NetworkError(f"Ошибка ({response.status}): {response_text[:150]}")

        except asyncio.TimeoutError:
            raise NetworkError("Таймаут запроса.", delay_seconds=30)
        except TRANSPORT_ERRORS as e:
            # Здесь мы видим, куда пытались стучаться
            raise NetworkError(f"Сбой сети ({type(e).__name__}) при обращении к {self.base_url}: {e}", delay_seconds=10) from e
        except (RateLimitExceededError, ContentFilterError, NetworkError, 
                PartialGenerationError, ModelNotFoundError, LocationBlockedError, 
                ValidationFailedError, TemporaryRateLimitError) as e:
            raise e
        except Exception as e:
            traceback.print_exc()
            raise Exception(f"Критическая ошибка при работе с Gemini REST API: {e}")
