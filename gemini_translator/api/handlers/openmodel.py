# -*- coding: utf-8 -*-

import aiohttp
import asyncio
import json
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


class OpenModelApiHandler(BaseApiHandler):
    """Handler for OpenModel's Anthropic-compatible Messages API."""

    DEFAULT_ROOT_URL = "https://api.openmodel.ai"
    DEFAULT_MESSAGES_PATH = "/v1/messages"
    DEFAULT_ANTHROPIC_VERSION = "2023-06-01"

    def setup_client(self, client_override=None, proxy_settings=None):
        super().setup_client(client_override, proxy_settings)

        if not client_override:
            return False

        self.worker.api_key = client_override.api_key
        self.worker.model_id = self.worker.model_config.get("id", "deepseek-v4-flash")
        self.base_url = self._normalize_messages_url(
            self.worker.model_config.get("base_url")
            or self.worker.provider_config.get("base_url")
            or self.DEFAULT_ROOT_URL
        )
        self.anthropic_version = (
            self.worker.model_config.get("anthropic_version")
            or self.worker.provider_config.get("anthropic_version")
            or self.DEFAULT_ANTHROPIC_VERSION
        )

        self._proactive_session_init()
        return True

    @classmethod
    def _normalize_messages_url(cls, base_url):
        url = str(base_url or cls.DEFAULT_ROOT_URL).strip().rstrip("/")
        if not url:
            url = cls.DEFAULT_ROOT_URL

        if url.endswith("/v1/messages"):
            return url
        if url.endswith("/v1"):
            return f"{url}/messages"
        return f"{url}{cls.DEFAULT_MESSAGES_PATH}"

    @staticmethod
    def _coerce_positive_int(value):
        if isinstance(value, bool) or value is None:
            return None
        try:
            if isinstance(value, str):
                normalized = value.strip().replace(" ", "").replace("_", "").replace(",", "")
                if not normalized.isdigit():
                    return None
                number = int(normalized)
            else:
                number = int(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None

    def _resolve_max_tokens(self, allow_incomplete=False, max_output_tokens=None):
        requested_max_tokens = self._coerce_positive_int(max_output_tokens)
        if requested_max_tokens is not None:
            return requested_max_tokens

        configured_max_tokens = self._coerce_positive_int(
            self.worker.model_config.get("max_output_tokens")
        )
        if configured_max_tokens is None:
            configured_max_tokens = 8192

        if allow_incomplete:
            return max(1, int(configured_max_tokens * 0.98))

        return configured_max_tokens

    def _build_payload(self, prompt, allow_incomplete=False, use_stream=True, max_output_tokens=None):
        payload = {
            "model": self.worker.model_id,
            "max_tokens": self._resolve_max_tokens(
                allow_incomplete=allow_incomplete,
                max_output_tokens=max_output_tokens,
            ),
            "messages": [{"role": "user", "content": prompt}],
            "stream": use_stream,
        }

        system_instruction = getattr(self.worker.prompt_builder, "system_instruction", None)
        if system_instruction:
            payload["system"] = system_instruction

        temperature = self._temperature_payload_value()
        if temperature is not None:
            payload["temperature"] = temperature

        return payload

    def _extract_text_from_content(self, content):
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

    def _extract_text_from_result(self, result, allow_incomplete=False):
        candidates = [result]
        if isinstance(result, dict):
            for key in ("message", "response", "result", "data", "output"):
                nested = result.get(key)
                if isinstance(nested, dict):
                    candidates.append(nested)

        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue

            content = candidate.get("content")
            text = self._extract_text_from_content(content).strip()
            if text:
                stop_reason = candidate.get("stop_reason")
                if stop_reason == "max_tokens" and not allow_incomplete:
                    raise PartialGenerationError(
                        "OpenModel response was cut off by max_tokens",
                        partial_text=text,
                        reason="LENGTH",
                    )
                return text

            for key in ("output_text", "generated_text", "text"):
                if key in candidate:
                    text = self._extract_text_from_content(candidate.get(key)).strip()
                    if text:
                        return text

        raise Exception(f"Empty JSON response from OpenModel: {result}")

    def _extract_stream_text_delta(self, chunk):
        if not isinstance(chunk, dict):
            return ""

        choices = chunk.get("choices")
        if isinstance(choices, list) and choices:
            choice = choices[0]
            if isinstance(choice, dict):
                delta = choice.get("delta")
                if isinstance(delta, dict):
                    return str(delta.get("content") or "")

        delta = chunk.get("delta")
        if isinstance(delta, dict):
            text = delta.get("text")
            if text is not None:
                return str(text)

        content_block = chunk.get("content_block")
        if isinstance(content_block, dict):
            text = content_block.get("text")
            if text:
                return str(text)

        return ""

    def _extract_stop_reason(self, chunk):
        if not isinstance(chunk, dict):
            return None

        delta = chunk.get("delta")
        if isinstance(delta, dict) and delta.get("stop_reason"):
            return delta.get("stop_reason")

        if chunk.get("stop_reason"):
            return chunk.get("stop_reason")

        choices = chunk.get("choices")
        if isinstance(choices, list) and choices:
            choice = choices[0]
            if isinstance(choice, dict) and choice.get("finish_reason"):
                return choice.get("finish_reason")

        return None

    async def _read_streaming_response(self, response, allow_incomplete=False, debug=False):
        collected_text = ""
        stop_reason = None
        raw_stream_lines = [] if (self._has_debug_trace() or debug) else None

        try:
            async for raw_line in response.content:
                decoded = raw_line.decode("utf-8", errors="replace")
                for line_str in decoded.splitlines():
                    line_str = line_str.strip()
                    if raw_stream_lines is not None:
                        raw_stream_lines.append(line_str)
                    if not line_str or line_str == "data: [DONE]" or line_str.startswith("event:"):
                        continue
                    if not line_str.startswith("data: "):
                        continue

                    json_str = line_str[6:]
                    try:
                        chunk = json.loads(json_str)
                    except json.JSONDecodeError:
                        continue

                    text_part = self._extract_stream_text_delta(chunk)
                    if text_part:
                        collected_text += text_part

                    current_stop_reason = self._extract_stop_reason(chunk)
                    if current_stop_reason:
                        stop_reason = current_stop_reason

        except Exception as stream_error:
            if collected_text:
                raise PartialGenerationError(
                    f"OpenModel stream interrupted: {stream_error}",
                    partial_text=collected_text,
                    reason="NETWORK_ERROR",
                )
            raise stream_error

        if raw_stream_lines is not None:
            self._debug_record_response(
                "\n".join(raw_stream_lines),
                status=stop_reason or "stream",
                extra={"mode": "stream", "http_status": response.status},
            )

        if stop_reason in {"max_tokens", "length"} and not allow_incomplete:
            raise PartialGenerationError(
                "OpenModel response was cut off by max_tokens",
                partial_text=collected_text,
                reason="LENGTH",
            )

        return collected_text.strip()

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
            "x-api-key": self.worker.api_key,
            "anthropic-version": self.anthropic_version,
            "content-type": "application/json",
        }
        payload = self._build_payload(
            prompt,
            allow_incomplete=allow_incomplete,
            use_stream=use_stream,
            max_output_tokens=max_output_tokens,
        )

        self._debug_record_request(
            {
                "method": "POST",
                "url": self.base_url,
                "headers": headers,
                "payload": payload,
            },
            extra={"use_stream": use_stream, "allow_incomplete": allow_incomplete},
        )

        max_retries = 3
        retry_count = 0

        while retry_count < max_retries:
            try:
                async with session.post(self.base_url, headers=headers, json=payload) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        txt_low = error_text.lower()
                        self._debug_record_response(
                            error_text,
                            attempt=retry_count + 1,
                            status=f"http_{response.status}",
                            extra={"mode": "error", "http_status": response.status},
                        )

                        if response.status in [500, 502, 503]:
                            wait_time = 15.0 * (retry_count + 1)
                            self.worker._post_event(
                                "log_message",
                                {
                                    "message": (
                                        f"OpenModel server is overloaded ({response.status}). "
                                        f"Retrying in {wait_time}s."
                                    )
                                },
                            )
                            await asyncio.sleep(wait_time)
                            retry_count += 1
                            continue

                        if response.status in [401, 403]:
                            raise RateLimitExceededError(
                                f"OpenModel authorization error ({response.status}); check API key (...{self.worker.api_key[-4:]})."
                            )
                        if response.status == 402 or "quota" in txt_low or "credit" in txt_low:
                            raise RateLimitExceededError("OpenModel balance or quota is exhausted.")
                        if response.status == 404:
                            raise ModelNotFoundError(f"Model {self.worker.model_id} is not available in OpenModel.")
                        if response.status == 429:
                            raise TemporaryRateLimitError("OpenModel request limit exceeded (429).", delay_seconds=20)

                        raise NetworkError(f"OpenModel error ({response.status}): {error_text[:200]}")

                    if use_stream:
                        return await self._read_streaming_response(
                            response,
                            allow_incomplete=allow_incomplete,
                            debug=debug,
                        )

                    result = await response.json(content_type=None)
                    self._debug_record_response(
                        result,
                        attempt=retry_count + 1,
                        status="http_200",
                        extra={"mode": "full", "http_status": response.status},
                    )
                    return self._extract_text_from_result(result, allow_incomplete=allow_incomplete)

            except asyncio.TimeoutError:
                raise NetworkError("OpenModel connection timeout", delay_seconds=10)
            except (aiohttp.ClientError, OSError) as error:
                raise NetworkError(
                    f"Network/SSL error ({type(error).__name__}) during OpenModel request: {error}",
                    delay_seconds=20,
                ) from error
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
                raise Exception(f"Critical OpenModel error: {error}")

        raise NetworkError(
            "Failed to get OpenModel response because the server stayed overloaded.",
            delay_seconds=30,
        )
