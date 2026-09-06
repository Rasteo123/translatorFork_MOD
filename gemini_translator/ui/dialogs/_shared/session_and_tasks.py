"""Общая логика управления ёмкостью сессии и очередью задач.

Этот блок был по отдельности скопирован из ``setup.py`` (``InitialSetupPage``)
в ``glossary_dialogs/ai_generation.py`` (``GenerationSessionPage``) и к
моменту дедупликации успел разойтись:

* копия в ``ai_generation.py`` не учитывала ``browser_profiles_count`` для
  провайдеров без API-ключа на legacy worker thread, хотя оба диалога
  используют один и тот же ``ModelSettingsWidget`` с этим полем;
* копия в ``ai_generation.py`` не поддерживала действия ``split_batch`` и
  ``reorder_batch_chapters`` (её ``chapter_list_widget`` их не эмитит, так
  что для неё это не баг, а просто более узкий набор операций).

Здесь — единственная реализация; диалоги параметризуют её под свои виджеты
вместо копирования тел методов.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from gemini_translator.api import config as api_config


def get_available_session_capacity(key_widget, model_settings_widget=None) -> int:
    """Считает, сколько параллельных AI-сессий можно запустить прямо сейчас.

    ``key_widget`` — виджет управления ключами (``KeyManagementWidget``):
    должен предоставлять ``get_selected_provider()``, ``get_active_keys()``
    и опционально ``can_start_ai_session()``.

    ``model_settings_widget``, если передан, используется для провайдеров
    без API-ключа на legacy worker thread (например, локальные браузерные
    профили): значение ``browser_profiles_count`` из его настроек может
    увеличить ёмкость сверх числа активных ключей. Диалоги без такого
    виджета передают ``None`` — тогда эта ветка просто не применяется.
    """
    provider_id = key_widget.get_selected_provider()
    active_sessions = len(key_widget.get_active_keys())

    can_start_ai_session = getattr(key_widget, "can_start_ai_session", None)
    if active_sessions <= 0 and callable(can_start_ai_session) and can_start_ai_session():
        return 1
    if active_sessions <= 0:
        return 0

    if model_settings_widget is not None:
        provider_config = api_config.api_providers().get(provider_id, {})
        if (
            not api_config.provider_requires_api_key(provider_id)
            and api_config.uses_legacy_worker_thread(provider_config)
        ):
            try:
                profile_count = int(
                    model_settings_widget.get_settings().get('browser_profiles_count', 1) or 1
                )
            except (TypeError, ValueError):
                profile_count = 1
            if profile_count > 1:
                return max(1, profile_count)

    provider_limit = api_config.provider_max_instances(provider_id)
    if provider_limit is None or provider_limit <= 0:
        provider_limit = active_sessions
    return min(active_sessions, provider_limit)


_REORDER_ACTIONS = ('top', 'bottom', 'up', 'down')


def resolve_task_action(
    task_manager,
    action: str,
    payload: Any,
    *,
    support_batch_split: bool = False,
) -> Tuple[Optional[Any], list]:
    """Возвращает ``(target_method, args)`` для действия над задачами очереди
    в ``task_manager``, либо ``(None, [])``, если действие не поддерживается.

    ``support_batch_split=True`` включает ``split_batch`` и
    ``reorder_batch_chapters`` — их поддерживает только диалог с виджетом
    управления пакетами (``setup.py``); ``ai_generation.py`` их не эмитит и
    оставляет флаг выключенным.
    """
    if action in _REORDER_ACTIONS:
        return task_manager.reorder_tasks, [action, payload]
    if action == 'remove':
        return task_manager.remove_tasks, [payload]
    if action == 'duplicate':
        return task_manager.duplicate_tasks, [payload]
    if support_batch_split:
        if action == 'split_batch':
            return task_manager.split_batches_into_chapters, [payload]
        if action == 'reorder_batch_chapters':
            return task_manager.reorder_batch_chapters, [payload[0], payload[1]]
    return None, []


def task_action_status_message(action: str) -> str:
    """Текст для статус-бара во время фоновой операции над очередью."""
    if action == 'split_batch':
        return "Разбиваю пакеты на главы..."
    if action == 'reorder_batch_chapters':
        return "Сохраняю порядок глав в пакете..."
    return "Обновление списка задач..."
