# -*- coding: utf-8 -*-
"""Общий helper для остановки треда/сессии обработчика провайдера.

Раньше существовало два независимых дубликата `_cleanup_handler`:
  - async-версия в gemini_translator/core/worker_helpers/provider_orchestrator.py,
    которая полностью глотала исключения очистки без единой записи в лог;
  - sync-метод ConsistencyEngine._cleanup_handler в
    gemini_translator/core/consistency_engine.py, логировавший обе точки
    отказа через logger.warning.

Каноническим выбрано поведение с логированием (см. cluster-44): обе точки
отказа — сам вызов `_close_thread_session_internal()` и ожидание его
awaitable-результата — логируются раздельно и не подавляются молча.
"""

import inspect
import logging

logger = logging.getLogger(__name__)


async def cleanup_provider_handler(handler, log: logging.Logger | None = None) -> None:
    """Закрывает внутреннюю тред/сессию обработчика `handler`, если она есть.

    `handler` может не иметь атрибута `_close_thread_session_internal` вовсе
    (тогда это no-op) либо иметь его как обычный callable, возвращающий либо
    plain-значение, либо awaitable (для async-обработчиков). Обе точки отказа
    логируются через `log` (по умолчанию — логгер этого модуля) отдельными
    предупреждениями, чтобы не терять диагностику утечек соединений/сессий.
    """
    log = log or logger
    cleanup = getattr(handler, "_close_thread_session_internal", None)
    if not callable(cleanup):
        return

    try:
        result = cleanup()
    except Exception as e:
        log.warning("Failed to start handler cleanup: %s", e)
        return

    if inspect.isawaitable(result):
        try:
            await result
        except Exception as e:
            log.warning("Failed to cleanup handler resources: %s", e)
