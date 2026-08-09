# -*- coding: utf-8 -*-
"""Фоновое обновление динамических моделей провайдера (HTTP discovery).

ensure_dynamic_provider_models синхронно опрашивает discovery-источники и при
лежащем локальном сервере блокирует вызвавший поток на таймаут по каждому
источнику. GUI-виджеты вместо прямого вызова используют DynamicModelsRefresher:
комбобоксы заполняются сразу из текущего реестра, а по завершении фонового
обновления получают сигнал refreshed(provider_id) в GUI-потоке.
"""

import threading

from PyQt6 import QtCore

from ...api import config as api_config


class DynamicModelsRefresher(QtCore.QObject):
    """Фоновое обновление моделей, не больше одного потока на провайдера."""

    refreshed = QtCore.pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        # Трогается только из GUI-потока: refresh_async и _finish_refresh
        # (queued-слот) выполняются в потоке владельца.
        self._in_flight = set()

    def refresh_async(self, provider_id, force: bool = False) -> bool:
        """Запускает фоновое обновление; True, если поток реально стартовал.

        Ничего не делает, когда обновление уже идёт или кэш discovery свеж
        (при force=False) — поэтому обработчики refreshed могут безопасно
        перезаполнять комбобоксы, не зацикливая обновления.
        """
        normalized = str(provider_id or "").strip()
        if not normalized or normalized in self._in_flight:
            return False
        if not force and not api_config.provider_needs_dynamic_model_refresh(normalized):
            return False

        self._in_flight.add(normalized)

        def run():
            try:
                api_config.ensure_dynamic_provider_models(normalized, force=force)
            except Exception:
                pass
            finally:
                try:
                    QtCore.QMetaObject.invokeMethod(
                        self,
                        "_finish_refresh",
                        QtCore.Qt.ConnectionType.QueuedConnection,
                        QtCore.Q_ARG(str, normalized),
                    )
                except RuntimeError:
                    pass  # владелец-виджет уже уничтожен

        threading.Thread(
            target=run, name=f"dynamic-models-{normalized}", daemon=True
        ).start()
        return True

    @QtCore.pyqtSlot(str)
    def _finish_refresh(self, provider_id: str):
        self._in_flight.discard(provider_id)
        self.refreshed.emit(provider_id)
