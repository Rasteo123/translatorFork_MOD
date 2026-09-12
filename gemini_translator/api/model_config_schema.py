# gemini_translator/api/model_config_schema.py
# -*- coding: utf-8 -*-

"""Проверка конфигурации одной модели.

Нужна ровно там, где данные приходят от человека: пользовательские модели из
`custom_provider_models`. Раньше строка в поле `rpm` проходила нормализацию
насквозь и обнаруживалась через несколько часов внутри воркера.

Схема намеренно неполная. Она знает только те поля, на которые полагается
рантайм (лимиты, размеры контекста, флаги), а всё остальное — `deepseek_thinking`,
`nvidia_reasoning`, `thinkingLevel` и прочую специфику провайдеров — пропускает
нетронутым: перечислять их здесь значило бы заводить второй источник правды.

Поставляемый `config/api_providers.json` в рантайме не проверяется: это наш
артефакт, а не пользовательский ввод, и его стережёт тест
`tests/test_custom_model_validation.py::ShippedProvidersFileTests`.
"""

from pydantic import BaseModel, ConfigDict, ValidationError

# Поля, которые обязаны быть числами: по ним считаются лимиты и размеры окна.
KNOWN_NUMERIC_FIELDS = (
    "rpm",
    "tpm",
    "rpd",
    "context_length",
    "context_window",
    "input_token_limit",
    "max_output_tokens",
    "max_concurrent_requests",
    "top_k",
    "min_p",
)


class ModelConfigSchema(BaseModel):
    """Известные поля конфигурации модели; незнакомые проходят как есть."""

    model_config = ConfigDict(extra="allow")

    id: str

    rpm: int | None = None
    tpm: int | None = None
    rpd: int | None = None
    context_length: int | None = None
    context_window: int | None = None
    input_token_limit: int | None = None
    max_output_tokens: int | None = None
    max_concurrent_requests: int | None = None
    top_k: int | None = None
    min_p: int | None = None

    default_temperature: float | None = None
    default_thinking_temperature: float | None = None
    thinking_top_p: float | None = None
    top_p: float | None = None

    needs_chunking: bool | None = None
    reasoning: bool | None = None
    strip_reasoning_tags: bool | None = None


def _describe(error: ValidationError) -> str:
    parts = []
    for item in error.errors():
        field = ".".join(str(piece) for piece in item.get("loc", ())) or "<корень>"
        parts.append(f"{field}: {item.get('msg', 'некорректное значение')}")
    return "; ".join(parts)


def validate_model_config(raw_config):
    """Возвращает `(проверенный словарь, None)` либо `(None, описание ошибки)`.

    Числа, пришедшие строками, приводятся к числам: диалог настроек отдаёт их
    именно так, и считать это ошибкой пользователя нельзя.

    Строгая проверка — для точки, где модель создают: там ошибку есть кому
    показать. На загрузке уже сохранённых настроек нужен `sanitize_model_config`.
    """
    if not isinstance(raw_config, dict):
        return None, "ожидался словарь с настройками модели"

    try:
        validated = ModelConfigSchema.model_validate(raw_config)
    except ValidationError as error:
        return None, _describe(error)

    for field in KNOWN_NUMERIC_FIELDS:
        value = getattr(validated, field, None)
        if value is not None and value < 0:
            return None, f"{field}: значение не может быть отрицательным"

    return validated.model_dump(exclude_none=True), None


def sanitize_model_config(raw_config):
    """Возвращает `(словарь без негодных полей, список ошибок)`.

    Модель не выбрасывается никогда. Пропажа модели из списка выглядит как
    потеря данных и ничем не объясняется — в приложении нет места, где
    пользователь увидел бы причину. Поэтому негодным считается ОТДЕЛЬНОЕ ПОЛЕ:
    его убирают, и провайдер применяет к модели своё значение по умолчанию.
    Главное достигнуто — строка вместо числа не доедет до воркера.
    """
    if not isinstance(raw_config, dict):
        return {}, ["ожидался словарь с настройками модели"]

    cleaned, error = validate_model_config(raw_config)
    if error is None:
        return cleaned, []

    # Разбираем по полю: выясняем, какие именно значения непригодны.
    errors = []
    candidate = dict(raw_config)
    for field in KNOWN_NUMERIC_FIELDS:
        if field not in candidate:
            continue
        probe = {"id": str(candidate.get("id") or "model"), field: candidate[field]}
        _probe_result, field_error = validate_model_config(probe)
        if field_error:
            errors.append(field_error)
            candidate.pop(field)

    cleaned, error = validate_model_config(candidate)
    if error is not None:
        # Непригодно что-то за пределами числовых полей — например, сам id.
        return {}, errors + [error]
    return cleaned, errors
