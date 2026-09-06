# -*- coding: utf-8 -*-
"""Общий цикл разбора OpenAI-совместимого SSE-стрима 'data: {...}'.

Используется хендлерами, чьи провайдеры отдают чанки в формате
choices[0].delta.content / choices[0].finish_reason (DeepSeek, Hugging Face,
NVIDIA NIM, OpenRouter). OpenModel (Anthropic-совместимый Messages API) сюда
не входит -- у него другой формат чанков (delta.text / content_block /
stop_reason) и многострочные SSE-блоки, поэтому он оставлен со своим
разбором в openmodel.py.
"""
import json


class SSEStreamInterrupted(Exception):
    """Соединение стрима оборвалось, но кое-что уже успели накопить.

    Несёт исходную ошибку и накопленное состояние на момент обрыва, чтобы
    вызывающий хендлер мог собрать свой собственный PartialGenerationError
    с провайдер-специфичным текстом сообщения (сообщения у разных хендлеров
    исторически разные, это часть их публичного поведения для пользователя).
    """

    def __init__(self, original_error, partial_text, finish_reason=None, raw_lines=None):
        super().__init__(str(original_error))
        self.original_error = original_error
        self.partial_text = partial_text
        self.finish_reason = finish_reason
        self.raw_lines = raw_lines


async def parse_openai_compatible_sse_stream(response, capture_raw=False):
    """Читает response.content построчно и собирает текст + finish_reason.

    - Пустые строки и 'data: [DONE]' пропускаются.
    - Строки без префикса 'data: ' пропускаются.
    - Невалидный JSON в отдельной строке молча пропускается (стрим продолжается).
    - choices[0].delta.content дописывается в collected_text.
    - choices[0].finish_reason запоминается (последнее непустое значение).

    capture_raw=True включает накопление сырых декодированных строк для
    последующей debug-трассировки вызывающей стороной (иначе raw_lines
    остаётся None, чтобы не тратить память впустую на обычных прогонах).

    Если соединение обрывается посреди чтения и что-то уже накоплено --
    поднимает SSEStreamInterrupted с partial_text. Если не накоплено ничего --
    исходное исключение пробрасывается как есть.

    Возвращает (collected_text, finish_reason, raw_lines_or_None).
    """
    collected_text = ""
    finish_reason = None
    raw_lines = [] if capture_raw else None

    try:
        async for line in response.content:
            line_str = line.decode("utf-8").strip()
            if raw_lines is not None:
                raw_lines.append(line_str)
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

                f_reason = chunk["choices"][0].get("finish_reason")
                if f_reason:
                    finish_reason = f_reason
    except Exception as stream_error:
        if collected_text:
            raise SSEStreamInterrupted(
                stream_error, collected_text, finish_reason, raw_lines
            ) from stream_error
        raise

    return collected_text, finish_reason, raw_lines
