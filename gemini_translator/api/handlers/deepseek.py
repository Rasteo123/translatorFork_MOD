import aiohttp
import asyncio
import time
from ..base import BaseApiHandler
from ..errors import (
    ContentFilterError, NetworkError, LocationBlockedError,
    RateLimitExceededError, ModelNotFoundError, ValidationFailedError,
    TemporaryRateLimitError, PartialGenerationError
)
from . import _deepseek_common
from ._sse_stream import SSEStreamInterrupted, parse_openai_compatible_sse_stream

class DeepseekApiHandler(BaseApiHandler):
    """
    Хендлер для официального API DeepSeek.
    """

    def _apply_deepseek_thinking_options(self, payload):
        model_config = self.worker.model_config if isinstance(self.worker.model_config, dict) else {}
        _deepseek_common.build_deepseek_thinking_options(payload, model_config, self.worker)

    def setup_client(self, client_override=None, proxy_settings=None):
        super().setup_client(client_override, proxy_settings)

        if not client_override:
            return False
        
        self.worker.api_key = client_override.api_key
        # Дефолтная модель для DeepSeek
        self.worker.model_id = self.worker.model_config.get("id", "deepseek-chat")
        # Берем URL из конфига или ставим дефолт
        self.base_url = self.worker.provider_config.get("base_url", "https://api.deepseek.com/chat/completions")
        
        self._proactive_session_init()
        return True

    async def call_api(self, prompt, log_prefix, allow_incomplete=False, use_stream=True, debug=False, max_output_tokens=None):
        session = await self._get_or_create_session_internal()

        headers = {
            "Authorization": f"Bearer {self.worker.api_key}",
            "Content-Type": "application/json"
        }

        messages = (
            [{"role": "system", "content": self.worker.prompt_builder.system_instruction}]
            if self.worker.prompt_builder.system_instruction
            else []
        ) + [{"role": "user", "content": prompt}]

        payload = {
            "model": self.worker.model_id,
            "messages": messages,
            "stream": use_stream
        }
        temperature = self._temperature_payload_value()
        if temperature is not None:
            payload["temperature"] = temperature
        self._apply_deepseek_thinking_options(payload)

        if max_output_tokens:
            payload["max_tokens"] = max_output_tokens
        elif allow_incomplete:
            payload["max_tokens"] = int(self.worker.model_config.get("max_output_tokens", 8192) * 0.98)

        self._debug_record_request(
            {
                "method": "POST",
                "url": self.base_url,
                "headers": headers,
                "payload": payload,
            },
            extra={"use_stream": use_stream, "allow_incomplete": allow_incomplete},
        )

        # Цикл попыток на случай перегрузки серверов DeepSeek (503)
        max_retries = 3
        retry_count = 0

        while retry_count < max_retries:
            try:
                async with session.post(self.base_url, headers=headers, json=payload) as response:
                    
                    # --- 1. ОБРАБОТКА ОШИБОК (Статус != 200) ---
                    if response.status != 200:
                        error_text = await response.text()
                        self._debug_record_response(
                            error_text,
                            attempt=retry_count + 1,
                            status=f"http_{response.status}",
                            extra={"http_status": response.status, "mode": "error"},
                        )
                        
                        # Обработка перегрузки (API DeepSeek часто выдает 503 при высокой нагрузке)
                        if response.status in [500, 502, 503]:
                            wait_time = 15.0 * (retry_count + 1)
                            log_msg = f"⏳ Сервер DeepSeek перегружен ({response.status}). Ждем {wait_time}с перед повтором."
                            self.worker._post_event('log_message', {'message': log_msg})
                            
                            await asyncio.sleep(wait_time)
                            retry_count += 1
                            continue
                        
                        # Стандартные ошибки
                        if response.status == 401:
                            raise RateLimitExceededError(f"Неверный токен (…{self.worker.api_key[-4:]}) DeepSeek.")
                        if response.status == 402:
                            raise RateLimitExceededError("Недостаточно средств на балансе DeepSeek (402).")
                        if response.status == 404:
                            raise ModelNotFoundError(f"Модель {self.worker.model_id} недоступна.")
                        if response.status == 429:
                            raise TemporaryRateLimitError("Превышен лимит запросов DeepSeek (429).", delay_seconds=20)
                        
                        # Любая другая ошибка
                        raise NetworkError(f"Ошибка DeepSeek ({response.status}): {error_text[:200]}")

                    # --- 2. ОБРАБОТКА УСПЕШНОГО ОТВЕТА (200 OK) ---
                    
                    # Ветка А: СТРИМИНГ
                    if use_stream:
                        try:
                            collected_text, finish_reason, raw_stream_lines = await parse_openai_compatible_sse_stream(
                                response, capture_raw=(self._has_debug_trace() or debug)
                            )
                        except SSEStreamInterrupted as interrupted:
                            raise PartialGenerationError(
                                f"Обрыв стрима DeepSeek: {interrupted.original_error}",
                                partial_text=interrupted.partial_text,
                                reason="NETWORK_ERROR"
                            ) from interrupted.original_error

                        if raw_stream_lines is not None:
                            self._debug_record_response(
                                "\n".join(raw_stream_lines),
                                attempt=retry_count + 1,
                                status=finish_reason or "stream",
                                extra={"mode": "stream", "http_status": response.status},
                            )

                        if finish_reason == "length" and not allow_incomplete:
                             raise PartialGenerationError(
                                "Превышен лимит токенов (length)",
                                partial_text=collected_text,
                                reason="LENGTH"
                             )
                        
                        return collected_text

                    # Ветка Б: ОБЫЧНЫЙ ЗАПРОС (JSON)
                    else:
                        result = await response.json()
                        self._debug_record_response(
                            result,
                            attempt=retry_count + 1,
                            status="http_200",
                            extra={"mode": "full", "http_status": response.status},
                        )
                        if 'choices' in result and result['choices']:
                            choice = result['choices'][0]
                            content = choice['message']['content']
                            
                            if choice.get('finish_reason') == "length" and not allow_incomplete:
                                raise PartialGenerationError(
                                    "Превышен лимит токенов (length)",
                                    partial_text=content,
                                    reason="LENGTH"
                                )
                                
                            return content
                        
                        raise Exception(f"Пустой ответ JSON от DeepSeek: {result}")

            except asyncio.TimeoutError:
                raise NetworkError("Таймаут соединения с DeepSeek", delay_seconds=10)
            except (aiohttp.ClientError, OSError) as e:
                error_msg = self._format_transport_error(e, "DeepSeek")
                raise NetworkError(error_msg, delay_seconds=20) from e
            except (RateLimitExceededError, ContentFilterError, NetworkError, 
                    PartialGenerationError, ModelNotFoundError, LocationBlockedError, 
                    ValidationFailedError, TemporaryRateLimitError) as e:
                raise e
            
            except Exception as e:
                import traceback
                traceback.print_exc()
                raise Exception(f"Критическая ошибка DeepSeek: {e}")
        
        raise NetworkError("Не удалось получить ответ от DeepSeek из-за перегрузки серверов (Retry Limit).", delay_seconds=30)
