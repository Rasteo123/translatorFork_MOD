"""Наборы активных ключей по провайдерам (``active_keys_by_provider``).

Ключ принадлежит провайдеру, под которым он сохранён в ``api_keys_with_status``.
Окно ключей раньше записывало в набор нового провайдера ключи прежнего, и такие
наборы уже лежат в settings.json. Отправлять провайдеру чужой ключ нельзя,
поэтому наборы сверяются с сохранёнными ключами при загрузке, сохранении
и перед использованием.
"""

from ..api import config as api_config


def saved_keys_by_provider(key_statuses) -> dict[str, list[str]]:
    """Сохранённые ключи, сгруппированные по полю ``provider``, в порядке хранения."""
    grouped: dict[str, list[str]] = {}
    for key_info in key_statuses or []:
        if not isinstance(key_info, dict):
            continue
        provider_id = str(key_info.get("provider") or "").strip()
        key = str(key_info.get("key") or "").strip()
        if not provider_id or not key:
            continue
        provider_keys = grouped.setdefault(provider_id, [])
        if key not in provider_keys:
            provider_keys.append(key)
    return grouped


def owned_active_keys(active_keys, saved_keys) -> list[str]:
    """Активные ключи, которые есть среди сохранённых ключей провайдера, без повторов."""
    saved = set(saved_keys)
    owned: list[str] = []
    for key in active_keys or ():
        key = str(key).strip()
        if key in saved and key not in owned:
            owned.append(key)
    return owned


def sanitize_active_keys_by_provider(active_keys_by_provider, key_statuses) -> dict[str, list[str]]:
    """Оставляет в наборе каждого провайдера только его сохранённые ключи.

    У провайдеров без API-ключа в наборе лежит заглушка сессии, которой среди
    сохранённых ключей нет: такие наборы остаются как есть.
    """
    saved = saved_keys_by_provider(key_statuses)
    sanitized: dict[str, list[str]] = {}
    for provider_id, keys in (active_keys_by_provider or {}).items():
        provider_id = str(provider_id or "").strip()
        if not provider_id or not isinstance(keys, (list, tuple, set)):
            continue
        keys = [str(key).strip() for key in keys if str(key).strip()]
        if api_config.provider_requires_api_key(provider_id):
            keys = owned_active_keys(keys, saved.get(provider_id, ()))
        sanitized[provider_id] = keys
    return sanitized
