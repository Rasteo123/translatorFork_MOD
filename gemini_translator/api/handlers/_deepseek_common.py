# -*- coding: utf-8 -*-
"""Общая логика построения опций DeepSeek "thinking" для payload запроса.

Используется DeepseekApiHandler (официальный API DeepSeek) и NvidiaApiHandler
(режим nvidia_reasoning == "deepseek", т.к. NVIDIA NIM хостит модели DeepSeek
через OpenAI-совместимый API с теми же полями payload).
"""


def build_deepseek_thinking_options(payload, model_config, worker):
    """Устанавливает payload["thinking"] / payload["reasoning_effort"] по конфигу модели.

    payload: dict запроса, мутируется на месте.
    model_config: dict конфигурации модели (или что-то, приводимое к dict вызывающей
        стороной — эта функция ожидает уже нормализованный dict).
    worker: объект воркера с атрибутами thinking_enabled / thinking_level (через getattr,
        оба опциональны).
    """
    configured_mode = str(model_config.get("deepseek_thinking") or "").strip().lower()
    has_thinking_config = "thinkingLevel" in model_config or "min_thinking_budget" in model_config
    supports_thinking = (
        configured_mode in {"enabled", "disabled"}
        or model_config.get("thinkingLevel") is not None
        or (has_thinking_config and model_config.get("min_thinking_budget") is not False)
    )
    if not supports_thinking:
        return

    if configured_mode in {"enabled", "disabled"}:
        thinking_enabled = configured_mode == "enabled"
    else:
        thinking_enabled = bool(getattr(worker, "thinking_enabled", False))

    payload["thinking"] = {"type": "enabled" if thinking_enabled else "disabled"}
    if not thinking_enabled:
        return

    effort = (
        getattr(worker, "thinking_level", None)
        or model_config.get("default_reasoning_effort")
        or model_config.get("min_thinking_budget")
        or "high"
    )
    effort = str(effort).strip().lower()
    payload["reasoning_effort"] = "max" if effort in {"max", "xhigh"} else "high"

    # DeepSeek ignores sampling params in thinking mode; removing them keeps debug payloads honest.
    payload.pop("temperature", None)
