"""Общая база для воркеров вида «выполнить callable в QThread».

``FunctionWorker`` (gemini_translator/utils/updater.py) и ``TaskDBWorker``
(gemini_translator/core/task_manager.py) реализуют один и тот же паттерн —
вызвать функцию в фоновом потоке и передать результат/ошибку наружу — но с
несовместимыми сигнальными протоколами: FunctionWorker эмитит
``done(result)``/``failed(str)``, а TaskDBWorker кладёт результат в
``self.result`` и не объявляет сигналов вовсе. Более десятка мест уже читают
``worker.result`` после ``finished`` у TaskDBWorker, поэтому протоколы не
объединяются (см. pcluster-66) — общей вынесена только логика ``run()``
(try/except вокруг вызова), а контракт результата/ошибки и хранение
аргументов остаются в подклассах.
"""

from PyQt6.QtCore import QThread


class _CallableThread(QThread):
    """Приватная база: run() вызывает :meth:`_call` и делегирует исход в хуки.

    Подклассы обязаны переопределить все три метода:

    * ``_call()`` — выполняет целевую функцию и возвращает результат;
    * ``_on_success(result)`` — обрабатывает успешный результат;
    * ``_on_error(exc)`` — обрабатывает исключение.
    """

    def _call(self):
        raise NotImplementedError

    def _on_success(self, result):
        raise NotImplementedError

    def _on_error(self, exc):
        raise NotImplementedError

    def run(self):
        try:
            result = self._call()
        except Exception as e:  # noqa: BLE001
            self._on_error(e)
        else:
            self._on_success(result)
