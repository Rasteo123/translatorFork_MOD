"""
Generic ServerManager.
Управляет любыми локальными серверами через паттерн Стратегия.
"""

import logging
from PyQt6.QtCore import QObject, pyqtSignal
from ..api.factory import get_server_class
from ..api import config as api_config

logger = logging.getLogger("LocalServerManager")
logger.setLevel(logging.INFO)

class GuiLogHandler(logging.Handler):
    def __init__(self, event_bus):
        super().__init__()
        self.event_bus = event_bus

    def emit(self, record):
        if self.event_bus:
            message = self.format(record)
            self.event_bus.event_posted.emit(
                {"event": "log_message", "data": {"message": message}}
            )

class ServerManager(QObject):
    server_status_changed = pyqtSignal(bool, str)
    server_log = pyqtSignal(str)

    def __init__(self, event_bus=None, settings_manager=None):
        super().__init__()
        self.event_bus = event_bus
        self.settings_manager = settings_manager
        self.server_instance = None 
        
        if self.event_bus:
            self.gui_handler = GuiLogHandler(self.event_bus)
            self.gui_handler.setFormatter(logging.Formatter("[LOCAL-SERVER] %(message)s"))
            logger.addHandler(self.gui_handler)

    def start_server(self, anonymous=True, provider_id=None):
        if self.is_server_running():
            self.server_log.emit("Сервер уже запущен.")
            return

        target_server_class_name = None
        
        if provider_id:
            provider_config = api_config.api_providers().get(provider_id, {})
            target_server_class_name = provider_config.get("server_class")
        
        if not target_server_class_name:
            self.server_log.emit("Ошибка: Не найдена конфигурация сервера для выбранного провайдера.")
            return

        try:
            ServerClass = get_server_class(target_server_class_name)
            self.server_instance = ServerClass()
            self.server_instance.start(anonymous=anonymous)
            
            url = self.server_instance.get_url()
            self.server_log.emit(f"Сервер ({target_server_class_name}) запущен: {url}")
            self.server_status_changed.emit(True, f"Running ({url})")
            
            if self.event_bus:
                 self.event_bus.event_posted.emit({
                    "event": "local_server_started",
                    "data": {"url": url, "provider_id": provider_id}
                })

        except Exception as e:
            self.server_log.emit(f"Критическая ошибка запуска: {e}")
            logger.exception("Server start failed")
            self.server_instance = None

    def stop_server(self):
        if self.server_instance:
             try:
                self.server_instance.stop()
             except Exception as e:
                self.server_log.emit(f"Ошибка при остановке: {e}")
             finally:
                self.server_instance = None
        
        self.server_log.emit("Сервер остановлен.")
        self.server_status_changed.emit(False, "Stopped")
        
        if self.event_bus:
            self.event_bus.event_posted.emit({"event": "local_server_stopped"})

    def is_server_running(self) -> bool:
         if self.server_instance:
             return self.server_instance.is_running()
         return False

    def validate_tokens_batch(self, tokens):
        if self.server_instance and self.is_server_running():
            return self.server_instance.validate_batch(tokens)
        return []