import aiohttp
import asyncio
import json
import traceback
import os
import platform
from ..base import BaseApiHandler
from ..errors import (
    ContentFilterError, NetworkError, LocationBlockedError,
    RateLimitExceededError, ModelNotFoundError, ValidationFailedError,
    TemporaryRateLimitError, PartialGenerationError
)

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

        headers = {
            "Authorization": f"Bearer {self.worker.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/gemini-translator",
            "X-Title": "Gemini Epub Translator"
        }

        messages = ([{"role": "system", "content": self.worker.prompt_builder.system_instruction}] if self.worker.prompt_builder.system_instruction else []) + [{"role": "user", "content": prompt}]

        payload = {
            "model": self.worker.model_id,
            "messages": messages,
            "temperature": self.worker.temperature,
            "stream": use_stream
        }
        if max_output_tokens is not None: payload["max_tokens"] = max_output_tokens
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

        try:
            async with session.post(self.base_url, headers=headers, json=payload) as response:
                if response.status != 200:
                    response_text = await response.text()
                    self._debug_record_response(
                        response_text,
                        status=f"http_{response.status}",
                        extra={"http_status": response.status, "mode": "error"},
                    )
                    txt_low = response_text.lower()
                    
                    if response.status in [401, 403]: raise RateLimitExceededError(f"Ошибка доступа ({response.status}): {response_text[:150]}")
                    if response.status == 402 or "quota" in txt_low: raise RateLimitExceededError("Недостаточно средств/Квота (402).")
                    if response.status == 429: raise TemporaryRateLimitError("Лимит запросов (429).", delay_seconds=20)
                    if response.status == 404: raise ModelNotFoundError(f"Модель {self.worker.model_id} не найдена (404).")
                    
                    raise NetworkError(f"Ошибка ({response.status}): {response_text[:150]}")

                if use_stream:
                    collected_text = ""
                    finish_reason = None
                    raw_stream_lines = [] if (self._has_debug_trace() or debug) else None
                    try:
                        async for line in response.content:
                            line_str = line.decode('utf-8').strip()
                            if raw_stream_lines is not None:
                                raw_stream_lines.append(line_str)
                            if not line_str or line_str == 'data: [DONE]': continue
                            if line_str.startswith('data: '):
                                json_str = line_str[6:]
                                try:
                                    chunk = json.loads(json_str)
                                    if 'choices' in chunk and chunk['choices']:
                                        delta = chunk['choices'][0].get('delta', {})
                                        content_part = delta.get('content', '')
                                        if content_part: collected_text += content_part
                                        if f_reason := chunk['choices'][0].get('finish_reason'): finish_reason = f_reason
                                except json.JSONDecodeError: continue
                    except Exception as stream_e:
                        if collected_text: raise PartialGenerationError(f"Обрыв стрима: {stream_e}", partial_text=collected_text, reason="NETWORK_ERROR")
                        raise stream_e
                    
                    if raw_stream_lines is not None:
                        self._debug_record_response(
                            "\n".join(raw_stream_lines),
                            status=finish_reason or "stream",
                            extra={"mode": "stream", "http_status": response.status},
                        )

                    if finish_reason == "length" and not allow_incomplete:
                        raise PartialGenerationError("Превышен лимит токенов", partial_text=collected_text, reason="LENGTH")
                    return collected_text
                else:
                    result = await response.json()
                    self._debug_record_response(
                        result,
                        status="http_200",
                        extra={"mode": "full", "http_status": response.status},
                    )
                    if 'choices' in result and result['choices']:
                        return result['choices'][0]['message']['content']
                    raise Exception(f"Пустой ответ: {result}")

        except asyncio.TimeoutError:
            raise NetworkError("Таймаут запроса.", delay_seconds=30)
        except (aiohttp.ClientError, OSError) as e:
            # Здесь мы видим, куда пытались стучаться
            raise NetworkError(f"Сбой сети ({type(e).__name__}) при обращении к {self.base_url}: {e}", delay_seconds=10) from e
        except (RateLimitExceededError, ContentFilterError, NetworkError, 
                PartialGenerationError, ModelNotFoundError, LocationBlockedError, 
                ValidationFailedError, TemporaryRateLimitError) as e:
            raise e
        except Exception as e:
            traceback.print_exc()
            raise Exception(f"Критическая ошибка при работе с Gemini REST API: {e}")
