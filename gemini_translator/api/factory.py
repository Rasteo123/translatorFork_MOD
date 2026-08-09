# Пакеты handlers/servers ленивые (PEP 562): обращение к атрибуту импортирует
# ровно один модуль хендлера. Прежние явные карты классов заставляли грузить
# ВСЕ хендлеры (и их зависимости: curl_cffi, playwright, flask, aiohttp —
# ~79МБ) при импорте factory, то есть при старте GUI.
from . import handlers
from . import servers


def get_api_handler_class(handler_name: str):
    """Возвращает класс обработчика; его модуль грузится при первом обращении."""
    try:
        return getattr(handlers, handler_name)
    except AttributeError:
        raise ValueError(
            f"Обработчик API '{handler_name}' не найден.\n"
            f"Проверьте, что класс добавлен в api/handlers/__init__.py"
        )


def get_server_class(server_name: str):
    """Возвращает класс сервера (стратегию)."""
    try:
        return getattr(servers, server_name)
    except AttributeError:
        raise ValueError(f"Сервер '{server_name}' не найден.")
