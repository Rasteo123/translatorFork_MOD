# gemini_translator/api/handlers/local.py

import requests
import json
from .. import config as api_config
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

    def _get_http_session(self):
        """Персистентная requests.Session: переиспользует TCP-соединения между
        вызовами вместо открытия нового на каждый запрос."""
        session = getattr(self, "_http_session", None)
        if session is None:
            session = requests.Session()
            self._http_session = session
        return session

    def _drop_http_session(self):
        session = getattr(self, "_http_session", None)
        self._http_session = None
        if session is not None:
            try:
                session.close()
            except Exception:
                pass

    async def _close_thread_session_internal(self):
        self._drop_http_session()
        await super()._close_thread_session_internal()

    def _collect_local_stream(self, response, debug=False):
        """Синхронно читает OpenAI-совместимый SSE-поток от локального сервера
        построчно через response.iter_lines() (эквивалент async-парсера в
        _sse_stream.py, но без aiohttp -- requests синхронный).

        Формат ровно тот же, что у остальных хендлеров: строки
        'data: {...}' с choices[0].delta.content / choices[0].finish_reason,
        пустые строки и 'data: [DONE]' пропускаются, невалидный JSON в
        отдельной строке молча пропускается.

        Дополнительно отслеживает saw_sse -- была ли хоть одна строка с
        префиксом 'data: '. Если сервер проигнорировал payload['stream']=True
        и ответил обычным JSON одним телом, ни одна строка префикса не
        получит, и вызывающий код (call_api) сможет разобрать накопленное в
        pre_sse_lines сырое тело как обычный синхронный JSON-ответ вместо
        того, чтобы потерять текст.

        Если соединение обрывается посреди чтения (Timeout/ConnectionError/
        ChunkedEncodingError и т.п.) и что-то уже накоплено -- поднимает
        PartialGenerationError с накопленным текстом (и сбрасывает
        персистентную HTTP-сессию через _drop_http_session(), как и при
        обрыве вне стрима) вместо того, чтобы потерять текст молча. Если не
        накоплено ничего -- исходное исключение requests пробрасывается как
        есть и обрабатывается обычными except-ветками call_api
        (Timeout/ConnectionError/RequestException).
        """
        collected_text = ""
        finish_reason = None
        capture_raw = debug or self._has_debug_trace()
        raw_lines = [] if capture_raw else None
        saw_sse = False
        pre_sse_lines = []

        try:
            for raw_line in response.iter_lines(decode_unicode=True):
                if raw_line is None:
                    continue
                line_str = raw_line if isinstance(raw_line, str) else raw_line.decode("utf-8", "ignore")
                line_str = line_str.strip()
                if raw_lines is not None:
                    raw_lines.append(line_str)
                if not saw_sse:
                    pre_sse_lines.append(line_str)
                if not line_str or line_str == "data: [DONE]":
                    continue
                if not line_str.startswith("data: "):
                    continue
                saw_sse = True

                json_str = line_str[6:]
                try:
                    chunk = json.loads(json_str)
                except json.JSONDecodeError:
                    continue

                self._remember_openai_usage(chunk.get("usage"))
                choices = chunk.get("choices")
                if choices:
                    delta = choices[0].get("delta") or {}
                    content_part = delta.get("content", "")
                    if content_part:
                        collected_text += content_part

                    f_reason = choices[0].get("finish_reason")
                    if f_reason:
                        finish_reason = f_reason
        except (requests.exceptions.RequestException, OSError) as stream_error:
            if collected_text:
                self._drop_http_session()
                raise PartialGenerationError(
                    f"Обрыв потока локального сервера: {stream_error}",
                    partial_text=collected_text,
                    reason="NETWORK_ERROR",
                ) from stream_error
            raise

        return collected_text, finish_reason, raw_lines, saw_sse, pre_sse_lines

    def _close_stream_response(self, response):
        """Закрывает потоковый response, чтобы requests.Session вернул
        соединение в пул -- при stream=True это не происходит само по себе,
        пока тело не дочитано полностью или response не закрыт явно."""
        close = getattr(response, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    def _try_parse_non_sse_json_body(self, lines):
        """Если сервер проигнорировал payload['stream']=True и ответил
        обычным JSON одним телом (не SSE), пытается разобрать накопленные
        до первой валидной SSE-строки сырые строки как этот JSON. Возвращает
        dict при успехе, иначе None (тело пустое, не JSON, либо не dict)."""
        if not lines:
            return None
        body = "\n".join(lines).strip()
        if not body:
            return None
        try:
            result = json.loads(body)
        except json.JSONDecodeError:
            return None
        return result if isinstance(result, dict) else None

    def _apply_reasoning_options(self, payload):
        model_config = self.worker.model_config
        supports_reasoning = model_config.get(
            "supports_reasoning_effort", self.worker.provider_config.get("supports_reasoning_effort", False)
        )
        if not supports_reasoning or not getattr(self.worker, "thinking_enabled", False):
            return

        raw_effort = getattr(self.worker, "thinking_level", None) or model_config.get(
            "default_reasoning_effort"
        )
        effort = str(raw_effort or "").strip().lower()
        allowed = {str(level).strip().lower() for level in model_config.get("thinkingLevel", [])}
        if not effort or effort not in allowed:
            raise ValidationFailedError(f"Недоступный уровень рассуждений: '{effort}'.")
        payload["reasoning_effort"] = effort

    def call_api(self, prompt, log_prefix, allow_incomplete=False, use_stream=True, debug=False, max_output_tokens=None):
        """
        СИНХРОННАЯ реализация вызова.
        Аргумент `session` (aiohttp) здесь всегда None и не используется.

        ВАЖНО про timeout при use_stream=True: requests трактует `timeout` в
        потоковом режиме как таймаут ожидания СЛЕДУЮЩЕГО чанка, а не общего
        времени ответа (в отличие от use_stream=False, где это таймаут на
        весь запрос). Локальный сервер, медленно капающий токенами дольше
        timeout_seconds суммарно, но не дающий пауз между чанками длиннее
        timeout_seconds, не будет прерван по общему дедлайну -- это такое же
        поведение, как у остальных стримингующих хендлеров (aiohttp ведёт
        себя аналогично), но раньше локальный путь всегда был ограничен
        общим self.timeout_seconds, и это стоит иметь в виду при диагностике
        "зависших" локальных генераций.
        """
        headers = { "Content-Type": "application/json" }
        api_key = str(getattr(self.worker, "api_key", "") or "").strip()
        if api_key and not api_key.startswith("__"):
            headers["Authorization"] = f"Bearer {api_key}"
        
        messages = (
            [{"role": "system", "content": self.worker.prompt_builder.system_instruction}]
            if self.worker.prompt_builder.system_instruction
            else []
        ) + [{"role": "user", "content": prompt}]

        payload = {
            "model": self.worker.model_id,
            "messages": messages,
            "stream": bool(use_stream),
        }
        temperature = self._temperature_payload_value()
        if temperature is not None:
            payload["temperature"] = temperature
        self._apply_reasoning_options(payload)
        
        requested_max_tokens = api_config._coerce_positive_int(max_output_tokens)
        if requested_max_tokens is not None:
            payload["max_tokens"] = requested_max_tokens
        elif allow_incomplete:
            configured_max_tokens = api_config._coerce_positive_int(
                self.worker.model_config.get("max_output_tokens")
            )
            if configured_max_tokens is not None:
                payload["max_tokens"] = max(1, int(configured_max_tokens * 0.98))
        
        # ставим таймаут из конфига
        timeout_seconds = self.timeout_seconds

        debug_headers = dict(headers)
        if "Authorization" in debug_headers:
            debug_headers["Authorization"] = "Bearer ..."

        self._debug_record_request(
            {
                "method": "POST",
                "url": self.base_url,
                "headers": debug_headers,
                "payload": payload,
                "proxies": self.prepared_proxies,
            },
            extra={"allow_incomplete": allow_incomplete, "timeout_seconds": timeout_seconds},
        )

        try:
            # --- ГЛАВНЫЙ ВЫЗОВ ---
            # Передаем proxies, который пришел аргументом
            response = self._get_http_session().post(
                self.base_url,
                headers=headers,
                json=payload,
                proxies=self.prepared_proxies,
                timeout=timeout_seconds,
                stream=use_stream,
            )

            # --- Обработка ответа ---

            if response.status_code == 200:
                if use_stream:
                    # Потоковый путь. В отличие от синхронного ниже, здесь НЕ
                    # требуем finish_reason == "stop" -- ни один другой хендлер
                    # (см. deepseek.py) такого требования к стриму не предъявляет,
                    # а локальные OpenAI-совместимые серверы не всегда шлют
                    # финальный чанк с явным finish_reason. Единственный особый
                    # случай -- "length" + allow_incomplete, как и раньше.
                    try:
                        content, finish_reason, raw_lines, saw_sse, pre_sse_lines = self._collect_local_stream(
                            response, debug=debug
                        )
                    finally:
                        # requests не возвращает соединение в пул при stream=True,
                        # пока response не закрыт явно -- закрываем и на успехе, и
                        # при исключении (в т.ч. PartialGenerationError из-за обрыва).
                        self._close_stream_response(response)

                    fallback_result = None if saw_sse else self._try_parse_non_sse_json_body(pre_sse_lines)
                    if fallback_result is not None and fallback_result.get("choices"):
                        # Сервер проигнорировал payload["stream"]=True и ответил
                        # обычным JSON одним телом -- разбираем как синхронный ответ,
                        # вместо того чтобы терять текст.
                        self._debug_record_response(
                            fallback_result,
                            status="http_200",
                            extra={"mode": "full_ignored_stream_flag", "http_status": response.status_code},
                        )
                        self._remember_openai_usage(fallback_result.get("usage"))
                        choice = fallback_result["choices"][0]
                        content = choice.get("message", {}).get("content", "") or ""
                        finish_reason = choice.get("finish_reason")
                    elif raw_lines is not None:
                        self._debug_record_response(
                            "\n".join(raw_lines),
                            status=finish_reason or "stream",
                            extra={"mode": "stream", "http_status": response.status_code},
                        )

                    has_content = bool(content) or finish_reason is not None
                    if not has_content:
                        raise Exception("Пустой потоковый ответ от сервера: не получено ни одного choices-чанка")

                    if finish_reason == "length" and allow_incomplete:
                        # Логируем предупреждение через воркер (это потокобезопасно)
                        if "max_tokens" in payload:
                            limit_source = f"client max_tokens={payload['max_tokens']}"
                        else:
                            limit_source = "server/context limit; client max_tokens was not set"
                        log_payload = {'message': f"[WARN] Ответ локальной модели обрезан лимитом ({limit_source})."}
                        self.worker._post_event('log_message', log_payload)
                        raise PartialGenerationError(
                            "Ответ локальной модели обрезан лимитом",
                            partial_text=content,
                            reason="LENGTH",
                        )

                    return content

                # Синхронный (эталонный) путь -- поведение не изменилось.
                result = response.json()
                self._debug_record_response(
                    result,
                    status="http_200",
                    extra={"mode": "full", "http_status": response.status_code},
                )
                # An answer cut by the length limit is billed as well.
                self._remember_openai_usage(result.get("usage"))
                has_content = bool(result.get('choices'))
                if has_content:
                    choice = result['choices'][0]
                    finish_reason = choice.get('finish_reason')
                    content = choice['message']['content']

                    is_successful_stop = (finish_reason == "stop")
                    is_acceptable_incomplete = (finish_reason == "length" and allow_incomplete)

                    if is_successful_stop or is_acceptable_incomplete:
                        if is_acceptable_incomplete:
                            # Логируем предупреждение через воркер (это потокобезопасно)
                            if "max_tokens" in payload:
                                limit_source = f"client max_tokens={payload['max_tokens']}"
                            else:
                                limit_source = "server/context limit; client max_tokens was not set"
                            log_payload = {'message': f"[WARN] Ответ локальной модели обрезан лимитом ({limit_source})."}
                            self.worker._post_event('log_message', log_payload)
                            raise PartialGenerationError(
                                "Ответ локальной модели обрезан лимитом",
                                partial_text=content,
                                reason="LENGTH",
                            )

                        return content
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
        except (
            ContentFilterError, NetworkError, LocationBlockedError,
            RateLimitExceededError, ModelNotFoundError, ValidationFailedError,
            TemporaryRateLimitError, PartialGenerationError,
        ):
            raise
        except requests.exceptions.Timeout:
            self._drop_http_session()
            raise NetworkError(f"Таймаут запроса ({timeout_seconds}с). Модель думает слишком долго.", delay_seconds=30)
        except requests.exceptions.ConnectionError as e:
            self._drop_http_session()
            raise NetworkError(f"Нет соединения с {self.base_url}. Сервер запущен? Ошибка: {e}", delay_seconds=60)
        except requests.exceptions.RequestException as e:
            self._drop_http_session()
            raise NetworkError(f"Сетевая ошибка requests: {e}", delay_seconds=30)
        except Exception as e:
             raise Exception(f"Критическая ошибка в локальном хендлере: {e}")
