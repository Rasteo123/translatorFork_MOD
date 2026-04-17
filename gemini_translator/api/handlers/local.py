# gemini_translator/api/handlers/local.py

import requests
import json
from ..base import BaseApiHandler
from ..errors import (
    ContentFilterError, NetworkError, LocationBlockedError, 
    RateLimitExceededError, ModelNotFoundError, ValidationFailedError, 
    TemporaryRateLimitError, PartialGenerationError
)

class LocalApiHandler(BaseApiHandler):
    """
    ЭТАЛОННЫЙ СИНХРОННЫЙ ХЕНДЛЕР.
    Использует библиотеку `requests`.
    
    Особенности:
    1. В конфиге api_providers.json должно быть "is_async": false.
    2. Метод call_api не имеет async/await.
    3. Принимает аргумент `proxies` и передает его в requests.
    """

    def setup_client(self, client_override=None, proxy_settings=None):
        # 1. Сохраняем сырые настройки
        super().setup_client(client_override, proxy_settings)

        self.worker.api_key = client_override.api_key
        self.worker.model_id = self.worker.model_config.get("id", "llama3:8b")
        
        # Логика выбора URL
        model_base_url = self.worker.model_config.get("base_url")
        provider_base_url = self.worker.provider_config.get("base_url")
        fallback_url = "http://127.0.0.1:11434/v1/chat/completions"
        self.base_url = model_base_url or provider_base_url or fallback_url
        
        # Логика выбора timeout
        model_base_timout = self.worker.model_config.get("base_timeout")
        provider_base_timout = self.worker.provider_config.get("base_timeout")
        fallback_timout = 3300  # 55 минут
        self.timeout_seconds = model_base_timout or provider_base_timout or fallback_timout
        
        # 2. ПОДГОТОВКА ПРОКСИ
        self.prepared_proxies = None
        
        # ПРОВЕРКА НА ЛОКАЛЬНОСТЬ:
        # Если мы стучимся домой, прокси не нужен, даже если он включен в настройках.
        is_localhost = "127.0.0.1" in self.base_url or "localhost" in self.base_url or "0.0.0.0" in self.base_url
        
        if not is_localhost and self.proxy_settings and self.proxy_settings.get('enabled'):
            host = self.proxy_settings.get('host')
            port = self.proxy_settings.get('port')
            if host and port:
                p_type = self.proxy_settings.get('type', 'SOCKS5').lower()
                user = self.proxy_settings.get('user')
                pwd = self.proxy_settings.get('pass')
                
                auth = f"{user}:{pwd}@" if user and pwd else ""
                url = f"{p_type}://{auth}{host}:{port}"
                
                self.prepared_proxies = {'http': url, 'https': url}
                self.worker._post_event('log_message', {'message': f"[LocalApiHandler] Прокси настроен для удаленного сервера: {url}"})
        elif is_localhost:
             self.worker._post_event('log_message', {'message': "[LocalApiHandler] Обнаружен локальный адрес. Прокси принудительно отключен."})
        
        return True

    def call_api(self, prompt, log_prefix, allow_incomplete=False, use_stream=True, debug=False, max_output_tokens=None):
        """
        СИНХРОННАЯ реализация вызова.
        Аргумент `session` (aiohttp) здесь всегда None и не используется.
        """
        headers = { "Content-Type": "application/json" }
        
        messages = (
            [{"role": "system", "content": self.worker.prompt_builder.system_instruction}]
            if self.worker.prompt_builder.system_instruction
            else []
        ) + [{"role": "user", "content": prompt}]

        payload = {
            "model": self.worker.model_id,
            "messages": messages,
            "temperature": self.worker.temperature,
            "stream": False # Синхронные хендлеры обычно проще писать без стриминга
        }
        
        if allow_incomplete:
            max_tokens = self.worker.model_config.get("max_output_tokens", 8192)
            payload["max_tokens"] = int(max_tokens * 0.98)
        
        # ставим таймаут из конфига
        timeout_seconds = self.timeout_seconds

        self._debug_record_request(
            {
                "method": "POST",
                "url": self.base_url,
                "headers": headers,
                "payload": payload,
                "proxies": self.prepared_proxies,
            },
            extra={"allow_incomplete": allow_incomplete, "timeout_seconds": timeout_seconds},
        )

        try:
            # --- ГЛАВНЫЙ ВЫЗОВ ---
            # Передаем proxies, который пришел аргументом
            response = requests.post(
                self.base_url, 
                headers=headers, 
                json=payload, 
                proxies=self.prepared_proxies,
                timeout=timeout_seconds
            )
            
            # --- Обработка ответа ---
            
            if response.status_code == 200:
                result = response.json()
                self._debug_record_response(
                    result,
                    status="http_200",
                    extra={"mode": "full", "http_status": response.status_code},
                )
                if 'choices' in result and result['choices']:
                    choice = result['choices'][0]
                    finish_reason = choice.get('finish_reason')

                    is_successful_stop = (finish_reason == "stop")
                    is_acceptable_incomplete = (finish_reason == "length" and allow_incomplete)

                    if is_successful_stop or is_acceptable_incomplete:
                        if is_acceptable_incomplete:
                            # Логируем предупреждение через воркер (это потокобезопасно)
                            log_payload = {'message': f"[WARN] Ответ локальной модели обрезан лимитом."}
                            self.worker._post_event('log_message', log_payload)
                        
                        return choice['message']['content']
                    else:
                        raise ValidationFailedError(f"Генерация остановлена: '{finish_reason}'.")

                raise Exception(f"Пустой ответ от сервера: {result}")

            # --- Обработка ошибок HTTP ---
            response_text = response.text
            self._debug_record_response(
                response_text,
                status=f"http_{response.status_code}",
                extra={"mode": "error", "http_status": response.status_code},
            )
            
            if response.status_code == 404:
                 raise ModelNotFoundError(f"Модель '{self.worker.model_id}' не найдена (404).")
            
            if response.status_code >= 500:
                if response.status_code == 503:
                     raise NetworkError(f"Сервер занят/загружается (503). Повтор через 30с.", delay_seconds=30)
                raise NetworkError(f"Ошибка сервера (код {response.status_code}): {response_text[:150]}", delay_seconds=30)
            
            raise Exception(f"Ошибка API ({response.status_code}): {response_text[:200]}")

        # --- Перехват исключений requests ---
        except requests.exceptions.Timeout:
            raise NetworkError(f"Таймаут запроса ({timeout_seconds}с). Модель думает слишком долго.", delay_seconds=30)
        except requests.exceptions.ConnectionError as e:
            raise NetworkError(f"Нет соединения с {self.base_url}. Сервер запущен? Ошибка: {e}", delay_seconds=60)
        except requests.exceptions.RequestException as e:
            raise NetworkError(f"Сетевая ошибка requests: {e}", delay_seconds=30)
        except Exception as e:
             raise Exception(f"Критическая ошибка в локальном хендлере: {e}")
