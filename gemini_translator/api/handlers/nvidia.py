# -*- coding: utf-8 -*-

import aiohttp
import asyncio
import json
import re
import traceback

from ..base import BaseApiHandler
from ..errors import (
    ContentFilterError,
    LocationBlockedError,
    ModelNotFoundError,
    NetworkError,
    PartialGenerationError,
    RateLimitExceededError,
    TemporaryRateLimitError,
    ValidationFailedError,
)


class NvidiaApiHandler(BaseApiHandler):
    """
    Хендлер для NVIDIA NIM API.
    Использует OpenAI-совместимый REST API (v1/chat/completions).
    Документация: https://docs.api.nvidia.com
    """

    STATUS_ENDPOINT_TEMPLATE = "https://integrate.api.nvidia.com/v1/status/{request_id}"

    def setup_client(self, client_override=None, proxy_settings=None):
        super().setup_client(client_override, proxy_settings)

        if not client_override:
            return False

        self.worker.api_key = client_override.api_key
        primary_model_id = self.worker.model_config.get("id", "meta/llama-3.3-70b-instruct")
        alternate_ids = self.worker.model_config.get("alternate_ids", [])
        self._model_id_candidates = [primary_model_id] + [
            model_id for model_id in alternate_ids if model_id and model_id != primary_model_id
        ]
        self._model_id_index = 0
        self.worker.model_id = self._model_id_candidates[self._model_id_index]

        self.base_url = (
            self.worker.model_config.get("base_url")
            or self.worker.provider_config.get("base_url")
            or "https://integrate.api.nvidia.com/v1/chat/completions"
        )

        self._proactive_session_init()
        return True

    def _switch_to_next_model_id(self):
        if self._model_id_index + 1 >= len(self._model_id_candidates):
            return None

        self._model_id_index += 1
        self.worker.model_id = self._model_id_candidates[self._model_id_index]
        return self.worker.model_id

    def _normalize_content(self, content):
        if isinstance(content, str):
            return content

        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text")
                    if text is None:
                        text = item.get("content")
                    if text is None and item.get("type") == "output_text":
                        text = item.get("text")
                    if text is not None:
                        parts.append(str(text))
                elif item is not None:
                    parts.append(str(item))
            return "".join(parts)

        if content is None:
            return ""

        return str(content)

    def _clean_response_text(self, text):
        cleaned = self._normalize_content(text)
        if self.worker.model_config.get("strip_reasoning_tags"):
            cleaned = re.sub(r"<think>.*?</think>\s*", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
        return cleaned.strip()

    def _extract_text_from_result(self, result, allow_incomplete=False):
        candidates = [result]
        if isinstance(result, dict):
            for key in ("response", "result", "data", "output"):
                nested = result.get(key)
                if isinstance(nested, dict):
                    candidates.append(nested)

        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue

            choices = candidate.get("choices")
            if isinstance(choices, list) and choices:
                choice = choices[0]
                if not isinstance(choice, dict):
                    continue

                finish_reason = choice.get("finish_reason")
                message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
                content = message.get("content")

                if content is None and isinstance(choice.get("delta"), dict):
                    content = choice["delta"].get("content")
                if content is None:
                    content = choice.get("text")

                content = self._clean_response_text(content)
                if content:
                    if finish_reason == "length" and not allow_incomplete:
                        raise PartialGenerationError(
                            "Превышен лимит токенов (length)",
                            partial_text=content,
                            reason="LENGTH",
                        )
                    return content

            message = candidate.get("message")
            if isinstance(message, dict):
                content = self._clean_response_text(message.get("content"))
                if content:
                    return content

            for key in ("output_text", "generated_text", "text", "content"):
                if key in candidate:
                    content = self._clean_response_text(candidate.get(key))
                    if content:
                        return content

        raise Exception(f"Пустой ответ JSON от NVIDIA NIM: {result}")

    def _extract_request_id(self, response, response_text=""):
        for header_name in (
            "NVCF-REQID",
            "NVCF-REQUEST-ID",
            "X-Request-ID",
            "request-id",
            "x-request-id",
            "Location",
            "location",
        ):
            header_value = response.headers.get(header_name)
            if not header_value:
                continue

            header_value = header_value.strip()
            if header_name.lower() == "location":
                header_value = header_value.rstrip("/").split("/")[-1].split("?")[0]

            if header_value:
                return header_value

        if response_text:
            try:
                payload = json.loads(response_text)
            except json.JSONDecodeError:
                payload = None

            if isinstance(payload, dict):
                for key in ("requestId", "request_id", "reqId", "id"):
                    value = payload.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()

                for container_key in ("data", "response", "result"):
                    container = payload.get(container_key)
                    if not isinstance(container, dict):
                        continue
                    for key in ("requestId", "request_id", "reqId", "id"):
                        value = container.get(key)
                        if isinstance(value, str) and value.strip():
                            return value.strip()

            match = re.search(r'"(?:requestId|request_id|reqId|id)"\s*:\s*"([^"]+)"', response_text)
            if match:
                return match.group(1).strip()

        return None

    def _get_retry_delay_seconds(self, response, fallback_seconds=2.0):
        retry_after = response.headers.get("Retry-After")
        if not retry_after:
            return fallback_seconds

        try:
            return max(1.0, float(retry_after))
        except (TypeError, ValueError):
            return fallback_seconds

    async def _poll_accepted_request(self, session, headers, initial_response, allow_incomplete=False):
        initial_text = await initial_response.text()
        request_id = self._extract_request_id(initial_response, initial_text)
        if not request_id:
            raise NetworkError("NVIDIA NIM вернул 202 Accepted, но не передал requestId для status polling.")

        status_url = self.STATUS_ENDPOINT_TEMPLATE.format(request_id=request_id)
        default_delay = float(self.worker.model_config.get("status_poll_delay_seconds", 2.0))
        poll_delay = self._get_retry_delay_seconds(initial_response, default_delay)
        max_attempts = int(self.worker.model_config.get("status_poll_attempts", 120))

        self.worker._post_event(
            "log_message",
            {
                "message": (
                    f"[INFO] NVIDIA NIM перевел запрос для {self.worker.model_id} "
                    "в async-обработку (202). Запускаем status polling…"
                )
            },
        )

        for _ in range(max_attempts):
            await asyncio.sleep(poll_delay)

            async with session.get(status_url, headers=headers) as status_response:
                if status_response.status == 202:
                    poll_delay = self._get_retry_delay_seconds(status_response, default_delay)
                    continue

                if status_response.status == 200:
                    try:
                        result = await status_response.json(content_type=None)
                    except (aiohttp.ContentTypeError, json.JSONDecodeError):
                        raw_text = await status_response.text()
                        if raw_text.strip():
                            return self._clean_response_text(raw_text)
                        raise NetworkError("NVIDIA NIM вернул пустой ответ после status polling.")

                    return self._extract_text_from_result(result, allow_incomplete=allow_incomplete)

                error_text = await status_response.text()
                txt_low = error_text.lower()

                if status_response.status in [500, 502, 503]:
                    poll_delay = max(default_delay, self._get_retry_delay_seconds(status_response, 5.0))
                    continue

                if status_response.status in [401, 403]:
                    raise RateLimitExceededError(
                        f"Ошибка авторизации NVIDIA status polling ({status_response.status}): "
                        f"проверьте API-ключ (…{self.worker.api_key[-4:]})."
                    )

                if status_response.status == 404:
                    raise NetworkError(f"NVIDIA status polling не нашел requestId {request_id} (404).")

                if status_response.status == 429:
                    raise TemporaryRateLimitError(
                        "Превышен лимит запросов NVIDIA NIM при status polling (429).",
                        delay_seconds=20,
                    )

                if "quota" in txt_low or "credits" in txt_low:
                    raise RateLimitExceededError(
                        "Недостаточно кредитов NVIDIA NIM при status polling."
                    )

                raise NetworkError(
                    f"Ошибка NVIDIA NIM status polling ({status_response.status}): {error_text[:200]}"
                )

        raise NetworkError(
            "NVIDIA NIM не вернул финальный ответ после status polling.",
            delay_seconds=20,
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
        session = await self._get_or_create_session_internal()

        headers = {
            "Authorization": f"Bearer {self.worker.api_key}",
            "Content-Type": "application/json",
        }

        messages = (
            [{"role": "system", "content": self.worker.prompt_builder.system_instruction}]
            if self.worker.prompt_builder.system_instruction
            else []
        ) + [{"role": "user", "content": prompt}]

        payload = {
            "model": self.worker.model_id,
            "messages": messages,
            "temperature": self.worker.temperature,
            "stream": use_stream,
        }

        if max_output_tokens:
            payload["max_tokens"] = max_output_tokens
        elif allow_incomplete:
            payload["max_tokens"] = int(self.worker.model_config.get("max_output_tokens", 8192) * 0.98)

        max_retries = 3
        retry_count = 0

        while retry_count < max_retries:
            try:
                async with session.post(self.base_url, headers=headers, json=payload) as response:
                    if response.status == 202:
                        return await self._poll_accepted_request(
                            session=session,
                            headers=headers,
                            initial_response=response,
                            allow_incomplete=allow_incomplete,
                        )

                    if response.status != 200:
                        error_text = await response.text()
                        txt_low = error_text.lower()

                        if response.status in [500, 502, 503]:
                            wait_time = 15.0 * (retry_count + 1)
                            self.worker._post_event(
                                "log_message",
                                {
                                    "message": (
                                        f"⏳ Сервер NVIDIA NIM перегружен ({response.status}). "
                                        f"Ждем {wait_time}с перед повтором."
                                    )
                                },
                            )
                            await asyncio.sleep(wait_time)
                            retry_count += 1
                            continue

                        if response.status in [401, 403]:
                            raise RateLimitExceededError(
                                f"Ошибка авторизации NVIDIA ({response.status}): "
                                f"проверьте API-ключ (…{self.worker.api_key[-4:]})."
                            )

                        if response.status == 402 or "quota" in txt_low or "credits" in txt_low:
                            raise RateLimitExceededError("Недостаточно кредитов NVIDIA NIM (402).")

                        if response.status == 429:
                            raise TemporaryRateLimitError(
                                "Превышен лимит запросов NVIDIA NIM (429).",
                                delay_seconds=20,
                            )

                        if response.status == 404:
                            previous_model_id = self.worker.model_id
                            alternate_model_id = self._switch_to_next_model_id()
                            if alternate_model_id:
                                payload["model"] = alternate_model_id
                                self.worker._post_event(
                                    "log_message",
                                    {
                                        "message": (
                                            f"[INFO] Модель {previous_model_id} вернула 404 в NVIDIA NIM. "
                                            f"Пробуем alias {alternate_model_id}."
                                        )
                                    },
                                )
                                continue

                            raise ModelNotFoundError(
                                f"Модель {self.worker.model_id} не найдена в NVIDIA NIM (404)."
                            )

                        raise NetworkError(
                            f"Ошибка NVIDIA NIM ({response.status}): {error_text[:200]}"
                        )

                    if use_stream:
                        collected_text = ""
                        finish_reason = None

                        try:
                            async for line in response.content:
                                line_str = line.decode("utf-8").strip()
                                if not line_str or line_str == "data: [DONE]":
                                    continue

                                if not line_str.startswith("data: "):
                                    continue

                                json_str = line_str[6:]
                                try:
                                    chunk = json.loads(json_str)
                                except json.JSONDecodeError:
                                    continue

                                if "choices" in chunk and chunk["choices"]:
                                    delta = chunk["choices"][0].get("delta", {})
                                    content_part = delta.get("content", "")
                                    if content_part:
                                        collected_text += content_part

                                    current_finish_reason = chunk["choices"][0].get("finish_reason")
                                    if current_finish_reason:
                                        finish_reason = current_finish_reason

                        except Exception as stream_error:
                            if collected_text:
                                raise PartialGenerationError(
                                    f"Обрыв стрима NVIDIA NIM: {stream_error}",
                                    partial_text=collected_text,
                                    reason="NETWORK_ERROR",
                                )
                            raise stream_error

                        if finish_reason == "length" and not allow_incomplete:
                            raise PartialGenerationError(
                                "Превышен лимит токенов (length)",
                                partial_text=collected_text,
                                reason="LENGTH",
                            )

                        return self._clean_response_text(collected_text)

                    result = await response.json(content_type=None)
                    return self._extract_text_from_result(result, allow_incomplete=allow_incomplete)

            except asyncio.TimeoutError:
                raise NetworkError("Таймаут соединения с NVIDIA NIM", delay_seconds=10)
            except (aiohttp.ClientError, OSError) as error:
                error_msg = (
                    f"Сбой сети/SSL ({type(error).__name__}) при запросе к NVIDIA NIM: {error}"
                )
                raise NetworkError(error_msg, delay_seconds=20) from error
            except (
                RateLimitExceededError,
                ContentFilterError,
                NetworkError,
                PartialGenerationError,
                ModelNotFoundError,
                LocationBlockedError,
                ValidationFailedError,
                TemporaryRateLimitError,
            ) as error:
                raise error
            except Exception as error:
                traceback.print_exc()
                raise Exception(f"Критическая ошибка NVIDIA NIM: {error}")

        raise NetworkError(
            "Не удалось получить ответ от NVIDIA NIM из-за перегрузки серверов (Retry Limit).",
            delay_seconds=30,
        )
