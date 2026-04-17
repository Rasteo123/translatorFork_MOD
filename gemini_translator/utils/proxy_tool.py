from PyQt6.QtCore import QObject, pyqtSlot


class GlobalProxyController(QObject):
    """
    Прокси-контроллер (режим 'Летописец').
    Не создает объектов соединений, только транслирует настройки.
    """
    def __init__(self, event_bus):
        super().__init__()
        self.event_bus = event_bus
        event_bus.event_posted.connect(self.on_event)
        print("[PROXY] GlobalProxyController (режим летописца) инициализирован.")

    @pyqtSlot(dict)
    def on_event(self, event: dict):
        event_name = event.get('event')
        # Реагируем на изменение настроек или старт
        if event_name == 'proxy_started' or event_name == 'proxy_settings_changed':
            settings = event.get('data', {})
            # Просто уведомляем всех заинтересованных (например, UI)
            self.event_bus.event_posted.emit({
                'event': 'current_proxy_status',
                'source': 'GlobalProxyController',
                'data': settings
            })