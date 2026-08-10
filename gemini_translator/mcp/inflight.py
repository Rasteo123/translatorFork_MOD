"""Реестр MCP-запросов, висящих в ожидании ответа AI-клиента.

Зачем отдельный реестр. Штатная отмена MCP-запроса идёт через
`asyncio.CancelledError` в воркере: движок ловит «стоп», отменяет корутины, и
`McpApiHandler` просит демон снять запрос. Но заявка, которую не забрал ни один
AI-агент, держит поток пула в чтении HTTP до самого таймаута (до 30 минут), и
если поток движка в этот момент ждёт результата, он не успевает обработать
событие «стоп» — Qt-слоты движка встают, интерфейс замирает в «Остановка…», а
отмена не доезжает до демона.

Поэтому идентификаторы висящих запросов живут здесь, в модуле, а не внутри
движка: нажатие «стоп» снимает их напрямую из потока интерфейса, не завися от
того, жив ли цикл событий движка.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_inflight: set[str] = set()
_cancel_threads: set[threading.Thread] = set()

CANCEL_RETRIES = 2
CANCEL_RETRY_DELAY_SEC = 0.5


def register(request_id) -> None:
    """Запоминает запрос как висящий."""
    if not request_id:
        return
    with _lock:
        _inflight.add(str(request_id))


def unregister(request_id) -> None:
    """Убирает запрос из реестра. Неизвестный id — не ошибка."""
    if not request_id:
        return
    with _lock:
        _inflight.discard(str(request_id))


def snapshot() -> list[str]:
    with _lock:
        return sorted(_inflight)


def clear() -> None:
    with _lock:
        _inflight.clear()


def _default_client_factory():
    from .client import load_client

    return load_client()


def _cancel_one(client, request_id) -> bool:
    cancel = getattr(client, "cancel_ai_completion", None)
    if not callable(cancel):
        return False
    try:
        cancel(str(request_id))
        return True
    except Exception:
        return False


def _cancel_ids(ids, client_factory) -> int:
    import time

    cancelled = 0
    for attempt in range(CANCEL_RETRIES):
        try:
            client = client_factory()
        except Exception:
            # Демон недоступен — отмена по кнопке не имеет права падать.
            if attempt + 1 < CANCEL_RETRIES:
                time.sleep(CANCEL_RETRY_DELAY_SEC)
                continue
            return cancelled

        pending = []
        for request_id in ids:
            if _cancel_one(client, request_id):
                cancelled += 1
            else:
                pending.append(request_id)

        if not pending:
            return cancelled
        ids = pending
        if attempt + 1 < CANCEL_RETRIES:
            time.sleep(CANCEL_RETRY_DELAY_SEC)

    return cancelled


def cancel_all(*, client_factory=None, background: bool = True) -> int:
    """Просит демон снять все висящие MCP-запросы.

    По умолчанию работает в фоновом потоке: вызывающий — поток интерфейса, и
    блокировать его синхронным HTTP нельзя. Возвращает число снятых запросов
    (в фоновом режиме — 0, результат придёт асинхронно).
    """
    client_factory = client_factory or _default_client_factory

    with _lock:
        ids = sorted(_inflight)
        _inflight.clear()

    if not ids:
        return 0

    if not background:
        return _cancel_ids(ids, client_factory)

    thread = threading.Thread(
        target=_cancel_ids,
        args=(ids, client_factory),
        name="mcp-cancel-all",
        daemon=True,
    )
    with _lock:
        _cancel_threads.add(thread)
    thread.start()
    return 0


def wait_for_pending_cancels(timeout: float = 5.0) -> None:
    """Только для тестов: дождаться фоновых отмен."""
    with _lock:
        threads = list(_cancel_threads)
        _cancel_threads.clear()
    for thread in threads:
        thread.join(timeout=timeout)
