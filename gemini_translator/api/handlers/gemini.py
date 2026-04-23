import aiohttp
import asyncio
import json
import traceback
import re
from ..base import BaseApiHandler
from ..errors import (
    ContentFilterError, NetworkError, LocationBlockedError, 
    RateLimitExceededError, ModelNotFoundError, ValidationFailedError, 
    TemporaryRateLimitError, PartialGenerationError
)

class GeminiApiHandler(BaseApiHandler):
    
    def setup_client(self, client_override=None, proxy_settings=None):
        super().setup_client(client_override, proxy_settings)

        if not client_override or not hasattr(client_override, 'api_key'):
            return False
            
        self.worker.api_key = client_override.api_key
        self.worker.model_id = self.worker.model_config["id"]
        
        self.default_url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.worker.model_id}:generateContent?key={self.worker.api_key}"
        
        self._proactive_session_init()
        return True

    async def call_api(self, prompt, log_prefix, allow_incomplete=False, use_stream=True, debug=False, max_output_tokens=None):
        session = await self._get_or_create_session_internal()

        url = self.default_url.replace(":generateContent", ":streamGenerateContent") if use_stream else self.default_url
        # debug=True
        headers = {"Content-Type": "application/json"}
        contents = [{"parts": [{"text": prompt}]}]
        safety_settings = [{"category": c, "threshold": "BLOCK_NONE"} for c in ["HARM_CATEGORY_HATE_SPEECH", "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT", "HARM_CATEGORY_HARASSMENT"]]
  
        generation_config_params = {}
        temperature = self._temperature_payload_value()
        if temperature is not None:
            generation_config_params["temperature"] = temperature
        
        if max_output_tokens is not None:
            generation_config_params["maxOutputTokens"] = max_output_tokens
        elif allow_incomplete and not use_stream:
            max_tokens_from_config = self.worker.model_config.get("max_output_tokens", 8192)
            generation_config_params["maxOutputTokens"] = int(max_tokens_from_config * 0.98)
        
        if debug:
            print("\nSession Url:\n" + url + "\n\n")
        
        # Thinking Logic v2.3 (Strict Support Guard)
        # Получаем конфиг минимума. 
        # Если он строго равен False (bool), значит модель НЕ поддерживает Thinking.
        # В этом случае мы НИЧЕГО не добавляем в параметры, чтобы избежать 400 Bad Request.
        min_budget_cfg = self.worker.model_config.get("min_thinking_budget")
        
        if min_budget_cfg is not False:
            
            user_enabled = getattr(self.worker, 'thinking_enabled', False)
            user_level = getattr(self.worker, 'thinking_level', None)
            user_budget = getattr(self.worker, 'thinking_budget', 0)
            
            # Определяем режим: УРОВНИ (строка) или БЮДЖЕТ (число)
            # Если минимум задан строкой (напр. "minimal") - это уровневая модель.
            is_level_mode = isinstance(min_budget_cfg, str)
            
            # --- ЛОГИКА УРОВНЕЙ ---
            if is_level_mode:

                final_level = None
                
                # 1. Приоритет: выбор пользователя
                if user_enabled and user_level:
                    final_level = user_level
                # 2. Фолбэк: жесткий минимум из конфига (даже если юзер выключил)
                elif isinstance(min_budget_cfg, str):
                    final_level = min_budget_cfg 

                if final_level:
                    generation_config_params["thinkingConfig"] = {
                        "thinkingLevel": final_level.upper()
                    }
            
            # --- ВЕТКА Б: БЮДЖЕТ (BUDGET) ---
            else:
                final_budget = None
                
                # Приводим минимум к числу. None -> 0.
                min_val = min_budget_cfg if isinstance(min_budget_cfg, (int, float)) else 0
                
                # Строгий минимум - это число > 0. 
                # (-1 не является строгим минимумом, это "рекомендованный дефолт")
                has_strict_min = (min_val > 0)

                if user_enabled:
                    # Пользователь включил:
                    if user_budget == -1:
                        final_budget = -1
                    else:
                        # Если min_val положительный, не даем опуститься ниже.
                        # Если min_val = -1 или 0, просто берем user_budget.
                        final_budget = max(user_budget, min_val) if has_strict_min else user_budget
                else:
                    # Пользователь выключил:
                    if has_strict_min:
                        # Не можем выключить, модель требует минимум (напр. 1024).
                        final_budget = min_val
                    else:
                        # Модель позволяет выключение.
                        # ВАЖНО: Мы обязаны отправить 0, иначе API использует дефолт (часто -1).
                        final_budget = 0

                # Отправляем значение (даже если это 0), так как 0 != None
                if final_budget is not None:
                    generation_config_params["thinkingConfig"] = {"thinkingBudget": final_budget}

        
        
        payload = {"contents": contents, "generationConfig": generation_config_params, "safetySettings": safety_settings}
        if self.worker.prompt_builder.system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": self.worker.prompt_builder.system_instruction}]}

        self._debug_record_request(
            {
                "method": "POST",
                "url": url,
                "headers": headers,
                "payload": payload,
            },
            extra={"use_stream": use_stream, "allow_incomplete": allow_incomplete},
        )
        
        if debug:
            # --- ЧАСТЬ 1: ДАМП ЗАПРОСА ---
            debug_payload_str = json.dumps(payload, indent=2, ensure_ascii=False)
            print(f"--- GEMINI DEBUG REQUEST PAYLOAD ---\n{debug_payload_str}\n----------------------------------")

        try:
            async with session.post(url, headers=headers, json=payload) as response:
                
                if response.status != 200:
                    # В случае ошибки мы тоже хотим видеть сырой ответ
                    raw_error_text = await response.text()
                    self._debug_record_response(
                        raw_error_text,
                        status=f"http_{response.status}",
                        extra={"http_status": response.status, "mode": "error"},
                    )
                    if debug:
                        print(f"--- GEMINI DEBUG ERROR RESPONSE ---\n{raw_error_text}\n-----------------------------------")
                    await self._handle_error_response(response, None)

                # --- ВЕТКА 1: ПРОФЕССИОНАЛЬНЫЙ СТРИМИНГ ---
                if use_stream:
                    collected_text = ""
                    final_finish_reason = None
                    buffer = ""
                    decoder = json.JSONDecoder()
                    debug_stream_chunks = [] if (self._has_debug_trace() or debug) else None
                     
                    if debug:
                        debug_stream_chunks = [] # Буфер для сырого дампа

                    try:
                        async for chunk_bytes in response.content.iter_any():
                            chunk_str = chunk_bytes.decode('utf-8')
                            if debug_stream_chunks is not None:
                                debug_stream_chunks.append(chunk_str) # Собираем сырой дамп
                            
                            buffer += chunk_str
                            
                            if buffer.strip().startswith('['): buffer = buffer.strip()[1:]
                            while buffer:
                                buffer = buffer.lstrip()
                                if buffer.startswith(','): buffer = buffer[1:].lstrip()
                                if buffer.startswith(']'): buffer = ""; break
                                
                                try:
                                    chunk_data, end_index = decoder.raw_decode(buffer)
                                    buffer = buffer[end_index:]
                                    
                                    # --- 1. ПРОВЕРКА НА ОШИБКУ ВНУТРИ СТРИМА (FIX) ---
                                    if 'error' in chunk_data:
                                        error_msg = chunk_data['error'].get('message', 'Unknown Stream Error')
                                        error_status = chunk_data['error'].get('status', 'API_ERROR')
                                        # Мы прерываемся, но СОХРАНЯЕМ накопленный текст (collected_text)
                                        raise PartialGenerationError(
                                            f"Gemini Stream Error: {error_msg}", 
                                            partial_text=collected_text, 
                                            reason=error_status # Теперь причиной будет "INTERNAL" или код ошибки
                                        )

                                    # --- 2. Стандартная обработка ---
                                    if chunk_data.get('promptFeedback', {}).get('blockReason'):
                                        raise ContentFilterError(f"Блокировка на уровне промпта: {chunk_data['promptFeedback']['blockReason']}")

                                    if 'candidates' in chunk_data and chunk_data['candidates']:
                                        candidate = chunk_data['candidates'][0]
                                        for part in candidate.get('content', {}).get('parts', []):
                                            collected_text += part.get('text', '')
                                        
                                        if candidate.get('finishReason'):
                                            final_finish_reason = candidate.get('finishReason')

                                except json.JSONDecodeError:
                                    break
                                    
                    finally: # Гарантируем вывод дампа даже при ошибке
                        if debug_stream_chunks is not None:
                            self._debug_record_response(
                                "".join(debug_stream_chunks),
                                status=final_finish_reason or "stream",
                                extra={"mode": "stream", "http_status": response.status},
                            )
                        if debug:
                            # --- ЧАСТЬ 2 (STREAM): ДАМП ОТВЕТА ---
                            full_raw_stream = "".join(debug_stream_chunks)
                            print(f"--- GEMINI DEBUG RAW STREAM RESPONSE ---\n{full_raw_stream}\n--------------------------------------")
                    
                    if final_finish_reason == "STOP": return collected_text
                    
                    # FIX: Если причина None, но текст есть — ставим дефолтную причину
                    safe_reason = final_finish_reason if final_finish_reason else "UNKNOWN_INTERRUPT"
                    
                    if collected_text:
                        raise PartialGenerationError(f"Генерация прервана (причина: {safe_reason})", collected_text, safe_reason)
                    
                    if safe_reason in ["SAFETY", "PROHIBITED_CONTENT"]:
                        raise ContentFilterError(f"Ответ заблокирован (причина: {safe_reason})")
                    raise ValidationFailedError(f"Генерация без результата (причина: {safe_reason})")

                # --- ВЕТКА 2: ОБЫЧНЫЙ ЗАПРОС ---
                else:
                    # --- ЧАСТЬ 2 (FULL): ДАМП ОТВЕТА ---
                    # Сначала читаем сырые байты, чтобы их можно было залогировать
                    raw_response_bytes = await response.read()
                    response_text_for_log = raw_response_bytes.decode('utf-8', 'ignore')
                    self._debug_record_response(
                        response_text_for_log,
                        status="http_200",
                        extra={"mode": "full", "http_status": response.status},
                    )
                    if debug:
                        print(f"--- GEMINI DEBUG RAW FULL RESPONSE ---\n{response_text_for_log}\n------------------------------------")
                    
                    # И только потом парсим из памяти
                    response_data = json.loads(raw_response_bytes)
                    
                    if response_data.get('promptFeedback', {}).get('blockReason'):
                        raise ContentFilterError(f"Блокировка на уровне промпта: {response_data['promptFeedback']['blockReason']}")
                    
                    if 'candidates' in response_data and response_data['candidates']:
                        candidate = response_data['candidates'][0]
                        finish_reason = candidate.get('finishReason')
                        text_content = ''.join(part.get('text', '') for part in candidate.get('content', {}).get('parts', []))

                        if finish_reason == "STOP": return text_content
                        if text_content:
                            raise PartialGenerationError(f"Генерация прервана (причина: {finish_reason})", text_content, finish_reason)
                        if finish_reason in ["SAFETY", "PROHIBITED_CONTENT"]:
                           raise ContentFilterError(f"Ответ заблокирован (причина: {finish_reason})")
                        raise ValidationFailedError(f"Генерация без результата (причина: {finish_reason})")
                    
                    return ""

        except asyncio.TimeoutError:
            raise NetworkError(f"Таймаут запроса к Gemini.", delay_seconds=30)
        except (aiohttp.ClientError, OSError) as e:
            # Это подавит трейсбек в консоли и отправит ошибку в штатный обработчик ретраев
            error_msg = f"Сбой сети/SSL ({type(e).__name__}): {e}"
            raise NetworkError(error_msg, delay_seconds=30) from e
        
        except (RateLimitExceededError, ContentFilterError, NetworkError, 
                PartialGenerationError, ModelNotFoundError, LocationBlockedError, 
                ValidationFailedError, TemporaryRateLimitError) as e:
            raise e
        
        except Exception as e:
            traceback.print_exc()
            raise Exception(f"Критическая ошибка при работе с Gemini REST API: {e}")
     
    async def _handle_error_response(self, response, response_json_arg):
        # 1. Получаем тело ошибки максимально надежно
        error_dict = {}
        error_text_raw = ""
        try:
            error_text_raw = await response.text()
            error_dict = json.loads(error_text_raw)
        except:
            error_dict = {'error': {'message': f"Status {response.status}: {error_text_raw[:200]}"}}
        
        # Обработка случая, если API вернул список ошибок (бывает у Google)
        if isinstance(error_dict, list) and error_dict:
            error_dict = error_dict[0]
        
        if not isinstance(error_dict, dict):
             error_dict = {'error': {'message': f"Unknown error format: {str(error_dict)}"}}

        error_details = error_dict.get('error', {})
        # Если message нет внутри error, возможно это плоский словарь
        error_message = error_details.get('message') or str(error_dict)
        error_str = error_message.lower()

        # --- КЛАССИФИКАЦИЯ ОШИБОК ---

        # 400: Bad Request
        if response.status == 400:
            if "user location" in error_str: raise LocationBlockedError("Геоблокировка Gemini (User location is not supported).")
            if "api key" in error_str: raise RateLimitExceededError(f"Невалидный API ключ (400): {self.worker.api_key[-4:]}.")
            if "model" in error_str: raise ModelNotFoundError(f"Модель не найдена или не поддерживается: {error_message}")
            raise ValidationFailedError(f"Ошибка валидации запроса (400): {error_message}")

        # 401/403: Permissions
        if response.status in [401, 403]:
            if "user location" in error_str: raise LocationBlockedError("Геоблокировка Gemini.")
            if any(x in error_str for x in ["suspended", "api key", "permission"]): 
                raise RateLimitExceededError(f"Ошибка доступа ({response.status}): {error_message}")
            if "model" in error_str: raise ModelNotFoundError(f"Модель недоступна: {error_message}")
            raise RateLimitExceededError(f"Ошибка доступа ({response.status}): {error_message}")
        if response.status in [404]:
            if "model" in error_str: raise ModelNotFoundError(f"Модель недоступна: {error_message}")
        
        # 429: Too Many Requests (САМОЕ ВАЖНОЕ)
        if response.status == 429:
            if "perday" in error_str:
                 raise RateLimitExceededError(f"Суточный лимит для ключа …{self.worker.api_key[-4:]} исчерпан: {error_message}")
            
            # 1. Пытаемся найти точное время, которое просит сервер
            retry_delay_seconds = self._extract_retry_delay(error_details, error_message)

            if retry_delay_seconds is not None:
                final_delay = retry_delay_seconds + 2 # Добавляем 2 секунды буфера
                raise TemporaryRateLimitError(f"API запросил паузу на {final_delay}с. ({error_message[:100]})", delay_seconds=final_delay)
            
            # 2. Дефолтная пауза для RPM
            raise TemporaryRateLimitError(f"Временный лимит запросов (429).", delay_seconds=60)

        # 5xx: Server Errors
        if response.status >= 500:
            if response.status == 503:
                raise NetworkError("Сервер Gemini перегружен (503).", delay_seconds=20)
            raise NetworkError(f"Ошибка сервера ({response.status}): {error_message}", delay_seconds=25)
        
        raise Exception(f"Неизвестная ошибка Gemini ({response.status}): {error_message}")

    def _extract_retry_delay(self, error_details: dict, error_message: str) -> int | None:
        """
        Универсальный поисковик времени задержки.
        Ищет retryDelay в JSON (рекурсивно) или в тексте сообщения.
        """
        # --- 1. ЯВНАЯ ПРОВЕРКА (Самый частый путь Google) ---
        try:
            details_list = error_details.get('details', [])
            if details_list and isinstance(details_list, list) and isinstance(details_list[0], dict):
                 metadata = details_list[0].get('metadata', {})
                 if metadata:
                     delay_str = metadata.get('retryInfo', {}).get('retryDelay', {}).get('seconds')
                     if delay_str is not None:
                         return int(delay_str)
        except (ValueError, TypeError, IndexError, AttributeError):
            pass

        # --- 2. ГЛУБОКИЙ РЕКУРСИВНЫЙ ПОИСК ---
        def _parse_delay_value(value) -> int | None:
            try:
                if isinstance(value, (int, float)): return int(value)
                if isinstance(value, str):
                    value_lower = value.lower()
                    if value_lower.endswith('s'): return int(float(value_lower[:-1]))
                    return int(float(value))
            except (ValueError, TypeError): return None
            return None

        def _find_delay_recursively(data) -> int | None:
            if isinstance(data, dict):
                for key, value in data.items():
                    key_lower = key.lower()
                    if 'retry' in key_lower and 'delay' in key_lower:
                        delay = _parse_delay_value(value)
                        if delay is not None: return delay
                        # Google иногда кладет структуру {'seconds': '60'}
                        if isinstance(value, dict):
                             delay_from_seconds = _parse_delay_value(value.get('seconds'))
                             if delay_from_seconds is not None: return delay_from_seconds
                    found_in_value = _find_delay_recursively(value)
                    if found_in_value is not None: return found_in_value
            elif isinstance(data, list):
                for item in data:
                    found_in_item = _find_delay_recursively(item)
                    if found_in_item is not None: return found_in_item
            return None

        delay_from_structure = _find_delay_recursively(error_details)
        if delay_from_structure is not None:
            return delay_from_structure

        # --- 3. ПОИСК В ТЕКСТЕ (Regex) ---
        match = re.search(r"retry in ([\d.]+)s", error_message)
        if match:
            try:
                return int(float(match.group(1)))
            except (ValueError, IndexError):
                pass
        
        return None
