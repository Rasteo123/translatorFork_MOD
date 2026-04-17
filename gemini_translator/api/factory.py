import sys

# 1. Импортируем ВСЕ доступные хендлеры.
# Теперь они находятся в пространстве имен этого файла.
from . import handlers  # <-- Импортируем только пакет целиком!
from . import servers

# 1. Явная карта (для гарантии и переопределения)
HANDLER_CLASS_MAP = {
    "GeminiApiHandler": handlers.GeminiApiHandler,
    "OpenRouterApiHandler": handlers.OpenRouterApiHandler,
    "HuggingFaceApiHandler": handlers.HuggingFaceApiHandler,
    "DeepseekApiHandler": handlers.DeepseekApiHandler,
    "DryRunApiHandler": handlers.DryRunApiHandler,
    "LocalApiHandler": handlers.LocalApiHandler,
    "BrowserApiHandler": handlers.BrowserApiHandler,
    "WorkAsciiChatGptApiHandler": handlers.WorkAsciiChatGptApiHandler,
}

# 2. Server Map
SERVER_CLASS_MAP = {
    "PerplexityServer": servers.PerplexityServer
}

def get_api_handler_class(handler_name: str):
    """
    Возвращает класс обработчика.
    """
    
    # 1. Сначала проверяем карту (Приоритет №1 - переназначение)
    if handler_name in HANDLER_CLASS_MAP:
        return HANDLER_CLASS_MAP[handler_name]
    
    # 2. Автоматический поиск внутри пакета handlers (Приоритет №2 - по имени)
    # Мы спрашиваем у пакета handlers: "Есть ли у тебя атрибут с именем handler_name?"
    if hasattr(handlers, handler_name):
        return getattr(handlers, handler_name)

    # 3. Если не нашли
    raise ValueError(
        f"Обработчик API '{handler_name}' не найден.\n"
        f"Проверьте, что класс добавлен в api/handlers/__init__.py"
    )
    
def get_server_class(server_name: str):
    """Возвращает класс сервера (стратегию)."""
    if server_name in SERVER_CLASS_MAP:
        return SERVER_CLASS_MAP[server_name]
    if hasattr(servers, server_name):
        return getattr(servers, server_name)
    raise ValueError(f"Сервер '{server_name}' не найден.")
